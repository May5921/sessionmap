#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic verification for the sessionmap pipeline.

This is intentionally dependency-free.  It checks facts against the source
ZCode database, then checks the semantic projection and rendered event line
against a small hand-reviewed gold sample.

Usage:
    python verify.py
    python verify.py --source-db C:\\Users\\Luke\\.zcode\\cli\\db\\db.sqlite
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = r"%USERPROFILE%\.zcode\cli\db\db.sqlite"


def fail(checks: list[dict[str, Any]], name: str, detail: str) -> None:
    checks.append({"name": name, "ok": False, "detail": detail})


def ok(checks: list[dict[str, Any]], name: str, detail: str) -> None:
    checks.append({"name": name, "ok": True, "detail": detail})


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_counts(source_path: str, workspace: str) -> dict[str, int]:
    con = sqlite3.connect("file:" + source_path.replace("\\", "/") + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id,parent_id FROM session WHERE directory LIKE ? AND time_deleted IS NULL", ("%" + workspace + "%",)).fetchall()
    session_ids = [row["id"] for row in rows]
    if not session_ids:
        raise RuntimeError("source workspace has no sessions")
    ph = ",".join("?" for _ in session_ids)
    messages = {}
    for row in con.execute(f"SELECT id,data FROM message WHERE session_id IN ({ph})", tuple(session_ids)):
        try:
            messages[row["id"]] = json.loads(row["data"])
        except Exception:
            messages[row["id"]] = {}
    tool_calls = con.execute(f"SELECT count(*) FROM part WHERE session_id IN ({ph}) AND json_extract(data,'$.type')='tool'", tuple(session_ids)).fetchone()[0]
    user_messages = 0
    assistant_messages = 0
    for row in con.execute(f"SELECT message_id,data FROM part WHERE session_id IN ({ph}) AND json_extract(data,'$.type')='text'", tuple(session_ids)):
        message = messages.get(row["message_id"], {})
        if message.get("synthetic") or (message.get("metadata") or {}).get("visibility") == "model-only":
            continue
        if message.get("role") == "user" and (message.get("semantics") or {}).get("origin") in (None, "real_user"):
            user_messages += 1
        elif message.get("role") == "assistant":
            assistant_messages += 1
    todo_changes = con.execute(f"SELECT count(*) FROM todo WHERE session_id IN ({ph})", tuple(session_ids)).fetchone()[0]
    compactions = set()
    for row in con.execute(f"SELECT time_created,data FROM part WHERE session_id IN ({ph}) AND json_extract(data,'$.type')='compaction'", tuple(session_ids)):
        try:
            data = json.loads(row["data"])
        except Exception:
            continue
        pre = data.get("preCompactTokenCount") or 0
        post = data.get("truePostCompactTokenCount") or 0
        if pre and post:
            compactions.add((row["time_created"], pre, post))
    con.close()
    return {"sessions": len(rows), "main_sessions": sum(1 for row in rows if not row["parent_id"]),
            "child_sessions": sum(1 for row in rows if row["parent_id"]), "tool_calls": tool_calls,
            "user_messages": user_messages, "assistant_messages": assistant_messages,
            "todo_changes": todo_changes, "memory_breaks": len(compactions)}


def run(args: argparse.Namespace) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    gold = load_json(Path(args.gold))
    view = load_json(Path(args.view))
    expected = gold["expected"]
    source = source_counts(args.source_db, gold["workspace"])

    for key, expected_value in expected.items():
        if key == "decisions":
            continue
        actual = source.get(key)
        if actual == expected_value:
            ok(checks, f"source.{key}", str(actual))
        else:
            fail(checks, f"source.{key}", f"expected {expected_value}, got {actual}")

    stats = view.get("stats", {})
    for key in ("sessions", "main_sessions", "child_sessions", "tool_calls", "user_messages", "todo_changes", "memory_breaks"):
        if stats.get(key) == source.get(key):
            ok(checks, f"projection.{key}", str(stats.get(key)))
        else:
            fail(checks, f"projection.{key}", f"source {source.get(key)}, view {stats.get(key)}")

    events = view.get("decisions", [])
    ids = [d.get("id") for d in events]
    if len(ids) == len(set(ids)):
        ok(checks, "semantic.unique_decision_ids", str(len(ids)))
    else:
        fail(checks, "semantic.unique_decision_ids", "duplicate decision ids")
    times = [d.get("when") for d in events]
    if times == sorted(times):
        ok(checks, "semantic.chronological", f"{len(times)} decisions")
    else:
        fail(checks, "semantic.chronological", "decision order is not chronological")
    if len(events) == expected["decisions"]:
        ok(checks, "semantic.decision_count", str(len(events)))
    else:
        fail(checks, "semantic.decision_count", f"expected {expected['decisions']}, got {len(events)}")

    by_id = {d.get("id"): d for d in events}
    for sample in gold["decisions"]:
        actual = by_id.get(sample["id"])
        if not actual:
            fail(checks, f"gold.{sample['id']}", "missing decision")
            continue
        mismatches = []
        for field in ("when", "title", "track", "status"):
            if actual.get(field) != sample[field]:
                mismatches.append(f"{field}: {actual.get(field)!r} != {sample[field]!r}")
        evidence_text = " ".join((ref.get("quote") or "") for ref in actual.get("evidence_refs", []))
        missing_keywords = [word for word in sample.get("evidence_keywords", []) if word.lower() not in evidence_text.lower()]
        if missing_keywords:
            mismatches.append("evidence missing: " + ", ".join(missing_keywords))
        if mismatches:
            fail(checks, f"gold.{sample['id']}", "; ".join(mismatches))
        else:
            ok(checks, f"gold.{sample['id']}", "fields and evidence matched")

    refs = sum(int(d.get("evidence_count") or 0) for d in events)
    coverage = view.get("analysis", {}).get("evidence_coverage")
    if coverage == 1.0 and all(d.get("evidence_count") for d in events):
        ok(checks, "semantic.evidence_coverage", f"{refs} links / {len(events)} decisions")
    else:
        fail(checks, "semantic.evidence_coverage", f"coverage={coverage}")
    if events and all(d.get("confidence") is not None for d in events):
        ok(checks, "semantic.confidence", "every decision has persisted confidence")
    else:
        fail(checks, "semantic.confidence", "one or more decisions have no confidence")
    if view.get("analysis", {}).get("semantic_source") == "canonical.sqlite":
        ok(checks, "semantic.display_source", "project view reads the persisted semantic projection")
    else:
        fail(checks, "semantic.display_source", f"source={view.get('analysis', {}).get('semantic_source')}")

    breaks = view.get("breaks", [])
    if len(breaks) == expected["memory_breaks"] and all(float(b.get("loss_pct") or 0) > 90 for b in breaks):
        ok(checks, "semantic.memory_breaks", f"{len(breaks)} breaks above 90% loss")
    else:
        fail(checks, "semantic.memory_breaks", f"got {len(breaks)} breaks")

    page_text = Path(args.page).read_text(encoding="utf-8")
    # The page is client-rendered from DATA, so the concrete row count is
    # verified from the view above.  Here we verify the single shipped surface
    # still contains its three load-bearing parts.
    if all(marker in page_text for marker in ('id="ledger"', 'id="range"', 'id="nowNext"', "const DATA=")):
        ok(checks, "render.single_surface", "re-entry block, ledger and one range control")
    else:
        fail(checks, "render.single_surface", "single page surface or mount points missing")
    if "row.strike" in page_text and "abandoned" in page_text:
        ok(checks, "render.reversal_mark", "abandoned rows are struck through")
    else:
        fail(checks, "render.reversal_mark", "reversal mark missing from the page")
    for retired in ("evidence.html", "sourceFilter", "toolFilter", 'id="search"'):
        if retired in page_text:
            fail(checks, "render.no_retired_controls", f"retired surface still shipped: {retired}")
            break
    else:
        ok(checks, "render.no_retired_controls", "search, source filter and the second page are gone")

    passed = sum(1 for check in checks if check["ok"])
    result = {"passed": passed, "failed": len(checks) - passed, "checks": checks}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="验证 sessionmap 数据、语义和事件线输出")
    parser.add_argument("--source-db", default=Path(DEFAULT_SOURCE).expanduser())
    parser.add_argument("--store", default=ROOT / "sessionmap.db", help="保留参数，方便后续扩展")
    parser.add_argument("--view", default=ROOT / "project-view.json")
    parser.add_argument("--page", default=ROOT / "timeline.html")
    parser.add_argument("--gold", default=ROOT / "tests" / "gold_pi_mini.json")
    args = parser.parse_args()
    # Expand %USERPROFILE% on Windows when the default was used.
    args.source_db = str(args.source_db).replace("%USERPROFILE%", str(Path.home()))
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
