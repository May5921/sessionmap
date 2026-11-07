#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render the evidence-driven project dashboard."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · sessionmap</title>
<style>
:root {
  --paper: #f4f3ee;
  --paper-deep: #eceae2;
  --ink: #20211d;
  --muted: #777970;
  --faint: #a9aaa2;
  --line: #d7d7cf;
  --line-strong: #bbbcb3;
  --rust: #b64c35;
  --rust-soft: #edd7d0;
  --blue: #345b73;
  --olive: #68704a;
  --purple: #765f76;
  --shadow: 0 20px 45px rgba(32,33,29,.08);
}
* { box-sizing: border-box; }
html { background: var(--paper); }
body {
  margin: 0;
  color: var(--ink);
  background:
    linear-gradient(rgba(32,33,29,.025) 1px, transparent 1px),
    var(--paper);
  background-size: 100% 28px;
  font: 14px/1.65 "Noto Serif SC", "Source Han Serif SC", "Songti SC", Georgia, serif;
  -webkit-font-smoothing: antialiased;
}
button, input { font: inherit; }
button { cursor: pointer; }
.mono, .meta, .eyebrow, .stat-label, .when, .badge, .activity-label {
  font-family: "Cascadia Mono", "SFMono-Regular", Consolas, monospace;
}
.shell { max-width: 1180px; margin: 0 auto; padding: 46px 34px 96px; }
.topline { display: flex; justify-content: space-between; gap: 24px; align-items: start; }
.eyebrow { color: var(--muted); font-size: 11px; letter-spacing: .13em; text-transform: uppercase; }
h1 { margin: 12px 0 8px; max-width: 850px; font-size: clamp(29px, 4vw, 52px); line-height: 1.1; letter-spacing: -.045em; font-weight: 500; }
.lede { max-width: 720px; margin: 0; color: var(--muted); font-size: 16px; line-height: 1.75; }
.toolbar { display: flex; gap: 8px; align-items: center; }
.toolbar button { border: 1px solid var(--line-strong); background: transparent; color: var(--ink); padding: 7px 11px; font-size: 12px; }
.toolbar button:hover { background: var(--ink); color: var(--paper); }
.source { margin-top: 24px; padding-top: 11px; border-top: 1px solid var(--line-strong); color: var(--muted); font-size: 11px; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0; margin-top: 44px; border-top: 1px solid var(--line-strong); border-bottom: 1px solid var(--line-strong); }
.stat { padding: 17px 18px 16px 0; border-right: 1px solid var(--line); }
.stat + .stat { padding-left: 18px; }
.stat:last-child { border-right: 0; }
.stat-label { color: var(--muted); font-size: 10px; letter-spacing: .08em; text-transform: uppercase; }
.stat-value { margin-top: 4px; font-size: 26px; letter-spacing: -.04em; }
.stat-note { color: var(--muted); font-size: 11px; }
section { margin-top: 54px; }
.section-head { display: flex; justify-content: space-between; gap: 16px; align-items: baseline; margin-bottom: 14px; }
h2 { margin: 0; font-size: 17px; font-weight: 500; letter-spacing: -.02em; }
.section-note { color: var(--muted); font-size: 12px; }
.now { display: grid; grid-template-columns: minmax(280px, 1.25fr) repeat(3, 1fr); border-top: 1px solid var(--line-strong); border-bottom: 1px solid var(--line-strong); }
.now > div { min-height: 115px; padding: 17px 18px; border-right: 1px solid var(--line); }
.now > div:first-child { padding-left: 0; }
.now > div:last-child { border-right: 0; }
.now-label { color: var(--muted); font-size: 11px; }
.now-value { margin-top: 8px; font-size: 15px; line-height: 1.55; }
.now-value strong { font-size: 18px; font-weight: 500; }
.state-dot { display: inline-block; width: 7px; height: 7px; margin-right: 7px; background: var(--olive); border-radius: 50%; }
.state-dot.open { background: var(--rust); }
.state-dot.dead { background: var(--faint); }
.activity { padding: 16px 0 8px; border-top: 1px solid var(--line-strong); border-bottom: 1px solid var(--line-strong); }
.activity-grid { display: grid; grid-template-columns: repeat(var(--cells), minmax(2px, 1fr)); align-items: end; gap: 2px; height: 74px; }
.activity-cell { min-height: 3px; background: var(--ink); opacity: .12; transition: transform .15s ease, opacity .15s ease; }
.activity-cell:hover { transform: translateY(-4px); opacity: 1 !important; }
.activity-cell.break { background: var(--rust); opacity: 1; }
.activity-axis { display: flex; justify-content: space-between; color: var(--muted); font: 10px "Cascadia Mono", monospace; margin-top: 8px; }
.activity-caption { display: flex; justify-content: space-between; gap: 16px; margin-top: 11px; color: var(--muted); font-size: 12px; }
.changes { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--line-strong); border-bottom: 1px solid var(--line-strong); }
.change { min-height: 110px; padding: 14px 16px 15px 0; border-right: 1px solid var(--line); }
.change + .change { padding-left: 16px; }
.change:last-child { border-right: 0; }
.change .when { color: var(--muted); font-size: 10px; }
.change .change-type { margin-top: 8px; color: var(--rust); font-size: 11px; }
.change p { margin: 4px 0 0; font-size: 13px; line-height: 1.5; }
.tree-wrap { position: relative; border-top: 1px solid var(--line-strong); border-bottom: 1px solid var(--line-strong); }
.tree-row { display: grid; grid-template-columns: 88px minmax(0, 1fr) 112px; min-height: 94px; border-bottom: 1px solid var(--line); cursor: pointer; transition: background .15s ease; }
.tree-row:last-child { border-bottom: 0; }
.tree-row:hover { background: rgba(255,255,255,.48); }
.tree-rail { position: relative; border-right: 1px solid var(--line); }
.lane-line { position: absolute; top: 0; bottom: 0; left: calc(24px + var(--lane) * 11px); width: 1px; background: var(--line-strong); }
.lane-line.dead { background: repeating-linear-gradient(to bottom, var(--rust) 0 4px, transparent 4px 8px); }
.node { position: absolute; z-index: 2; top: 50%; left: calc(24px + var(--lane) * 11px); width: 11px; height: 11px; transform: translate(-50%, -50%); border: 2px solid var(--ink); background: var(--paper); border-radius: 50%; }
.node.done { background: var(--olive); border-color: var(--olive); }
.node.abandoned { background: var(--rust); border-color: var(--rust); }
.branch-link { position: absolute; top: 50%; left: 24px; width: calc(var(--lane) * 11px); height: 1px; background: var(--line-strong); }
.decision-copy { padding: 15px 18px 15px 20px; min-width: 0; }
.decision-top { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.decision-title { font-size: 16px; font-weight: 600; letter-spacing: -.01em; }
.badge { display: inline-block; padding: 1px 6px; color: var(--muted); border: 1px solid var(--line-strong); font-size: 10px; line-height: 1.5; }
.badge.open { color: var(--rust); border-color: var(--rust); }
.badge.done { color: var(--olive); border-color: var(--olive); }
.badge.abandoned { color: var(--rust); background: var(--rust-soft); border-color: var(--rust); }
.decision-what { margin: 4px 0 0; color: var(--muted); font-size: 13px; line-height: 1.55; }
.decision-evidence { margin-top: 8px; color: var(--blue); font-size: 11px; }
.when { padding: 17px 12px 0 0; color: var(--muted); text-align: right; font-size: 10px; line-height: 1.5; }
.cut-row { display: flex; gap: 12px; align-items: center; padding: 12px 18px; color: var(--rust); background: rgba(182,76,53,.045); border-bottom: 1px solid var(--rust); border-top: 1px solid var(--rust); font-size: 12px; }
.cut-row strong { font-family: "Cascadia Mono", monospace; font-weight: 500; }
.track-key { display: flex; flex-wrap: wrap; gap: 7px 14px; margin: 13px 0 0 88px; color: var(--muted); font-size: 11px; }
.track-key span::before { content: ""; display: inline-block; width: 12px; height: 2px; margin: 0 6px 3px 0; background: var(--ink); }
.track-key span:nth-child(2)::before { background: var(--blue); }
.track-key span:nth-child(3)::before { background: var(--purple); }
.track-key span:nth-child(4)::before { background: var(--olive); }
.unfinished { display: grid; grid-template-columns: 1fr 1fr; gap: 0 26px; border-top: 1px solid var(--line-strong); border-bottom: 1px solid var(--line-strong); }
.unfinished-item { display: flex; gap: 10px; padding: 12px 0; border-bottom: 1px solid var(--line); font-size: 13px; }
.unfinished-item:nth-last-child(-n+2) { border-bottom: 0; }
.unfinished-item::before { content: ""; flex: 0 0 7px; height: 7px; margin-top: 8px; border: 1px solid var(--rust); border-radius: 50%; }
.drawer-backdrop { position: fixed; inset: 0; display: none; background: rgba(32,33,29,.18); z-index: 9; }
.drawer-backdrop.open { display: block; }
.drawer { position: fixed; z-index: 10; top: 0; right: 0; bottom: 0; width: min(430px, 94vw); padding: 28px 28px 38px; overflow: auto; background: var(--paper); box-shadow: var(--shadow); transform: translateX(105%); transition: transform .22s ease; }
.drawer.open { transform: translateX(0); }
.drawer-close { float: right; border: 0; background: transparent; color: var(--muted); font-size: 20px; }
.drawer h3 { margin: 32px 0 7px; font-size: 24px; line-height: 1.2; font-weight: 500; letter-spacing: -.03em; }
.drawer .drawer-meta { color: var(--muted); font: 11px "Cascadia Mono", monospace; }
.drawer-section { margin-top: 28px; padding-top: 12px; border-top: 1px solid var(--line-strong); }
.drawer-section h4 { margin: 0 0 8px; color: var(--muted); font: 10px "Cascadia Mono", monospace; letter-spacing: .08em; text-transform: uppercase; }
.drawer-section p { margin: 0; line-height: 1.7; }
.evidence { margin: 0; padding: 0; list-style: none; }
.evidence li { padding: 11px 0; border-bottom: 1px solid var(--line); }
.evidence li:last-child { border-bottom: 0; }
.evidence .ev-meta { color: var(--muted); font: 10px "Cascadia Mono", monospace; }
.evidence blockquote { margin: 5px 0 0; color: var(--ink); font-size: 13px; line-height: 1.6; }
.empty { padding: 28px 0; color: var(--muted); }
footer { margin-top: 58px; padding-top: 12px; border-top: 1px solid var(--line); color: var(--muted); font-size: 11px; }
@media (max-width: 820px) {
  .shell { padding: 34px 18px 80px; }
  .topline { display: block; }
  .toolbar { margin-top: 18px; }
  .stats, .now, .changes { grid-template-columns: 1fr 1fr; }
  .stat:nth-child(2), .now > div:nth-child(2), .change:nth-child(2) { border-right: 0; }
  .stat:nth-child(n+3), .now > div:nth-child(n+3), .change:nth-child(n+3) { border-top: 1px solid var(--line); }
  .now > div:first-child, .change:first-child { padding-left: 0; }
  .now > div:nth-child(odd), .change:nth-child(odd) { padding-left: 0; }
  .tree-row { grid-template-columns: 62px minmax(0, 1fr) 74px; }
  .track-key { margin-left: 62px; }
  .unfinished { grid-template-columns: 1fr; }
  .unfinished-item:nth-last-child(-n+2) { border-bottom: 1px solid var(--line); }
  .unfinished-item:last-child { border-bottom: 0; }
}
@media (max-width: 520px) {
  h1 { font-size: 32px; }
  .stats, .now, .changes { grid-template-columns: 1fr; }
  .stat, .stat + .stat, .now > div, .now > div:first-child, .change, .change + .change { padding-left: 0; border-right: 0; border-top: 1px solid var(--line); }
  .stat:first-child, .now > div:first-child, .change:first-child { border-top: 0; }
  .tree-row { grid-template-columns: 48px minmax(0, 1fr); }
  .when { grid-column: 2; padding: 0 18px 14px 20px; text-align: left; }
  .track-key { margin-left: 48px; }
}
</style>
</head>
<body>
<main class="shell">
  <header>
    <div class="topline">
      <div>
        <div class="eyebrow">SESSIONMAP / PROJECT MEMORY</div>
        <h1 id="headline"></h1>
        <p class="lede">把分散在多个会话里的工作，整理成可以继续使用的项目记忆。</p>
      </div>
      <div class="toolbar">
        <button id="refresh">刷新数据</button>
        <button id="closeDrawer" hidden>关闭详情</button>
      </div>
    </div>
    <div class="source" id="source"></div>
  </header>

  <div class="stats" id="stats"></div>

  <section>
    <div class="section-head"><h2>现在</h2><span class="section-note">先回答“项目做到哪了”</span></div>
    <div class="now" id="now"></div>
  </section>

  <section>
    <div class="section-head"><h2>自上次查看以来</h2><span class="section-note">变化先于历史</span></div>
    <div class="changes" id="changes"></div>
  </section>

  <section>
    <div class="section-head"><h2>活动节律</h2><span class="section-note">密度是项目的呼吸</span></div>
    <div class="activity" id="activity"></div>
  </section>

  <section>
    <div class="section-head"><h2>工作树</h2><span class="section-note">每个节点都能回到证据</span></div>
    <div class="tree-wrap" id="tree"></div>
    <div class="track-key" id="trackKey"></div>
  </section>

  <section>
    <div class="section-head"><h2>还没收口</h2><span class="section-note">状态来自会话记录与待办</span></div>
    <div class="unfinished" id="unfinished"></div>
  </section>

  <footer id="footer"></footer>
</main>

<div class="drawer-backdrop" id="backdrop"></div>
<aside class="drawer" id="drawer" aria-live="polite">
  <button class="drawer-close" id="drawerX" aria-label="关闭">×</button>
  <div class="eyebrow">DECISION / EVIDENCE</div>
  <h3 id="drawerTitle">选择一个节点</h3>
  <div class="drawer-meta" id="drawerMeta"></div>
  <div class="drawer-section"><h4>做了什么</h4><p id="drawerWhat"></p></div>
  <div class="drawer-section"><h4>为什么</h4><p id="drawerWhy"></p></div>
  <div class="drawer-section"><h4>证据</h4><ul class="evidence" id="drawerEvidence"></ul></div>
</aside>

<script>
const DATA = __DATA__;
const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const statusText = {open:'进行中', done:'已落地', abandoned:'已放弃'};
const typeText = {user_message:'用户输入', todo_change:'待办变化', memory_break:'记忆断裂', error:'错误'};
const colors = ['#20211d','#345b73','#765f76','#68704a','#b64c35','#806548'];

function renderStats() {
  const s = DATA.stats;
  const items = [
    ['sessions', s.sessions, '个会话', `${s.main_sessions} 主线 · ${s.child_sessions} 子代理`],
    ['events', s.events.toLocaleString(), '个事件', `${s.user_messages} 条用户输入 · ${s.tool_calls.toLocaleString()} 次工具调用`],
    ['span', s.span_hours + 'h', '时间跨度', `${s.first_when} → ${s.last_when}`],
    ['breaks', s.memory_breaks, '次记忆断裂', `${s.active_hours} 个小时有实际活动`],
  ];
  $('#stats').innerHTML = items.map(([_, value, label, note]) => `<div class="stat"><div class="stat-label">${label}</div><div class="stat-value">${esc(value)}</div><div class="stat-note">${esc(note)}</div></div>`).join('');
}

function renderNow() {
  const tracks = DATA.tracks || [];
  const active = tracks.filter(t => t.state === 'open').map(t => `<span class="state-dot open"></span>${esc(t.name)} <small>(${t.open})</small>`).join('<br>') || '—';
  const done = tracks.filter(t => t.state === 'done').map(t => `<span class="state-dot"></span>${esc(t.name)}`).join('<br>') || '—';
  const dead = tracks.filter(t => t.state === 'abandoned').map(t => `<span class="state-dot dead"></span>${esc(t.name)}`).join('<br>') || '—';
  $('#now').innerHTML = `<div><div class="now-label">项目现在</div><div class="now-value"><strong>${esc(DATA.headline)}</strong></div></div>` +
    `<div><div class="now-label">在跑</div><div class="now-value">${active}</div></div>` +
    `<div><div class="now-label">已落地</div><div class="now-value">${done}</div></div>` +
    `<div><div class="now-label">走死</div><div class="now-value">${dead}</div></div>`;
}

function renderChanges() {
  const changes = DATA.recent_changes || [];
  $('#changes').innerHTML = changes.length ? changes.slice(-4).map(change => `<article class="change"><div class="when">${esc(change.when)}</div><div class="change-type">${esc(typeText[change.type] || change.type)}</div><p>${esc(change.summary)}</p></article>`).join('') : '<div class="empty">还没有可显示的变化。</div>';
}

function renderActivity() {
  const activity = DATA.activity || {buckets:[],max:0};
  const max = Math.max(1, activity.max);
  const cells = activity.buckets.map((bucket, index) => {
    const opacity = .12 + .88 * Math.sqrt(bucket.count / max);
    const title = `${bucket.count} 个事件 · ${bucket.user} 条输入 · ${bucket.tool} 次工具调用${bucket.break ? ` · ${bucket.break} 次记忆断裂` : ''}`;
    return `<i class="activity-cell${bucket.break ? ' break' : ''}" style="opacity:${opacity}" title="${esc(title)}"></i>`;
  }).join('');
  $('#activity').innerHTML = `<div class="activity-grid" style="--cells:${Math.max(1, activity.buckets.length)}">${cells}</div><div class="activity-axis"><span>${esc(DATA.stats.first_when)}</span><span>${esc(DATA.stats.last_when)}</span></div><div class="activity-caption"><span>${DATA.stats.active_hours} 个小时有实际活动，空白也是项目节奏。</span><span class="mono">红色 = context break</span></div>`;
}

function renderTree() {
  const tracks = DATA.tracks || [];
  const trackIndex = Object.fromEntries(tracks.map(t => [t.name, t.index]));
  const decisions = [...(DATA.decisions || [])].sort((a,b) => String(a.when).localeCompare(String(b.when)));
  const rows = decisions.map((d, i) => {
    const lane = trackIndex[d.track] ?? 0;
    const klass = d.status === 'abandoned' ? 'abandoned' : d.status === 'done' ? 'done' : 'open';
    const color = colors[lane % colors.length];
    return `<article class="tree-row" data-id="${esc(d.id)}" style="--lane:${lane};--branch:${color}"><div class="tree-rail"><span class="lane-line ${klass === 'abandoned' ? 'dead' : ''}"></span>${lane ? '<span class="branch-link"></span>' : ''}<span class="node ${klass}"></span></div><div class="decision-copy"><div class="decision-top"><span class="decision-title">${esc(d.title)}</span><span class="badge ${klass}">${esc(statusText[d.status] || d.status)}</span><span class="badge">${esc(d.track || '其他')}</span></div><p class="decision-what">${esc(d.what)}</p><div class="decision-evidence">${d.evidence_count ? `↳ ${d.evidence_count} 条证据 · 点击查看` : '↳ 暂无证据链接'}</div></div><div class="when">${esc((d.when || '').slice(5, 10))}<br>${esc((d.when || '').slice(11, 16))}</div></article>`;
  }).join('');
  $('#tree').innerHTML = rows || '<div class="empty">还没有决策节点。</div>';
  $('#trackKey').innerHTML = tracks.map((t, i) => `<span style="--track:${i}">${esc(t.name)}</span>`).join('');
  document.querySelectorAll('.tree-row').forEach(row => row.addEventListener('click', () => openDecision(row.dataset.id)));
}

function renderUnfinished() {
  const items = DATA.unfinished || [];
  $('#unfinished').innerHTML = items.length ? items.map(item => `<div class="unfinished-item">${esc(item.text)}</div>`).join('') : '<div class="empty">没有未完成事项。</div>';
}

function openDecision(id) {
  const d = (DATA.decisions || []).find(item => item.id === id);
  if (!d) return;
  $('#drawerTitle').textContent = d.title || d.id;
  $('#drawerMeta').textContent = `${d.when || '—'} · ${d.track || '其他'} · ${statusText[d.status] || d.status}`;
  $('#drawerWhat').textContent = d.what || '—';
  $('#drawerWhy').textContent = d.why || '—';
  const evidence = d.evidence_refs || [];
  $('#drawerEvidence').innerHTML = evidence.length ? evidence.map(ev => `<li><div class="ev-meta">${esc(ev.when)} · ${esc(ev.session_title)} · ${Math.round((ev.confidence || 0) * 100)}% match</div><blockquote>“${esc(ev.quote)}”</blockquote></li>`).join('') : '<li class="empty">当前节点还没有匹配到原始证据。</li>';
  $('#drawer').classList.add('open'); $('#backdrop').classList.add('open'); $('#closeDrawer').hidden = false;
}
function closeDrawer() { $('#drawer').classList.remove('open'); $('#backdrop').classList.remove('open'); $('#closeDrawer').hidden = true; }

function render() {
  $('#headline').textContent = DATA.headline || '项目记忆';
  $('#source').textContent = `${DATA.workspace.name} · source: ${DATA.workspace.source} · ${DATA.stats.first_when} → ${DATA.stats.last_when}`;
  $('#footer').textContent = `sessionmap · 事实来自只读源库，语义节点保留证据 · 生成于 ${new Date(DATA.generated_at).toLocaleString()}`;
  renderStats(); renderNow(); renderChanges(); renderActivity(); renderTree(); renderUnfinished();
}
$('#refresh').addEventListener('click', () => location.reload());
$('#drawerX').addEventListener('click', closeDrawer); $('#backdrop').addEventListener('click', closeDrawer); $('#closeDrawer').addEventListener('click', closeDrawer);
render();
</script>
</body>
</html>'''


def render(view_path: str, out_path: str) -> None:
    data = json.loads(Path(view_path).read_text(encoding="utf-8"))
    html_doc = TEMPLATE.replace("__TITLE__", esc(data["workspace"]["name"]))
    html_doc = html_doc.replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    Path(out_path).write_text(html_doc, encoding="utf-8")
    print(f"已生成 {out_path} ({len(html_doc)} 字符)")


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 sessionmap 项目状态页")
    ap.add_argument("view", help="view.py 生成的 project-view.json")
    ap.add_argument("--out", default="report.html")
    args = ap.parse_args()
    render(args.view, args.out)


if __name__ == "__main__":
    main()
