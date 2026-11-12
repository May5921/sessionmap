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

from workspace_merge import discover_source_stores, merge_stores, resolve_root_hint


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
    ap.add_argument("--extract", action="store_true",
                    help="调用本机 CLI 重新提炼语义（会话原文会离开本机，默认不做）")
    ap.add_argument("--no-proxy", action="store_true",
                    help="只对本次本机 CLI 调用移除代理环境变量，不修改系统配置")
    ap.add_argument("--no-merge", action="store_true",
                    help="关闭同一仓库下多 Agent canonical store 的自动合并")
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
    timeline_html = out / "timeline.html"
    workspace_args = ["--workspace-id", args.workspace_id] if args.workspace_id else []

    run("同步 ZCode 和 Git 事实", [str(ROOT / "sync.py"), args.workspace, "--db", args.db, "--out", str(store)])
    run("生成规则时间线", [str(ROOT / "scan.py"), args.workspace, "--db", args.db, "--verbose", "--out", str(timeline_md)])
    if args.extract or not timeline_json.exists():
        print("[sessionmap] 警告：--extract 会把会话原文交给本机 CLI，若该 CLI 走云端 API，内容即离开本机",
              file=sys.stderr)
        extract_args = [str(ROOT / "extract.py"), str(timeline_md), "--out", str(timeline_json)]
        if args.cli:
            extract_args.extend(["--cli", args.cli])
        if args.no_proxy:
            extract_args.append("--no-proxy")
        run("调用本机 CLI 提炼语义", extract_args)
    else:
        print("[sessionmap] 复用已有 timeline.json（需要新语义时加 --extract）", file=sys.stderr)
    run("生成证据匹配", [str(ROOT / "view.py"), str(store), str(timeline_json), "--out", str(view), *workspace_args])
    run("持久化语义层", [str(ROOT / "semantic.py"), str(store), str(timeline_json), "--view", str(view), *workspace_args])
    run("生成最终项目视图", [str(ROOT / "view.py"), str(store), str(timeline_json), "--out", str(view), *workspace_args])
    run("生成工具无关时间线", [str(ROOT / "canonical_timeline.py"), str(store), "--out", str(canonical_timeline), *workspace_args])
    final_store, final_view, final_canonical = store, view, canonical_timeline
    source_stores = discover_source_stores(store, ROOT)
    root_hint = resolve_root_hint(args.workspace)
    if not args.no_merge and len(source_stores) > 1:
        merged_store = out / "sessionmap-merged.db"
        merge_stores(source_stores, merged_store, root_hint)
        final_store = merged_store
        final_view = out / "merged-project-view.json"
        final_canonical = out / "merged-canonical-timeline.json"
        run("生成多 Agent 合并视图", [str(ROOT / "view.py"), str(final_store), str(timeline_json), "--out", str(final_view)])
        run("生成合并时间线", [str(ROOT / "canonical_timeline.py"), str(final_store), "--out", str(final_canonical)])
    run("渲染项目时间线", [str(ROOT / "timeline_view.py"), str(final_view), "--out", str(timeline_html)])
    print(json.dumps({"store": str(final_store), "timeline": str(timeline_json), "canonical_timeline": str(final_canonical),
                      "view": str(final_view), "page": str(timeline_html),
                      "merged_sources": len(source_stores) if not args.no_merge else 1}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
