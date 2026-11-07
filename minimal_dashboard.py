#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render the deliberately minimal sessionmap work tree."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


TEMPLATE = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · sessionmap</title>
<style>
:root{--paper:#faf9f5;--ink:#20211d;--muted:#7b7c74;--line:#dddcd4;--strong:#a9aaa1;--warn:#b14c35;--soft:#efede5}
*{box-sizing:border-box}html{background:var(--paper)}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.6 "Noto Serif SC","Source Han Serif SC","Songti SC",Georgia,serif;-webkit-font-smoothing:antialiased}button{font:inherit;cursor:pointer}.mono,.eyebrow,.meta,.time,.branch,.drawer-meta{font-family:"Cascadia Mono","SFMono-Regular",Consolas,monospace}
.page{max-width:980px;margin:0 auto;padding:64px 34px 90px}.eyebrow{color:var(--muted);font-size:10px;letter-spacing:.14em}.head{display:flex;justify-content:space-between;gap:24px;align-items:start}.head h1{max-width:780px;margin:15px 0 8px;font-size:clamp(30px,4vw,48px);font-weight:500;line-height:1.12;letter-spacing:-.05em}.head p{margin:0;color:var(--muted);font-size:13px}.head button{border:1px solid var(--strong);padding:6px 10px;background:transparent;color:var(--ink);font-size:11px}.head button:hover{background:var(--ink);color:var(--paper)}
.repo{display:flex;gap:16px;align-items:center;margin-top:27px;padding:10px 0;border-top:1px solid var(--strong);border-bottom:1px solid var(--line);color:var(--muted);font-size:11px}.repo strong{color:var(--ink);font-weight:500}.repo .dirty{color:var(--warn)}.repo .sep{color:var(--line-strong)}
.change{margin-top:20px;color:var(--muted);font-size:12px}.change strong{color:var(--ink);font-weight:500}.change .warn{color:var(--warn)}
.tree-head{display:flex;justify-content:space-between;align-items:baseline;margin-top:49px;margin-bottom:12px}.tree-head h2{margin:0;font-size:17px;font-weight:500;letter-spacing:-.02em}.tree-head span{color:var(--muted);font-size:11px}.tree{border-top:1px solid var(--strong);border-bottom:1px solid var(--strong)}
.row{display:grid;grid-template-columns:82px 72px minmax(0,1fr);min-height:72px;border-bottom:1px solid var(--line);cursor:pointer;transition:background .15s ease}.row:last-child{border-bottom:0}.row:hover{background:rgba(255,255,255,.6)}.rail{position:relative;border-right:1px solid var(--line)}.rail .line{position:absolute;left:25px;top:0;bottom:0;width:1px;background:var(--strong)}.rail .line.dead{background:repeating-linear-gradient(to bottom,var(--warn) 0 4px,transparent 4px 8px)}.rail .node{position:absolute;z-index:1;top:50%;left:25px;width:10px;height:10px;transform:translate(-50%,-50%);border:1px solid var(--ink);background:var(--paper);border-radius:50%}.rail .node.done{background:var(--ink)}.rail .node.abandoned{border-radius:0;background:var(--warn);border-color:var(--warn);transform:translate(-50%,-50%) rotate(45deg)}.rail .branch{position:absolute;top:50%;left:25px;width:calc(var(--lane) * 12px);height:1px;background:var(--strong)}.time{padding:15px 12px 0 0;text-align:right;color:var(--muted);font-size:10px;line-height:1.45}.copy{min-width:0;padding:13px 16px}.top{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}.title{font-size:15px;font-weight:600;letter-spacing:-.01em}.status{color:var(--muted);font-size:10px}.status.warn{color:var(--warn)}.meaning{margin:3px 0 0;color:var(--muted);font-size:12px;line-height:1.48;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.source{margin-top:5px;color:var(--muted);font:10px "Cascadia Mono",monospace}.source strong{color:var(--ink);font-weight:400}.cut{display:flex;gap:12px;align-items:center;padding:9px 16px;color:var(--warn);background:rgba(177,76,53,.035);border-bottom:1px solid var(--warn);border-top:1px solid var(--warn);font-size:11px}.cut strong{font:11px "Cascadia Mono",monospace;font-weight:500}.keys{display:flex;gap:15px;flex-wrap:wrap;margin:12px 0 0 82px;color:var(--muted);font-size:10px}.keys span:before{content:"";display:inline-block;width:12px;height:1px;margin:0 6px 3px 0;background:var(--ink)}.keys span:nth-child(2):before{background:var(--strong)}.keys span:nth-child(3):before{background:var(--warn)}
.tail{display:flex;justify-content:space-between;gap:16px;margin-top:18px;color:var(--muted);font-size:11px}.tail button{border:0;border-bottom:1px solid var(--strong);padding:0;background:transparent;color:var(--ink);font-size:11px}.unfinished{display:none;margin-top:12px;padding:12px 0;border-top:1px solid var(--line);color:var(--muted);font-size:12px}.unfinished.open{display:block}.unfinished p{margin:4px 0}.unfinished p:before{content:"·";margin-right:8px;color:var(--warn)}footer{margin-top:52px;color:var(--muted);font-size:10px}
.backdrop{position:fixed;inset:0;z-index:5;display:none;background:rgba(32,33,29,.16)}.backdrop.open{display:block}.drawer{position:fixed;z-index:6;top:0;right:0;bottom:0;width:min(430px,94vw);padding:30px 29px 44px;overflow:auto;background:var(--paper);box-shadow:-18px 0 50px rgba(32,33,29,.08);transform:translateX(104%);transition:transform .22s ease}.drawer.open{transform:translateX(0)}.drawer-close{float:right;border:0;background:transparent;color:var(--muted);font-size:20px}.drawer h3{margin:35px 0 5px;font-size:25px;line-height:1.2;font-weight:500;letter-spacing:-.04em}.drawer-meta{color:var(--muted);font-size:10px}.drawer-section{margin-top:25px;padding-top:11px;border-top:1px solid var(--strong)}.drawer-section h4{margin:0 0 7px;color:var(--muted);font:10px "Cascadia Mono",monospace;letter-spacing:.08em;text-transform:uppercase}.drawer-section p{margin:0;font-size:13px;line-height:1.7}.evidence{margin:0;padding:0;list-style:none}.evidence li{padding:10px 0;border-bottom:1px solid var(--line)}.evidence .ev-meta{color:var(--muted);font:10px "Cascadia Mono",monospace}.evidence blockquote{margin:4px 0 0;font-size:12px;line-height:1.65}
@media(max-width:680px){.page{padding:38px 17px 70px}.head{display:block}.head button{margin-top:17px}.repo{flex-wrap:wrap;gap:6px 12px}.row{grid-template-columns:54px 59px minmax(0,1fr)}.rail .line,.rail .node,.rail .branch{left:20px}.time{padding-right:8px}.copy{padding-left:12px}.keys{margin-left:54px}.meaning{white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}.tail{display:block}.tail span{display:block;margin-top:7px}}
</style></head><body><main class="page">
<header class="head"><div><div class="eyebrow">PROJECT MEMORY / __SOURCE__</div><h1 id="headline"></h1><p id="period"></p></div><button id="refresh">刷新</button></header>
<div class="repo" id="repo"></div><div class="change" id="change"></div>
<div class="tree-head"><h2>工作树</h2><span>时间向下 · 分支向右 · 点击查看证据</span></div>
<div class="tree" id="tree"></div><div class="keys" id="keys"></div>
<div class="tail"><span id="tailText"></span><button id="toggleUnfinished">查看未收口事项</button></div><div class="unfinished" id="unfinished"></div>
<footer id="footer"></footer></main>
<div class="backdrop" id="backdrop"></div><aside class="drawer" id="drawer"><button class="drawer-close" id="drawerX">×</button><div class="eyebrow">DECISION / EVIDENCE</div><h3 id="drawerTitle"></h3><div class="drawer-meta" id="drawerMeta"></div><div class="drawer-section"><h4>做了什么</h4><p id="drawerWhat"></p></div><div class="drawer-section"><h4>为什么</h4><p id="drawerWhy"></p></div><div class="drawer-section"><h4>证据</h4><ul class="evidence" id="drawerEvidence"></ul></div></aside>
<script>
const DATA=__DATA__;const $=s=>document.querySelector(s);const statusText={open:'进行中',done:'已落地',abandoned:'已放弃'};const typeText={user_message:'用户输入',todo_change:'待办变化',memory_break:'记忆断裂',error:'错误'};
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function renderRepo(){const r=DATA.repository;if(!r){$('#repo').innerHTML='<span>当前工作区没有 Git 仓库</span>';return}const dirty=r.changed_count?`<span class="dirty">${r.changed_count} 个文件有改动</span>`:'<span>工作区干净</span>';const untracked=r.untracked_count?` · ${r.untracked_count} 个未跟踪` : '';$('#repo').innerHTML=`<strong>${esc(r.branch||'detached')}</strong><span class="sep">·</span>${dirty}${esc(untracked)}<span class="sep">·</span><span>HEAD ${esc((r.head_sha||'').slice(0,7))}</span>`}
function renderHeader(){const s=DATA.stats;$('#headline').textContent=DATA.headline||'项目记忆';$('#period').textContent=`${s.first_when} → ${s.last_when} · ${s.sessions} 个会话 · ${s.events.toLocaleString()} 个事件`;const last=(DATA.recent_changes||[]).at(-1);const open=(DATA.tracks||[]).filter(t=>t.state==='open').map(t=>t.name).join('、')||'—';$('#change').innerHTML=last?`<strong>最近：</strong> ${esc(last.when)} ${esc(typeText[last.type]||last.type)} · ${esc(last.summary)}<br><span>当前活跃：${esc(open)}</span>`:`<strong>最近：</strong> 暂无变化`}
function renderTree(){const tracks=DATA.tracks||[];const idx=Object.fromEntries(tracks.map(t=>[t.name,t.index]));const decisions=[...(DATA.decisions||[])].sort((a,b)=>String(a.when).localeCompare(String(b.when)));const breaks=[...(DATA.breaks||[])].sort((a,b)=>(a.at||0)-(b.at||0));const merged=[];for(const d of decisions){for(const b of breaks){if(!b.used&&(b.at||0)<(d.when_ms||0)){merged.push({kind:'break',data:b});b.used=true}}merged.push({kind:'decision',data:d})}for(const b of breaks)if(!b.used)merged.push({kind:'break',data:b});let html='';for(const item of merged){if(item.kind==='break'){const b=item.data;html+=`<div class="cut"><strong>${esc(b.when)}</strong><span>记忆断裂 · 丢失 ${esc(b.loss_pct)}% · ${Number(b.pre_tokens||0).toLocaleString()} → ${Number(b.post_tokens||0).toLocaleString()} token</span></div>`;continue}const d=item.data;const lane=idx[d.track]??0;const st=d.status==='abandoned'?'abandoned':d.status==='done'?'done':'open';const why=d.evidence_count?`↳ ${d.evidence_count} 条证据`:'↳ 暂无证据';html+=`<article class="row" data-id="${esc(d.id)}" style="--lane:${lane}"><div class="rail"><span class="line ${st==='abandoned'?'dead':''}"></span>${lane?'<span class="branch"></span>':''}<span class="node ${st}"></span></div><div class="time">${esc((d.when||'').slice(5,10))}<br>${esc((d.when||'').slice(11,16))}</div><div class="copy"><div class="top"><span class="title">${esc(d.title)}</span><span class="status ${st==='abandoned'?'warn':''}">${esc(statusText[d.status]||d.status)} · ${esc(d.track||'其他')}</span></div><p class="meaning">${esc(d.what)}</p><div class="source">${esc(why)} · 点击展开</div></div></article>`}$('#tree').innerHTML=html||'<div class="change">还没有决策节点。</div>';$('#keys').innerHTML=tracks.map(t=>`<span>${esc(t.name)}</span>`).join('');document.querySelectorAll('.row').forEach(row=>row.addEventListener('click',()=>openDecision(row.dataset.id)))}
function renderTail(){const count=(DATA.unfinished||[]).length;$('#tailText').textContent=`${count} 件事还没收口 · 详细活动和会话记录已收进证据层`;$('#unfinished').innerHTML=(DATA.unfinished||[]).map(x=>`<p>${esc(x.text)}</p>`).join('')||'<p>没有未完成事项。</p>'}
function openDecision(id){const d=(DATA.decisions||[]).find(x=>x.id===id);if(!d)return;$('#drawerTitle').textContent=d.title||d.id;$('#drawerMeta').textContent=`${d.when||'—'} · ${d.track||'其他'} · ${statusText[d.status]||d.status}`;$('#drawerWhat').textContent=d.what||'—';$('#drawerWhy').textContent=d.why||'—';const refs=d.evidence_refs||[];$('#drawerEvidence').innerHTML=refs.length?refs.map(e=>`<li><div class="ev-meta">${esc(e.when)} · ${esc(e.session_title)} · ${Math.round((e.confidence||0)*100)}% match</div><blockquote>“${esc(e.quote)}”</blockquote></li>`).join(''):'<li class="change">暂无匹配证据</li>';$('#drawer').classList.add('open');$('#backdrop').classList.add('open')}
function closeDrawer(){$('#drawer').classList.remove('open');$('#backdrop').classList.remove('open')}
$('#refresh').addEventListener('click',()=>location.reload());$('#drawerX').addEventListener('click',closeDrawer);$('#backdrop').addEventListener('click',closeDrawer);$('#toggleUnfinished').addEventListener('click',()=>{$('#unfinished').classList.toggle('open');$('#toggleUnfinished').textContent=$('#unfinished').classList.contains('open')?'收起未收口事项':'查看未收口事项'});renderHeader();renderRepo();renderTree();renderTail();$('#footer').textContent=`sessionmap · ${new Date(DATA.generated_at).toLocaleString()} · 事实只读，语义节点可回溯`;
</script></body></html>'''


def render(view_path: str, out_path: str) -> None:
    data = json.loads(Path(view_path).read_text(encoding="utf-8"))
    source = data.get("workspace", {}).get("source", "local")
    document = TEMPLATE.replace("__TITLE__", esc(data.get("workspace", {}).get("name", "project")))
    document = document.replace("__SOURCE__", esc(source))
    document = document.replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    Path(out_path).write_text(document, encoding="utf-8")
    print(f"已生成 {out_path} ({len(document)} 字符)")


def main() -> None:
    ap = argparse.ArgumentParser(description="生成极简 sessionmap 工作树")
    ap.add_argument("view")
    ap.add_argument("--out", default="report.html")
    args = ap.parse_args()
    render(args.view, args.out)


if __name__ == "__main__":
    main()
