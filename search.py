#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Search canonical events and semantic decisions across tools."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


def terms(query: str) -> list[str]:
    return [part for part in re.findall(r"[\w./:+-]{2,}", query.lower())]


def search_store(store: str, query: str, workspace_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    wanted = terms(query)
    if not wanted:
        return []
    con = sqlite3.connect(store)
    con.row_factory = sqlite3.Row
    args: list[Any] = []
    where = ""
    if workspace_id:
        where = " AND workspace_id=?"
        args.append(workspace_id)
    rows = con.execute(f"SELECT event_id,workspace_id,session_id,source_kind,event_type,occurred_at,summary,evidence_text,raw_ref FROM event WHERE 1=1{where} ORDER BY occurred_at DESC", args).fetchall()
    results = []
    for row in rows:
        text = " ".join(str(row[key] or "") for key in ("summary", "evidence_text", "event_type", "source_kind")).lower()
        score = sum(1 for token in wanted if token in text)
        if score:
            result = dict(row)
            result["kind"] = "event"
            result["score"] = score
            results.append(result)
    decision_args = [workspace_id] if workspace_id else []
    decision_where = " WHERE workspace_id=?" if workspace_id else ""
    try:
        decision_rows = con.execute(
            f"SELECT external_id,workspace_id,occurred_at,title,track,what,why,status FROM decision{decision_where}",
            decision_args,
        ).fetchall()
    except sqlite3.OperationalError:
        decision_rows = []
    for row in decision_rows:
        text = " ".join(str(row[key] or "") for key in ("external_id", "title", "track", "what", "why", "status")).lower()
        score = sum(1 for token in wanted if token in text)
        if score:
            results.append({**dict(row), "kind": "decision", "score": score})
    results.sort(key=lambda item: (-item["score"], -(item.get("occurred_at") or 0)))
    con.close()
    return results[:max(1, limit)]


def main() -> None:
    ap = argparse.ArgumentParser(description="搜索 sessionmap 的统一事件")
    ap.add_argument("store")
    ap.add_argument("query")
    ap.add_argument("--workspace-id", default=None)
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    print(json.dumps(search_store(args.store, args.query, args.workspace_id, args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
