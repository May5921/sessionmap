#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render the single sessionmap surface: the project's re-entry line.

One page, one column.  The top answers "where am I, why did it stop, what is
next".  Below it the project's history reads downward as a dated ledger:
work items are the spine, conclusions hang under the item they belong to, and
anything the data can prove was abandoned or overwritten is struck through.

No search, no stage collapse, no source filters, no second page.  Time range is
the only control, because it is the only one that changes what you read today.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

DAY_MS = 86_400_000


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def fmt_time(ms: Any) -> str:
    import datetime as dt
    if not ms:
        return ""
    return dt.datetime.fromtimestamp(int(ms) / 1000).strftime("%H:%M")


def fmt_day(ms: Any) -> str:
    import datetime as dt
    if not ms:
        return "未标注日期"
    d = dt.datetime.fromtimestamp(int(ms) / 1000)
    return f"{d.month} 月 {d.day} 日 · 周{'一二三四五六日'[d.weekday()]}"


def short_date(ms: Any) -> str:
    import datetime as dt
    if not ms:
        return "—"
    d = dt.datetime.fromtimestamp(int(ms) / 1000)
    return f"{d.year}-{d.month:02d}-{d.day:02d}"


def basename(path: str) -> str:
    text = str(path or "").replace("\\", "/")
    return text.rsplit("/", 1)[-1] or text


def clamp(text: object, limit: int = 180) -> str:
    """Keep one sentence readable.  A pasted agent dump is not a sentence."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


STATUS_TEXT = {"open": "进行中", "done": "已完成", "abandoned": "已放弃", "pending": "未开始"}
KIND_TEXT = {"feature": "功能", "bugfix": "修复", "investigation": "调研",
             "refactor": "重构", "release": "发布", "milestone": "里程碑"}


def actor_of(refs: list[dict[str, Any]]) -> str:
    """Who closed it: the person, or the agent that ran the turn."""
    for ref in refs or []:
        if ref.get("event_type") == "user_message":
            return "你"
        source = ref.get("source_kind")
        if source:
            return f"{source} agent"
    return "未标注"


def quote_of(refs: list[dict[str, Any]]) -> str:
    for ref in refs or []:
        if ref.get("event_type") == "user_message" and (ref.get("quote") or "").strip():
            return ref["quote"].strip()
    for ref in refs or []:
        if (ref.get("quote") or "").strip():
            return ref["quote"].strip()
    return ""


def build_model(data: dict[str, Any]) -> dict[str, Any]:
    stats = data.get("stats") or {}
    progress = data.get("progress") or {}
    items = sorted(progress.get("work_items") or [], key=lambda x: x.get("when_ms") or 0)
    milestones = sorted(progress.get("milestones") or [], key=lambda x: x.get("when_ms") or 0)
    commits = sorted(progress.get("commits") or [], key=lambda x: x.get("when_ms") or 0)
    decisions = sorted(data.get("decisions") or [], key=lambda x: x.get("when_ms") or 0)
    breaks = sorted(data.get("breaks") or [], key=lambda x: x.get("at") or 0)

    # ---- 弱取代关系：只声明数据能证明的（同文件被后续工作项再动过） ----
    file_sets = [({basename(f) for f in (w.get("files") or []) if f}) for w in items]
    for index, item in enumerate(items):
        later = []
        for other_index in range(index + 1, len(items)):
            shared = file_sets[index] & file_sets[other_index]
            smaller = max(1, min(len(file_sets[index]), len(file_sets[other_index])))
            # One shared `app.js` proves nothing.  Require a real overlap so the
            # strike-through never becomes decoration.
            if len(shared) >= 3 and len(shared) / smaller >= 0.3:
                later.append(items[other_index])
        item["_later_touch"] = later
        item["_superseded_by"] = later[0]["title"] if later else None
        item["_superseded_count"] = len(later)

    decisions_by_item: dict[str, list[dict[str, Any]]] = {}
    for decision in decisions:
        wid = decision.get("work_item_id")
        if wid:
            decisions_by_item.setdefault(wid, []).append(decision)
            continue
        # 没有归属的结论按时间落到最近的工作项上，但不改数据，只在展示层挂靠
        owner = None
        for item in items:
            if (item.get("when_ms") or 0) <= (decision.get("when_ms") or 0):
                owner = item
        if owner:
            decisions_by_item.setdefault(owner["id"], []).append(decision)

    rows: list[dict[str, Any]] = []

    def work_row(item: dict[str, Any]) -> dict[str, Any]:
        refs = item.get("evidence_refs") or []
        files = [basename(f) for f in (item.get("files") or []) if f]
        seen, top_files = set(), []
        for name in files:
            if name in seen:
                continue
            seen.add(name)
            top_files.append(name)
            if len(top_files) == 5:
                break
        todos = item.get("open_todos") or []
        attached = decisions_by_item.get(item["id"], [])
        meta = [KIND_TEXT.get(item.get("kind"), "工作"), STATUS_TEXT.get(item.get("status"), "")]
        if item.get("session_count"):
            meta.append(f"{item['session_count']} 个会话")
        if item.get("file_count"):
            meta.append(f"{item['file_count']} 个文件")
        if item.get("span_hours"):
            meta.append(f"{item['span_hours']:g} 小时")
        sections = []
        stopping = [t for t in todos if (t.get("status") or "open") != "done"]
        opening = clamp(item.get("opening_quote"), 200)
        closing = clamp(item.get("closing_quote"), 200)
        if opening:
            sections.append({"label": "这一轮从这句开始", "quotes": [opening]})
        if closing and closing != opening:
            sections.append({"label": "最后一句", "quotes": [closing]})
        if top_files:
            sections.append({"label": "改到的文件",
                             "text": " · ".join(top_files) + (f" 等 {item['file_count']} 个" if (item.get("file_count") or 0) > len(top_files) else "")})
        if stopping:
            sections.append({"label": "停在这里", "lines": [
                {"text": t.get("text", ""), "note": STATUS_TEXT.get(t.get("status"), t.get("status") or "")}
                for t in stopping[:6]
            ]})
        if attached:
            sections.append({"label": "这一轮的结论", "links": [
                {"target": f"decision-{d.get('id')}", "text": d.get("title", "")} for d in attached
            ]})
        quotes = [clamp(r.get("quote"), 220) for r in refs
                  if r.get("event_type") in ("user_message", "assistant_message")]
        quotes = [q for q in quotes if q and q not in (opening, closing)][:3]
        if quotes:
            sections.append({"label": "原话", "quotes": quotes})
        state = "open" if item.get("status") == "open" else "done"
        superseded = item.get("_superseded_by")
        if superseded:
            state = "superseded"
        return {
            "id": item["id"], "kind": "work", "at": item.get("when_ms") or 0,
            "time": fmt_time(item.get("when_ms")), "title": item.get("title") or "未命名工作项",
            "meta": [m for m in meta if m], "state": state,
            "strike": bool(superseded),
            "note": (f"此后又被改过 {item['_superseded_count']} 次 · 最近一次：{superseded}" if superseded else ""),
            "actor": "", "sections": sections,
            "decisions": [d.get("id") for d in attached],
        }

    def decision_row(decision: dict[str, Any]) -> dict[str, Any]:
        refs = decision.get("evidence_refs") or []
        status = decision.get("status") or "open"
        sections = []
        if decision.get("what"):
            sections.append({"label": "定了什么", "text": decision["what"]})
        if decision.get("why"):
            sections.append({"label": "为什么", "text": decision["why"]})
        if decision.get("alternative"):
            sections.append({"label": "放弃的方案", "text": decision["alternative"]})
        quote = quote_of(refs)
        if quote:
            sections.append({"label": "原话", "quotes": [quote]})
        sources = sorted({r.get("session_title") for r in refs if r.get("session_title")})
        if sources:
            sections.append({"label": "来源会话", "text": " · ".join(sources[:3])})
        return {
            "id": f"decision-{decision.get('id')}", "kind": "decision",
            "at": decision.get("when_ms") or 0, "time": fmt_time(decision.get("when_ms")),
            "title": decision.get("title") or "未命名结论",
            "meta": [decision.get("track") or "", STATUS_TEXT.get(status, status), actor_of(refs)],
            "state": "abandoned" if status == "abandoned" else "decision",
            "strike": status == "abandoned",
            "note": "",
            "sections": sections,
        }

    def break_row(item: dict[str, Any]) -> dict[str, Any]:
        return {"id": f"break-{item.get('at')}", "kind": "break", "at": item.get("at") or 0,
                "time": fmt_time(item.get("at")), "title": "上下文被压缩",
                "meta": [], "state": "break", "strike": False,
                "note": f"丢失 {item.get('loss_pct')}% · {int(item.get('pre_tokens') or 0):,} → {int(item.get('post_tokens') or 0):,} token",
                "actor": "", "sections": []}

    def milestone_row(item: dict[str, Any]) -> dict[str, Any]:
        release_commit = str(item.get("commit_id") or "").split(":", 1)[-1]
        return {"id": item.get("id") or f"milestone-{item.get('when_ms')}", "kind": "milestone",
                "at": item.get("when_ms") or 0, "time": fmt_time(item.get("when_ms")),
                "title": item.get("title") or "里程碑",
                "meta": [KIND_TEXT.get(item.get("kind"), "里程碑")], "state": "milestone",
                "strike": False, "note": "", "actor": "",
                "sections": [{"label": "落地了什么", "text": item.get("what") or item.get("title") or ""},
                             {"label": "提交", "text": release_commit[:8]}]
                + ([{"label": "同一发布窗口", "lines": [{"text": c.get("title") or "", "note": fmt_time(c.get("when_ms"))} for c in commits[:6]]}] if commits else [])}

    for item in items:
        rows.append(work_row(item))
        for decision in decisions_by_item.get(item["id"], []):
            rows.append(decision_row(decision))
    attached_ids = {d.get("id") for group in decisions_by_item.values() for d in group}
    for decision in decisions:
        if decision.get("id") not in attached_ids:
            rows.append(decision_row(decision))
    rows.extend(milestone_row(m) for m in milestones)
    rows.extend(break_row(b) for b in breaks)
    rows.sort(key=lambda r: (r["at"] - r["at"] % 60_000,
                             {"work": 0, "milestone": 0, "decision": 1, "break": 2}.get(r["kind"], 3),
                             r["at"]))

    # ---- 回来第一屏 ----
    open_items = [i for i in items if i.get("status") == "open"]
    # "Where am I" means the effort that is actually still moving, not simply
    # the last one that happened to be open.
    head_item = (max(open_items, key=lambda i: (len(i.get("open_todos") or []), i.get("when_ms") or 0))
                 if open_items else (items[-1] if items else None))
    next_steps = []
    if head_item:
        for todo in head_item.get("open_todos") or []:
            if (todo.get("status") or "open") != "done":
                next_steps.append(todo.get("text", ""))
    why_stopped = ""
    if head_item:
        why_stopped = clamp(head_item.get("closing_quote") or head_item.get("opening_quote"), 200)
    last_row = max(rows, key=lambda r: r["at"]) if rows else None
    last_at = max([r["at"] for r in rows] or [0])
    days: list[dict[str, Any]] = []
    for row in rows:
        label = fmt_day(row["at"])
        if not days or days[-1]["label"] != label:
            days.append({"label": label, "rows": []})
        days[-1]["rows"].append(row)
    return {
        "workspace": (data.get("workspace") or {}),
        "name": basename((data.get("workspace") or {}).get("name") or "project"),
        "path": (data.get("workspace") or {}).get("name") or "",
        "period": f"{short_date(min([r['at'] for r in rows] or [0]) or stats.get('first_at'))} → {short_date(last_at)}",
        "counts": {
            "sessions": stats.get("sessions") or 0,
            "main": stats.get("main_sessions") or 0,
            "items": len(items),
            "decisions": len(decisions),
            "abandoned": sum(1 for d in decisions if d.get("status") == "abandoned"),
            "breaks": len(breaks),
            "lost_pct": (breaks[0].get("loss_pct") if breaks else None),
        },
        "now": {
            "title": (head_item or {}).get("title") or "还没有工作项",
            "status": STATUS_TEXT.get((head_item or {}).get("status"), ""),
            "why_stopped": why_stopped,
            "next_steps": next_steps[:4],
            "head_at": (head_item or {}).get("when_ms") or 0,
            "head_anchor": (head_item or {}).get("id") or "",
        },
        "days": days,
        "recent": (f"{short_date(last_row['at'])} {last_row['time']} · {last_row['title']}" if last_row else ""),
        "last_at": last_at,
        "generated_at": data.get("generated_at") or 0,
    }


TEMPLATE = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>__NAME__ · sessionmap</title>
<style>
:root{
  --paper:#f2f1ec; --ink:#16181a; --soft:#4a4d4a; --muted:#6e6b62;
  --rule:#d5d2c7; --rule-strong:#b3afa1;
  --seal:#a6321f; --live:#2b5449;
  --serif:"Songti SC","Noto Serif SC","Source Han Serif SC",Georgia,"Times New Roman",serif;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
  --mono:"Cascadia Mono","SF Mono",Consolas,monospace;
  color-scheme:light;
}
*{box-sizing:border-box}
html{background:var(--paper);-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 var(--sans);-webkit-font-smoothing:antialiased}
::selection{background:rgba(166,50,31,.14)}
.page{max-width:780px;margin:0 auto;padding:64px 28px 120px}
a{color:inherit}
/* ---- masthead ---- */
.masthead{padding-bottom:22px;border-bottom:2px solid var(--ink)}
.masthead h1{margin:0 0 10px;font-family:var(--serif);font-weight:600;font-size:clamp(30px,5vw,42px);line-height:1.14;letter-spacing:-.01em}
.masthead .fact{color:var(--muted);font-size:12.5px;font-variant-numeric:tabular-nums}
.masthead .fact b{color:var(--soft);font-weight:600}
.masthead .recent{margin-top:6px;font-family:var(--mono);font-size:11px;color:var(--soft)}
/* ---- re-entry: the one deliberate block ---- */
.reentry{margin:26px 0 8px;border:1px solid var(--rule-strong);background:#faf9f5;position:relative}
.reentry .band{display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:12px 18px;border-bottom:1px solid var(--rule)}
.reentry .band h2{margin:0;font-size:12.5px;font-weight:600;letter-spacing:.02em;color:var(--live)}
.reentry .band .since{font-family:var(--mono);font-size:11px;color:var(--muted)}
.reentry .body{padding:16px 18px 18px}
.reentry .now{font-family:var(--serif);font-size:19px;line-height:1.45;margin:0 0 4px}
.reentry .now small{font-family:var(--sans);font-size:11.5px;color:var(--muted);margin-left:8px;letter-spacing:0}
.reentry .stop{margin:12px 0 0;font-size:13.5px;color:var(--soft);padding-left:14px;border-left:2px solid var(--seal)}
.reentry ol.next{margin:14px 0 0;padding:0;list-style:none;counter-reset:n}
.reentry ol.next li{counter-increment:n;position:relative;padding:5px 0 5px 26px;font-size:13.5px;line-height:1.5;border-top:1px dotted var(--rule)}
.reentry ol.next li:first-child{border-top:0}
.reentry ol.next li::before{content:counter(n);position:absolute;left:0;top:5px;width:17px;height:17px;border:1px solid var(--rule-strong);border-radius:50%;font:10px/16px var(--mono);text-align:center;color:var(--muted)}
.reentry .empty{font-size:13px;color:var(--muted);margin:8px 0 0}
.reentry .jump{margin-top:14px;border:0;background:none;padding:0;font:12.5px var(--sans);color:var(--live);border-bottom:1px solid var(--live)}
/* ---- controls: time range only ---- */
.controls{display:flex;align-items:baseline;gap:10px;margin:30px 0 4px}
.controls span{font-size:12px;color:var(--muted)}
.controls select{font:12.5px var(--sans);color:var(--ink);background:none;border:1px solid var(--rule-strong);border-radius:0;padding:3px 6px}
/* ---- ledger ---- */
.day{margin-top:26px}
.day > .label{display:flex;align-items:center;gap:12px;font-family:var(--mono);font-size:11px;color:var(--muted);letter-spacing:.04em}
.day > .label::after{content:"";flex:1;height:1px;background:var(--rule)}
.row{display:grid;grid-template-columns:66px 1fr;gap:14px;position:relative}
.row > .time{font-family:var(--mono);font-size:11.5px;color:var(--muted);text-align:right;padding-top:9px;font-variant-numeric:tabular-nums}
.row > .col{border-left:1px solid var(--rule);padding:6px 0 6px 16px;position:relative}
.row > .col::before{content:"";position:absolute;left:-3.5px;top:15px;width:6px;height:6px;background:var(--paper);border:1px solid var(--rule-strong);border-radius:50%}
.row.open > .col::before{background:var(--live);border-color:var(--live)}
.row.milestone > .col::before{background:var(--seal);border-color:var(--seal);border-radius:0;transform:rotate(45deg)}
.row.decision{grid-template-columns:66px 1fr}
.row.decision > .col{margin-left:0;border-left:1px dotted var(--rule);padding-left:20px}
.row.decision > .col::before{left:-2px;top:13px;width:3px;height:3px;background:var(--rule-strong);border:0}
.trigger{display:block;width:100%;text-align:left;border:0;background:none;padding:0;font:inherit;color:inherit;cursor:pointer;transition:background-color .12s}
.row:hover > .col{background:rgba(255,255,255,.5)}
.row > .col{border-radius:0 3px 3px 0;transition:background-color .12s}
.title .open-mark{font:11px var(--mono);color:var(--rule-strong);margin-left:7px;vertical-align:1px}
.row.open-detail .title .open-mark{color:var(--seal)}
.trigger:focus-visible{outline:2px solid var(--seal);outline-offset:2px}
.title{font-family:var(--serif);font-size:17px;line-height:1.4;letter-spacing:0}
.row.decision .title{font-family:var(--sans);font-size:13.5px;color:var(--soft);font-weight:500}
.row.commit .title,.row.break .title{font-family:var(--mono);font-size:11.5px;color:var(--muted);font-weight:400}
.row.break .col::before{display:none}
.row.strike .title{text-decoration:line-through;text-decoration-color:var(--rule-strong);text-decoration-thickness:1px;color:var(--muted)}
.row.abandoned .title{color:var(--muted)}
.sub{margin-top:2px;font-size:11.5px;color:var(--muted);display:flex;gap:10px;flex-wrap:wrap}
.sub .who{color:var(--soft)}
.seal{color:var(--seal);font-size:11.5px}
.note{margin-top:3px;font-size:11.5px;color:var(--muted)}
.breakbar{display:grid;grid-template-columns:66px 1fr;gap:14px;margin:10px 0}
.breakbar .time{font-family:var(--mono);font-size:11px;color:var(--seal);text-align:right;padding-top:2px}
.breakbar .col{border-left:1px solid var(--seal);padding-left:16px;font-size:11.5px;color:var(--seal)}
/* ---- inline detail ---- */
.more{display:none;padding:10px 0 4px}
.row.open-detail .more{display:block}
.more section{padding:7px 0 0}
.more section:first-child{padding-top:2px}
.more h4{margin:0 0 3px;font-size:10.5px;font-weight:600;color:var(--muted);letter-spacing:.06em}
.more p{margin:0;font-size:13px;line-height:1.65;color:var(--soft)}
.more blockquote{margin:4px 0 0;padding-left:12px;border-left:2px solid var(--rule-strong);font-family:var(--serif);font-size:13.5px;line-height:1.6;color:var(--ink)}
.more ul{margin:0;padding:0;list-style:none}
.more li{font-size:12.5px;line-height:1.55;color:var(--soft);padding:2px 0 2px 12px;position:relative}
.more li::before{content:"—";position:absolute;left:0;color:var(--rule-strong)}
.more li em{font-style:normal;color:var(--muted);font-size:11px;margin-left:6px}
.more .link{border:0;background:none;padding:0;font:12.5px var(--sans);color:var(--live);border-bottom:1px solid var(--rule-strong);cursor:pointer}
.marker{display:grid;grid-template-columns:66px 1fr;gap:14px;margin:18px 0 6px}
.marker .col{grid-column:2;border-top:1px dashed var(--seal);padding-top:5px;font:11px var(--mono);color:var(--seal);letter-spacing:.04em}
footer{margin-top:56px;padding-top:14px;border-top:1px solid var(--rule);font-size:11px;color:var(--muted);display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}
.hidden{display:none}
@media(max-width:620px){
 .page{padding:40px 18px 80px}
 .row,.row.decision,.breakbar,.marker{grid-template-columns:48px 1fr;gap:10px}
 .row > .col{padding-left:12px}
 .reentry .band{display:block}.reentry .band .since{display:block;margin-top:2px}
}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
html{scroll-behavior:smooth}
</style></head>
<body><main class="page">
<header class="masthead">
  <h1>__NAME__</h1>
  <div class="fact"><b>__COUNT_ITEMS__</b> 个工作项 · <b>__COUNT_DECISIONS__</b> 条结论 · <b>__COUNT_SESSIONS__</b> 个会话 · __PERIOD__ · 数据全部来自本机会话记录</div>
  <div class="recent">最近一次活动：__RECENT__</div>
</header>

<section class="reentry" aria-labelledby="nowTitle">
  <div class="band"><h2 id="nowTitle">当前枝头</h2><span class="since" id="since"></span></div>
  <div class="body">
    <p class="now" id="nowTitleText"></p>
    <p class="stop" id="nowStop"></p>
    <ol class="next" id="nowNext"></ol>
    <button class="jump" id="jumpLast">回到上次看到的位置</button>
  </div>
</section>

<div class="controls">
  <span>只看</span>
  <select id="range" aria-label="时间范围">
    <option value="all">全部</option>
    <option value="30">最近 30 天</option>
    <option value="7">最近 7 天</option>
  </select>
</div>

<div id="ledger"></div>

<footer><span>__PATH__</span><span>sessionmap · 只读 · 每条都能回溯到原始会话 · <span id="stamp"></span></span></footer>
</main>
<script>
const DATA=__DATA__;
const $=s=>document.querySelector(s);
const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const SUMMARY={work:"工作项",milestone:"里程碑",decision:"结论",commit:"提交",break:"记忆断裂"};

function stamp(){const ms=DATA.generated_at;if(!ms)return "";return "生成于 "+new Date(ms).toLocaleString("zh-CN",{hour12:false})}
function rowClass(r){
  const parts=["row",r.kind];
  if(r.state)parts.push(r.state);
  if(r.strike)parts.push("strike");
  return parts.join(" ");
}
function rowHTML(r){
  if(r.kind==="break")return `<div class="breakbar" data-at="${r.at}"><div class="time">${esc(r.time)}</div><div class="col">上下文被压缩 · ${esc(r.note)}</div></div>`;
  const meta=[...(r.meta||[])].filter(Boolean).map(esc).join(" · ");
  const note=r.note?`<div class="note">${esc(r.note)}</div>`:"";
  const seal=(r.strike&&r.kind!=="decision")?`<span class="seal">已被后续工作覆盖</span>`:"";
  const body=(r.sections||[]).map(s=>{
    let inner="";
    if(s.text)inner+=`<p>${esc(s.text)}</p>`;
    if(s.quotes)inner+=s.quotes.map(q=>`<blockquote>${esc(q)}</blockquote>`).join("");
    if(s.lines)inner+=`<ul>${s.lines.map(l=>`<li>${esc(l.text)}${l.note?`<em>${esc(l.note)}</em>`:""}</li>`).join("")}</ul>`;
    if(s.links)inner+=`<p>${s.links.map(l=>`<button class="link" data-goto="${esc(l.target)}">${esc(l.text)}</button>`).join(" ")}</p>`;
    return `<section><h4>${esc(s.label)}</h4>${inner}</section>`;
  }).join("");
  const hasBody=body.trim().length>0;
  const trigger=`<button class="trigger" ${hasBody?`aria-expanded="false"`:"disabled"}><span class="title">${esc(r.title)}${hasBody?`<span class="open-mark" aria-hidden="true">+</span>`:""}</span><div class="sub">${meta}${seal}</div>${note}</button>`;
  return `<div class="${rowClass(r)}" id="${esc(r.id)}" data-at="${r.at}" data-kind="${r.kind}">
    <div class="time">${esc(r.time)}</div>
    <div class="col">${trigger}${hasBody?`<div class="more">${body}</div>`:""}</div>
  </div>`;
}
function renderLedger(){
  const html=DATA.days.map(d=>`<div class="day"><div class="label">${esc(d.label)}</div>${d.rows.map(rowHTML).join("")}</div>`).join("");
  $("#ledger").innerHTML=html;
  document.querySelectorAll(".trigger").forEach(b=>{
    if(b.disabled)return;
    b.addEventListener("click",()=>{
      const row=b.closest(".row");
      const open=row.classList.toggle("open-detail");
      b.setAttribute("aria-expanded",String(open));
      const mark=b.querySelector(".open-mark");
      if(mark)mark.textContent=open?"−":"+";
    });
  });
  document.querySelectorAll("[data-goto]").forEach(b=>b.addEventListener("click",()=>{
    const target=document.getElementById(b.dataset.goto);
    if(!target)return;
    target.scrollIntoView({block:"center"});
    const trigger=target.querySelector(".trigger");
    if(trigger&&!trigger.disabled&&!target.classList.contains("open-detail"))trigger.click();
  }));
}
function renderNow(){
  const n=DATA.now;
  $("#nowTitleText").innerHTML=`${esc(n.title)}${n.status?`<small>${esc(n.status)}</small>`:""}`;
  const stop=$("#nowStop");
  if(n.why_stopped){stop.textContent="停在这句："+n.why_stopped}else{stop.textContent="这一轮没有留下用户的明确指令，只能看到文件改动。";stop.style.borderLeftColor="var(--rule-strong)"}
  const ol=$("#nowNext");
  if(n.next_steps.length){ol.innerHTML=n.next_steps.map(t=>`<li>${esc(t)}</li>`).join("")}
  else{ol.outerHTML=`<p class="empty">没有登记在案的下一步，说明这一轮是自然结束的。</p>`}
}
const VIEW_KEY="sessionmap:last-view:"+(DATA.workspace&&DATA.workspace.id||"workspace");
function renderSince(){
  const previous=Number(localStorage.getItem(VIEW_KEY)||0);
  const since=$("#since");
  if(!previous){since.textContent="首次打开 · 这台机器上的第一次基线";$("#jumpLast").classList.add("hidden")}
  else{
    const d=new Date(previous);
    since.textContent="上次看到 "+d.toLocaleString("zh-CN",{hour12:false});
    const first=DATA.days.flatMap(x=>x.rows).find(r=>r.at>previous);
    if(first){
      const anchor=document.getElementById(first.id);
      if(anchor){
        const mark=document.createElement("div");
        mark.className="marker";
        mark.innerHTML=`<div class="col">上次看到这里</div>`;
        anchor.parentElement.insertBefore(mark,anchor);
        $("#jumpLast").addEventListener("click",()=>{mark.scrollIntoView({block:"center"})});
      }
    }else{$("#jumpLast").classList.add("hidden")}
  }
}
function applyRange(){
  const value=$("#range").value;
  const cutoff=value==="all"?0:Date.now()-Number(value)*86400000;
  document.querySelectorAll(".row,.breakbar").forEach(el=>{
    const at=Number(el.dataset.at||0);
    el.classList.toggle("hidden",cutoff>0&&at<cutoff);
  });
  document.querySelectorAll(".day").forEach(day=>{
    const visible=[...day.querySelectorAll(".row:not(.hidden),.breakbar:not(.hidden)")];
    day.classList.toggle("hidden",visible.length===0);
  });
}
$("#range").addEventListener("change",applyRange);
renderLedger();renderNow();renderSince();
$("#stamp").textContent=stamp();
setTimeout(()=>localStorage.setItem(VIEW_KEY,String(DATA.last_at||Date.now())),4000);
</script></body></html>'''


def render(view_path: str, out_path: str) -> None:
    data = json.loads(Path(view_path).read_text(encoding="utf-8"))
    model = build_model(data)
    name = (model.get("name") or "project")
    counts = model["counts"]
    document = (TEMPLATE
                .replace("__NAME__", esc(name))
                .replace("__COUNT_ITEMS__", str(counts["items"]))
                .replace("__COUNT_DECISIONS__", str(counts["decisions"]))
                .replace("__COUNT_SESSIONS__", str(counts["sessions"]))
                .replace("__PERIOD__", esc(model["period"]))
                .replace("__RECENT__", esc(model.get("recent") or "暂无"))
                .replace("__PATH__", esc(model.get("path") or ""))
                .replace("__DATA__", json.dumps(model, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")))
    out = Path(out_path)
    out.write_text(document, encoding="utf-8")
    print(f"已生成 {out} ({len(document)} 字符, {counts['items']} 个工作项, {counts['decisions']} 条结论)")


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 sessionmap 项目时间线（单页面）")
    ap.add_argument("view")
    ap.add_argument("--out", default="timeline.html")
    args = ap.parse_args()
    render(args.view, args.out)


if __name__ == "__main__":
    main()
