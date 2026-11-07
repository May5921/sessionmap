#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sessionmap v1 - 把 ZCode 工作区的会话导出成可供提炼的结构化时间线。

用法:
    python scan.py "pi-mini"
    python scan.py "pi-mini" --since 2026-11-01
    python scan.py "pi-mini" --out timeline.md --verbose

设计原则:
  * 只读 DB，绝不写入 (mode=ro)
  * 子会话 (parent_id 非空) 折叠为计数 —— 标题由编排器生成, 对人的决策是噪音
  * 系统注入消息用 metadata 过滤, 不用字符串匹配
  * 信息分层: 意图(用户原话) / 行动(工具调用聚合) / 状态(todo) / 断裂(compaction)
"""
import argparse
import datetime as dt
import json
import re
import os
import sqlite3
import sys
from collections import Counter

DEFAULT_DB = os.path.expandvars(r"%USERPROFILE%\.zcode\cli\db\db.sqlite")

# 系统注入的两种标记, 出现任一即非人类发言
NOISE_SQL = """
      and json_extract(m.data, '$.synthetic') is null
      and json_extract(m.data, '$.metadata.visibility') is null
"""


def ts(ms, fmt="%Y-%m-%d %H:%M"):
    if not ms:
        return "?"
    return dt.datetime.fromtimestamp(ms / 1000.0).strftime(fmt)


def connect(db_path):
    if not os.path.exists(db_path):
        sys.exit("找不到数据库: %s" % db_path)
    return sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)


def session_ids(con, workspace):
    return [r[0] for r in con.execute(
        "select id from session where directory like ?", ("%" + workspace + "%",))]


def fetch_sessions(con, workspace):
    return con.execute("""
        select id, title, parent_id, directory, time_created, time_updated,
               summary_files, summary_additions, summary_deletions
        from session
        where directory like ? and time_deleted is null
        order by time_created asc
    """, ("%" + workspace + "%",)).fetchall()


def fetch_user_texts(con, session_id, max_msgs, trunc, budget=8000):
    sql = """
        select m.time_created, p.data
        from message m join part p on p.message_id = m.id
        where m.session_id = ?
          and json_extract(m.data, '$.role') = 'user'
          and json_extract(p.data, '$.type') = 'text'
    """ + NOISE_SQL + """
        order by m.time_created asc, p.sequence asc
    """
    out, used = [], 0
    for created, data in con.execute(sql, (session_id,)):
        try:
            text = " ".join((json.loads(data).get("text") or "").split())
        except Exception:
            continue
        if not text:
            continue
        piece = text[:trunc] + ("…" if len(text) > trunc else "")
        out.append((created, piece))
        used += len(piece)
        # 按字符预算而不是条数截断: 用户消息大多很短, 截条数会误杀关键决策
        if used >= budget or len(out) >= max_msgs:
            break
    return out


def fetch_child_user_texts(con, session_ids, trunc, max_msgs=8, budget=3500):
    """Keep a bounded, chronological sample of child-agent user messages.

    Child sessions are still collapsed in the main session tree, but their
    high-signal prompts can contain the actual delegated decision.  Preserve a
    small deduplicated sample so semantic extraction can see that context
    without expanding every tool transcript.
    """
    if not session_ids:
        return []
    rows = []
    for session_id in session_ids:
        rows.extend(fetch_user_texts(con, session_id, max_msgs=12, trunc=trunc, budget=2200))
    rows.sort(key=lambda row: (row[0] or 0, row[1]))
    result, seen, used = [], set(), 0
    for created, text in rows:
        key = " ".join(str(text or "").split())
        if not key or key in seen:
            continue
        if len(result) >= max_msgs or used + len(key) > budget:
            continue
        seen.add(key)
        result.append((created, key))
        used += len(key)
    return result


def fetch_action(con, session_ids):
    """工具调用聚合: 工具分布 / 改动最多的文件 / 跑过的命令"""
    if not session_ids:
        return Counter(), Counter(), []
    counts, files, cmds = Counter(), Counter(), []
    ph = ",".join("?" * len(session_ids))
    sql = "select data from part where session_id in (%s) and json_extract(data,'$.type')='tool'" % ph
    for (d,) in con.execute(sql, session_ids):
        try:
            o = json.loads(d)
        except Exception:
            continue
        name = o.get("tool") or "?"
        counts[name] += 1
        inp = (o.get("state") or {}).get("input") or {}
        fp = inp.get("filePath") or inp.get("path") or inp.get("file_path")
        if fp:
            files[str(fp).replace("\\", "/").split("/")[-1]] += 1
        if name.lower() == "bash" and inp.get("command"):
            first = str(inp["command"]).strip().split("\n")[0]
            # 只保留主命令, 砍掉 && / || / | 后面的链式部分
            first = re.split(r"\s*(?:&&|\|\||\|)\s*", first)[0][:60].strip()
            if first:
                cmds.append(first)
    return counts, files, cmds


def fetch_todos(con, session_ids):
    if not session_ids:
        return Counter(), []
    ph = ",".join("?" * len(session_ids))
    rows = con.execute(
        "select status, content from todo where session_id in (%s) order by session_id, position"
        % ph, session_ids).fetchall()
    return Counter(r[0] for r in rows), rows


def fetch_compactions(con, workspace):
    sql = """
        select p.data, p.time_created, s.title from part p
        join session s on s.id = p.session_id
        where s.directory like ? and json_extract(p.data,'$.type') = 'compaction'
        order by p.time_created asc
    """
    out, seen = [], set()
    for d, tc, title in con.execute(sql, ("%" + workspace + "%",)):
        try:
            o = json.loads(d)
        except Exception:
            continue
        pre = o.get("preCompactTokenCount") or 0
        post = o.get("truePostCompactTokenCount") or 0
        if not (pre and post):
            continue
        key = (tc, pre, post)          # 同一次压缩会在多个会话里各记一条, 去重
        if key in seen:
            continue
        seen.add(key)
        out.append((tc, title, pre, post, o.get("compactReason") or "?"))
    return out


def build(workspace, db_path, since, per_session, trunc, verbose):
    con = connect(db_path)
    rows = fetch_sessions(con, workspace)
    if not rows:
        sys.exit("该工作区没有会话: %s" % workspace)

    total = con.execute(
        "select count(*) from message m join session s on s.id=m.session_id "
        "where s.directory like ?", ("%" + workspace + "%",)).fetchone()[0]
    real = con.execute(
        "select count(*) from message m join session s on s.id=m.session_id "
        "where s.directory like ?" + NOISE_SQL, ("%" + workspace + "%",)).fetchone()[0]

    mains, children = [], {}
    for r in rows:
        if since and dt.datetime.fromtimestamp(r[4] / 1000.0) < since:
            continue
        if r[2]:
            children.setdefault(r[2], []).append(r[0])
        else:
            mains.append(r)

    def descendants(root):
        """递归取全部子孙会话 —— 子代理还能再开子代理, 只取一层会漏"""
        out, stack = [], [root]
        while stack:
            x = stack.pop()
            out.append(x)
            stack.extend(children.get(x, []))
        return out

    L = []
    L.append("# 工作区: %s" % rows[0][3])
    L.append("")
    L.append("- 会话: %d (主 %d / 子 %d)"
             % (len(rows), len(mains), len(rows) - len(mains)))
    L.append("- 消息: %d (人发 %d / 系统注入 %d)"
             % (total, real, total - real))
    L.append("- 时间: %s ~ %s" % (ts(rows[0][4]), ts(rows[-1][5], "%Y-%m-%d %H:%M")))

    comps = fetch_compactions(con, workspace)
    if comps:
        pre_sum = sum(c[2] for c in comps)
        post_sum = sum(c[3] for c in comps)
        L.append("- **上下文压缩: %d 次, 累计 %s → %s token (丢 %.1f%%)**"
                 % (len(comps), format(pre_sum, ","), format(post_sum, ","),
                    100.0 * (1 - post_sum / pre_sum)))
        L.append("")
        L.append("## 记忆断裂点")
        L.append("")
        for tc, title, pre, post, reason in comps:
            L.append("- %s · %s · %s → %s (−%.0f%%, %s)"
                     % (ts(tc), (title or "")[:40], format(pre, ","),
                        format(post, ","), 100 * (1 - post / pre), reason))
        L.append("")

    L.append("---")
    L.append("")
    cur_day = None
    for sid, title, parent, directory, tc, tu, nfiles, adds, dels in mains:
        d = ts(tc, "%Y-%m-%d")
        if d != cur_day:
            cur_day = d
            L.append("## %s" % d)
            L.append("")

        tree = descendants(sid)
        subs = tree[1:]
        span = max(0, (tu - tc) // 60000)
        tools, files, cmds = fetch_action(con, tree)
        tstat, trows = fetch_todos(con, tree)

        L.append("### %s · %s · %d 分钟"
                 % (ts(tc, "%H:%M"), (title or "(无标题)").strip(), span))

        meta = []
        if tools:
            meta.append("工具 " + " / ".join("%s %d" % (k, v) for k, v in tools.most_common(5)))
        if subs:
            meta.append("子会话 ×%d" % len(subs))
        if nfiles:
            meta.append("改动 %d 文件 (+%s/−%s)" % (nfiles, adds or 0, dels or 0))
        if meta:
            L.append("*%s*" % " · ".join(meta))
            L.append("")

        if files:
            L.append("改动最多: " + ", ".join("%s ×%d" % (k, v) for k, v in files.most_common(5)))
        if cmds:
            uniq = list(dict.fromkeys(cmds))[:8]
            L.append("命令: " + " | ".join(uniq))
        if tstat:
            L.append("待办: " + " / ".join("%s %d" % (k, v) for k, v in tstat.most_common()))
        if verbose and trows:
            for st, content in trows:
                L.append("  - [%s] %s" % (st, content[:80]))
        if files or cmds or tstat:
            L.append("")

        for created, text in fetch_user_texts(con, sid, per_session, trunc):
            if created:
                L.append("- `%s` %s" % (ts(created, "%m-%d %H:%M"), text))
            else:
                L.append("- %s" % text)
        child_texts = fetch_child_user_texts(con, subs, trunc)
        if child_texts:
            L.append("子会话关键信息:")
            for created, text in child_texts:
                if created:
                    L.append("- `[子代理 %s]` %s" % (ts(created, "%m-%d %H:%M"), text))
                else:
                    L.append("- `[子代理]` %s" % text)
        L.append("")

    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace", help="工作区路径关键字, 如 pi-mini")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--since", default=None, help="YYYY-MM-DD")
    ap.add_argument("--per-session", type=int, default=300)
    ap.add_argument("--trunc", type=int, default=220)
    ap.add_argument("--out", default=None)
    ap.add_argument("--verbose", action="store_true", help="列出每条待办")
    a = ap.parse_args()

    since = dt.datetime.strptime(a.since, "%Y-%m-%d") if a.since else None
    text = build(a.workspace, a.db, since, a.per_session, a.trunc, a.verbose)

    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("已写入 %s (%d 字符, %d 行)" % (a.out, len(text), text.count("\n") + 1))
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)


if __name__ == "__main__":
    main()
