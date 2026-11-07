#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persist human corrections to the semantic projection.

Overrides are additive metadata in sessionmap.db.  Source transcripts and
the user's Git repository are never modified.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from typing import Any


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS decision_override (
        decision_id TEXT PRIMARY KEY, title TEXT, track TEXT, status TEXT, what TEXT, why TEXT,
        override_json TEXT NOT NULL DEFAULT '{}', updated_at INTEGER NOT NULL)""")


def set_override(store: str, decision_id: str, values: dict[str, Any]) -> None:
    allowed = {key: values.get(key) for key in ("title", "track", "status", "what", "why")}
    con = sqlite3.connect(store)
    ensure_schema(con)
    con.execute(
        """INSERT INTO decision_override(decision_id,title,track,status,what,why,override_json,updated_at)
           VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(decision_id) DO UPDATE SET
           title=COALESCE(excluded.title,decision_override.title),
           track=COALESCE(excluded.track,decision_override.track),
           status=COALESCE(excluded.status,decision_override.status),
           what=COALESCE(excluded.what,decision_override.what),
           why=COALESCE(excluded.why,decision_override.why),
           override_json=excluded.override_json,updated_at=excluded.updated_at""",
        (decision_id, allowed["title"], allowed["track"], allowed["status"], allowed["what"], allowed["why"],
         json.dumps(values, ensure_ascii=False), int(dt.datetime.now().timestamp() * 1000)),
    )
    con.commit()
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="保存人工决策修正（只写 sessionmap.db）")
    ap.add_argument("store")
    ap.add_argument("decision_id")
    for field in ("title", "track", "status", "what", "why"):
        ap.add_argument(f"--{field}")
    args = ap.parse_args()
    values = {field: getattr(args, field) for field in ("title", "track", "status", "what", "why")
              if getattr(args, field) is not None}
    if not values:
        ap.error("至少提供一个修正字段")
    set_override(args.store, args.decision_id, values)
    print(json.dumps({"store": args.store, "decision_id": args.decision_id, "updated": values}, ensure_ascii=False))


if __name__ == "__main__":
    main()
