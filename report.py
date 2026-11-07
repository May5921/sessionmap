#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sessionmap report - 一页看完一个工作区。

四层, 从"现在"读到"过程":
  1 现状     先回答"我做到哪了"
  2 活跃节律 每小时的活动密度, 空白也是信息
  3 会话流   每条会话一条带, 看出并行与爆发
  4 决策台账 左边纯图形, 右边纯文字

用法:
    python report.py "pi-mini" timeline.json --out report.html
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

DB = os.path.expandvars(r"%USERPROFILE%\.zcode\cli\db\db.sqlite")


def ts(v):
    return dt.datetime.fromtimestamp(v / 1000.0)


# ---------------------------------------------------------------- 取数
def load(con, workspace):
    ws = "%" + workspace + "%"
    sessions = con.execute("""
        select id, title, parent_id, time_created, time_updated
        from session where directory like ? and time_deleted is null
        order by time_created""", (ws,)).fetchall()

    events = con.execute("""
        select p.session_id, p.time_created, json_extract(p.data,'$.type')
        from part p join session s on s.id = p.session_id
        where s.directory like ?
          and json_extract(p.data,'$.type') in ('tool','text')
          and json_extract(p.data,'$.type') != 'compaction'""", (ws,)).fetchall()

    comps = []
    for d, t in con.execute("""
            select p.data, p.time_created from part p
            join session s on s.id = p.session_id
            where s.directory like ? and json_extract(p.data,'$.type')='compaction'""", (ws,)):
        try:
            o = json.loads(d)
        except Exception:
            continue
        pre, post = o.get("preCompactTokenCount") or 0, o.get("truePostCompactTokenCount") or 0
        if pre and post:
            comps.append((t, pre, post, round(100 * (1 - post / pre))))
    # 去重(同一次压缩在多个会话各记一条)
    seen, uniq = set(), []
    for c in sorted(comps):
        k = (c[0], c[1], c[2])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)
    return sessions, events, uniq


# ---------------------------------------------------------------- 计算
def analyse(sessions, events, decisions):
    if not events:
        sys.exit("该工作区没有事件")
    t0, t1 = min(e[1] for e in events), max(e[1] for e in events)
    span_h = (t1 - t0) / 3600e3

    # 每小时活动量
    hours = int(span_h) + 1
    buckets = [0] * hours
    for _, t, _ in events:
        buckets[min(hours - 1, int((t - t0) / 3600e3))] += 1
    active_hours = sum(1 for b in buckets if b)
    peak = max(buckets) if buckets else 0

    # 会话 → 主题线(取该会话用户消息最近的决策)
    ds = sorted(decisions, key=lambda d: d.get("when", ""))
    dtimes = []
    for d in ds:
        try:
            dtimes.append((dt.datetime.strptime(d["when"], "%Y-%m-%d %H:%M").timestamp() * 1000,
                           d.get("track") or "其他"))
        except Exception:
            pass

    def track_of(t):
        if not dtimes:
            return "其他"
        return min(dtimes, key=lambda x: abs(x[0] - t))[1]

    # 每条会话的时间跨度与消息数
    per = {}
    for sid, t, kind in events:
        s = per.setdefault(sid, {"a": t, "b": t, "n": 0})
        s["a"] = min(s["a"], t)
        s["b"] = max(s["b"], t)
        s["n"] += 1

    bands = []
    for sid, title, parent, tc, tu in sessions:
        p = per.get(sid)
        if not p:
            continue
        bands.append({"id": sid, "title": title or "", "parent": parent,
                      "a": p["a"], "b": p["b"], "n": p["n"],
                      "track": track_of(p["a"])})

    # 主题线分组统计
    by_track = {}
    for d in ds:
        t = d.get("track") or "其他"
        g = by_track.setdefault(t, {"done": 0, "open": 0, "abandoned": 0})
        g[d.get("status", "open")] = g.get(d.get("status", "open"), 0) + 1

    return {"t0": t0, "t1": t1, "span_h": span_h, "buckets": buckets,
            "active_hours": active_hours, "peak": peak,
            "bands": bands, "by_track": by_track, "events": len(events)}


# ---------------------------------------------------------------- 渲染
def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render(workspace, data, decisions, comps, unfinished, headline):
    t0, t1 = data["t0"], data["t1"]
    span = max(1, t1 - t0)
    pct = lambda t: 100.0 * (t - t0) / span

    # --- 节律条
    cells = "".join(
        '<i style="opacity:%.2f"></i>' % (0 if not c else 0.06 + 0.94 * (c / data["peak"]) ** 0.55)
        for c in data["buckets"])
    axis = "".join(
        '<b style="left:%.2f%%">%s</b>' % (100.0 * h * 3600e3 / span if span else 0, "")
        for h in range(0))

    # 日期刻度
    ticks = []
    cur = None
    for h in range(len(data["buckets"])):
        d = ts(t0 + h * 3600e3).strftime("%m-%d")
        if d != cur:
            cur = d
            ticks.append('<b style="left:%.2f%%">%s</b>' % (100.0 * h / len(data["buckets"]), d))

    cuts_html = "".join(
        '<i class="cut" style="left:%.2f%%" title="%s · %s → %s"></i>'
        % (pct(t), ts(t).strftime("%m-%d %H:%M"), format(pre, ","), format(post, ","))
        for t, pre, post, _ in comps)

    # --- 会话带: 主会话画成长条, 子代理只画一个刻度
    bands = sorted(data["bands"], key=lambda b: b["a"])
    mains = [b for b in bands if not b["parent"]]
    subs = [b for b in bands if b["parent"]]
    band_rows = ""
    for b in mains:
        a, z = pct(b["a"]), pct(b["b"])
        w = max(z - a, 0.4)
        band_rows += ('<div class="band" title="%s · %d 条消息">'
                      '<i style="left:%.2f%%;width:%.2f%%"></i>'
                      '<em style="left:%.2f%%">%s</em></div>'
                      % (esc(b["title"][:44]), b["n"], a, w, a,
                         esc(ts(b["a"]).strftime("%m-%d %H:%M"))))
    if subs:
        ticks_s = "".join('<i class="tick" style="left:%.2f%%"></i>' % pct(b["a"]) for b in subs)
        band_rows += ('<div class="band subs" title="%d 个子代理">%s</div>'
                      % (len(subs), ticks_s))

    # --- 现状
    groups = {"在跑": [], "已落地": [], "走死": []}
    for t, g in data["by_track"].items():
        if g["abandoned"] == sum(g.values()):
            groups["走死"].append(t)
        elif g["open"]:
            groups["在跑"].append("%s（%d）" % (t, g["open"]))
        else:
            groups["已落地"].append(t)

    def lst(k):
        v = groups[k]
        return "、".join(v) if v else "—"

    blocked = unfinished[0] if unfinished else "—"

    # --- 台账
    seq = []
    allc = []
    for d in decisions:
        try:
            t = dt.datetime.strptime(d["when"], "%Y-%m-%d %H:%M").timestamp() * 1000
        except Exception:
            continue
        allc.append({"type": "d", "t": t, "d": d})
    for t, pre, post, p in comps:
        allc.append({"type": "c", "t": t, "pre": pre, "post": post, "p": p})
    allc.sort(key=lambda x: x["t"])

    order = []
    for d in decisions:
        t = d.get("track") or "其他"
        if t not in order:
            order.append(t)
    order.sort(key=lambda x: (x != "主干", x))
    lane = {t: i for i, t in enumerate(order)}
    is_dead = {t: all(x.get("status") == "abandoned"
                      for x in decisions if (x.get("track") or "其他") == t) for t in order}

    first, last = {}, {}
    for i, r in enumerate(allc):
        if r["type"] != "d":
            continue
        t = r["d"].get("track") or "其他"
        first.setdefault(t, i)
        last[t] = i

    ledger = ""
    for i, r in enumerate(allc):
        if r["type"] == "c":
            ledger += ('<div class="row cut"><div class="g"></div><div class="date"></div>'
                       '<div class="body">记忆在这里被删除 <b>%d%%</b>'
                       '<span style="opacity:.65"> · %s → %s token</span></div></div>'
                       % (r["p"], format(r["pre"], ","), format(r["post"], ",")))
            continue
        d = r["d"]
        tr = d.get("track") or "其他"
        li = lane[tr]
        gx = 18 + li * 18
        segs = ""
        for k, tk in enumerate(order):
            if i < first[tk] or i > last[tk]:
                continue
            cls = "seg t" if k == 0 else ("seg dead" if is_dead[tk] else "seg")
            segs += '<i class="%s" style="left:%dpx"></i>' % (cls, 18 + k * 18)
        if li > 0 and i == first[tr]:
            segs += '<i class="fork" style="left:18px;width:%dpx"></i>' % (gx - 18)
        if is_dead[tr] and i == last[tr]:
            segs += '<i class="endx" style="left:%dpx">✕</i>' % gx
        dc = "dot" + (" t" if li == 0 else "") + \
             (" done" if d.get("status") == "done" else "") + \
             (" gone" if d.get("status") == "abandoned" else "")
        segs += '<i class="%s" style="left:%dpx"></i>' % (dc, gx)
        when = d.get("when", "")
        ledger += ('<div class="row d%s"><div class="g">%s</div>'
                   '<div class="date">%s<br>%s</div>'
                   '<div class="body"><div class="h">%s<span class="tag">%s</span></div>'
                   '<div class="w">%s</div></div></div>'
                   % (" gone" if d.get("status") == "abandoned" else "", segs,
                      esc(when[5:10]), esc(when[11:]), esc(d.get("title", "")),
                      esc(tr), esc(d.get("what", ""))))

    fin = "".join("<li>%s</li>" % esc(u) for u in (unfinished or ["—"]))

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(workspace)}</title>
<style>
:root{{--bg:#FAFAF8;--ink:#151513;--soft:#4A4B45;--mute:#8A8B84;--line:#DCDDD7;--cut:#8E3620}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
 font:14px/1.65 "PingFang SC","Microsoft YaHei","Source Han Sans SC",system-ui,sans-serif;
 -webkit-font-smoothing:antialiased}}
.wrap{{max-width:780px;margin:0 auto;padding:64px 28px 100px}}
.path{{font-size:11px;color:var(--mute);margin-bottom:20px}}
h1{{margin:0;font-size:19px;line-height:1.6;font-weight:600;letter-spacing:-.005em;max-width:32ch}}
.sub{{font-size:11.5px;color:var(--mute);margin-top:6px}}
h2{{margin:44px 0 10px;font-size:11px;font-weight:500;color:var(--mute);letter-spacing:.06em}}

/* 现状 */
.now{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;background:var(--line);
 border:1px solid var(--line)}}
.now div{{background:var(--bg);padding:13px 14px}}
.now .k{{font-size:10.5px;color:var(--mute);margin-bottom:5px}}
.now .v{{font-size:13px;line-height:1.55}}
.now .v em{{font-style:normal;color:var(--cut)}}
.blocked{{margin-top:1px;background:var(--bg);border:1px solid var(--line);
 border-top:none;padding:13px 14px}}
.blocked .k{{font-size:10.5px;color:var(--mute);margin-bottom:5px}}
.blocked .v{{font-size:13px}}

/* 节律 */
.rhythm{{position:relative;display:flex;height:44px;gap:1px;align-items:stretch}}
.rhythm i{{flex:1;background:var(--ink)}}
.rhythm .cut{{position:absolute;top:-6px;bottom:-6px;width:1px;background:var(--cut);
 z-index:2;flex:none}}
.ticks{{position:relative;height:14px;margin-top:4px}}
.ticks b{{position:absolute;top:0;font-size:10px;color:var(--mute);font-weight:400;
 transform:translateX(-50%)}}
.rnote{{font-size:11px;color:var(--mute);margin-top:8px}}

/* 会话带 */
.bands{{position:relative;border-top:1px solid var(--line);padding-top:8px}}
.band{{position:relative;height:30px}}
.band i{{position:absolute;top:16px;height:4px;background:var(--ink);opacity:.7;border-radius:2px}}
.band em{{position:absolute;top:0;font-style:normal;font-size:10px;color:var(--mute);
 transform:translateX(2px);white-space:nowrap}}
.band.subs{{height:20px}}
.band.subs i.tick{{position:absolute;top:7px;width:1px;height:6px;background:var(--cut);
 opacity:.45;border-radius:0}}

/* 台账 */
.legend{{margin-top:14px;padding-bottom:8px;border-bottom:1px solid var(--line);
 font-size:11px;color:var(--mute)}}
.legend b{{font-weight:500;color:var(--soft)}}
.row{{display:grid;grid-template-columns:112px 52px minmax(0,1fr);align-items:stretch;
 border-bottom:1px solid rgba(220,221,215,.55)}}
.row.d{{min-height:52px}}
.row.cut{{min-height:36px;border-top:1px solid var(--cut);border-bottom:1px solid var(--cut);
 background:linear-gradient(180deg,rgba(142,54,32,.05),transparent)}}
.g{{position:relative}}
.seg{{position:absolute;top:0;bottom:0;width:1px;background:var(--line)}}
.seg.t{{background:var(--ink)}}
.seg.dead{{background:repeating-linear-gradient(180deg,var(--cut) 0 3px,transparent 3px 6px)}}
.fork{{position:absolute;top:50%;height:1px;background:var(--line)}}
.dot{{position:absolute;top:50%;width:7px;height:7px;border-radius:50%;background:var(--bg);
 border:1.4px solid var(--mute);transform:translate(-50%,-50%);z-index:3}}
.dot.t{{background:var(--ink);border-color:var(--ink)}}
.dot.done{{border-color:var(--ink);background:var(--ink)}}
.dot.gone{{background:var(--cut);border-color:var(--cut)}}
.endx{{position:absolute;top:50%;transform:translate(-50%,-50%);font-size:9px;color:var(--cut);
 background:var(--bg);padding:0 1px;z-index:3}}
.date{{padding:14px 0 0;font-size:11px;color:var(--mute);line-height:1.45;
 font-variant-numeric:tabular-nums}}
.body{{padding:11px 0 12px}}
.body .h{{font-size:14px;font-weight:600;line-height:1.5}}
.body .tag{{font-size:10.5px;color:var(--mute);font-weight:400;margin-left:7px}}
.body .w{{font-size:12.5px;color:var(--soft);line-height:1.5;margin-top:1px}}
.row.gone .body .h{{color:var(--mute);text-decoration:line-through;
 text-decoration-thickness:1px;text-decoration-color:var(--cut)}}
.cut .body{{padding:8px 0;font-size:11px;color:var(--cut)}}

.list{{margin-top:44px}}
.list ol{{margin:0;padding-left:18px}}
.list li{{margin-bottom:6px;font-size:13px;color:var(--soft)}}
.list li::marker{{color:var(--mute)}}
footer{{margin-top:52px;font-size:10.5px;color:var(--mute)}}
@media(max-width:620px){{
 .wrap{{padding:40px 16px 80px}}
 .row{{grid-template-columns:72px 46px minmax(0,1fr)}}
 .now{{grid-template-columns:1fr}}
 .body .h{{font-size:13.5px}}
}}
</style></head><body><div class="wrap">

<div class="path">{esc(workspace)}</div>
<h1>{esc(headline)}</h1>
<div class="sub">{len(data["bands"])} 个会话 · {data["events"]:,} 个事件 · {esc(ts(t0).strftime("%m-%d %H:%M"))} → {esc(ts(t1).strftime("%m-%d %H:%M"))}</div>

<h2>现在</h2>
<div class="now">
  <div><div class="k">在跑</div><div class="v">{esc(lst("在跑"))}</div></div>
  <div><div class="k">已落地</div><div class="v">{esc(lst("已落地"))}</div></div>
  <div><div class="k">走死</div><div class="v"><em>{esc(lst("走死"))}</em></div></div>
</div>
<div class="blocked"><div class="k">卡在</div><div class="v">{esc(blocked)}</div></div>

<h2>这三天怎么过的</h2>
<div class="rhythm">{cells}{cuts_html}</div>
<div class="ticks">{"".join(ticks)}</div>
<div class="rnote">{data["span_h"]:.0f} 小时里只有 {data["active_hours"]} 个小时在干活；红色竖线是记忆被删除的位置。</div>

<h2>会话流</h2>
<div class="bands">{band_rows}</div>
<div class="rnote">每条是一个会话，长条是主会话，短条是它派出的子代理。</div>

<h2>决策</h2>
<div class="legend"><b>线（左→右）</b> {esc(" · ".join(order))}</div>
{ledger}

<div class="list"><h2>未完成</h2><ol>{fin}</ol></div>
<footer>由 sessionmap 生成 · 规则提取 + 本地 agent CLI 提炼 · 数据不出本机</footer>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace")
    ap.add_argument("decisions")
    ap.add_argument("--out", default="report.html")
    a = ap.parse_args()

    with open(a.decisions, encoding="utf-8") as f:
        tj = json.load(f)
    con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    sessions, events, comps = load(con, a.workspace)
    data = analyse(sessions, events, tj.get("decisions", []))

    ws = ""
    for sid, title, parent, tc, tu in sessions:
        pass
    ws = con.execute("select directory from session where directory like ? limit 1",
                     ("%" + a.workspace + "%",)).fetchone()[0]

    html = render(ws, data, tj.get("decisions", []), comps,
                  tj.get("unfinished", []), tj.get("headline", ""))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("已生成 %s (%d 字符)" % (a.out, len(html)))


if __name__ == "__main__":
    main()
