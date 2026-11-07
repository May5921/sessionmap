#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the sessionmap pipeline with one command.

Default mode reuses the existing semantic timeline so refreshing the facts and
the page is fast and reproducible.  Pass --extract when a new local CLI
analysis is explicitly wanted.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE_DB = os.path.expandvars(r"%USERPROFILE%\.zcode\cli\db\db.sqlite")


def run(label: str, args: list[str]) -> None:
    print(f"[sessionmap] {label}", file=sys.stderr)
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="刷新 sessionmap 工作区事实、语义和事件线")
    ap.add_argument("workspace", help="工作区名称或路径关键字，例如 pi-mini")
    ap.add_argument("--db", default=SOURCE_DB, help="ZCode 源数据库，只读")
    ap.add_argument("--cli", default=None, help="--extract 时使用的本机 CLI")
    ap.add_argument("--extract", action="store_true", help="调用本机 CLI 重新提炼 timeline.json")
    ap.add_argument("--incremental", action="store_true",
                    help="--extract 时只提炼 semantic.py --plan 标出的受影响节点")
    ap.add_argument("--no-proxy", action="store_true",
                    help="只对本次本机 CLI 调用移除代理环境变量，不修改系统配置")
    ap.add_argument("--workspace-id", default=None, help="多工作区 store 中明确选择的 workspace_id")
    ap.add_argument("--out", default=str(ROOT), help="输出目录，默认当前 sessionmap 目录")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    store = out / "sessionmap.db"
    timeline_md = out / "timeline.md"
    timeline_json = out / "timeline.json"
    view = out / "project-view.json"
    canonical_timeline = out / "canonical-timeline.json"
    report = out / "report.html"
    workspace_args = ["--workspace-id", args.workspace_id] if args.workspace_id else []

    run("同步 ZCode 和 Git 事实", [str(ROOT / "sync.py"), args.workspace, "--db", args.db, "--out", str(store)])
    run("生成规则时间线", [str(ROOT / "scan.py"), args.workspace, "--db", args.db, "--verbose", "--out", str(timeline_md)])
    if args.extract or not timeline_json.exists():
        extract_args = [str(ROOT / "extract.py"), str(timeline_md), "--out", str(timeline_json)]
        incremental_run = False
        if args.incremental and timeline_json.exists():
            plan = out / "semantic-incremental-plan.json"
            run("计算受影响语义范围", [str(ROOT / "semantic.py"), str(store), str(timeline_json),
                                      "--plan", "--plan-out", str(plan), *workspace_args])
            plan_data = json.loads(plan.read_text(encoding="utf-8"))
            if plan_data.get("mode") in {"changed", "matched"}:
                extract_args.extend(["--incremental-from", str(timeline_json),
                                      "--incremental-plan", str(plan)])
                incremental_run = True
            else:
                print("[sessionmap] 增量范围为 %s，回退到完整语义提炼" % plan_data.get("mode"), file=sys.stderr)
        if args.cli:
            extract_args.extend(["--cli", args.cli])
        if args.no_proxy:
            extract_args.append("--no-proxy")
        run("增量提炼受影响语义" if incremental_run else "调用本机 CLI 提炼语义", extract_args)
    else:
        print("[sessionmap] 复用已有 timeline.json（需要新语义时加 --extract）", file=sys.stderr)
    run("生成证据匹配", [str(ROOT / "view.py"), str(store), str(timeline_json), "--out", str(view), *workspace_args])
    run("持久化语义层", [str(ROOT / "semantic.py"), str(store), str(timeline_json), "--view", str(view), *workspace_args])
    run("生成最终项目视图", [str(ROOT / "view.py"), str(store), str(timeline_json), "--out", str(view), *workspace_args])
    run("生成工具无关时间线", [str(ROOT / "canonical_timeline.py"), str(store), "--out", str(canonical_timeline), *workspace_args])
    run("渲染事件线", [str(ROOT / "eventline_dashboard.py"), str(view), "--out", str(report)])
    print(json.dumps({"store": str(store), "timeline": str(timeline_json), "canonical_timeline": str(canonical_timeline),
                      "view": str(view), "report": str(report)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
