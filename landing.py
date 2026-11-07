#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a shareable, local-first sessionmap landing page from a project view."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


TEMPLATE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><title>sessionmap · project memory for coding agents</title><style>
:root{--paper:#f5f5f7;--ink:#1d1d1f;--muted:#6e6e73;--line:#d2d2d7;--rust:#ff3b30;--soft:#e8e8ed;color-scheme:light}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Helvetica Neue",Arial,sans-serif}a{color:inherit}.mono,.eyebrow,.metric-label{font-family:"Cascadia Mono","SFMono-Regular",Consolas,monospace}.wrap{max-width:980px;margin:0 auto;padding:60px 30px 100px}.eyebrow{font-size:10px;letter-spacing:.16em;color:var(--muted)}h1{max-width:820px;margin:18px 0 12px;font-size:clamp(38px,6vw,74px);font-weight:500;line-height:1.02;letter-spacing:-.055em}.lead{max-width:670px;margin:0;color:var(--muted);font-size:17px;line-height:1.7}.hero-rule{margin-top:34px;border-top:1px solid var(--ink);padding-top:12px;display:flex;justify-content:space-between;gap:20px;color:var(--muted);font-size:11px}.hero-rule strong{color:var(--ink);font-weight:500}.metrics{display:grid;grid-template-columns:repeat(3,1fr);margin-top:50px;border-top:1px solid var(--strong);border-bottom:1px solid var(--strong)}.metric{padding:18px 20px 18px 0;border-right:1px solid var(--line)}.metric+.metric{padding-left:20px}.metric:last-child{border:0}.metric-label{color:var(--muted);font-size:10px;letter-spacing:.08em}.metric-value{margin-top:4px;font-size:30px;letter-spacing:-.04em}.metric-note{color:var(--muted);font-size:11px}.story{margin-top:66px;display:grid;grid-template-columns:1fr 1fr;gap:40px}.story h2{margin:0 0 10px;font-size:24px;font-weight:500;letter-spacing:-.03em}.story p{margin:0;color:var(--muted);line-height:1.75}.story .accent{padding-left:16px;border-left:2px solid var(--rust)}.compare{margin-top:65px;border-top:1px solid var(--strong);border-bottom:1px solid var(--strong)}.compare-row{display:grid;grid-template-columns:1fr 1fr;border-bottom:1px solid var(--line)}.compare-row:last-child{border:0}.compare-row>div{padding:15px 18px 15px 0}.compare-row>div+div{padding-left:18px;border-left:1px solid var(--line)}.compare-row .label{color:var(--muted);font-size:11px}.compare-head{font-size:13px;font-weight:600}.compare-muted{color:var(--muted);font-size:13px}.demo{margin-top:65px;padding:26px 0;border-top:1px solid var(--ink);border-bottom:1px solid var(--ink)}.demo h2{margin:0;font-size:27px;font-weight:500;letter-spacing:-.04em}.demo p{max-width:650px;color:var(--muted)}.demo a{display:inline-block;margin-top:8px;padding:9px 14px;border-radius:10px;background:var(--ink);color:var(--paper);text-decoration:none;font-size:12px;transition:opacity .18s ease}.demo a:hover{opacity:.78}.note{margin-top:24px;color:var(--muted);font-size:11px}footer{margin-top:55px;color:var(--muted);font-size:10px}@media(max-width:650px){.wrap{padding:40px 17px 75px}.hero-rule{display:block}.hero-rule span{display:block;margin-top:6px}.metrics,.story{grid-template-columns:1fr}.metric,.metric+.metric{padding-left:0;border-right:0;border-bottom:1px solid var(--line)}.metric:last-child{border:0}.story{gap:28px}.compare-row{grid-template-columns:1fr}.compare-row>div+div{padding-left:0;border-left:0;border-top:1px solid var(--line)}}
::selection{background:rgba(255,59,48,.18)}::-webkit-scrollbar{width:10px}::-webkit-scrollbar-thumb{background:#c7c7cc;border:3px solid var(--paper);border-radius:10px}@media(prefers-reduced-motion:reduce){.demo a{transition:none}}</style></head><body><main class="wrap"><header><div class="eyebrow">SESSIONMAP / PROJECT MEMORY FOR CODING AGENTS</div><h1>你的 Agent 记得每一次调用。<br>你不记得项目为什么走到这里。</h1><p class="lead">sessionmap 把多天、多工具、多会话里的工作，整理成一条可以继续使用的项目故事。每个转折都能回到原始证据。</p><div class="hero-rule"><strong>运行观测之后，是项目记忆。</strong><span>local-first · evidence-linked · workspace-level</span></div></header><section class="metrics"><div class="metric"><div class="metric-label">EVENTS</div><div class="metric-value">__EVENTS__</div><div class="metric-note">真实工作区事件</div></div><div class="metric"><div class="metric-label">MEMORY BREAKS</div><div class="metric-value">__BREAKS__</div><div class="metric-note">上下文断裂切口</div></div><div class="metric"><div class="metric-label">DECISIONS</div><div class="metric-value">__DECISIONS__</div><div class="metric-note">可追溯项目转折</div></div></section><section class="story"><div><h2>你打开项目时，真正想知道什么？</h2><p>不是某次 Agent 调了哪个工具，而是：现在做到哪里、为什么这样走、哪条路被放弃、上次离开后发生了什么。</p></div><div class="accent"><h2>一条项目事件线</h2><p>主干、转折、放弃、记忆断裂和当前枝头放在同一条时间线上。点开节点，看到用户原话、工具调用、文件变化和 Git 证据。</p></div></section><section class="compare"><div class="compare-row"><div><div class="label">单次运行观测</div><div class="compare-head">Agent 刚才做了什么？</div></div><div><div class="label">sessionmap</div><div class="compare-head">项目为什么走到今天？</div></div></div><div class="compare-row"><div><div class="compare-muted">执行图、成本、延迟、工具链</div></div><div><div class="compare-muted">跨会话决策、放弃分支、Git 事实、记忆断裂</div></div></div><div class="compare-row"><div><div class="compare-muted">一次运行结束，故事结束</div></div><div><div class="compare-muted">项目继续生长，事件线继续更新</div></div></div></section><section class="demo"><h2>看一个真实项目是怎么走到这里的</h2><p>下面的 Demo 使用本地真实工作区数据生成，没有上传会话内容。它展示的是 sessionmap 的统一事件层和项目事件线。</p><a href="demo/index.html">打开项目事件线 →</a></section><p class="note">当前支持 ZCode、Claude、Codex、Pi、DSH 和 OpenCode；统一事件层、Git 事实层和语义层保持分离。</p><footer>sessionmap · project memory for coding agents</footer></main></body></html>'''


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 sessionmap 传播 landing page")
    ap.add_argument("view", default="project-view.json", nargs="?")
    ap.add_argument("--out", default="landing.html")
    args = ap.parse_args()
    data = json.loads(Path(args.view).read_text(encoding="utf-8"))
    html_doc = TEMPLATE.replace("__EVENTS__", f"{data['stats']['events']:,}")
    html_doc = html_doc.replace("__BREAKS__", str(data['stats']['memory_breaks']))
    html_doc = html_doc.replace("__DECISIONS__", str(len(data.get('decisions', []))))
    Path(args.out).write_text(html_doc, encoding="utf-8")
    print(f"已生成 {args.out} ({len(html_doc)} 字符)")


if __name__ == "__main__":
    main()
