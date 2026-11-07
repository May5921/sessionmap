#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import a file-backed adapter into the canonical sessionmap store.

This is intentionally separate from the ZCode SQLite adapter.  It uses the
same canonical tables and per-file byte cursors, so the view layer remains
source-neutral.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from adapters import SourceAdapter, default_adapters
from sync import SCHEMA, event_fingerprint, now_ms, store_connect, sync_git_snapshot


CURSOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS adapter_cursor (
  source_kind TEXT NOT NULL,
  source_path TEXT NOT NULL,
  session_id TEXT NOT NULL,
  byte_offset INTEGER NOT NULL DEFAULT 0,
  synced_at INTEGER NOT NULL,
  PRIMARY KEY(source_kind, source_path, session_id)
);
"""


def stable_workspace_id(source_kind: str, root: str) -> str:
    return f"{source_kind}:" + hashlib.sha1(root.replace("\\", "/").lower().encode()).hexdigest()[:16]


def ensure_workspace(store: sqlite3.Connection, workspace: Any) -> str:
    workspace_id = workspace.workspace_id or stable_workspace_id(workspace.source_kind, workspace.root)
    stamp = now_ms()
    store.execute(
        """INSERT INTO workspace(workspace_id,name,source_kind,source_path,first_seen,last_seen,updated_at)
           VALUES(?,?,?,?,?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET name=excluded.name,
           last_seen=excluded.last_seen,updated_at=excluded.updated_at""",
        (workspace_id, workspace.name or workspace.root, workspace.source_kind, workspace.root,
         stamp, stamp, stamp),
    )
    return workspace_id


def import_adapter(adapter: SourceAdapter, store_path: str, workspace_filter: str | None = None) -> dict[str, Any]:
    store = store_connect(store_path)
    store.executescript(CURSOR_SCHEMA)
    status = adapter.probe()
    if status.status not in ("ready", "unsupported"):
        raise SystemExit(f"{status.source_kind}: {status.status}: {status.reason}")
    workspaces = adapter.discover_workspaces()
    if workspace_filter:
        workspaces = [w for w in workspaces if workspace_filter.lower() in (w.root + " " + w.name).lower()]
    if not workspaces:
        raise SystemExit(f"{status.source_kind}: 没有匹配的工作区 ({status.reason})")
    stamp = now_ms()
    session_count = event_count = changed_count = 0
    skipped_sessions: list[dict[str, str]] = []
    seen_event_ids: set[str] = set()
    for workspace in workspaces:
        workspace_id = ensure_workspace(store, workspace)
        sessions = adapter.list_sessions(workspace.workspace_id)
        for session in sessions:
            session_count += 1
            raw = dict(session.metadata)
            store.execute(
                """INSERT INTO session(session_id,workspace_id,source_kind,source_id,parent_id,title,directory,
                   started_at,updated_at,deleted_at,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(session_id) DO UPDATE SET title=excluded.title,parent_id=excluded.parent_id,
                   updated_at=excluded.updated_at,raw_json=excluded.raw_json""",
                (f"{session.source_kind}:{session.session_id}", workspace_id, session.source_kind,
                 session.session_id, session.parent_id, session.title or "", workspace.root,
                 session.started_at, session.updated_at, None, json.dumps(raw, ensure_ascii=False)),
            )
            cursor_row = store.execute(
                "SELECT byte_offset FROM adapter_cursor WHERE source_kind=? AND source_path=? AND session_id=?",
                (session.source_kind, session.source_path, session.session_id),
            ).fetchone()
            cursor = int(cursor_row[0]) if cursor_row else 0
            try:
                for record in adapter.read_incremental(session, cursor):
                    event = adapter.normalize(record, session)
                    if event is None:
                        continue
                    event_id = event.event_id
                    payload = event.payload or record
                    summary = event.summary or event.event_type
                    event_fp = event_fingerprint(
                        workspace_id=workspace_id, event_id=event_id,
                        event_type=event.event_type, actor=event.actor,
                        occurred_at=event.occurred_at, summary=summary,
                        payload=payload, evidence_text=summary,
                        raw_ref=event.raw_ref,
                    )
                    existing = store.execute("SELECT fingerprint FROM event WHERE event_id=?", (event_id,)).fetchone()
                    store.execute(
                        """INSERT INTO event(event_id,workspace_id,session_id,source_kind,source_id,event_type,actor,
                           occurred_at,summary,payload_json,evidence_text,raw_ref,fingerprint,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET
                           occurred_at=excluded.occurred_at,event_type=excluded.event_type,actor=excluded.actor,
                           summary=excluded.summary,payload_json=excluded.payload_json,
                           evidence_text=excluded.evidence_text,raw_ref=excluded.raw_ref,
                           fingerprint=excluded.fingerprint""",
                        (event_id, workspace_id, f"{session.source_kind}:{session.session_id}", event.source_kind,
                         event.source_id, event.event_type, event.actor, event.occurred_at, summary,
                         json.dumps(payload, ensure_ascii=False), summary, event.raw_ref,
                         event_fp, stamp),
                    )
                    if existing is None:
                        event_count += 1
                    elif existing[0] != event_fp and event_id not in seen_event_ids:
                        changed_count += 1
                    seen_event_ids.add(event_id)
            except RuntimeError as exc:
                # Keep discovery useful even when one format is compressed or
                # not yet supported; do not mark fabricated data as imported.
                skipped_sessions.append({"session_id": session.session_id,
                                         "source_path": session.source_path,
                                         "error": str(exc)})
                continue
            source_path = Path(session.source_path)
            try:
                next_cursor = adapter.next_cursor(session, cursor)
            except (OSError, sqlite3.Error):
                next_cursor = source_path.stat().st_size if source_path.exists() else cursor
            store.execute(
                """INSERT INTO adapter_cursor(source_kind,source_path,session_id,byte_offset,synced_at)
                   VALUES(?,?,?,?,?) ON CONFLICT(source_kind,source_path,session_id)
                   DO UPDATE SET byte_offset=excluded.byte_offset,synced_at=excluded.synced_at""",
                (session.source_kind, session.source_path, session.session_id, next_cursor, stamp),
            )
        # Git is optional for file-backed sources.  Only a real repository root
        # is inspected; the adapter's log directory is never treated as code.
        if Path(workspace.root).is_dir() and (Path(workspace.root) / ".git").exists():
            sync_git_snapshot(store, workspace_id, workspace.root, stamp)
    store.commit()
    result = {"source_kind": status.source_kind,
              "status": "partial" if skipped_sessions else status.status,
              "workspaces": len(workspaces), "sessions": session_count, "events": event_count,
              "changed_events": changed_count,
              "skipped_sessions": skipped_sessions,
              "errors": [item["error"] for item in skipped_sessions],
              "store": store_path}
    store.close()
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="把 Claude/Codex JSONL 适配器导入统一事件库")
    ap.add_argument("source", choices=("claude", "codex", "pi", "opencode", "dsh"))
    ap.add_argument("--home", default=None)
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--store", default="sessionmap.db")
    args = ap.parse_args()
    adapters = default_adapters(args.home)
    result = import_adapter(adapters[args.source], args.store, args.workspace)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
