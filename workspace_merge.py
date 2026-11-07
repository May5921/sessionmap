#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Merge source-specific canonical stores into one workspace store.

The source stores remain untouched.  A merged store gets one workspace ID per
normalized repository root while sessions and events keep their source kind,
source IDs and raw references.  Semantic decisions from the latest successful
run of each source are copied into one synthetic merged run so the existing
view layer can render a cross-agent story without source-specific branches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from sync import SCHEMA, event_fingerprint, now_ms, store_connect


def normalize_root(value: str) -> str:
    text = str(value or "").replace("\\", "/").rstrip("/")
    return text.lower() or "."


def merged_workspace_id(root: str) -> str:
    return "workspace:" + hashlib.sha1(normalize_root(root).encode("utf-8")).hexdigest()[:16]


def workspace_root(row: sqlite3.Row) -> str:
    """Resolve the actual repository root represented by a workspace row.

    ZCode stores the source database path in ``source_path`` and the actual
    repository directory in ``name``; file-backed adapters store the root in
    ``source_path``.  Keeping this distinction here prevents ZCode from being
    stranded as a separate workspace during a multi-agent merge.
    """
    source = str(row["source_kind"] or "")
    name = str(row["name"] or "")
    if source == "zcode" and (":" in name or "/" in name or "\\" in name):
        return normalize_root(name)
    return normalize_root(row["source_path"])


def open_readonly(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect("file:" + str(Path(path).resolve()).replace("\\", "/") + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _workspace_rows(con: sqlite3.Connection, root_filter: str | None) -> list[sqlite3.Row]:
    rows = con.execute("SELECT * FROM workspace ORDER BY workspace_id").fetchall()
    if not root_filter:
        return rows
    target = normalize_root(root_filter)
    return [row for row in rows if workspace_root(row) == target]


def _source_workspace_key(row: sqlite3.Row) -> tuple[str, str]:
    return str(row["source_kind"]), normalize_root(row["source_path"])


def _load_latest_runs(con: sqlite3.Connection, workspace_id: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM analysis_run WHERE workspace_id=? AND status='succeeded' "
        "ORDER BY completed_at DESC, run_id DESC LIMIT 1", (workspace_id,)
    ).fetchone()


def merge_stores(input_paths: Iterable[str | Path], output_path: str | Path,
                 root_filter: str | None = None) -> dict[str, Any]:
    """Merge all matching source workspaces into ``output_path``.

    The output is deterministic for the same source snapshots.  Existing
    output rows for the selected merged roots are replaced, which makes a
    repeated refresh idempotent while leaving every source database read-only.
    """
    sources = [Path(path) for path in input_paths]
    if not sources:
        raise ValueError("至少需要一个源 store")
    output = Path(output_path)
    output_resolved = output.resolve()
    if any(path.resolve() == output_resolved for path in sources):
        raise ValueError("合并输出不能覆盖源 store")
    store = store_connect(str(output))
    selected: list[tuple[sqlite3.Connection, sqlite3.Row]] = []
    try:
        for path in sources:
            con = open_readonly(path)
            for row in _workspace_rows(con, root_filter):
                selected.append((con, row))
        if not selected:
            raise ValueError("没有匹配的工作区")

        roots = sorted({workspace_root(row) for _, row in selected})
        stamp = now_ms()
        result_roots: list[dict[str, Any]] = []
        for root in roots:
            workspace_id = merged_workspace_id(root)
            # Rebuild only this merged root so reruns do not duplicate events.
            old_sessions = [r[0] for r in store.execute(
                "SELECT session_id FROM session WHERE workspace_id=?", (workspace_id,)
            ).fetchall()]
            if old_sessions:
                store.execute(f"DELETE FROM event WHERE workspace_id=?", (workspace_id,))
                store.execute(f"DELETE FROM session WHERE workspace_id=?", (workspace_id,))
            store.execute("DELETE FROM repository_commit WHERE workspace_id=?", (workspace_id,))
            store.execute("DELETE FROM repository_snapshot WHERE workspace_id=?", (workspace_id,))
            store.execute("DELETE FROM evidence_link WHERE decision_id IN (SELECT decision_id FROM decision WHERE workspace_id=?)", (workspace_id,))
            store.execute("DELETE FROM analysis_note WHERE workspace_id=?", (workspace_id,))
            store.execute("DELETE FROM analysis_event_snapshot WHERE run_id IN (SELECT run_id FROM analysis_run WHERE workspace_id=?)", (workspace_id,))
            store.execute("DELETE FROM analysis_node_manifest WHERE workspace_id=?", (workspace_id,))
            store.execute("DELETE FROM analysis_run WHERE workspace_id=?", (workspace_id,))
            store.execute("DELETE FROM decision WHERE workspace_id=?", (workspace_id,))

            matching = [(con, row) for con, row in selected if workspace_root(row) == root]
            name = root
            first_seen = min((row["first_seen"] or stamp) for _, row in matching)
            last_seen = max((row["last_seen"] or stamp) for _, row in matching)
            source_kinds = sorted({str(row["source_kind"]) for _, row in matching})
            store.execute(
                """INSERT INTO workspace(workspace_id,name,source_kind,source_path,first_seen,last_seen,updated_at,stats_json)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(workspace_id) DO UPDATE SET name=excluded.name,source_kind=excluded.source_kind,
                   source_path=excluded.source_path,first_seen=excluded.first_seen,last_seen=excluded.last_seen,
                   updated_at=excluded.updated_at,stats_json=excluded.stats_json""",
                (workspace_id, name, "merged", root, first_seen, last_seen, stamp,
                 json.dumps({"sources": source_kinds}, ensure_ascii=False, sort_keys=True)),
            )

            session_maps: dict[tuple[str, str], str] = {}
            event_maps: dict[tuple[str, str], str] = {}
            decision_sources: list[tuple[sqlite3.Connection, sqlite3.Row, sqlite3.Row]] = []
            session_count = event_count = 0
            for con, workspace in matching:
                source_kind = str(workspace["source_kind"])
                source_id = str(workspace["workspace_id"])
                for row in con.execute("SELECT * FROM session WHERE workspace_id=?", (source_id,)):
                    raw_source_id = str(row["source_id"])
                    new_id = f"{source_kind}:{raw_source_id}"
                    parent = row["parent_id"]
                    parent_new = f"{source_kind}:{parent}" if parent else None
                    session_maps[(source_kind, raw_source_id)] = new_id
                    store.execute(
                        """INSERT OR REPLACE INTO session(session_id,workspace_id,source_kind,source_id,parent_id,title,directory,
                           started_at,updated_at,deleted_at,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (new_id, workspace_id, source_kind, raw_source_id, parent_new, row["title"] or "",
                         row["directory"] or root, row["started_at"], row["updated_at"], row["deleted_at"], row["raw_json"] or "{}"),
                    )
                    session_count += 1
                run = _load_latest_runs(con, source_id)
                if run:
                    decision_sources.append((con, workspace, run))
                for row in con.execute("SELECT * FROM event WHERE workspace_id=? ORDER BY occurred_at,event_id", (source_id,)):
                    raw_event_id = str(row["event_id"])
                    new_event_id = f"{source_kind}:{raw_event_id}"
                    event_maps[(source_kind, raw_event_id)] = new_event_id
                    payload = _json(row["payload_json"], {})
                    fp = event_fingerprint(
                        workspace_id=workspace_id, event_id=new_event_id,
                        event_type=row["event_type"], actor=row["actor"], occurred_at=row["occurred_at"],
                        summary=row["summary"], payload=payload, evidence_text=row["evidence_text"],
                        raw_ref=row["raw_ref"],
                    )
                    raw_session = row["session_id"]
                    session_id = session_maps.get((source_kind, str(raw_session))) if raw_session else None
                    store.execute(
                        """INSERT OR REPLACE INTO event(event_id,workspace_id,session_id,source_kind,source_id,event_type,actor,
                           occurred_at,summary,payload_json,evidence_text,raw_ref,fingerprint,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (new_event_id, workspace_id, session_id, source_kind, row["source_id"], row["event_type"],
                         row["actor"], row["occurred_at"], row["summary"], json.dumps(payload, ensure_ascii=False),
                         row["evidence_text"], row["raw_ref"], fp, stamp),
                    )
                    event_count += 1

                repo = con.execute("SELECT * FROM repository_snapshot WHERE workspace_id=?", (source_id,)).fetchone()
                if repo:
                    current = store.execute("SELECT checked_at FROM repository_snapshot WHERE workspace_id=?", (workspace_id,)).fetchone()
                    if not current or int(repo["checked_at"] or 0) >= int(current[0] or 0):
                        store.execute(
                            """INSERT OR REPLACE INTO repository_snapshot(workspace_id,root_path,branch,upstream,head_sha,head_subject,
                               changed_count,untracked_count,status_json,checked_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                            (workspace_id, root, repo["branch"], repo["upstream"], repo["head_sha"], repo["head_subject"],
                             repo["changed_count"], repo["untracked_count"], repo["status_json"], repo["checked_at"]),
                        )
                for commit in con.execute("SELECT sha,committed_at,subject FROM repository_commit WHERE workspace_id=?", (source_id,)):
                    store.execute("INSERT OR REPLACE INTO repository_commit(workspace_id,sha,committed_at,subject) VALUES(?,?,?,?)",
                                  (workspace_id, commit["sha"], commit["committed_at"], commit["subject"]))

            # Copy source semantic decisions into one synthetic run.  They
            # remain source-labelled IDs so duplicate agent interpretations are
            # visible and never silently overwrite one another.
            decisions: list[tuple[int, str, str, sqlite3.Row, sqlite3.Connection, str]] = []
            for con, workspace, run in decision_sources:
                source_kind = str(workspace["source_kind"])
                for decision in con.execute("SELECT * FROM decision WHERE run_id=?", (run["run_id"],)):
                    decisions.append((int(decision["occurred_at"] or 0), source_kind, str(decision["external_id"]), decision, con, str(workspace["workspace_id"])))
            decisions.sort(key=lambda item: (item[0], item[1], item[2]))
            run_id = f"semantic:merged:{workspace_id}:{hashlib.sha256('|'.join(f'{x[1]}:{x[2]}' for x in decisions).encode()).hexdigest()[:20]}"
            if decisions:
                store.execute(
                    """INSERT INTO analysis_run(run_id,workspace_id,semantic_source,input_hash,model,prompt_version,facts_version,status,
                       started_at,completed_at,summary_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, workspace_id, "merged", hashlib.sha256(run_id.encode()).hexdigest(), "merged", "merge-v1", 1,
                     "succeeded", stamp, max(item[3]["updated_at"] or stamp for item in decisions), "{}"),
                )
                for ordinal, (_at, source_kind, external_id, decision, con, source_workspace_id) in enumerate(decisions, 1):
                    external = f"{source_kind}:{external_id}"
                    decision_id = f"{run_id}:{external}"
                    source_json = _json(decision["source_json"], {})
                    source_json["source_kind"] = source_kind
                    store.execute(
                        """INSERT INTO decision(decision_id,run_id,workspace_id,external_id,ordinal,occurred_at,track,title,what,why,
                           alternative,status,confidence,source_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (decision_id, run_id, workspace_id, external, ordinal, decision["occurred_at"], decision["track"],
                         decision["title"], decision["what"], decision["why"], decision["alternative"], decision["status"],
                         decision["confidence"], json.dumps(source_json, ensure_ascii=False), stamp, stamp),
                    )
                    for ref in con.execute("SELECT * FROM evidence_link WHERE decision_id=? ORDER BY rank,link_id", (decision["decision_id"],)):
                        new_event = event_maps.get((source_kind, str(ref["event_id"]))) if ref["event_id"] else None
                        new_session = session_maps.get((source_kind, str(ref["session_id"]))) if ref["session_id"] else None
                        link_id = hashlib.sha256(f"{decision_id}\x1f{new_event}\x1f{ref['quote']}".encode()).hexdigest()
                        store.execute(
                            """INSERT OR REPLACE INTO evidence_link(link_id,decision_id,event_id,session_id,quote,confidence,rank,source_json,created_at)
                               VALUES(?,?,?,?,?,?,?,?,?)""",
                            (link_id, decision_id, new_event, new_session, ref["quote"], ref["confidence"], ref["rank"],
                             ref["source_json"], stamp),
                        )
                store.execute("UPDATE analysis_run SET summary_json=? WHERE run_id=?",
                              (json.dumps({"decisions": len(decisions), "sources": source_kinds}, ensure_ascii=False), run_id))
            result_roots.append({"root": root, "workspace_id": workspace_id, "sources": source_kinds,
                                 "sessions": session_count, "events": event_count, "decisions": len(decisions)})
        store.commit()
        return {"store": str(output), "roots": result_roots, "workspaces": len(result_roots)}
    finally:
        for con, _row in selected:
            con.close()
        store.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="把同一目录下多个 Agent 的 canonical store 合并为一个工作区")
    ap.add_argument("stores", nargs="+", help="源 canonical SQLite store")
    ap.add_argument("--out", required=True, help="合并后的本地 store")
    ap.add_argument("--root", default=None, help="只合并这个工作区根目录")
    args = ap.parse_args()
    print(json.dumps(merge_stores(args.stores, args.out, args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
