#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refresh the local event line as a workspace changes.

The watcher only advances the factual pipeline.  It never invokes a model and
never writes to the source workspace.  New high-signal facts appear as
``待提炼`` nodes until an explicit semantic extraction is run.

Usage:
    python watch.py pi-mini --once
    python watch.py pi-mini --interval 5
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from workspace_merge import discover_source_stores, merge_stores, resolve_root_hint


ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = os.path.expandvars(r"%USERPROFILE%\.zcode\cli\db\db.sqlite")


def run(label: str, args: list[str]) -> str:
    print(f"[sessionmap] {label}", file=sys.stderr)
    result = subprocess.run([sys.executable, *args], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{label} 失败: {detail[-1200:]}")
    return result.stdout.strip()


def has_semantic_run(store: Path) -> bool:
    if not store.exists():
        return False
    try:
        con = sqlite3.connect(store)
        row = con.execute("SELECT 1 FROM analysis_run WHERE status='succeeded' LIMIT 1").fetchone()
        con.close()
        return row is not None
    except sqlite3.Error:
        return False


def refresh(workspace: str, source_db: str, out_dir: Path, workspace_id: str | None = None,
            timeline_path: Path | None = None, merge_sources: bool = True) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    store = out_dir / "sessionmap.db"
    timeline = timeline_path or (out_dir / "timeline.json")
    view = out_dir / "project-view.json"
    canonical = out_dir / "canonical-timeline.json"
    report = out_dir / "report.html"
    workspace_args = ["--workspace-id", workspace_id] if workspace_id else []

    sync_output = run("同步新增事实", [str(ROOT / "sync.py"), workspace,
                                      "--db", source_db, "--out", str(store)])
    final_store = store
    if timeline.exists():
        if not has_semantic_run(store):
            # Seed a fresh local output store from the last persisted semantic
            # JSON without invoking a model.  Existing stores keep their
            # semantic completion time so later facts remain visible as
            # pending nodes until an explicit extraction is run.
            run("载入已有项目语义", [str(ROOT / "semantic.py"), str(store), str(timeline)])
        # Persisted semantic runs remain authoritative.  This pass is
        # intentionally facts-only: an explicit --extract is still required
        # before a local model is allowed to change the project story.
        run("生成待提炼事实视图", [str(ROOT / "view.py"), str(store), str(timeline),
                                  "--out", str(view), "--include-pending-facts", *workspace_args])
    else:
        run("生成事实视图", [str(ROOT / "view.py"), str(store),
                             "--out", str(view), *workspace_args])
    source_stores = discover_source_stores(store, ROOT)
    if merge_sources and len(source_stores) > 1:
        final_store = out_dir / "sessionmap-merged.db"
        merge_stores(source_stores, final_store, resolve_root_hint(workspace))
        view = out_dir / "merged-project-view.json"
        canonical = out_dir / "merged-canonical-timeline.json"
        run("生成多 Agent 合并视图", [str(ROOT / "view.py"), str(final_store), "--out", str(view)])
    run("生成工具无关时间线", [str(ROOT / "canonical_timeline.py"), str(final_store),
                              "--out", str(canonical)])
    run("渲染事件线", [str(ROOT / "eventline_dashboard.py"), str(view), "--out", str(report)])
    data: dict[str, Any] = {}
    try:
        data = json.loads(view.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "workspace": workspace,
        "store": str(store),
        "final_store": str(final_store),
        "view": str(view),
        "report": str(report),
        "events": data.get("stats", {}).get("events"),
        "decisions": data.get("analysis", {}).get("decision_count"),
        "pending_facts": data.get("analysis", {}).get("pending_fact_count", 0),
        "merged_sources": len(source_stores) if merge_sources else 1,
        "sync": sync_output[-800:],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="持续刷新本地项目事件线（不调用模型）")
    ap.add_argument("workspace", help="工作区名称或路径关键字，例如 pi-mini")
    ap.add_argument("--db", default=DEFAULT_SOURCE, help="ZCode 源数据库，只读")
    ap.add_argument("--out", default=str(ROOT), help="本地输出目录")
    ap.add_argument("--timeline", default=None, help="已有语义 JSON；只读，不调用模型")
    ap.add_argument("--workspace-id", default=None)
    ap.add_argument("--interval", type=float, default=5.0, help="轮询秒数，默认 5")
    ap.add_argument("--once", action="store_true", help="只刷新一次")
    ap.add_argument("--no-merge", action="store_true", help="关闭同一仓库下多 Agent canonical store 的自动合并")
    args = ap.parse_args()
    if args.interval <= 0:
        ap.error("--interval 必须大于 0")
    out_dir = Path(args.out).resolve()
    timeline = Path(args.timeline).resolve() if args.timeline else (
        ROOT / "timeline.json" if out_dir == ROOT else out_dir / "timeline.json"
    )
    while True:
        try:
            print(json.dumps(refresh(args.workspace, args.db, out_dir, args.workspace_id, timeline, not args.no_merge),
                             ensure_ascii=False, indent=2))
        except KeyboardInterrupt:
            print("[sessionmap] 已停止", file=sys.stderr)
            return
        except RuntimeError as exc:
            print(f"[sessionmap] {exc}", file=sys.stderr)
            if args.once:
                raise SystemExit(1)
        if args.once:
            return
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("[sessionmap] 已停止", file=sys.stderr)
            return


if __name__ == "__main__":
    main()
