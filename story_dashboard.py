#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render the single-story sessionmap view.

The page deliberately has one visual: a project tree with memory cuts.  The
tree carries structure; labels and evidence stay in the drill-down layer.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def build_svg(data: dict) -> str:
    decisions = sorted(data.get("decisions", []), key=lambda d: str(d.get("when", "")))
    tracks = {t.get("name"): t for t in data.get("tracks", [])}
    t0 = data.get("stats", {}).get("first_at") or 0
    t1 = data.get("stats", {}).get("last_at") or t0 + 1
    span = max(1, t1 - t0)
    left = 118
    lane_gap = 78
    top, bottom = 48, 525
    width, height = 860, 565

    def y(at: int | None) -> float:
        if not at:
            return top
        return top + (at - t0) / span * (bottom - top)

    def x(d: dict) -> float:
        lane = tracks.get(d.get("track") or "其他", {}).get("index", 0)
        return left + lane * lane_gap

    by_track: dict[str, list[dict]] = {}
    for d in decisions:
        by_track.setdefault(d.get("track") or "其他", []).append(d)

    paths = [f'<path class="trunk" d="M {left} {top - 18} V {bottom + 14}"/>']
    for track, items in by_track.items():
        if not items:
            continue
        first, last = items[0], items[-1]
        lane_x = x(first)
        if lane_x == left:
            continue
        fy, ly = y(first.get("when_ms")), y(last.get("when_ms"))
        paths.append(f'<path class="branch-line" d="M {left} {fy} H {lane_x} V {ly}"/>')

    cuts = []
    for item in data.get("breaks", []):
        yy = y(item.get("at"))
        cuts.append(f'<g class="cut" data-when="{esc(item.get("when"))}"><path d="M 54 {yy} H {width - 22}"/><text x="{width - 18}" y="{yy - 6}">−{esc(item.get("loss_pct"))}%</text></g>')

    nodes = []
    for index, d in enumerate(decisions, 1):
        xx, yy = x(d), y(d.get("when_ms"))
        status = d.get("status") or "open"
        cls = "node done" if status == "done" else "node abandoned" if status == "abandoned" else "node open"
        if status == "abandoned":
            nodes.append(f'<g class="node-wrap {cls}" data-id="{esc(d.get("id"))}" tabindex="0"><rect x="{xx - 6}" y="{yy - 6}" width="12" height="12" transform="rotate(45 {xx} {yy})"/><text x="{xx + 13}" y="{yy + 4}">{index}</text></g>')
        else:
            nodes.append(f'<g class="node-wrap {cls}" data-id="{esc(d.get("id"))}" tabindex="0"><circle cx="{xx}" cy="{yy}" r="{7 if index == len(decisions) else 5}"/><text x="{xx + 13}" y="{yy + 4}">{index}</text></g>')

    lane_labels = []
    for track, meta in sorted(tracks.items(), key=lambda pair: pair[1].get("index", 0)):
        index = meta.get("index", 0)
        lane_labels.append(f'<text class="lane-label" x="{left + index * lane_gap}" y="28">{esc(track)}</text>')
    return f'''<svg class="story-svg" viewBox="0 0 {width} {height}" role="img" aria-label="项目决策工作树">
      <g class="lane-labels">{"".join(lane_labels)}</g>
      <g class="paths">{"".join(paths)}</g>
      <g class="cuts">{"".join(cuts)}</g>
      <g class="nodes">{"".join(nodes)}</g>
    </svg>'''


TEMPLATE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__ · sessionmap</title>
<style>
:root{--paper:#fbfaf6;--ink:#20211d;--muted:#85867d;--line:#d9d8d0;--rust:#ae4a34;--soft:#efede5}
*{box-sizing:border-box}html{background:var(--paper)}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.6 "Noto Serif SC","Source Han Serif SC","Songti SC",Georgia,serif;-webkit-font-smoothing:antialiased}button{font:inherit;cursor:pointer}.mono,.eyebrow,.meta,.chapter-meta,.drawer-meta{font-family:"Cascadia Mono","SFMono-Regular",Consolas,monospace}
.page{max-width:1050px;margin:0 auto;padding:60px 34px 90px}.head{display:flex;justify-content:space-between;gap:20px;align-items:start}.eyebrow{font-size:10px;color:var(--muted);letter-spacing:.15em}.head h1{max-width:830px;margin:15px 0 7px;font-size:clamp(31px,4.5vw,56px);font-weight:500;line-height:1.08;letter-spacing:-.055em}.meta{color:var(--muted);font-size:10px}.head button{border:1px solid #aaa9a1;background:transparent;padding:6px 10px;color:var(--ink);font-size:11px}.head button:hover{background:var(--ink);color:var(--paper)}
.hook{max-width:720px;margin:34px 0 0;padding-left:16px;border-left:2px solid var(--rust);font-size:15px;line-height:1.7}.hook strong{font-weight:500}.hook small{display:block;color:var(--muted);font-size:11px;margin-top:6px}.visual{margin-top:35px;border-top:1px solid #aaa9a1;border-bottom:1px solid #aaa9a1}.story-svg{display:block;width:100%;height:auto;overflow:visible}.paths path{fill:none;stroke:#8a8b83;stroke-width:1.25;vector-effect:non-scaling-stroke;stroke-dasharray:1000;stroke-dashoffset:1000;animation:draw 1.2s cubic-bezier(.2,.7,.2,1) forwards}.paths .branch-line{stroke:#b5b5ac}.lane-label{fill:var(--muted);font:10px "Cascadia Mono",monospace;text-anchor:middle}.node-wrap{cursor:pointer;outline:none}.node-wrap circle,.node-wrap rect{vector-effect:non-scaling-stroke;transition:all .15s ease}.node-wrap.open circle{fill:var(--paper);stroke:var(--ink);stroke-width:1.3}.node-wrap.done circle{fill:var(--ink);stroke:var(--ink);stroke-width:1.3}.node-wrap.abandoned rect{fill:var(--rust);stroke:var(--rust);stroke-width:1.3}.node-wrap:hover circle,.node-wrap:focus circle{r:8;stroke:var(--rust);stroke-width:2}.node-wrap text{fill:var(--muted);font:10px "Cascadia Mono",monospace}.cuts path{stroke:var(--rust);stroke-width:1.6;vector-effect:non-scaling-stroke}.cuts text{fill:var(--rust);font:10px "Cascadia Mono",monospace;text-anchor:end}.cut{opacity:0;animation:appear .35s ease .8s forwards}@keyframes draw{to{stroke-dashoffset:0}}@keyframes appear{to{opacity:1}}
.tree-caption{display:flex;justify-content:space-between;gap:20px;color:var(--muted);font-size:11px;margin:12px 0 0}.tree-caption .warn{color:var(--rust)}.chapters{margin-top:31px;border-top:1px solid var(--line)}.chapter{display:grid;grid-template-columns:38px 90px minmax(0,1fr);gap:10px;padding:13px 0;border-bottom:1px solid var(--line);cursor:pointer}.chapter:hover{background:rgba(255,255,255,.5)}.chapter-num{font:12px "Cascadia Mono",monospace;color:var(--muted)}.chapter-meta{color:var(--muted);font-size:10px;line-height:1.45}.chapter-title{font-size:14px;font-weight:600;line-height:1.35}.chapter-what{margin:3px 0 0;color:var(--muted);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.more{padding:12px 0;color:var(--muted);font-size:11px;border-bottom:1px solid var(--line);cursor:pointer}.more:hover{color:var(--ink)}.tail{display:flex;justify-content:space-between;gap:20px;margin-top:17px;color:var(--muted);font-size:11px}.tail strong{color:var(--ink);font-weight:500}.tail button{border:0;border-bottom:1px solid #aaa9a1;background:transparent;color:var(--ink);font-size:11px;padding:0}.details{display:none;margin-top:12px;padding:12px 0;border-top:1px solid var(--line);color:var(--muted);font-size:12px}.details.open{display:block}.details p{margin:4px 0}.details p:before{content:"·";margin-right:8px;color:var(--rust)}footer{margin-top:51px;color:var(--muted);font-size:10px}
.backdrop{position:fixed;inset:0;z-index:5;display:none;background:rgba(32,33,29,.16)}.backdrop.open{display:block}.drawer{position:fixed;z-index:6;top:0;right:0;bottom:0;width:min(430px,94vw);padding:30px 29px 42px;overflow:auto;background:var(--paper);box-shadow:-18px 0 50px rgba(32,33,29,.09);transform:translateX(104%);transition:transform .22s ease}.drawer.open{transform:translateX(0)}.drawer-close{float:right;border:0;background:transparent;color:var(--muted);font-size:20px}.drawer h2{margin:35px 0 5px;font-size:25px;line-height:1.2;font-weight:500;letter-spacing:-.04em}.drawer-meta{color:var(--muted);font-size:10px}.drawer-section{margin-top:25px;padding-top:11px;border-top:1px solid #aaa9a1}.drawer-section h3{margin:0 0 7px;color:var(--muted);font:10px "Cascadia Mono",monospace;letter-spacing:.08em;text-transform:uppercase}.drawer-section p{margin:0;font-size:13px;line-height:1.7}.evidence{margin:0;padding:0;list-style:none}.evidence li{padding:10px 0;border-bottom:1px solid var(--line)}.evidence .ev-meta{color:var(--muted);font:10px "Cascadia Mono",monospace}.evidence blockquote{margin:4px 0 0;font-size:12px;line-height:1.65}@media(max-width:700px){.page{padding:40px 16px 70px}.head{display:block}.head button{margin-top:17px}.hook{margin-top:25px}.tree-caption{display:block}.tree-caption span{display:block;margin-top:5px}.chapter{grid-template-columns:28px 59px minmax(0,1fr)}.chapter-what{white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}}
</style></head><body><main class="page"><header class="head"><div><div class="eyebrow">PROJECT MEMORY / __SOURCE__</div><h1 id="headline"></h1><div class="meta" id="period"></div></div><button id="refresh">刷新</button></header><div class="hook" id="hook"></div><div class="visual" id="visual">__SVG__</div><div class="tree-caption"><span>纵轴是时间，横向是工作线。实心节点已落地，空心节点仍在继续。</span><span class="warn" id="cuts"></span></div><div class="chapters" id="chapters"></div><div class="tail"><span id="tail"></span><button id="toggle">查看未收口事项</button></div><div class="details" id="details"></div><footer id="footer"></footer></main><div class="backdrop" id="backdrop"></div><aside class="drawer" id="drawer"><button class="drawer-close" id="drawerX">×</button><div class="eyebrow">DECISION / EVIDENCE</div><h2 id="drawerTitle"></h2><div class="drawer-meta" id="drawerMeta"></div><div class="drawer-section"><h3>做了什么</h3><p id="drawerWhat"></p></div><div class="drawer-section"><h3>为什么</h3><p id="drawerWhy"></p></div><div class="drawer-section"><h3>证据</h3><ul class="evidence" id="drawerEvidence"></ul></div></aside><script>
const DATA=__DATA__;const $=s=>document.querySelector(s);const statusText={open:'进行中',done:'已落地',abandoned:'已放弃'};function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function render(){const s=DATA.stats;const ds=[...(DATA.decisions||[])].sort((a,b)=>String(a.when).localeCompare(String(b.when)));const last=ds.at(-1);const open=[...ds].reverse().find(d=>d.status==='open')||last;$('#headline').textContent=DATA.headline||'项目记忆';$('#period').textContent=`${s.first_when} → ${s.last_when} · ${s.sessions} 个会话`;$('#hook').innerHTML=`<strong>这个项目在 ${s.span_hours} 小时里经历了 ${s.memory_breaks} 次记忆断裂，仍然在继续。</strong><small>现在的枝头：${esc(open?.title||'等待下一步')} · ${esc(open?.track||'主干')}</small>`;$('#cuts').textContent=`${s.memory_breaks} 次记忆切口`;const visible=ds.slice(-6);const old=ds.slice(0,-6);let h='';if(old.length)h+=`<div class="more" id="more">＋ 展开前面的 ${old.length} 个节点</div>`;h+=visible.map(d=>chapter(d,ds.indexOf(d)+1)).join('');$('#chapters').innerHTML=h;$('#more')?.addEventListener('click',()=>{document.querySelector('#more').outerHTML=old.map(d=>chapter(d,ds.indexOf(d)+1)).join('')});$('#tail').innerHTML=`<strong>当前枝头：</strong>${esc(open?.title||'—')} · ${DATA.unfinished?.length||0} 件事未收口`;$('#details').innerHTML=(DATA.unfinished||[]).map(x=>`<p>${esc(x.text)}</p>`).join('')||'<p>没有未完成事项。</p>';$('#footer').textContent=`sessionmap · 事实与证据可回溯 · ${new Date(DATA.generated_at).toLocaleString()}`;document.querySelector('.story-svg')?.addEventListener('click',e=>{const node=e.target.closest('.node-wrap');if(node)openDecision(node.dataset.id)});document.querySelectorAll('.chapter').forEach(n=>n.addEventListener('click',()=>openDecision(n.dataset.id)))}
function chapter(d,n){const st=d.status==='abandoned'?' warn':'';return `<article class="chapter" data-id="${esc(d.id)}"><div class="chapter-num">${String(n).padStart(2,'0')}</div><div class="chapter-meta">${esc((d.when||'').slice(5,10))}<br>${esc(d.track||'主干')}</div><div><div class="chapter-title${st}">${esc(d.title)} <span class="status">· ${esc(statusText[d.status]||d.status)}</span></div><p class="chapter-what">${esc(d.what)}</p></div></article>`}
function openDecision(id){const d=(DATA.decisions||[]).find(x=>x.id===id);if(!d)return;$('#drawerTitle').textContent=d.title||d.id;$('#drawerMeta').textContent=`${d.when||'—'} · ${d.track||'其他'} · ${statusText[d.status]||d.status}`;$('#drawerWhat').textContent=d.what||'—';$('#drawerWhy').textContent=d.why||'—';const refs=d.evidence_refs||[];$('#drawerEvidence').innerHTML=refs.length?refs.map(e=>`<li><div class="ev-meta">${esc(e.when)} · ${esc(e.session_title)} · ${Math.round((e.confidence||0)*100)}% match</div><blockquote>“${esc(e.quote)}”</blockquote></li>`).join(''):'<li>暂无匹配证据</li>';$('#drawer').classList.add('open');$('#backdrop').classList.add('open')}
function closeDrawer(){$('#drawer').classList.remove('open');$('#backdrop').classList.remove('open')}$('#refresh').addEventListener('click',()=>location.reload());$('#drawerX').addEventListener('click',closeDrawer);$('#backdrop').addEventListener('click',closeDrawer);$('#toggle').addEventListener('click',()=>{$('#details').classList.toggle('open');$('#toggle').textContent=$('#details').classList.contains('open')?'收起未收口事项':'查看未收口事项'});render();</script></body></html>'''


def render(view_path: str, out_path: str) -> None:
    data = json.loads(Path(view_path).read_text(encoding="utf-8"))
    document = TEMPLATE.replace("__TITLE__", esc(data.get("workspace", {}).get("name", "project")))
    document = document.replace("__SOURCE__", esc(data.get("workspace", {}).get("source", "local")))
    document = document.replace("__SVG__", build_svg(data))
    document = document.replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    Path(out_path).write_text(document, encoding="utf-8")
    print(f"已生成 {out_path} ({len(document)} 字符)")


def main() -> None:
    ap = argparse.ArgumentParser(description="生成单一故事主视觉的 sessionmap 页面")
    ap.add_argument("view")
    ap.add_argument("--out", default="report.html")
    args = ap.parse_args()
    render(args.view, args.out)


if __name__ == "__main__":
    main()
