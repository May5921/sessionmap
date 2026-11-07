#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sessionmap canonical store.

Read a ZCode SQLite database without writing to it and project the useful
records into a small, local, append-friendly SQLite database.  The source
adapter is deliberately kept separate from the view and the semantic layer:
the same canonical tables can later receive Codex/Claude/Pi adapters.

Usage:
    python sync.py "pi-mini"
    python sync.py "pi-mini" --out sessionmap.db
    python sync.py "pi-mini" --json canonical.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

DEFAULT_SOURCE = os.path.expandvars(r"%USERPROFILE%\.zcode\cli\db\db.sqlite")
DEFAULT_STORE = str(Path(__file__).with_name("sessionmap.db"))


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS workspace (
    workspace_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_path TEXT NOT NULL,
    first_seen INTEGER,
    last_seen INTEGER,
    updated_at INTEGER NOT NULL,
    stats_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS session (
    session_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    parent_id TEXT,
    title TEXT NOT NULL DEFAULT '',
    directory TEXT NOT NULL DEFAULT '',
    started_at INTEGER,
    updated_at INTEGER,
    deleted_at INTEGER,
    raw_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_kind, source_id)
);
CREATE TABLE IF NOT EXISTS event (
    event_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    session_id TEXT,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'system',
    occurred_at INTEGER,
    summary TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    evidence_text TEXT NOT NULL DEFAULT '',
    raw_ref TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(source_kind, source_id),
    UNIQUE(fingerprint)
);
CREATE TABLE IF NOT EXISTS sync_state (
    source_kind TEXT NOT NULL,
    source_path TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    cursor_ms INTEGER NOT NULL DEFAULT 0,
    synced_at INTEGER NOT NULL,
    PRIMARY KEY(source_kind, source_path, workspace_id)
);
CREATE TABLE IF NOT EXISTS repository_snapshot (
    workspace_id TEXT PRIMARY KEY,
    root_path TEXT NOT NULL,
    branch TEXT NOT NULL DEFAULT '',
    upstream TEXT NOT NULL DEFAULT '',
    head_sha TEXT NOT NULL DEFAULT '',
    head_subject TEXT NOT NULL DEFAULT '',
    changed_count INTEGER NOT NULL DEFAULT 0,
    untracked_count INTEGER NOT NULL DEFAULT 0,
    status_json TEXT NOT NULL DEFAULT '[]',
    checked_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS repository_commit (
    workspace_id TEXT NOT NULL,
    sha TEXT NOT NULL,
    committed_at INTEGER,
    subject TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(workspace_id, sha)
);
CREATE TABLE IF NOT EXISTS analysis_run (
    run_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    semantic_source TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    facts_version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'succeeded',
    started_at INTEGER NOT NULL,
    completed_at INTEGER NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(workspace_id, input_hash)
);
CREATE TABLE IF NOT EXISTS decision (
    decision_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    occurred_at INTEGER,
    track TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    what TEXT NOT NULL DEFAULT '',
    why TEXT NOT NULL DEFAULT '',
    alternative TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    confidence REAL,
    source_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(run_id, external_id)
);
CREATE TABLE IF NOT EXISTS evidence_link (
    link_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    event_id TEXT,
    session_id TEXT,
    quote TEXT NOT NULL DEFAULT '',
    confidence REAL,
    rank INTEGER NOT NULL DEFAULT 0,
    source_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    UNIQUE(decision_id, event_id, quote)
);
CREATE TABLE IF NOT EXISTS decision_override (
    decision_id TEXT PRIMARY KEY,
    title TEXT,
    track TEXT,
    status TEXT,
    what TEXT,
    why TEXT,
    override_json TEXT NOT NULL DEFAULT '{}',
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS analysis_note (
    note_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    UNIQUE(run_id, kind)
);
CREATE TABLE IF NOT EXISTS analysis_event_snapshot (
    run_id TEXT NOT NULL, event_id TEXT NOT NULL, session_id TEXT,
    fingerprint TEXT NOT NULL, PRIMARY KEY(run_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_analysis_event_snapshot_event
    ON analysis_event_snapshot(event_id, run_id);
CREATE TABLE IF NOT EXISTS analysis_node_manifest (
    run_id TEXT NOT NULL, workspace_id TEXT NOT NULL, external_id TEXT NOT NULL,
    node_hash TEXT NOT NULL, event_ids_json TEXT NOT NULL DEFAULT '[]',
    event_hashes_json TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL,
    reason TEXT NOT NULL, affected INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL, PRIMARY KEY(run_id, external_id)
);
CREATE INDEX IF NOT EXISTS idx_analysis_node_manifest_workspace
    ON analysis_node_manifest(workspace_id, external_id, run_id);
CREATE INDEX IF NOT EXISTS idx_event_workspace_time ON event(workspace_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_event_session_time ON event(session_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_event_type ON event(event_type);
CREATE INDEX IF NOT EXISTS idx_session_workspace ON session(workspace_id, started_at);
CREATE INDEX IF NOT EXISTS idx_decision_workspace_time ON decision(workspace_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_evidence_decision ON evidence_link(decision_id, rank);
"""


def now_ms() -> int:
    return int(dt.datetime.now().timestamp() * 1000)


def parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def compact(text: Any, limit: int = 500) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def fingerprint(*values: Any) -> str:
    raw = "\x1f".join(str(v or "") for v in values)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def event_fingerprint(*, workspace_id: str, event_id: str, event_type: str,
                      actor: str, occurred_at: int | None, summary: str,
                      payload: Any, evidence_text: str, raw_ref: str) -> str:
    """Hash all mutable canonical event content deterministically.

    Event IDs make re-imports idempotent, while this hash tells the semantic
    layer when an existing event's payload or metadata actually changed.
    JSON sorting avoids false changes from dictionary insertion order.
    """
    value = {
        "workspace_id": workspace_id, "event_id": event_id,
        "event_type": event_type, "actor": actor, "occurred_at": occurred_at,
        "summary": summary, "payload": payload, "evidence_text": evidence_text,
        "raw_ref": raw_ref,
    }
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str).encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()


def source_connect(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        raise SystemExit(f"找不到 ZCode 数据库: {path}")
    con = sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def git_run(root: str, *args: str) -> str:
    try:
        proc = subprocess.run(["git", "-C", root, *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def sync_git_snapshot(store: sqlite3.Connection, workspace_id: str, directory: str, checked_at: int) -> dict[str, Any]:
    root = git_run(directory, "rev-parse", "--show-toplevel")
    if not root:
        store.execute("DELETE FROM repository_snapshot WHERE workspace_id=?", (workspace_id,))
        return {"available": False}
    branch = git_run(root, "branch", "--show-current")
    head_sha = git_run(root, "rev-parse", "HEAD")
    log = git_run(root, "log", "-8", "--date=unix", "--pretty=format:%H%x1f%ct%x1f%s")
    commits = []
    for line in log.splitlines():
        parts = line.split("\x1f", 2)
        if len(parts) != 3:
            continue
        sha, committed, subject = parts
        try:
            committed_at = int(committed) * 1000
        except ValueError:
            committed_at = None
        commits.append((workspace_id, sha, committed_at, subject))
    for row in commits:
        store.execute("INSERT OR REPLACE INTO repository_commit(workspace_id,sha,committed_at,subject) VALUES(?,?,?,?)", row)
        # Git commits are immutable facts.  Materialize them in the canonical
        # event stream so a later refresh can report new commits without
        # rescanning or re-analyzing the whole workspace.
        _, sha, committed_at, subject = row
        event_id = f"git:{workspace_id}:{sha}"
        payload = {"sha": sha, "subject": subject, "root": root.replace("\\", "/")}
        fp = event_fingerprint(workspace_id=workspace_id, event_id=event_id,
                               event_type="git_commit", actor="git",
                               occurred_at=committed_at, summary=subject,
                               payload=payload, evidence_text=subject,
                               raw_ref=f"git:{root}:{sha}")
        store.execute(
            """INSERT INTO event(event_id,workspace_id,session_id,source_kind,source_id,event_type,actor,
               occurred_at,summary,payload_json,evidence_text,raw_ref,fingerprint,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET
               occurred_at=excluded.occurred_at,summary=excluded.summary,payload_json=excluded.payload_json,
               evidence_text=excluded.evidence_text,fingerprint=excluded.fingerprint""",
            (event_id, workspace_id, None, "git", sha, "git_commit", "git", committed_at,
             subject, json.dumps(payload, ensure_ascii=False), subject, f"git:{root}:{sha}",
             fp, checked_at),
        )

    status = git_run(root, "status", "--porcelain=v1", "-b")
    status_lines = status.splitlines()
    header = status_lines[0] if status_lines and status_lines[0].startswith("##") else ""
    upstream = ""
    if "..." in header:
        upstream = header.split("...", 1)[1].split(" ", 1)[0]
    entries = []
    untracked = 0
    for line in status_lines[1:]:
        if not line.strip():
            continue
        code = line[:2]
        path = line[3:].strip() if len(line) > 3 else ""
        is_untracked = code == "??"
        untracked += int(is_untracked)
        entries.append({"code": code, "path": path, "untracked": is_untracked})
    snapshot = {"available": True, "root": root.replace("\\", "/"), "branch": branch,
                "upstream": upstream, "head_sha": head_sha, "head_subject": commits[0][3] if commits else "",
                "changed_count": len(entries), "untracked_count": untracked, "entries": entries[:80]}
    status_payload = {"branch": branch, "upstream": upstream, "head_sha": head_sha,
                      "changed_count": len(entries), "untracked_count": untracked,
                      "entries": entries[:80]}
    status_hash = fingerprint(workspace_id, "git_status", branch, head_sha,
                              json.dumps(entries[:80], ensure_ascii=False, sort_keys=True))
    status_event_id = f"git:status:{workspace_id}:{status_hash[:16]}"
    store.execute(
        """INSERT INTO event(event_id,workspace_id,session_id,source_kind,source_id,event_type,actor,
           occurred_at,summary,payload_json,evidence_text,raw_ref,fingerprint,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING""",
        (status_event_id, workspace_id, None, "git", status_event_id, "git_status", "git", checked_at,
         f"{branch or 'detached'} · {len(entries)} 个改动文件 · 未跟踪 {untracked}",
         json.dumps(status_payload, ensure_ascii=False),
         json.dumps(status_payload, ensure_ascii=False), f"git-status:{root}", status_hash, checked_at),
    )
    store.execute(
        """INSERT INTO repository_snapshot(workspace_id,root_path,branch,upstream,head_sha,head_subject,
           changed_count,untracked_count,status_json,checked_at) VALUES(?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(workspace_id) DO UPDATE SET root_path=excluded.root_path,branch=excluded.branch,
           upstream=excluded.upstream,head_sha=excluded.head_sha,head_subject=excluded.head_subject,
           changed_count=excluded.changed_count,untracked_count=excluded.untracked_count,
           status_json=excluded.status_json,checked_at=excluded.checked_at""",
        (workspace_id, snapshot["root"], branch, upstream, head_sha, snapshot["head_subject"],
         len(entries), untracked, json.dumps(entries[:80], ensure_ascii=False), checked_at),
    )
    snapshot["commits"] = len(commits)
    return snapshot


def store_connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def find_workspace(source: sqlite3.Connection, workspace: str) -> tuple[str, str, list[sqlite3.Row]]:
    rows = source.execute(
        """
        SELECT id, project_id, parent_id, title, directory, time_created, time_updated,
               time_deleted, task_type, last_outcome, summary_files, summary_additions,
               summary_deletions
        FROM session
        WHERE directory LIKE ? AND time_deleted IS NULL
        ORDER BY time_created ASC
        """,
        ("%" + workspace + "%",),
    ).fetchall()
    if not rows:
        raise SystemExit(f"该工作区没有会话: {workspace}")
    directory = rows[0]["directory"]
    # The workspace id is stable across adapters but does not expose the raw
    # path as the primary key used by views.
    workspace_id = "zcode:" + hashlib.sha1(directory.encode("utf-8")).hexdigest()[:16]
    return workspace_id, directory, rows


def real_user_message(message: dict[str, Any]) -> bool:
    if message.get("role") != "user":
        return False
    if message.get("synthetic"):
        return False
    metadata = message.get("metadata") or {}
    if metadata.get("visibility") == "model-only":
        return False
    semantics = message.get("semantics") or {}
    if semantics.get("origin") not in (None, "real_user"):
        return False
    return True


def extract_part(part: sqlite3.Row, messages: dict[str, dict[str, Any]]) -> tuple[str, str, str, dict[str, Any], str] | None:
    data = parse_json(part["data"])
    kind = data.get("type")
    message = messages.get(part["message_id"], {})
    actor = "user" if message.get("role") == "user" else "assistant"
    occurred = part["time_created"] or message.get("time", {}).get("created")
    raw_ref = f"part:{part['id']}"

    if kind == "text":
        text = compact(data.get("text"), 1200)
        if not text:
            return None
        if actor == "user" and not real_user_message(message):
            return None
        return ("user_message" if actor == "user" else "assistant_message",
                actor, text, {"text": text, "message_id": part["message_id"]}, raw_ref)

    if kind == "tool":
        tool = data.get("tool") or data.get("name") or "tool"
        state = data.get("state") or {}
        inp = state.get("input") or data.get("input") or {}
        if not isinstance(inp, dict):
            inp = {"value": compact(inp)}
        file_path = inp.get("filePath") or inp.get("file_path") or inp.get("path")
        command = inp.get("command")
        detail = file_path or command or inp.get("url") or ""
        summary = f"{tool}{' · ' + compact(detail, 180) if detail else ''}"
        payload = {"tool": tool, "input": inp, "message_id": part["message_id"]}
        return ("tool_call", "assistant", compact(summary), payload, raw_ref)

    if kind == "compaction":
        pre = data.get("preCompactTokenCount") or 0
        post = data.get("truePostCompactTokenCount") or 0
        if not pre or not post:
            return None
        pct = round(100 * (1 - post / pre), 1)
        return ("memory_break", "system", f"上下文压缩 {pct:g}% · {pre:,} → {post:,} token",
                {"pre_tokens": pre, "post_tokens": post, "loss_pct": pct,
                 "reason": data.get("compactReason")}, raw_ref)

    if kind in ("error", "step-finish", "step-start"):
        if kind == "error":
            text = compact(data.get("error") or data.get("message") or data, 600)
            return ("error", "system", text, {"data": data}, raw_ref)
        return None
    return None


def sync_workspace(workspace: str, source_path: str, store_path: str) -> dict[str, Any]:
    source = source_connect(source_path)
    store = store_connect(store_path)
    try:
        workspace_id, directory, sessions = find_workspace(source, workspace)
        seen_at = now_ms()
        first_seen = min(r["time_created"] for r in sessions if r["time_created"])
        last_seen = max((r["time_updated"] or r["time_created"] or 0) for r in sessions)
        store.execute(
            """INSERT INTO workspace(workspace_id,name,source_kind,source_path,first_seen,last_seen,updated_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(workspace_id) DO UPDATE SET name=excluded.name,
               last_seen=excluded.last_seen,updated_at=excluded.updated_at""",
            (workspace_id, directory, "zcode", source_path, first_seen, last_seen, seen_at),
        )

        session_ids = {r["id"] for r in sessions}
        for row in sessions:
            raw = {k: row[k] for k in row.keys()}
            store.execute(
                """INSERT INTO session(session_id,workspace_id,source_kind,source_id,parent_id,title,directory,
                   started_at,updated_at,deleted_at,raw_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(session_id) DO UPDATE SET parent_id=excluded.parent_id,title=excluded.title,
                   updated_at=excluded.updated_at,deleted_at=excluded.deleted_at,raw_json=excluded.raw_json""",
                (row["id"], workspace_id, "zcode", row["id"], row["parent_id"], row["title"] or "",
                 row["directory"] or "", row["time_created"], row["time_updated"], row["time_deleted"],
                 json.dumps(raw, ensure_ascii=False)),
            )
            # ZCode exposes parent/child sessions and timestamps, but not a
            # separate sub-agent lifecycle stream. Derive stable observed
            # boundaries so the canonical contract can represent it without
            # inventing completion details.
            lifecycle = "subagent" if row["parent_id"] else "session"
            title = (row["title"] or "未命名会话").strip()
            completed = bool(row["last_outcome"])
            end_label = "结束" if completed else "最后观测"
            for event_id, event_type, occurred, summary in (
                (f"zcode:{lifecycle}_start:{row['id']}", f"{lifecycle}_start",
                 row["time_created"], f"{title} · 开始"),
                (f"zcode:{lifecycle}_end:{row['id']}", f"{lifecycle}_end",
                 row["time_updated"], f"{title} · {end_label}"),
            ):
                if not occurred:
                    continue
                payload = {"session_id": row["id"], "parent_session_id": row["parent_id"],
                           "title": title, "boundary": event_type,
                           "completed": completed if event_type.endswith("_end") else None,
                           "last_outcome": row["last_outcome"] if event_type.endswith("_end") else None}
                fp = event_fingerprint(workspace_id=workspace_id, event_id=event_id,
                                       event_type=event_type, actor="system",
                                       occurred_at=occurred, summary=summary,
                                       payload=payload, evidence_text=summary,
                                       raw_ref=f"session:{row['id']}")
                store.execute(
                    """INSERT INTO event(event_id,workspace_id,session_id,source_kind,source_id,event_type,actor,
                       occurred_at,summary,payload_json,evidence_text,raw_ref,fingerprint,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET
                       occurred_at=excluded.occurred_at,summary=excluded.summary,payload_json=excluded.payload_json,
                       evidence_text=excluded.evidence_text,fingerprint=excluded.fingerprint""",
                    (event_id, workspace_id, row["id"], "zcode", event_id, event_type, "system",
                     occurred, summary, json.dumps(payload, ensure_ascii=False), summary,
                     f"session:{row['id']}", fp, seen_at),
                )

        # Build a message role index once; it lets us filter model-only text
        # without relying on fragile string prefixes.
        ph = ",".join("?" for _ in session_ids)
        messages = {}
        for row in source.execute(f"SELECT id,data FROM message WHERE session_id IN ({ph})", tuple(session_ids)):
            messages[row["id"]] = parse_json(row["data"])

        state = store.execute(
            "SELECT cursor_ms FROM sync_state WHERE source_kind=? AND source_path=? AND workspace_id=?",
            ("zcode", source_path, workspace_id),
        ).fetchone()
        cursor = int(state["cursor_ms"] if state else 0)
        # Parts are append-only in ZCode. Reading from the last timestamp keeps
        # the normal refresh cheap while the overlap handles equal timestamps.
        part_sql = f"""
            SELECT id,message_id,session_id,time_created,data
            FROM part WHERE session_id IN ({ph}) AND time_created >= ?
              AND json_extract(data,'$.type') != 'compaction'
            ORDER BY time_created ASC, sequence ASC
        """
        params = tuple(session_ids) + (max(0, cursor - 1),)
        event_count = 0
        max_cursor = cursor
        for row in source.execute(part_sql, params):
            parsed = extract_part(row, messages)
            max_cursor = max(max_cursor, row["time_created"] or 0)
            if parsed is None:
                continue
            event_type, actor, summary, payload, raw_ref = parsed
            if event_type == "memory_break":
                # ZCode records the same compaction in each related session.
                # Collapse those copies into one workspace-level event.
                event_id = "zcode:compaction:%s:%s:%s" % (
                    row["time_created"], payload.get("pre_tokens"), payload.get("post_tokens"))
            else:
                event_id = f"zcode:{row['id']}"
            payload_json = json.dumps(payload, ensure_ascii=False)
            evidence_text = payload.get("text", summary)
            fp = event_fingerprint(workspace_id=workspace_id, event_id=event_id,
                                   event_type=event_type, actor=actor,
                                   occurred_at=row["time_created"], summary=summary,
                                   payload=payload, evidence_text=evidence_text,
                                   raw_ref=raw_ref)
            store.execute(
                """INSERT INTO event(event_id,workspace_id,session_id,source_kind,source_id,event_type,actor,
                   occurred_at,summary,payload_json,evidence_text,raw_ref,fingerprint,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(event_id) DO UPDATE SET summary=excluded.summary,
                   payload_json=excluded.payload_json,evidence_text=excluded.evidence_text,
                   occurred_at=excluded.occurred_at,fingerprint=excluded.fingerprint""",
                (event_id, workspace_id, row["session_id"], "zcode", row["id"], event_type, actor,
                 row["time_created"], summary, payload_json,
                 evidence_text, raw_ref, fp, seen_at),
            )
            event_count += 1

        # Compaction is a session-level fact.  ZCode may write the same
        # compaction part into more than one child session, so rebuild this
        # tiny event class from all source rows and collapse by its payload.
        store.execute("DELETE FROM event WHERE workspace_id=? AND event_type='memory_break'",
                      (workspace_id,))
        comp_sql = f"""
            SELECT id,session_id,time_created,data
            FROM part WHERE session_id IN ({ph})
              AND json_extract(data,'$.type')='compaction'
            ORDER BY time_created ASC
        """
        seen_compactions: set[tuple[int, int, int]] = set()
        for row in source.execute(comp_sql, tuple(session_ids)):
            data = parse_json(row["data"])
            pre = data.get("preCompactTokenCount") or 0
            post = data.get("truePostCompactTokenCount") or 0
            key = (row["time_created"] or 0, pre, post)
            if not pre or not post or key in seen_compactions:
                continue
            seen_compactions.add(key)
            loss = round(100 * (1 - post / pre), 1)
            payload = {"pre_tokens": pre, "post_tokens": post, "loss_pct": loss,
                       "reason": data.get("compactReason")}
            event_id = "zcode:compaction:%s:%s:%s" % key
            summary = f"上下文压缩 {loss:g}% · {pre:,} → {post:,} token"
            fp = event_fingerprint(workspace_id=workspace_id, event_id=event_id,
                                   event_type="memory_break", actor="system",
                                   occurred_at=row["time_created"], summary=summary,
                                   payload=payload, evidence_text=summary,
                                   raw_ref=f"part:{row['id']}")
            store.execute(
                """INSERT INTO event(event_id,workspace_id,session_id,source_kind,source_id,event_type,actor,
                   occurred_at,summary,payload_json,evidence_text,raw_ref,fingerprint,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(event_id) DO UPDATE SET summary=excluded.summary,
                   payload_json=excluded.payload_json,evidence_text=excluded.evidence_text,
                   occurred_at=excluded.occurred_at,fingerprint=excluded.fingerprint""",
                (event_id, workspace_id, row["session_id"], "zcode", event_id, "memory_break", "system",
                 row["time_created"], summary, json.dumps(payload, ensure_ascii=False), summary,
                 f"part:{row['id']}", fp, seen_at),
            )

        todo_count = 0
        for row in source.execute(f"SELECT rowid,session_id,status,priority,position,content,time_created,time_updated FROM todo WHERE session_id IN ({ph})", tuple(session_ids)):
            payload = {"status": row["status"], "priority": row["priority"], "position": row["position"],
                       "content": row["content"], "todo_rowid": row["rowid"]}
            # Older stores used one mutable event per session/position.  Keep
            # that ID when the state is unchanged, and append a state-keyed ID
            # when the item is reopened, completed, or rewritten.  This keeps
            # refreshes idempotent while preserving state transitions.
            legacy_id = f"zcode:todo:{row['session_id']}:{row['position']}"
            state_hash = fingerprint(workspace_id, row["session_id"], row["position"],
                                     row["status"], row["content"])
            event_id = legacy_id
            old = store.execute("SELECT payload_json FROM event WHERE event_id=?", (legacy_id,)).fetchone()
            if old:
                old_payload = parse_json(old[0])
                if (old_payload.get("status"), old_payload.get("content")) != (row["status"], row["content"]):
                    event_id = f"zcode:todo:{row['session_id']}:{row['position']}:{state_hash[:16]}"
            summary = f"[{row['status']}] {compact(row['content'], 220)}"
            evidence_text = row["content"] or ""
            fp = event_fingerprint(workspace_id=workspace_id, event_id=event_id,
                                   event_type="todo_change", actor="system",
                                   occurred_at=row["time_updated"] or row["time_created"],
                                   summary=summary, payload=payload,
                                   evidence_text=evidence_text, raw_ref=event_id)
            store.execute(
                """INSERT INTO event(event_id,workspace_id,session_id,source_kind,source_id,event_type,actor,
                   occurred_at,summary,payload_json,evidence_text,raw_ref,fingerprint,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(event_id) DO UPDATE SET summary=excluded.summary,payload_json=excluded.payload_json,
                   evidence_text=excluded.evidence_text,occurred_at=excluded.occurred_at,fingerprint=excluded.fingerprint""",
                (event_id, workspace_id, row["session_id"], "zcode", event_id, "todo_change", "system",
                 row["time_updated"] or row["time_created"], summary,
                 json.dumps(payload, ensure_ascii=False), evidence_text, event_id, fp, seen_at),
            )
            todo_count += 1

        store.execute(
            """INSERT INTO sync_state(source_kind,source_path,workspace_id,cursor_ms,synced_at)
               VALUES(?,?,?,?,?) ON CONFLICT(source_kind,source_path,workspace_id)
               DO UPDATE SET cursor_ms=excluded.cursor_ms,synced_at=excluded.synced_at""",
            ("zcode", source_path, workspace_id, max_cursor, seen_at),
        )
        stats = {
            "sessions": len(sessions),
            "main_sessions": sum(1 for r in sessions if not r["parent_id"]),
            "child_sessions": sum(1 for r in sessions if r["parent_id"]),
            "events_seen": event_count,
            "todos_seen": todo_count,
            "cursor_ms": max_cursor,
        }
        git = sync_git_snapshot(store, workspace_id, directory, seen_at)
        stats["git_available"] = git.get("available", False)
        if git.get("available"):
            stats["git_branch"] = git.get("branch", "")
            stats["git_changed"] = git.get("changed_count", 0)
        store.execute("UPDATE workspace SET stats_json=?, updated_at=? WHERE workspace_id=?",
                      (json.dumps(stats, ensure_ascii=False), seen_at, workspace_id))
        store.commit()
        return {"workspace_id": workspace_id, "workspace": directory, "stats": stats,
                "store": store_path, "incremental": bool(cursor)}
    finally:
        source.close()
        store.close()


def export_json(store_path: str, workspace_id: str, out_path: str) -> dict[str, Any]:
    con = sqlite3.connect(store_path)
    con.row_factory = sqlite3.Row
    ws = con.execute("SELECT * FROM workspace WHERE workspace_id=?", (workspace_id,)).fetchone()
    if not ws:
        raise SystemExit("找不到已同步的工作区")
    sessions = [dict(r) for r in con.execute("SELECT * FROM session WHERE workspace_id=? ORDER BY started_at", (workspace_id,))]
    events = [dict(r) for r in con.execute("SELECT * FROM event WHERE workspace_id=? ORDER BY occurred_at", (workspace_id,))]
    repo = con.execute("SELECT * FROM repository_snapshot WHERE workspace_id=?", (workspace_id,)).fetchone()
    commits = [dict(r) for r in con.execute("SELECT * FROM repository_commit WHERE workspace_id=? ORDER BY committed_at DESC", (workspace_id,))]
    result = {"workspace": dict(ws), "sessions": sessions, "events": events,
              "repository": dict(repo) if repo else None, "commits": commits}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    con.close()
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="把 ZCode 工作区同步到 sessionmap 的统一事件库")
    ap.add_argument("workspace", help="工作区路径或名称关键字，例如 pi-mini")
    ap.add_argument("--db", default=DEFAULT_SOURCE, help="ZCode 源数据库，只读")
    ap.add_argument("--out", default=DEFAULT_STORE, help="sessionmap 本地数据库")
    ap.add_argument("--json", default=None, help="同步后导出 canonical JSON")
    args = ap.parse_args()
    result = sync_workspace(args.workspace, args.db, args.out)
    if args.json:
        export_json(args.out, result["workspace_id"], args.json)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
