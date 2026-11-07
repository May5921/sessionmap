#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sessionmap render - 决策地图。

版面照抄 git 客户端的成熟做法(Visual Studio 多分支图 / SourceTree / Git Scope):
  左列 = 纯图形, 一根线一个点, 一个字都不放
  右列 = 表格, 一行一个决策
两列共享行高, 天然对齐, 标签永远不会打架。

用法:
    python render.py timeline.json timeline.md
"""
import argparse
import json
import re

TPL = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
  :root{
    --bg:#FAFAF8; --ink:#151513; --soft:#4A4B45; --mute:#8A8B84;
    --line:#DCDDD7; --cut:#8E3620;
    --sans:"PingFang SC","Microsoft YaHei","Source Han Sans SC",system-ui,sans-serif;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:14px/1.65 var(--sans);-webkit-font-smoothing:antialiased}
  .wrap{max-width:780px;margin:0 auto;padding:72px 28px 110px}

  .path{font-size:11px;color:var(--mute);margin-bottom:22px}
  h1{margin:0 0 6px;font-size:19px;line-height:1.6;font-weight:600;
     letter-spacing:-.005em;max-width:32ch}
  .sub{font-size:11.5px;color:var(--mute)}

  /* ---------- 台账 ---------- */
  .legend{margin-top:44px;padding-bottom:10px;border-bottom:1px solid var(--line);
          font-size:11px;color:var(--mute)}
  .legend b{font-weight:500;color:var(--soft)}

  .row{display:grid;grid-template-columns:112px 54px minmax(0,1fr);
       align-items:stretch;border-bottom:1px solid rgba(220,221,215,.55)}
  .row.d{min-height:52px}
  .row.gap{min-height:30px}
  .row.cut{min-height:38px;border-bottom:none}

  /* 图形列 */
  .g{position:relative}
  .seg{position:absolute;top:0;bottom:0;width:1px;background:var(--line)}
  .seg.t{background:var(--ink)}
  .seg.dead{background:repeating-linear-gradient(180deg,var(--cut) 0 3px,transparent 3px 6px)}
  .fork{position:absolute;top:50%;height:1px;background:var(--line)}
  .dot{position:absolute;top:50%;width:7px;height:7px;border-radius:50%;
       background:var(--bg);border:1.4px solid var(--mute);transform:translate(-50%,-50%);z-index:3}
  .dot.t{background:var(--ink);border-color:var(--ink)}
  .dot.done{border-color:var(--ink);background:var(--ink)}
  .dot.gone{background:var(--cut);border-color:var(--cut)}
  .endx{position:absolute;top:50%;transform:translate(-50%,-50%);
        font-size:9px;color:var(--cut);z-index:3;background:var(--bg);padding:0 1px}

  /* 日期列 */
  .date{padding:14px 0 0;font-size:11px;color:var(--mute);
        font-variant-numeric:tabular-nums;line-height:1.45}

  /* 文字列 */
  .body{padding:11px 0 12px}
  .body .h{font-size:14px;font-weight:600;line-height:1.5}
  .body .tag{font-size:10.5px;color:var(--mute);font-weight:400;margin-left:7px}
  .body .w{font-size:12.5px;color:var(--soft);margin-top:1px;line-height:1.5}
  .row.gone .body .h{color:var(--mute);text-decoration:line-through;
                     text-decoration-thickness:1px;text-decoration-color:var(--cut)}

  /* 断档 */
  .gap .body{padding:8px 0;font-size:10.5px;color:var(--mute)}
  .gap .g{border-left:1px dashed var(--line);margin-left:50%}

  /* 记忆被删除 */
  .cut{border-top:1px solid var(--cut);border-bottom:1px solid var(--cut);
       background:linear-gradient(180deg,rgba(142,54,32,.05),transparent)}
  .cut .body{padding:9px 0;font-size:11px;color:var(--cut)}
  .cut .body b{font-weight:600}

  /* 未完成 */
  .list{margin-top:58px}
  .list h2{margin:0 0 12px;font-size:11.5px;font-weight:500;color:var(--mute);letter-spacing:.04em}
  .list ol{margin:0;padding-left:18px}
  .list li{margin-bottom:6px;font-size:13px;color:var(--soft)}
  .list li::marker{color:var(--mute)}
  footer{margin-top:56px;font-size:10.5px;color:var(--mute)}

  @media (max-width:620px){
    .wrap{padding:44px 16px 80px}
    .row{grid-template-columns:72px 46px minmax(0,1fr)}
    .body .h{font-size:13.5px}
    .body .w{font-size:12px}
  }
</style>
</head>
<body>
<div class="wrap">
  <div class="path" id="path"></div>
  <h1 id="headline"></h1>
  <div class="sub" id="sub"></div>

  <div class="legend" id="legend"></div>
  <div id="ledger"></div>

  <div class="list"><h2>未完成</h2><ol id="unfinished"></ol></div>
  <footer id="foot"></footer>
</div>

<script>
const DATA = __DATA__;
const esc = s => String(s==null?"":s)
  .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");

document.getElementById("path").textContent = DATA.workspace || "";
document.getElementById("headline").textContent = DATA.headline || "";
const st = DATA.stats || {};
document.getElementById("sub").textContent =
  `${st.sessions||"?"} 个会话 · ${st.messages||"?"} 条消息 · ${st.span||""}`;

const P = s => { const d = new Date(String(s).replace(" ","T")); return isNaN(d)?null:d.getTime(); };
const decs = (DATA.decisions||[]).slice().sort((a,b)=>String(a.when).localeCompare(String(b.when)));
const cuts = (DATA.compactions||[]).slice().sort((a,b)=>String(a.when).localeCompare(String(b.when)));

/* 分线 */
const order = [];
decs.forEach(d=>{ const t=d.track||"其他"; if(!order.includes(t)) order.push(t); });
order.sort((a,b)=> a==="主干"?-1 : b==="主干"?1 : 0);
const trackDecs = t => decs.filter(d=>(d.track||"其他")===t);
const isDead = t => trackDecs(t).every(d=>d.status==="abandoned");

document.getElementById("legend").innerHTML =
  `<b>线（左→右）</b> ${order.map(esc).join(" · ")}`;

/* ---------- 排成一行一行 ---------- */
const HR = 36e5;
const seq = [];
const all = [];
decs.forEach(d=>all.push({type:"d", t:P(d.when), d}));
cuts.forEach(c=>all.push({type:"c", t:P(c.when), c}));
all.sort((a,b)=>a.t-b.t);
all.forEach((it,i)=>{
  if (i>0 && it.t - all[i-1].t > 6*HR) seq.push({type:"gap", ms: it.t - all[i-1].t});
  seq.push(it);
});

/* 每条线的首末行 */
const first = {}, last = {};
seq.forEach((r,i)=>{
  if (r.type!=="d") return;
  const t = r.d.track||"其他";
  if (!(t in first)) first[t] = i;
  last[t] = i;
});

const GX = i => 20 + i*20;   // 每条线的 x
const ledger = document.getElementById("ledger");

seq.forEach((r,i)=>{
  const row = document.createElement("div");

  /* 断档 */
  if (r.type === "gap") {
    row.className = "row gap";
    const h = Math.round(r.ms/HR);
    row.innerHTML = `<div class="g"></div><div class="date"></div>
      <div class="body">— ${h>=24 ? Math.round(h/24)+" 天后" : h+" 小时后"} —</div>`;
    ledger.appendChild(row);
    return;
  }

  /* 记忆被删除 */
  if (r.type === "c") {
    row.className = "row cut";
    row.innerHTML = `<div class="g"></div><div class="date"></div>
      <div class="body">记忆在这里被删除 <b>${esc(r.c.pct)}%</b>
        <span style="opacity:.7">· ${esc(r.c.from)} → ${esc(r.c.to)} token</span></div>`;
    ledger.appendChild(row);
    return;
  }

  /* 决策 */
  const d = r.d, t = d.track||"其他", li = order.indexOf(t);
  row.className = "row d" + (d.status==="abandoned" ? " gone" : "");

  let segs = "";
  order.forEach((tk, k)=>{
    if (i < first[tk] || i > last[tk]) return;
    const cls = k===0 ? "seg t" : (isDead(tk) ? "seg dead" : "seg");
    segs += `<i class="${cls}" style="left:${GX(k)}px"></i>`;
  });
  // 分叉: 这条线从主干出来
  if (li > 0 && i === first[t]) {
    segs += `<i class="fork" style="left:${GX(0)}px;width:${GX(li)-GX(0)}px"></i>`;
  }
  // 断头: 走死的线在末端画一个叉
  if (isDead(t) && i === last[t]) {
    segs += `<i class="endx" style="left:${GX(li)}px">✕</i>`;
  }
  const dotCls = ["dot"];
  if (li === 0) dotCls.push("t");
  if (d.status === "done") dotCls.push("done");
  if (d.status === "abandoned") dotCls.push("gone");
  segs += `<i class="${dotCls.join(" ")}" style="left:${GX(li)}px"></i>`;

  const when = String(d.when||"");
  row.innerHTML = `
    <div class="g">${segs}</div>
    <div class="date">${esc(when.slice(5,10))}<br>${esc(when.slice(11))}</div>
    <div class="body">
      <div class="h">${esc(d.title)}<span class="tag">${esc(t)}</span></div>
      <div class="w">${esc(d.what)}</div>
    </div>`;
  ledger.appendChild(row);
});

/* 未完成 */
const ul = document.getElementById("unfinished");
(DATA.unfinished && DATA.unfinished.length ? DATA.unfinished : ["—"]).forEach(v=>{
  const li = document.createElement("li"); li.textContent = v; ul.appendChild(li);
});

document.getElementById("foot").textContent =
  `由 sessionmap 生成 · 规则提取 + 本地 agent CLI 提炼 · 数据不出本机`;
</script>
</body>
</html>
"""


def parse_md(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    ws = ""
    m = re.search(r"^# 工作区: (.+)$", text, re.M)
    if m:
        ws = m.group(1).strip()
    stats = {}
    m = re.search(r"^- 会话: (\d+)", text, re.M)
    if m:
        stats["sessions"] = m.group(1)
    m = re.search(r"^- 消息: ([\d,]+)", text, re.M)
    if m:
        stats["messages"] = m.group(1)
    m = re.search(r"^- 时间: (.+?) ~ (.+)$", text, re.M)
    if m:
        stats["span"] = m.group(1)[5:10] + " → " + m.group(2)[5:10]
    comps = []
    for line in text.splitlines():
        mm = re.match(r"^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) · (.+?) · ([\d,]+) → ([\d,]+) \(−(\d+)%", line)
        if mm:
            comps.append({"when": mm.group(1), "from": mm.group(3),
                          "to": mm.group(4), "pct": mm.group(5)})
    return ws, stats, comps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("decisions")
    ap.add_argument("timeline")
    ap.add_argument("--out", default="map.html")
    a = ap.parse_args()
    with open(a.decisions, encoding="utf-8") as f:
        data = json.load(f)
    ws, stats, comps = parse_md(a.timeline)
    data.update(stats=stats, compactions=comps, workspace=ws)
    html = (TPL.replace("__TITLE__", ws or "sessionmap")
               .replace("__DATA__", json.dumps(data, ensure_ascii=False)))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("已生成 %s (%d 字符)" % (a.out, len(html)))


if __name__ == "__main__":
    main()
