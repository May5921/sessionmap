#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Project the canonical store into a source-neutral project timeline.

The event table is the factual layer.  The latest semantic run is the
human-facing layer.  This module joins them without knowing whether the
events came from ZCode, Claude, Codex, or another adapter.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any


def fmt_ms(value: int | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not value:
        return "—"
    return dt.datetime.fromtimestamp(value / 1000).strftime(fmt)


def parse_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except Exception:
        return default


def choose_workspace(con: sqlite3.Connection, workspace_id: str | None = None) -> dict[str, Any]:
    con.row_factory = sqlite3.Row
    if workspace_id:
        row = con.execute("SELECT * FROM workspace WHERE workspace_id=?", (workspace_id,)).fetchone()
    else:
        row = con.execute("SELECT * FROM workspace ORDER BY updated_at DESC LIMIT 1").fetchone()
    if not row:
        raise ValueError("sessionmap.db 里还没有匹配的工作区")
    return dict(row)


def latest_run(con: sqlite3.Connection, workspace_id: str) -> dict[str, Any] | None:
    row = con.execute(
        "SELECT * FROM analysis_run WHERE workspace_id=? AND status='succeeded' "
        "ORDER BY completed_at DESC, run_id DESC LIMIT 1",
        (workspace_id,),
    ).fetchone()
    return dict(row) if row else None


def _override(con: sqlite3.Connection, decision_id: str) -> dict[str, Any]:
    row = con.execute("SELECT * FROM decision_override WHERE decision_id=?", (decision_id,)).fetchone()
    return dict(row) if row else {}


def load_decisions(con: sqlite3.Connection, workspace_id: str, run: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not run:
        return []
    rows = con.execute(
        "SELECT * FROM decision WHERE workspace_id=? AND run_id=? ORDER BY ordinal, occurred_at, decision_id",
        (workspace_id, run["run_id"]),
    ).fetchall()
    decisions: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        override = _override(con, item["decision_id"])
        for key in ("title", "track", "status", "what", "why"):
            if override.get(key) is not None:
                item[key] = override[key]
        source = parse_json(item.get("source_json"), {})
        item["id"] = item["external_id"]
        item["when"] = fmt_ms(item.get("occurred_at"))
        item["when_ms"] = item.get("occurred_at")
        item["alternative"] = item.get("alternative") or source.get("alternative")
        item["stage"] = item.get("stage") or source.get("stage")
        item["evidence"] = source.get("evidence", "")
        refs = con.execute(
            "SELECT * FROM evidence_link WHERE decision_id=? ORDER BY rank, link_id",
            (item["decision_id"],),
        ).fetchall()
        item["evidence_refs"] = []
        for ref in refs:
            evidence = dict(ref)
            source_ref = parse_json(evidence.get("source_json"), {})
            evidence["session_title"] = source_ref.get("session_title", "未命名会话")
            evidence["at"] = source_ref.get("at")
            evidence["when"] = fmt_ms(evidence.get("at") or evidence.get("created_at"))
            evidence["type"] = source_ref.get("type")
            evidence["summary"] = source_ref.get("summary", evidence.get("quote", ""))
            event_row = con.execute(
                "SELECT source_kind,raw_ref,event_type,payload_json FROM event WHERE event_id=?",
                (evidence.get("event_id"),),
            ).fetchone()
            event_payload = parse_json(event_row["payload_json"], {}) if event_row else {}
            item["evidence_refs"].append({
                "event_id": evidence.get("event_id"),
                "session_id": evidence.get("session_id"),
                "session_title": evidence.get("session_title"),
                "at": evidence.get("at"),
                "when": evidence.get("when"),
                "type": evidence.get("type"),
                "summary": evidence.get("summary"),
                "quote": evidence.get("quote", ""),
                "confidence": evidence.get("confidence"),
                "rank": evidence.get("rank", 0),
                "source_kind": event_row["source_kind"] if event_row else source_ref.get("source_kind"),
                "raw_ref": event_row["raw_ref"] if event_row else source_ref.get("raw_ref"),
                "event_type": event_row["event_type"] if event_row else evidence.get("type"),
                "fact": _fact_payload(event_payload),
            })
        item["evidence_count"] = len(item["evidence_refs"])
        for key in ("decision_id", "run_id", "workspace_id", "external_id", "ordinal", "occurred_at",
                    "source_json", "created_at", "updated_at"):
            item.pop(key, None)
        decisions.append(item)
    return decisions


def load_notes(con: sqlite3.Connection, workspace_id: str, run: dict[str, Any] | None) -> dict[str, Any]:
    if not run:
        return {}
    notes: dict[str, Any] = {}
    for row in con.execute("SELECT kind,content_json FROM analysis_note WHERE workspace_id=? AND run_id=?", (workspace_id, run["run_id"])):
        notes[row[0]] = parse_json(row[1], None)
    return notes


def _fact_payload(payload: Any) -> dict[str, Any]:
    """Keep only small, inspectable fields for an evidence drawer."""
    data = payload if isinstance(payload, dict) else {}
    keys = ("tool", "command", "path", "filePath", "file_path", "status", "content",
            "sha", "subject", "branch", "head_sha", "changed_count", "untracked_count")
    return {key: data[key] for key in keys if key in data and data[key] not in (None, "", [])}


def semantic_changes(con: sqlite3.Connection, workspace_id: str, run: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return only changes that can be proven from adjacent semantic runs."""
    if not run:
        return []
    runs = [dict(row) for row in con.execute(
        "SELECT * FROM analysis_run WHERE workspace_id=? AND status='succeeded' "
        "ORDER BY completed_at DESC, run_id DESC", (workspace_id,)
    )]
    if len(runs) < 2:
        return []
    current, previous = runs[0], runs[1]
    def decision_map(run_id: str) -> dict[str, dict[str, Any]]:
        return {row["external_id"]: dict(row) for row in con.execute(
            "SELECT external_id,occurred_at,title,status,track FROM decision WHERE workspace_id=? AND run_id=?",
            (workspace_id, run_id),
        )}
    now = decision_map(current["run_id"])
    old = decision_map(previous["run_id"])
    out: list[dict[str, Any]] = []
    for external_id, item in now.items():
        before = old.get(external_id)
        if before and before.get("status") != item.get("status"):
            out.append({"id": f"status:{external_id}:{item.get('occurred_at')}", "kind": "status_reversal",
                        "at": item.get("occurred_at") or current["completed_at"],
                        "when": fmt_ms(item.get("occurred_at") or current["completed_at"]),
                        "summary": f"{item.get('title') or external_id} · {before.get('status')} → {item.get('status')}",
                        "status": item.get("status"), "source": "semantic"})
        elif not before:
            out.append({"id": f"decision:{external_id}:{item.get('occurred_at')}", "kind": "decision_new",
                        "at": item.get("occurred_at") or current["completed_at"],
                        "when": fmt_ms(item.get("occurred_at") or current["completed_at"]),
                        "summary": item.get("title") or external_id, "status": item.get("status"),
                        "source": "semantic"})
    return out


def build_canonical_timeline(store_path: str, workspace_id: str | None = None) -> dict[str, Any]:
    """Return a stable, source-neutral projection for one workspace."""
    con = sqlite3.connect(store_path)
    con.row_factory = sqlite3.Row
    try:
        workspace = choose_workspace(con, workspace_id)
        wid = workspace["workspace_id"]
        sessions = [dict(row) for row in con.execute(
            "SELECT * FROM session WHERE workspace_id=? ORDER BY started_at, session_id", (wid,)
        )]
        events = []
        session_titles = {row["session_id"]: row["title"] or "未命名会话" for row in con.execute(
            "SELECT session_id,title FROM session WHERE workspace_id=?", (wid,)
        )}
        for row in con.execute("SELECT * FROM event WHERE workspace_id=? ORDER BY occurred_at, event_id", (wid,)):
            event = dict(row)
            event["payload"] = parse_json(event.pop("payload_json", "{}"), {})
            event["when"] = fmt_ms(event.get("occurred_at"))
            event["session_title"] = session_titles.get(event.get("session_id"), "未命名会话")
            events.append(event)

        run = latest_run(con, wid)
        notes = load_notes(con, wid, run)
        decisions = load_decisions(con, wid, run)
        semantic_summary = parse_json(run.get("summary_json"), {}) if run else {}
        changes: list[dict[str, Any]] = []
        event_change_types = {
            "todo_change": "todo", "memory_break": "memory_break", "error": "error",
            "git_commit": "git_commit", "git_status": "git_status",
        }
        for event in events:
            kind = event_change_types.get(event.get("event_type"))
            if not kind:
                continue
            changes.append({"id": event["event_id"], "kind": kind, "at": event.get("occurred_at"),
                            "when": event.get("when"), "summary": event.get("summary"),
                            "status": event.get("payload", {}).get("status"),
                            "source": event.get("source_kind"), "session_title": event.get("session_title")})
        for decision in decisions:
            changes.append({"id": f"decision:{decision.get('id')}:{decision.get('when_ms')}",
                            "kind": "decision", "at": decision.get("when_ms"),
                            "when": decision.get("when"), "summary": decision.get("title"),
                            "status": decision.get("status"), "source": "semantic"})
        changes.extend(semantic_changes(con, wid, run))
        changes.sort(key=lambda item: (item.get("at") or 0, item.get("id") or ""))
        repository = con.execute("SELECT * FROM repository_snapshot WHERE workspace_id=?", (wid,)).fetchone()
        repository_view = dict(repository) if repository else None
        if repository_view:
            repository_view["entries"] = parse_json(repository_view.pop("status_json", "[]"), [])
            repository_view["commits"] = [dict(row) for row in con.execute(
                "SELECT sha,committed_at,subject FROM repository_commit WHERE workspace_id=? "
                "ORDER BY committed_at DESC LIMIT 8", (wid,)
            )]

        return {
            "schema_version": 2,
            "generated_at": int(dt.datetime.now().timestamp() * 1000),
            "workspace": {"id": wid, "name": workspace["name"], "source": workspace["source_kind"],
                          "source_path": workspace["source_path"]},
            "semantic": {"run_id": run["run_id"] if run else None,
                         "source": run["semantic_source"] if run else None,
                         "input_hash": run["input_hash"] if run else None,
                         "model": run["model"] if run else None,
                         "prompt_version": run["prompt_version"] if run else None,
                         "facts_version": run["facts_version"] if run else None,
                         "completed_at": run["completed_at"] if run else None,
                         "affected_nodes": semantic_summary.get("affected_nodes") if run else None,
                         "previous_run_id": semantic_summary.get("previous_run_id") if run else None,
                         "mode": "semantic" if run and decisions else "facts-only"},
            "decisions": decisions,
            "events": events,
            "sessions": sessions,
            "notes": notes,
            "changes": changes,
            "repository": repository_view,
        }
    finally:
        con.close()


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="生成工具无关的 canonical timeline")
    ap.add_argument("store")
    ap.add_argument("--workspace-id", default=None)
    ap.add_argument("--out", default="canonical-timeline.json")
    args = ap.parse_args()
    result = build_canonical_timeline(args.store, args.workspace_id)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": args.out, "workspace_id": result["workspace"]["id"],
                      "events": len(result["events"]), "decisions": len(result["decisions"]),
                      "mode": result["semantic"]["mode"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
