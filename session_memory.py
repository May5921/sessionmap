#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small, standard-library-only memory layer for project briefs.

The canonical store answers *what happened*.  This module stores the
condensed claims produced by ``sm brief`` so a later refresh can supersede an
older conclusion instead of silently keeping two contradictory memories.

Only sqlite3 from the Python standard library is used.  The content column is
mirrored into an FTS5 external-content index so callers can search memory
without scanning every claim.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from typing import Any, Iterable


# Kept separately from the FTS objects: sync.py can create the plain table on
# every store, while FTS5 is installed by ensure_memory_schema().
MEMORY_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    claim_key TEXT NOT NULL,
    content TEXT NOT NULL,
    evidence_refs TEXT NOT NULL DEFAULT '[]',
    source_adapter TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    superseded_by TEXT,
    status TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_memory_project_status
    ON memory(project, status, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_claim
    ON memory(project, claim_key, status);
"""

MEMORY_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    content,
    content='memory',
    content_rowid='rowid',
    tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN
    INSERT INTO memory_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE OF content ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
    INSERT INTO memory_fts(rowid, content) VALUES (new.rowid, new.content);
END;
"""


def now_ms() -> int:
    return int(dt.datetime.now().timestamp() * 1000)


def ensure_memory_schema(con: sqlite3.Connection) -> None:
    """Create the memory table and its FTS5 mirror on an existing store."""
    if con.row_factory is None:
        con.row_factory = sqlite3.Row
    has_memory = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory'"
    ).fetchone() is not None
    has_fts = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_fts'"
    ).fetchone() is not None
    if has_memory and has_fts:
        return
    con.executescript(MEMORY_TABLE_SCHEMA)
    con.executescript(MEMORY_FTS_SCHEMA)
    if not has_fts:
        # The FTS table may be added after claims already exist in the plain
        # memory table.  Rebuild once so the FTS index is never silently empty.
        con.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")


def make_claim_key(*parts: Any) -> str:
    """Build a stable key from semantic identity, never from display order."""
    raw = "\x1f".join(" ".join(str(part or "").split()) for part in parts)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def _json_refs(refs: Iterable[dict[str, Any]]) -> str:
    return json.dumps(list(refs), ensure_ascii=False, sort_keys=True)


def record_claim(
    con: sqlite3.Connection,
    *,
    project: str,
    claim_key: str,
    content: str,
    evidence_refs: Iterable[dict[str, Any]],
    source_adapter: str,
    created_at: int | None = None,
) -> dict[str, Any]:
    """Persist one claim, superseding an older different claim of the same key.

    Re-running a brief with unchanged content is idempotent: the active row is
    reused and only its evidence references are refreshed.  A different
    conclusion for the same ``claim_key`` marks the active row superseded and
    points ``superseded_by`` at the new row.
    """
    ensure_memory_schema(con)
    refs = list(evidence_refs)
    refs_json = _json_refs(refs)
    active = con.execute(
        """SELECT * FROM memory
           WHERE project=? AND claim_key=? AND status='active'
           ORDER BY created_at DESC, id DESC LIMIT 1""",
        (project, claim_key),
    ).fetchone()
    if active is not None and str(active["content"]) == content:
        con.execute("UPDATE memory SET evidence_refs=? WHERE id=?", (refs_json, active["id"]))
        return {"id": active["id"], "superseded": None, "reused": True}

    stamp = int(created_at if created_at is not None else now_ms())
    new_id = "mem_" + hashlib.sha256(
        f"{project}\x1f{claim_key}\x1f{content}\x1f{stamp}".encode("utf-8", "replace")
    ).hexdigest()[:24]
    if active is not None:
        con.execute(
            "UPDATE memory SET status='superseded', superseded_by=? WHERE id=?",
            (new_id, active["id"]),
        )
    con.execute(
        """INSERT INTO memory(id,project,claim_key,content,evidence_refs,source_adapter,
                              created_at,superseded_by,status)
           VALUES(?,?,?,?,?,?,?,NULL,'active')""",
        (new_id, project, claim_key, content, refs_json, source_adapter, stamp),
    )
    return {"id": new_id, "superseded": active["id"] if active is not None else None, "reused": False}


def active_memories(con: sqlite3.Connection, project: str) -> list[dict[str, Any]]:
    ensure_memory_schema(con)
    rows = con.execute(
        """SELECT id,project,claim_key,content,evidence_refs,source_adapter,created_at,
                  superseded_by,status
           FROM memory WHERE project=? AND status='active'
           ORDER BY created_at DESC, id DESC""",
        (project,),
    ).fetchall()
    return [dict(row) for row in rows]


def search_memory(con: sqlite3.Connection, project: str, query: str,
                  limit: int = 20) -> list[dict[str, Any]]:
    """Search active claims through the FTS5 index."""
    ensure_memory_schema(con)
    rows = con.execute(
        """SELECT m.id,m.project,m.claim_key,m.content,m.evidence_refs,m.source_adapter,
                  m.created_at,m.superseded_by,m.status
           FROM memory_fts f JOIN memory m ON m.rowid=f.rowid
           WHERE memory_fts MATCH ? AND m.project=? AND m.status='active'
           ORDER BY rank LIMIT ?""",
        (query, project, int(limit)),
    ).fetchall()
    return [dict(row) for row in rows]
