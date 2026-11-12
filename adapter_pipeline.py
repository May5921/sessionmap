#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a source-neutral facts pipeline for file/SQLite adapters."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def run(args: list[str]) -> None:
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="运行 Claude/Codex/Pi/DSH/OpenCode 统一事实流水线")
    ap.add_argument("source", choices=("claude", "codex", "pi", "dsh", "opencode"))
    ap.add_argument("--home", default=None)
    ap.add_argument("--workspace", default=None, help="工作区名称或路径关键字")
    ap.add_argument("--store", default="sessionmap-adapter.db")
    ap.add_argument("--out", default="adapter-timeline.html")
    args = ap.parse_args()
    store = Path(args.store).resolve()
    view = store.with_name(store.stem + "-view.json")
    sync_args = [str(ROOT / "adapter_sync.py"), args.source, "--store", str(store)]
    if args.home:
        sync_args.extend(["--home", args.home])
    if args.workspace:
        sync_args.extend(["--workspace", args.workspace])
    run(sync_args)
    run([str(ROOT / "view.py"), str(store), "--out", str(view)])
    run([str(ROOT / "timeline_view.py"), str(view), "--out", str(Path(args.out).resolve())])
    print(json.dumps({"source": args.source, "store": str(store), "view": str(view),
                      "page": str(Path(args.out).resolve()), "semantic": "facts-only unless a semantic run is supplied"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
