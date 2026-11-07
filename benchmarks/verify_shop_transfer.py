#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reproducible acceptance checks for the large shop-transfer workspace.

This script is intentionally read-only with respect to the source workspace
and source ZCode database.  It validates a prepared canonical store, and can
optionally run one incremental sync against the local output store to measure
the refresh guard.  Semantic checks are explicit: facts-only is a valid
baseline, while ``--require-semantic`` turns the missing analysis into a hard
failure.

Examples:
    python benchmarks/verify_shop_transfer.py
    python benchmarks/verify_shop_transfer.py --measure-incremental
    python benchmarks/verify_shop_transfer.py --require-semantic --view project-view.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE = r"D:\shop-transfer-mini-program"
DEFAULT_SOURCE = Path(os.path.expandvars(r"%USERPROFILE%\.zcode\cli\db\db.sqlite"))
DEFAULT_STORE = ROOT / "sessionmap-shop.db"
DEFAULT_VIEW = ROOT / "bench-shop-transfer-view.json"
DEFAULT_BASELINE = ROOT / "benchmarks" / "shop-transfer-2026-11-07.json"


def add(checks: list[dict[str, Any]], name: str, ok: bool, detail: str, severity: str = "error") -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail, "severity": severity})


def parse_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except Exception:
        return default


def source_counts(source_db: Path, workspace: str) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id,parent_id FROM session WHERE directory LIKE ? AND time_deleted IS NULL",
        (f"%{workspace}%",),
    ).fetchall()
    if not rows:
        raise RuntimeError(f"source workspace has no sessions: {workspace}")
    ids = [row["id"] for row in rows]
    ph = ",".join("?" for _ in ids)
    messages: dict[str, dict[str, Any]] = {}
    for row in con.execute(f"SELECT id,data FROM message WHERE session_id IN ({ph})", ids):
        messages[row["id"]] = parse_json(row["data"], {})
    user = assistant = 0
    for row in con.execute(
        f"SELECT message_id,data FROM part WHERE session_id IN ({ph}) AND json_extract(data,'$.type')='text'",
        ids,
    ):
        message = messages.get(row["message_id"], {})
        part = parse_json(row["data"], {})
        if not " ".join(str(part.get("text") or "").split()):
            continue
        if message.get("synthetic") or (message.get("metadata") or {}).get("visibility") == "model-only":
            continue
        if message.get("role") == "user" and (message.get("semantics") or {}).get("origin") in (None, "real_user"):
            user += 1
        elif message.get("role") == "assistant":
            assistant += 1
    tools = con.execute(
        f"SELECT count(*) FROM part WHERE session_id IN ({ph}) AND json_extract(data,'$.type')='tool'", ids
    ).fetchone()[0]
    todos = con.execute(f"SELECT count(*) FROM todo WHERE session_id IN ({ph})", ids).fetchone()[0]
    breaks: set[tuple[Any, Any, Any]] = set()
    for row in con.execute(
        f"SELECT time_created,data FROM part WHERE session_id IN ({ph}) AND json_extract(data,'$.type')='compaction'", ids
    ):
        data = parse_json(row["data"], {})
        pre = data.get("preCompactTokenCount") or 0
        post = data.get("truePostCompactTokenCount") or 0
        if pre and post:
            breaks.add((row["time_created"], pre, post))
    con.close()
    return {
        "sessions": len(rows),
        "main_sessions": sum(1 for row in rows if not row["parent_id"]),
        "child_sessions": sum(1 for row in rows if row["parent_id"]),
        "tool_calls": tools,
        "user_messages": user,
        "assistant_messages": assistant,
        "todo_changes": todos,
        "memory_breaks": len(breaks),
    }


def store_counts(store: Path, workspace_id: str) -> dict[str, Any]:
    con = sqlite3.connect(store)
    con.row_factory = sqlite3.Row
    events = con.execute("SELECT event_type,count(*) AS n FROM event WHERE workspace_id=? GROUP BY event_type", (workspace_id,)).fetchall()
    types = {row["event_type"]: row["n"] for row in events}
    sessions = con.execute("SELECT parent_id FROM session WHERE workspace_id=?", (workspace_id,)).fetchall()
    dup_fp = con.execute(
        "SELECT count(*) FROM (SELECT fingerprint FROM event WHERE workspace_id=? GROUP BY fingerprint HAVING count(*)>1)",
        (workspace_id,),
    ).fetchone()[0]
    dup_source = con.execute(
        "SELECT count(*) FROM (SELECT source_kind,source_id FROM event WHERE workspace_id=? GROUP BY source_kind,source_id HAVING count(*)>1)",
        (workspace_id,),
    ).fetchone()[0]
    parent_missing = con.execute(
        "SELECT count(*) FROM session child WHERE child.workspace_id=? AND child.parent_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM session parent WHERE parent.workspace_id=child.workspace_id AND parent.session_id=child.parent_id)",
        (workspace_id,),
    ).fetchone()[0]
    workspace = con.execute("SELECT * FROM workspace WHERE workspace_id=?", (workspace_id,)).fetchone()
    repo = con.execute("SELECT * FROM repository_snapshot WHERE workspace_id=?", (workspace_id,)).fetchone()
    analysis = con.execute("SELECT * FROM analysis_run WHERE workspace_id=? ORDER BY completed_at DESC,run_id DESC LIMIT 1", (workspace_id,)).fetchone()
    decisions = con.execute("SELECT * FROM decision WHERE workspace_id=?", (workspace_id,)).fetchall()
    con.close()
    return {
        "workspace": dict(workspace) if workspace else None,
        "sessions": len(sessions),
        "main_sessions": sum(1 for row in sessions if not row["parent_id"]),
        "child_sessions": sum(1 for row in sessions if row["parent_id"]),
        "events": sum(types.values()),
        "event_types": types,
        "duplicate_fingerprints": dup_fp,
        "duplicate_source_ids": dup_source,
        "missing_parents": parent_missing,
        "repository": dict(repo) if repo else None,
        "analysis_run": dict(analysis) if analysis else None,
        "decisions": len(decisions),
    }


def git_snapshot(root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=20)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    status = run("status", "--porcelain=v1")
    return {
        "branch": run("branch", "--show-current"),
        "head_sha": run("rev-parse", "HEAD"),
        "changed_files": len([line for line in status.splitlines() if line.strip()]),
        "untracked_files": sum(1 for line in status.splitlines() if line.startswith("??")),
    }


def semantic_checks(store: Path, workspace_id: str, view_path: Path | None, checks: list[dict[str, Any]], require: bool) -> dict[str, Any]:
    con = sqlite3.connect(store)
    con.row_factory = sqlite3.Row
    run = con.execute("SELECT * FROM analysis_run WHERE workspace_id=? ORDER BY completed_at DESC,run_id DESC LIMIT 1", (workspace_id,)).fetchone()
    if not run:
        add(checks, "semantic.run_present", not require, "facts-only: no semantic run",
            "error" if require else "warning")
        con.close()
        return {"mode": "facts-only", "run": None, "decisions": 0, "evidence_links": 0}
    run = dict(run)
    add(checks, "semantic.run_succeeded", run.get("status") == "succeeded", f"status={run.get('status')}")
    add(checks, "semantic.input_hash", bool(run.get("input_hash")), "input_hash present")
    add(checks, "semantic.model_version", bool(run.get("model")) and bool(run.get("prompt_version")),
        f"model={run.get('model')!r}, prompt={run.get('prompt_version')!r}")
    decisions = [dict(row) for row in con.execute("SELECT * FROM decision WHERE run_id=? ORDER BY ordinal,occurred_at", (run["run_id"],))]
    ids = [row["external_id"] for row in decisions]
    times = [row["occurred_at"] or 0 for row in decisions]
    add(checks, "semantic.unique_decision_ids", len(ids) == len(set(ids)), f"{len(ids)} decisions")
    add(checks, "semantic.chronological", times == sorted(times), f"{len(times)} decisions")
    evidence = 0
    missing = []
    invalid_refs = 0
    for decision in decisions:
        refs = con.execute("SELECT event_id,session_id,confidence FROM evidence_link WHERE decision_id=? ORDER BY rank", (decision["decision_id"],)).fetchall()
        evidence += len(refs)
        if not refs:
            missing.append(decision["external_id"])
        for ref in refs:
            if ref["event_id"] and con.execute("SELECT 1 FROM event WHERE event_id=? AND workspace_id=?", (ref["event_id"], workspace_id)).fetchone() is None:
                invalid_refs += 1
            if ref["confidence"] is not None and not (0 <= float(ref["confidence"]) <= 1):
                invalid_refs += 1
    add(checks, "semantic.evidence_coverage", not missing, f"{evidence} links / {len(decisions)} decisions")
    add(checks, "semantic.evidence_refs_valid", invalid_refs == 0, f"invalid={invalid_refs}")
    if view_path and view_path.exists():
        view = json.loads(view_path.read_text(encoding="utf-8"))
        add(checks, "semantic.view_source", view.get("analysis", {}).get("semantic_source") == "canonical.sqlite",
            f"source={view.get('analysis', {}).get('semantic_source')}")
    con.close()
    return {"mode": "semantic", "run": run, "decisions": len(decisions), "evidence_links": evidence}


def measure_incremental(args: argparse.Namespace) -> dict[str, Any]:
    command = [sys.executable, str(ROOT / "sync.py"), args.workspace, "--db", str(args.source_db), "--out", str(args.store)]
    started = time.perf_counter()
    proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=args.timeout)
    elapsed = time.perf_counter() - started
    parsed = {}
    if proc.returncode == 0:
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            parsed = {}
    return {"seconds": round(elapsed, 3), "returncode": proc.returncode, "result": parsed,
            "stderr_tail": proc.stderr[-1000:]}


def main() -> int:
    ap = argparse.ArgumentParser(description="shop-transfer 第二真实工作区验收")
    ap.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    ap.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE)
    ap.add_argument("--view", type=Path, default=DEFAULT_VIEW)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--measure-incremental", action="store_true")
    ap.add_argument("--require-semantic", action="store_true")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    checks: list[dict[str, Any]] = []
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.exists() else {}
    workspace_id = None
    con = sqlite3.connect(args.store)
    row = con.execute("SELECT workspace_id FROM workspace WHERE name LIKE ? OR source_path LIKE ? ORDER BY updated_at DESC LIMIT 1",
                      (f"%{args.workspace}%", f"%{args.workspace}%")).fetchone()
    if row:
        workspace_id = row[0]
    con.close()
    add(checks, "facts.store_present", workspace_id is not None, f"workspace_id={workspace_id}")
    if not workspace_id:
        result = {"status": "failed", "checks": checks}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    expected = source_counts(args.source_db, args.workspace)
    actual = store_counts(args.store, workspace_id)
    for key in ("sessions", "main_sessions", "child_sessions"):
        add(checks, f"facts.{key}", actual[key] == expected[key], f"source={expected[key]}, store={actual[key]}")
    event_expectations = {
        "tool_calls": "tool_call", "user_messages": "user_message", "assistant_messages": "assistant_message",
        "todo_changes": "todo_change", "memory_breaks": "memory_break",
    }
    for key, event_type in event_expectations.items():
        add(checks, f"facts.{event_type}", actual["event_types"].get(event_type, 0) == expected[key],
            f"source={expected[key]}, store={actual['event_types'].get(event_type, 0)}")
    add(checks, "facts.unique_fingerprints", actual["duplicate_fingerprints"] == 0, f"duplicates={actual['duplicate_fingerprints']}")
    add(checks, "facts.unique_source_ids", actual["duplicate_source_ids"] == 0, f"duplicates={actual['duplicate_source_ids']}")
    add(checks, "facts.parent_links", actual["missing_parents"] == 0, f"missing={actual['missing_parents']}")

    git = git_snapshot(Path(args.workspace))
    repo = actual.get("repository") or {}
    add(checks, "facts.git_branch", not repo or repo.get("branch") == git.get("branch"), f"store={repo.get('branch')}, git={git.get('branch')}")
    add(checks, "facts.git_head", not repo or repo.get("head_sha") == git.get("head_sha"), "head SHA matches" if repo and repo.get("head_sha") == git.get("head_sha") else f"store={repo.get('head_sha')}, git={git.get('head_sha')}")
    add(checks, "facts.git_changed_count", not repo or repo.get("changed_count") == git.get("changed_files"), f"store={repo.get('changed_count')}, git={git.get('changed_files')}")

    semantic = semantic_checks(args.store, workspace_id, args.view, checks, args.require_semantic)
    incremental = None
    if args.measure_incremental:
        incremental = measure_incremental(args)
        add(checks, "performance.incremental_sync", incremental["returncode"] == 0, f"{incremental['seconds']}s")
        threshold = float((baseline.get("baseline") or {}).get("sync_incremental_seconds", 0)) * 1.25
        if threshold:
            add(checks, "performance.incremental_budget", incremental["seconds"] <= threshold,
                f"{incremental['seconds']}s <= {threshold:.3f}s (baseline × 1.25)")
        parsed_stats = (incremental.get("result") or {}).get("stats") or {}
        add(checks, "performance.incremental_no_event_growth", parsed_stats.get("events_seen", 0) == 0,
            f"events_seen={parsed_stats.get('events_seen')}")
    else:
        add(checks, "performance.incremental_sync", True, "not measured; pass --measure-incremental", "warning")

    errors = [c for c in checks if not c["ok"] and c["severity"] == "error"]
    warnings = [c for c in checks if c["severity"] == "warning"]
    result = {
        "status": "passed" if not errors else "failed",
        "captured_at": dt.datetime.now().isoformat(timespec="seconds"),
        "workspace": args.workspace,
        "workspace_id": workspace_id,
        "facts": {"source": expected, "store": actual},
        "semantic": semantic,
        "incremental": incremental,
        "checks": checks,
        "passed": sum(1 for c in checks if c["ok"]),
        "failed": len(errors),
        "warnings": len(warnings),
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
