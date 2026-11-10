#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render the project-progress timeline.

The spine is deterministic: work items (session efforts) and milestones (git
releases) are the primary structure.  Semantic decisions, commits and memory
breaks are annotations around it.  Visual style is deliberately restrained --
one accent colour, almost no tags, no "NEW" noise.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


TEMPLATE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><title>__TITLE__ · sessionmap</title><style>
:root{--paper:#f5f5f7;--ink:#1d1d1f;--muted:#86868b;--line:#e5e5ea;--accent:#ff3b30;color-scheme:light}
*{box-sizing:border-box}html{background:var(--paper)}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}
button{font:inherit;cursor:pointer;color:inherit}.mono{font-family:"Cascadia Mono","SFMono-Regular",Consolas,monospace}
.page{max-width:860px;margin:0 auto;padding:54px 30px 80px}
.head{display:flex;justify-content:space-between;align-items:start;gap:20px}
.eyebrow{font-size:10px;letter-spacing:.14em;color:var(--muted);text-transform:uppercase}
h1{max-width:760px;margin:12px 0 6px;font-size:clamp(26px,3.6vw,40px);font-weight:500;line-height:1.1;letter-spacing:-.04em}
.meta{color:var(--muted);font-size:11px}
.head button{border:1px solid var(--line);border-radius:8px;background:rgba(255,255,255,.7);padding:6px 11px;font-size:11px;white-space:nowrap}
.head button:hover{background:#fff}
.status{margin-top:20px;font-size:12px;color:var(--muted);display:flex;gap:14px;flex-wrap:wrap}
.status b{color:var(--ink);font-weight:500}
.status .sep{color:var(--line)}
.delta{margin-top:8px;font-size:11px;color:var(--muted)}
.delta strong{color:var(--ink);font-weight:500}
.line-head{display:flex;justify-content:space-between;align-items:baseline;gap:14px;margin:38px 0 8px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.line-head h2{margin:0;font-size:13px;font-weight:500;letter-spacing:.02em}
.line-head span{font-size:11px;color:var(--muted)}
.line-head .tools{display:flex;gap:12px}
.line-head button{border:0;background:none;padding:0;font-size:11px;color:var(--muted)}
.line-head button:hover{color:var(--ink)}
.timeline{position:relative;margin-top:4px}
.timeline::before{content:"";position:absolute;left:5px;top:6px;bottom:6px;width:1px;background:var(--line)}
.day{font-size:10px;letter-spacing:.1em;color:var(--muted);text-transform:uppercase;margin:20px 0 2px;padding-top:14px;border-top:1px solid var(--line)}
.day:first-child{border-top:0;padding-top:4px;margin-top:0}
.node{position:relative;padding:7px 0 7px 22px;cursor:pointer;border-radius:6px}
.node:hover{background:rgba(255,255,255,.6)}
.node:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.node .mark{position:absolute;left:0;top:13px;width:11px;height:11px;border-radius:50%;background:var(--paper);border:1.5px solid var(--ink)}
.node.s-done .mark{background:var(--ink)}
.node.milestone .mark{background:var(--accent);border-color:var(--accent);border-radius:2px;transform:rotate(45deg);width:9px;height:9px;top:14px}
.node .title{font-size:14px;font-weight:500;line-height:1.45}
.node .row{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.node .meta{font-size:11px;color:var(--muted)}
.node.milestone .title{font-weight:600}
.node.fact{padding-left:34px;cursor:pointer}
.node.fact:hover{background:none}
.node.fact .mark{display:none}
.node.fact .title{font-size:12px;font-weight:400;color:var(--muted);line-height:1.5}
.node.fact.commit .title{font-family:"Cascadia Mono","SFMono-Regular",Consolas,monospace;font-size:11px}
.node.fact.decision .title::before{content:"· ";color:var(--muted)}
.node.fact.small .title{font-size:11px}
.node.fact .why{display:none;margin-top:2px;font-size:11px;color:var(--muted)}
.timeline.detail .node.fact .why{display:block}
.timeline.detail .node .what{display:block}
.node .what{display:none;margin-top:3px;font-size:12px;color:var(--muted);line-height:1.55}
.cut{position:relative;margin:8px 0 8px 22px;padding:9px 12px;border-left:2px solid var(--accent);background:rgba(255,59,48,.045);font-size:12px;color:var(--muted)}
.cut strong{color:var(--accent);font-weight:500}
.caption{margin-top:14px;font-size:11px;color:var(--muted);display:flex;justify-content:space-between;gap:14px}
.tail{display:flex;justify-content:space-between;align-items:baseline;gap:16px;margin-top:22px;font-size:11px;color:var(--muted)}
.tail strong{color:var(--ink);font-weight:500}
.tail button{border:0;background:none;padding:0;font-size:11px;color:var(--ink);border-bottom:1px solid var(--line)}
.details{display:none;margin-top:10px;padding-top:10px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}
.details.open{display:block}
.details p{margin:5px 0}.details p:before{content:"· ";color:var(--accent)}
footer{margin-top:44px;font-size:10px;color:var(--muted)}
.backdrop{position:fixed;inset:0;z-index:5;display:none;background:rgba(32,33,29,.14)}.backdrop.open{display:block}
.drawer{position:fixed;z-index:6;top:0;right:0;bottom:0;width:min(440px,94vw);padding:28px 28px 40px;overflow:auto;background:rgba(245,245,247,.96);-webkit-backdrop-filter:blur(20px);backdrop-filter:blur(20px);box-shadow:-18px 0 50px rgba(0,0,0,.12);transform:translateX(104%);transition:transform .22s cubic-bezier(.22,.61,.36,1)}
.drawer.open{transform:translateX(0)}
.drawer-close{float:right;border:0;background:none;color:var(--muted);font-size:20px;line-height:1}
.drawer h2{margin:32px 0 4px;font-size:23px;line-height:1.25;font-weight:500;letter-spacing:-.03em}
.drawer-meta{color:var(--muted);font-size:11px}
.drawer-section{margin-top:22px;padding-top:10px;border-top:1px solid var(--line)}
.drawer-section h3{margin:0 0 6px;color:var(--muted);font-size:10px;letter-spacing:.1em;text-transform:uppercase;font-weight:500}
.drawer-section p{margin:0;font-size:13px;line-height:1.7}
.drawer-section ul{margin:0;padding:0;list-style:none}
.drawer-section li{padding:8px 0;border-bottom:1px solid var(--line);font-size:12px;line-height:1.6}
.drawer-section li:last-child{border-bottom:0}
.ev-meta{color:var(--muted);font-size:10px;font-family:"Cascadia Mono",monospace}
.ev-fact{margin-top:2px;color:var(--muted);font-size:11px;overflow-wrap:anywhere}
.ev-ref{margin-top:2px;color:var(--muted);font-size:10px;font-family:"Cascadia Mono",monospace;overflow-wrap:anywhere}
.evidence blockquote{margin:4px 0 0;font-size:12px;line-height:1.6;color:var(--ink)}
.evidence a{color:var(--muted);font-size:11px}
.tag{display:inline-block;padding:1px 6px;border:1px solid var(--line);border-radius:5px;font-size:10px;color:var(--muted);margin-right:4px}
.tag.open{color:var(--ink);border-color:var(--ink)}
.tag.done{color:var(--muted)}
.tag.bugfix{color:var(--accent);border-color:var(--accent)}
@media(prefers-reduced-motion:reduce){.drawer{transition:none}}
@media(max-width:640px){.page{padding:36px 16px 64px}.head{display:block}.head button{margin-top:14px}.node.fact{padding-left:26px}.caption{display:block}.caption span{display:block;margin-top:4px}}
::selection{background:rgba(255,59,48,.16)}::-webkit-scrollbar{width:10px}::-webkit-scrollbar-thumb{background:#c7c7cc;border:3px solid var(--paper);border-radius:10px}
</style></head><body><main class="page"><header class="head"><div><div class="eyebrow">PROJECT MEMORY / __SOURCE__</div><h1 id="headline"></h1><div class="meta" id="period"></div></div><button id="refresh">刷新</button></header><div class="status" id="status"></div><div class="delta" id="delta"></div><div class="line-head"><h2>事件线</h2><span>工作项与里程碑 · 点击看证据</span><div class="tools"><button id="density">展开细节</button><button id="smallToggle">补充事件</button></div></div><div class="timeline" id="timeline"></div><div class="caption"><span>节点是项目推进的骨架，不是每一条消息。</span><span id="breakCaption"></span></div><div class="tail"><span id="tail"></span><button id="toggle">查看未收口事项</button></div><div class="details" id="details"></div><footer id="footer"></footer></main><div class="backdrop" id="backdrop"></div><aside class="drawer" id="drawer"><button class="drawer-close" id="drawerX">×</button><div class="eyebrow" id="drawerEyebrow">PROJECT / EVIDENCE</div><h2 id="drawerTitle"></h2><div class="drawer-meta" id="drawerMeta"></div><div id="drawerBody"></div></aside><script>
const DATA=__DATA__;const $=s=>document.querySelector(s);
const statusText={open:'进行中',done:'已完成',abandoned:'已放弃',fact:'事实'};
const kindText={feature:'功能',bugfix:'修复',investigation:'调研',refactor:'重构',release:'发布',milestone:'里程碑',commit:'提交'};
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function byTime(a,b){return (a.when_ms||0)-(b.when_ms||0)}
function dayOf(ms){if(!ms)return '';const d=new Date(ms);return String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')}
function whenShort(ms){if(!ms)return '';const d=new Date(ms);return String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')}
function progress(){return DATA.progress||{work_items:[],milestones:[],commits:[],phases:[]}}
function workItems(){return progress().work_items||[]}
function milestones(){return progress().milestones||[]}
function commits(){return progress().commits||[]}
function allDecisions(){return [...(DATA.decisions||[])]}
function allSmall(){return (DATA.small_events||[]).filter(x=>!['git_commit','memory_break'].includes(x.event_type)&&String(x.title||'').trim().length>1)}
function findItem(id){return [...workItems(),...milestones(),...commits(),...allDecisions(),...allSmall()].find(x=>x.id===id)}

function spineRow(item,type){
  const isWork=type==='work';
  const status=item.status||'done';
  const kind=isWork?(kindText[item.kind]||'功能'):'里程碑';
  const bits=[kind,statusText[status]||status];
  if(isWork){if(item.session_count)bits.push(item.session_count+' 会话');if(item.file_count)bits.push(item.file_count+' 文件');}
  else if(item.what)bits.push(item.what);
  return `<article class="node ${isWork?'work':'milestone'} s-${esc(status)}" data-id="${esc(item.id)}" data-type="${esc(type)}" tabindex="0" aria-label="查看：${esc(item.title)}"><span class="mark"></span><div class="row"><span class="title">${esc(item.title)}</span><span class="meta">${esc(bits.join(' · '))}</span></div>${isWork&&item.files&&item.files.length?`<div class="what">${esc(item.files.slice(0,3).join(' · '))}</div>`:''}</article>`;
}
function factRow(item,kind){
  const cls=kind==='commit'?'commit':kind==='decision'?'decision':'small';
  const title=kind==='decision'?(item.title||''):(item.title||item.summary||'');
  const why=kind==='decision'?(item.why||''):(item.why||'');
  return `<article class="node fact ${cls}" data-id="${esc(item.id)}" data-type="${esc(kind)}" tabindex="0" aria-label="查看：${esc(title)}"><div class="title">${esc(title)}</div>${why?`<div class="why">${esc(why)}</div>`:''}</article>`;
}
function cutRow(b){return `<div class="cut"><strong>记忆断裂</strong> · 丢失 ${esc(b.loss_pct)}% · ${Number(b.pre_tokens||0).toLocaleString()} → ${Number(b.post_tokens||0).toLocaleString()} token</div>`}
function dayRow(day){return `<div class="day">${esc(day)}</div>`}

function buildRows(){
  const spine=[...workItems().map(x=>({...x,_type:'work',_order:0})),...milestones().map(x=>({...x,_type:'milestone',_order:0}))];
  const facts=[...commits().map(x=>({...x,_kind:'commit',_order:1})),...allDecisions().map(x=>({...x,_kind:'decision',_order:1})),...(smallOn?allSmall():[]).map(x=>({...x,_kind:'small',_order:1}))];
  const breaks=(DATA.breaks||[]).map(x=>({...x,_kind:'break',when_ms:x.at||x.when_ms,title:'记忆断裂',_order:0}));
  const all=[...spine,...facts,...breaks].sort((a,b)=>(a.when_ms||0)-(b.when_ms||0)||(a._order??3)-(b._order??3));
  const rows=[];let lastDay='';
  for(const item of all){
    const day=dayOf(item.when_ms);if(day&&day!==lastDay){rows.push(dayRow(day));lastDay=day}
    if(item._type){rows.push(spineRow(item,item._type));continue}
    if(item._kind==='break'){rows.push(cutRow(item));continue}
    rows.push(factRow(item,item._kind));
  }
  return rows.join('');
}

function render(){
  const s=DATA.stats||{};const p=progress();
  $('#headline').textContent=DATA.headline||'项目记忆';
  $('#period').textContent=`${(DATA.workspace&&DATA.workspace.name)||'工作区'} · ${s.first_when||''} → ${s.last_when||''} · ${s.sessions||0} 个会话`;
  const st=p.stats||{};
  const openWi=(p.work_items||[]).filter(x=>x.status==='open').length;
  const doneWi=(p.work_items||[]).filter(x=>x.status==='done').length;
  $('#status').innerHTML=`<span><b>${openWi}</b> 进行中</span><span class="sep">·</span><span><b>${doneWi}</b> 已完成</span><span class="sep">·</span><span><b>${(p.milestones||[]).length}</b> 里程碑</span><span class="sep">·</span><span><b>${(p.commits||[]).length}</b> 提交</span><span class="sep">·</span><span><b>${(DATA.unfinished||[]).length}</b> 件事未收口</span>`;
  const viewKey=`sessionmap:last-view:${(DATA.workspace&&DATA.workspace.id)||'workspace'}`;
  const previous=Number(localStorage.getItem(viewKey)||0);
  const changes=DATA.changes||[];
  const freshChanges=changes.filter(c=>(c.at||0)>previous);
  const countKind=kind=>freshChanges.filter(c=>c.kind===kind).length;
  const parts=[];
  if(countKind('decision'))parts.push(`${countKind('decision')} 个决策`);
  if(countKind('todo'))parts.push(`${countKind('todo')} 个待办变化`);
  if(countKind('git_commit'))parts.push(`${countKind('git_commit')} 个提交`);
  if(countKind('memory_break'))parts.push(`${countKind('memory_break')} 次记忆断裂`);
  $('#delta').innerHTML=previous?`<strong>自上次查看：</strong>${freshChanges.length?parts.join('、')||`${freshChanges.length} 项事实变化`:'没有新的项目变化'}`:'<strong>首次查看：</strong>已建立项目记忆基线';
  $('#timeline').innerHTML=buildRows();
  $('#breakCaption').textContent=(DATA.breaks||[]).length?`${(DATA.breaks||[]).length} 次记忆断裂`:'';
  const open=[...(p.work_items||[])].reverse().find(x=>x.status==='open');
  $('#tail').innerHTML=`<strong>当前枝头：</strong>${esc(open?open.title:'—')} · ${(DATA.unfinished||[]).length} 件事未收口`;
  $('#details').innerHTML=(DATA.unfinished||[]).map(x=>`<p>${esc(x.text)}</p>`).join('')||'<p>没有未完成事项。</p>';
  $('#footer').textContent=`sessionmap · 事实与证据可回溯 · ${new Date(DATA.generated_at).toLocaleString()}`;
  document.querySelectorAll('.node').forEach(n=>n.addEventListener('click',()=>openItem(n.dataset.id)));
  document.querySelectorAll('.node').forEach(n=>n.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openItem(n.dataset.id)}}));
  $('#smallToggle').textContent=smallOn?'隐藏补充':'显示补充';
  setTimeout(()=>localStorage.setItem(viewKey,String(s.last_at||Date.now())),2500);
}
let smallOn=false;

function openItem(id){
  const item=findItem(id);if(!item)return;
  const p=progress();
  const isWork=id.startsWith('wi:'),isMilestone=id.startsWith('milestone:'),isCommit=id.startsWith('commit:');
  $('#drawerEyebrow').textContent=isWork?'WORK ITEM':isMilestone?'MILESTONE':isCommit?'COMMIT':'EVENT / EVIDENCE';
  $('#drawerTitle').textContent=item.title||item.id;
  const status=item.status||'fact';
  const meta=[whenShort(item.when_ms||item.at)];
  if(isWork){meta.push(kindText[item.kind]||'功能',statusText[status]||status);if(item.confidence!=null)meta.push('信心 '+item.confidence+' '+item.confidence_band);if(item.span_hours)meta.push(item.span_hours+' 小时');}
  else if(isMilestone){meta.push('里程碑',item.what||'');}
  else if(isCommit){meta.push('提交',String(item.sha||'').slice(0,8));}
  else{meta.push(item.track||'其他',statusText[status]||status);}
  $('#drawerMeta').textContent=meta.join(' · ');
  let body='';
  if(isWork){
    const decisions=allDecisions().filter(d=>d.work_item_id===item.id);
    const why=decisions.map(d=>d.why).filter(Boolean).join('；');
    const what=decisions.map(d=>d.what).filter(Boolean).join('；')||item.title;
    body+=`<div class="drawer-section"><h3>发生了什么</h3><p>${esc(what||'—')}</p></div>`;
    if(why)body+=`<div class="drawer-section"><h3>为什么</h3><p>${esc(why)}</p></div>`;
    if(item.files&&item.files.length)body+=`<div class="drawer-section"><h3>文件</h3><p>${esc(item.files.slice(0,10).join(' · '))}</p></div>`;
    if(item.open_todos&&item.open_todos.length)body+=`<div class="drawer-section"><h3>未收口</h3><ul>${item.open_todos.slice(0,8).map(t=>`<li>${esc(t.text)} <span class="ev-meta">${esc(t.status||'')}</span></li>`).join('')}</ul></div>`;
    if(item.commits&&item.commits.length)body+=`<div class="drawer-section"><h3>提交</h3><ul>${item.commits.map(c=>`<li>${esc(c.title)} <span class="ev-meta">${esc(c.when||'')}</span></li>`).join('')}</ul></div>`;
    if(decisions.length)body+=`<div class="drawer-section"><h3>决策注解</h3><ul>${decisions.map(d=>`<li>${esc(d.title)} <span class="ev-meta">${esc(statusText[d.status]||d.status)}</span></li>`).join('')}</ul></div>`;
  } else if(isMilestone||isCommit){
    body+=`<div class="drawer-section"><h3>发生了什么</h3><p>${esc(item.what||item.title||'—')}</p></div>`;
  } else {
    body+=`<div class="drawer-section"><h3>发生了什么</h3><p>${esc(item.what||item.summary||'—')}</p></div>`;
    if(item.why)body+=`<div class="drawer-section"><h3>为什么</h3><p>${esc(item.why)}</p></div>`;
  }
  const refs=item.evidence_refs||[];
  if(refs.length){
    body+=`<div class="drawer-section"><h3>证据</h3><ul class="evidence">${refs.map(e=>{
      const fact=e.fact||{};const factText=[e.source_kind,e.event_type,fact.tool||fact.command||fact.path||fact.file_path||fact.sha].filter(Boolean).join(' · ');
      const rawRef=e.raw_ref||'';
      return `<li><div class="ev-meta">${esc(e.when||'')} · ${esc(e.session_title||'')}</div>${factText?`<div class="ev-fact">${esc(factText)}</div>`:''}${rawRef?`<div class="ev-ref">来源：${esc(rawRef)}</div>`:''}<blockquote>“${esc(e.quote||'')}”</blockquote></li>`;
    }).join('')}</ul></div>`;
  }
  const repo=DATA.repository||{};const rc=repo.commits||[];
  const commitText=rc.slice(0,3).map(c=>`${String(c.sha||'').slice(0,8)} ${c.subject||''}`).join(' · ');
  body+=`<div class="drawer-section"><h3>Git 事实</h3><p>${repo.branch?`${esc(repo.branch)} · ${String(repo.head_sha||'').slice(0,8)} · ${repo.changed_count||0} 个改动文件${commitText?' · 最近提交：'+esc(commitText):''}`:'暂无 Git 快照'}</p></div>`;
  $('#drawerBody').innerHTML=body;
  $('#drawer').classList.add('open');$('#backdrop').classList.add('open');
}
function closeDrawer(){$('#drawer').classList.remove('open');$('#backdrop').classList.remove('open')}
$('#refresh').addEventListener('click',()=>location.reload());
$('#drawerX').addEventListener('click',closeDrawer);
$('#backdrop').addEventListener('click',closeDrawer);
$('#toggle').addEventListener('click',()=>{$('#details').classList.toggle('open');$('#toggle').textContent=$('#details').classList.contains('open')?'收起未收口事项':'查看未收口事项'});
$('#density').addEventListener('click',()=>{const t=$('#timeline');t.classList.toggle('detail');$('#density').textContent=t.classList.contains('detail')?'收起细节':'展开细节';});
$('#smallToggle').addEventListener('click',()=>{smallOn=!smallOn;render()});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDrawer()});
render();
</script></body></html>'''


EVIDENCE_TEMPLATE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>原始证据 · sessionmap</title><style>body{max-width:820px;margin:0 auto;padding:40px 22px;background:#f5f5f7;color:#1d1d1f;font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:#6e6e73}h1{font-size:32px;font-weight:500;letter-spacing:-.04em}h2{margin-top:32px;font-size:17px;font-weight:500;border-top:1px solid #a1a1a6;padding-top:12px}.meta,pre{color:#6e6e73;font:12px/1.6 "Cascadia Mono",Consolas,monospace}blockquote{margin:10px 0;padding-left:14px;border-left:2px solid #ff3b30}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#fff;padding:14px;border:1px solid #d2d2d7;border-radius:8px}</style></head><body><a href="javascript:history.back()">← 返回事件线</a><main id="app"></main><script>const DATA=__DATA__;const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const id=decodeURIComponent(location.hash.replace(/^#event=/,''));const all=[...(DATA.progress?.work_items||[]),...(DATA.progress?.milestones||[]),...(DATA.progress?.commits||[]),...(DATA.decisions||[]),...(DATA.small_events||[])];let item=all.find(x=>x.id===id);let ref=null;if(!item)for(const candidate of all){const found=(candidate.evidence_refs||[]).find(x=>x.event_id===id);if(found){item=candidate;ref=found;break}}if(item&&!ref)ref=(item.evidence_refs||[])[0];const app=document.querySelector('#app');if(!item){app.innerHTML='<h1>找不到证据</h1><p>这个事件可能来自另一份本地视图。</p>'}else{const payload=ref?.payload||ref?.fact||{};const what=item.what||item.summary||item.title||'';app.innerHTML=`<h1>${esc(item.title||item.id)}</h1><div class="meta">${esc(item.when||'—')} · ${esc(item.track||item.kind||'')}</div><h2>发生了什么</h2><p>${esc(what)}</p>${item.why?`<h2>为什么</h2><p>${esc(item.why)}</p>`:''}<h2>原始引用</h2><div class="meta">${esc(ref?.source_kind||'')} · ${esc(ref?.event_type||'')} · ${esc(ref?.session_title||'')} · ${esc(ref?.raw_ref||'')}</div><blockquote>“${esc(ref?.quote||'—')}”</blockquote><h2>原始事实载荷</h2><pre>${esc(JSON.stringify(payload,null,2))}</pre>`}</script></body></html>'''


def render(view_path: str, out_path: str) -> None:
    data = json.loads(Path(view_path).read_text(encoding="utf-8"))
    document = TEMPLATE.replace("__TITLE__", esc(data.get("workspace", {}).get("name", "project")))
    document = document.replace("__SOURCE__", esc(data.get("workspace", {}).get("source", "local")))
    document = document.replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    Path(out_path).write_text(document, encoding="utf-8")
    evidence = EVIDENCE_TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    Path(out_path).with_name("evidence.html").write_text(evidence, encoding="utf-8")
    print(f"已生成 {out_path} ({len(document)} 字符)")


def main() -> None:
    ap = argparse.ArgumentParser(description="生成项目推进事件线 sessionmap 页面")
    ap.add_argument("view")
    ap.add_argument("--out", default="report.html")
    args = ap.parse_args()
    render(args.view, args.out)


if __name__ == "__main__":
    main()
