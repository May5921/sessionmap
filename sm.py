#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``sm``: small project-memory commands on top of the canonical store.

The first command is ``sm brief <project>``.  It is deliberately deterministic:
it reads canonical events (and an existing semantic run when one exists) and
extracts three short sections - what is being worked on, what was decided, and
what is blocked.  No model call is made, so the same store produces the same
brief and every bullet carries a source event/session reference.

Usage:
    python sm.py brief fangzi
    python sm.py brief fangzi --store sessionmap-fangzi.db
    python sm.py brief fangzi --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from session_memory import ensure_memory_schema, make_claim_key, record_claim

ROOT = Path(__file__).resolve().parent
DAY_MS = 86_400_000
MAX_BRIEF_CHARS = 600

NOISE_MARKERS = (
    "<codex_internal_context",
    "<INSTRUCTIONS>",
    "AGENTS.md instructions",
    "Continue working toward the active thread goal",
    "User set the goal",
    "The active thread goal objective",
)
SECRET_RE = re.compile(r"(密码|口令|password|passwd|api[_-]?key|token\s*[:=]|sk-[A-Za-z0-9]{10,})", re.IGNORECASE)
SIGNAL_RE = re.compile(
    r"(改|修|问题|歧义|流程|登录|文案|角色|录单|入口|首页|为什么|怎么|是否|待|没|未|不能|bug|错误|卡|验收|上线|部署)"
)
BLOCKER_RE = re.compile(r"(\?|？|歧义|为什么|怎么回事|问题|没|未|不能|待|是否|卡|失败|错误|bug)", re.IGNORECASE)
BLOCKER_HINT_RE = re.compile(r"(歧义|登录|入口|角色|流程|问题|为什么|怎么回事|是否|待|卡|失败|错误)")


def norm_root(value: Any) -> str:
    return str(value or "").replace("\\", "/").rstrip("/").lower()


def root_basename(value: Any) -> str:
    return norm_root(value).rsplit("/", 1)[-1]


def workspace_root(row: sqlite3.Row | dict[str, Any]) -> str:
    """Resolve the repository path stored by different source adapters."""
    kind = str(row["source_kind"] or "")
    name = str(row["name"] or "")
    source = str(row["source_path"] or "")
    if kind == "zcode" and any(mark in name for mark in (":", "/", "\\")):
        return name
    return source or name


def project_matches(root: str, project: str) -> bool:
    target = norm_root(project)
    actual = norm_root(root)
    if not target or not actual:
        return False
    return actual == target or root_basename(actual) == target or root_basename(actual) == root_basename(target)


def parse_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def now_ms() -> int:
    return int(dt.datetime.now().timestamp() * 1000)


def fmt_day(value: int | None) -> str:
    if not value:
        return "未知日期"
    return dt.datetime.fromtimestamp(value / 1000).strftime("%m-%d")


def fmt_stamp(value: int | None) -> str:
    if not value:
        return "未知时间"
    return dt.datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d %H:%M")


def clip(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: max(1, limit - 1)].rstrip() + "…"


def open_ro(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect("file:" + str(Path(path).resolve()).replace("\\", "/") + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def candidate_stores(project: str) -> list[Path]:
    """Return likely canonical stores, most specific filename first."""
    seen: set[Path] = set()
    out: list[Path] = []
    directories = [Path.cwd(), ROOT]
    for directory in directories:
        if not directory.exists():
            continue
        exact = [directory / f"sessionmap-{project}.db", directory / "sessionmap.db"]
        patterns = [directory / "sessionmap-*.db", directory / "sessionmap.db"]
        for path in exact:
            if path.exists() and path.resolve() not in seen:
                seen.add(path.resolve())
                out.append(path)
        for pattern in patterns:
            for path in sorted(directory.glob(pattern.name)):
                if not path.is_file() or path.name.endswith(("-shm", "-wal")):
                    continue
                if any(part in path.name for part in ("bench", "failed", "incremental-check")):
                    continue
                if path.resolve() not in seen:
                    seen.add(path.resolve())
                    out.append(path)
    return out


def matching_workspaces(con: sqlite3.Connection, project: str) -> list[dict[str, Any]]:
    try:
        rows = con.execute("SELECT * FROM workspace ORDER BY workspace_id").fetchall()
    except sqlite3.Error:
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        root = workspace_root(row)
        if not project_matches(root, project):
            continue
        count = con.execute(
            "SELECT count(*) FROM event WHERE workspace_id=?", (row["workspace_id"],)
        ).fetchone()[0]
        if count:
            result.append({"workspace_id": str(row["workspace_id"]), "root": root,
                           "source_kind": str(row["source_kind"] or ""), "events": int(count)})
    return result


def choose_store(project: str, explicit: str | None = None) -> tuple[Path, list[dict[str, Any]]]:
    """Pick the store that carries the most events for this project.

    For fangzi the merged store wins over each single-adapter store because it
    contains all source events.  Explicit ``--store`` bypasses discovery.
    """
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise SystemExit(f"找不到 store: {path}")
        con = open_ro(path)
        try:
            workspaces = matching_workspaces(con, project)
        finally:
            con.close()
        if not workspaces:
            raise SystemExit(f"{path} 中没有项目 {project} 的工作区")
        return path, workspaces

    inspected: list[tuple[Path, list[dict[str, Any]]]] = []
    for path in candidate_stores(project):
        try:
            con = open_ro(path)
        except sqlite3.Error:
            continue
        try:
            found = matching_workspaces(con, project)
        finally:
            con.close()
        if found:
            inspected.append((path, found))
    if not inspected:
        names = ", ".join(path.name for path in candidate_stores(project)) or "（无）"
        raise SystemExit(f"没有找到项目 {project} 的 canonical store；已检查: {names}")
    path, found = max(inspected, key=lambda item: sum(w["events"] for w in item[1]))
    return path, found


def load_events(con: sqlite3.Connection, workspace_ids: list[str]) -> dict[str, Any]:
    marks = ",".join("?" for _ in workspace_ids)
    params = list(workspace_ids)
    latest = con.execute(
        f"SELECT max(occurred_at) FROM event WHERE workspace_id IN ({marks})", params
    ).fetchone()[0] or now_ms()

    def rows(event_type: str, limit: int, since: int | None = None) -> list[dict[str, Any]]:
        sql = (f"SELECT event_id,workspace_id,session_id,source_kind,event_type,occurred_at,"
               f"summary,payload_json,evidence_text FROM event "
               f"WHERE workspace_id IN ({marks}) AND event_type=?")
        args: list[Any] = [*params, event_type]
        if since is not None:
            sql += " AND occurred_at>=?"
            args.append(since)
        sql += " ORDER BY occurred_at DESC, event_id DESC LIMIT ?"
        args.append(limit)
        out = []
        for row in con.execute(sql, args):
            item = dict(row)
            item["payload"] = parse_json(item.pop("payload_json"), {})
            out.append(item)
        return out

    sessions = {}
    for row in con.execute(
        f"SELECT session_id,title,source_kind,started_at,updated_at FROM session "
        f"WHERE workspace_id IN ({marks})", params
    ):
        sessions[row["session_id"]] = dict(row)
    return {
        "latest": int(latest),
        "user_messages": rows("user_message", 160, latest - 90 * DAY_MS),
        "commits": rows("git_commit", 40),
        "todos": rows("todo_change", 500),
        "errors": rows("error", 30, latest - 14 * DAY_MS),
        "memory_breaks": rows("memory_break", 30, latest - 7 * DAY_MS),
        "sessions": sessions,
    }


def load_decisions(con: sqlite3.Connection, workspace_ids: list[str]) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for workspace_id in workspace_ids:
        run = con.execute(
            """SELECT * FROM analysis_run WHERE workspace_id=? AND status='succeeded'
               ORDER BY completed_at DESC, run_id DESC LIMIT 1""",
            (workspace_id,),
        ).fetchone()
        if not run:
            continue
        for row in con.execute(
            "SELECT * FROM decision WHERE run_id=? ORDER BY ordinal,decision_id", (run["run_id"],)
        ):
            item = dict(row)
            override = con.execute(
                "SELECT * FROM decision_override WHERE decision_id=?", (item["decision_id"],)
            ).fetchone()
            if override:
                for key in ("title", "track", "status", "what", "why"):
                    if override[key]:
                        item[key] = override[key]
            links = []
            for link in con.execute(
                "SELECT * FROM evidence_link WHERE decision_id=? ORDER BY rank,link_id",
                (item["decision_id"],),
            ):
                links.append(dict(link))
            item["evidence"] = links
            decisions.append(item)
    decisions.sort(key=lambda item: (item.get("occurred_at") or 0, item.get("ordinal") or 0))
    return decisions


def clean_user_text(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if any(marker in text for marker in NOISE_MARKERS):
        return ""
    if SECRET_RE.search(text):
        return ""
    text = re.sub(r"<image[^>]*>", "", text)
    text = re.sub(r"\[Image #[^\]]*\]", "", text)
    text = re.sub(r"https?://\S+", "链接", text)
    text = " ".join(text.split())
    if len(text) < 6:
        return ""
    lowered = text.lower()
    if lowered.startswith(("#", "<", "curl ", "cd ", "python ", "npm ", "git ", "c:\\", "d:\\")):
        return ""
    if text.startswith(("你是 D:", "当前主线 HEAD")):
        return ""
    return text


def dedupe_texts(items: list[dict[str, Any]], key_field: str = "text") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: list[str] = []
    for item in items:
        key = re.sub(r"\s+", "", str(item.get(key_field) or ""))[:36].lower()
        if not key:
            continue
        if any(key == old or key.startswith(old[:18]) or old.startswith(key[:18]) for old in seen):
            continue
        seen.append(key)
        out.append(item)
    return out


def select_asks(events: dict[str, Any], latest: int) -> list[dict[str, Any]]:
    messages = []
    for event in events["user_messages"]:
        text = clean_user_text(event.get("summary"))
        if not text:
            continue
        messages.append({"text": text, "event": event})
    for window in (3 * DAY_MS, 14 * DAY_MS, 90 * DAY_MS):
        recent = [m for m in messages if (m["event"].get("occurred_at") or 0) >= latest - window]
        if recent:
            messages = recent
            break
    messages = dedupe_texts(messages)
    scored = [(len(SIGNAL_RE.findall(m["text"])), m["event"].get("occurred_at") or 0, m) for m in messages]
    # Recent messages win ties; signal keywords only break near-ties, so the
    # brief stays about the current work rather than an old loud complaint.
    scored.sort(key=lambda item: (item[1], item[0]), reverse=True)
    return [item[2] for item in scored[:4]]


def select_commits(events: dict[str, Any], limit: int = 4) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events["commits"]:
        payload = event.get("payload") or {}
        sha = str(payload.get("sha") or event.get("event_id") or "")
        if not sha or sha in seen:
            continue
        seen.add(sha)
        out.append({"text": str(payload.get("subject") or event.get("summary") or sha),
                    "sha": sha, "event": event})
    out.sort(key=lambda item: (item["event"].get("occurred_at") or 0), reverse=True)
    return out[:limit]


def latest_todo_states(events: dict[str, Any]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in events["todos"]:
        payload = event.get("payload") or {}
        content = clean_user_text(payload.get("content") or event.get("summary"))
        if not content:
            continue
        current = latest.get(content)
        at = event.get("occurred_at") or 0
        if current is None or at >= (current["event"].get("occurred_at") or 0):
            latest[content] = {"text": content, "status": str(payload.get("status") or ""),
                               "event": event}
    return list(latest.values())


def select_todos(events: dict[str, Any], latest: int, statuses: set[str],
                 window_days: int, limit: int) -> list[dict[str, Any]]:
    items = []
    for item in latest_todo_states(events):
        if item["status"].lower() not in statuses:
            continue
        at = item["event"].get("occurred_at") or 0
        if at and at < latest - window_days * DAY_MS:
            continue
        items.append(item)
    items.sort(key=lambda item: item["event"].get("occurred_at") or 0, reverse=True)
    return dedupe_texts(items)[:limit]


def event_ref(event: dict[str, Any], quote: str | None = None) -> dict[str, Any]:
    payload = event.get("payload") or {}
    ref = {
        "event_id": event.get("event_id"),
        "session_id": event.get("session_id"),
        "source_kind": event.get("source_kind"),
        "at": event.get("occurred_at"),
        "quote": clip(quote if quote is not None else event.get("summary"), 240),
    }
    if payload.get("sha"):
        ref["sha"] = payload.get("sha")
    return ref


def citation(ref: dict[str, Any]) -> str:
    event_id = str(ref.get("event_id") or "")
    sha = str(ref.get("sha") or "")
    if event_id:
        return "event " + event_id
    if sha:
        return "commit " + sha
    if ref.get("session_id"):
        return "session " + str(ref["session_id"])
    return "store"


def make_bullet(*, project: str, section: str, subject: str, text: str,
                refs: list[dict[str, Any]], source_adapter: str) -> dict[str, Any]:
    return {
        "section": section,
        "subject": subject,
        "text": text,
        "display": text,
        "refs": refs,
        "claim_key": make_claim_key(project, section, subject),
        "source_adapter": source_adapter,
    }


def build_claims(project: str, events: dict[str, Any], decisions: list[dict[str, Any]],
                 latest: int) -> dict[str, list[dict[str, Any]]]:
    asks = select_asks(events, latest)
    commits = select_commits(events, 5)
    used_event_ids: set[str] = set()

    doing: list[dict[str, Any]] = []
    if asks:
        chosen = asks[:2]
        text = "最近用户要求：" + "；".join(clip(item["text"], 26) for item in chosen)
        refs = [event_ref(item["event"]) for item in chosen]
        used_event_ids.update(str(ref["event_id"]) for ref in refs)
        doing.append(make_bullet(project=project, section="doing",
                                 subject="asks:recent",
                                 text=text, refs=refs, source_adapter="canonical-events"))
    doing_commits = commits[:2] if not asks else []
    if doing_commits:
        text = "近期提交：" + "；".join(clip(item["text"], 30) for item in doing_commits)
        refs = [event_ref(item["event"]) for item in doing_commits]
        used_event_ids.update(str(ref["event_id"]) for ref in refs)
        doing.append(make_bullet(project=project, section="doing",
                                 subject="commits:recent",
                                 text=text, refs=refs, source_adapter="canonical-events"))
    if not doing:
        latest_events = events["user_messages"] or events["commits"] or events["todos"]
        if latest_events:
            event = latest_events[0]
            doing.append(make_bullet(project=project, section="doing", subject="latest:" + str(event["event_id"]),
                                     text=f"最近活动集中在 {fmt_stamp(latest)}（{event.get('source_kind') or '未知来源'}）。",
                                     refs=[event_ref(event)], source_adapter="canonical-events"))

    decided: list[dict[str, Any]] = []
    if decisions:
        for decision in decisions[-3:][::-1]:
            evidence = decision.get("evidence") or []
            refs = []
            for link in evidence[:2]:
                event = next((e for e in events["commits"] if e.get("event_id") == link.get("event_id")), None)
                if event:
                    refs.append(event_ref(event, link.get("quote")))
                else:
                    refs.append({"event_id": link.get("event_id"), "session_id": link.get("session_id"),
                                 "source_kind": "semantic", "at": decision.get("occurred_at"),
                                 "quote": link.get("quote") or ""})
            if not refs:
                continue
            what = clip(decision.get("what"), 34)
            title = clip(decision.get("title"), 28)
            text = title + ("" if not what or what == title else "：" + what)
            decided.append(make_bullet(project=project, section="decision",
                                       subject="decision:" + str(decision.get("external_id") or decision.get("decision_id")),
                                       text=text, refs=refs, source_adapter="semantic"))
    else:
        remaining = [item for item in commits if str(item["event"].get("event_id")) not in used_event_ids][:1]
        if not asks:
            remaining = commits[2:3]
        if remaining:
            text = "已落地：" + "；".join(clip(item["text"], 30) for item in remaining)
            refs = [event_ref(item["event"]) for item in remaining]
            decided.append(make_bullet(project=project, section="decision",
                                       subject="landed:recent",
                                       text=text, refs=refs, source_adapter="canonical-events"))
        completed = select_todos(events, latest, {"completed", "done"}, 14, 2)
        if completed:
            text = "已核对：" + "；".join(clip(item["text"], 30) for item in completed)
            refs = [event_ref(item["event"]) for item in completed]
            decided.append(make_bullet(project=project, section="decision",
                                       subject="done:recent",
                                       text=text, refs=refs, source_adapter="canonical-events"))

    blockers: list[dict[str, Any]] = []
    pending = select_todos(events, latest, {"pending", "in_progress", "in-progress", "open"}, 30, 2)
    for item in pending:
        at = item["event"].get("occurred_at")
        text = f"[{fmt_day(at)}] 待定：{clip(item['text'], 30)}"
        blockers.append(make_bullet(project=project, section="blocker",
                                    subject="todo:" + item["text"],
                                    text=text, refs=[event_ref(item["event"])],
                                    source_adapter="canonical-events"))
    used_ask_ids = {str(ref.get("event_id")) for bullet in doing for ref in bullet["refs"]}
    blocker_asks = [item for item in asks
                    if str(item["event"].get("event_id")) not in used_ask_ids
                    and BLOCKER_RE.search(item["text"])]
    blocker_asks.sort(key=lambda item: (len(BLOCKER_HINT_RE.findall(item["text"])),
                                        item["event"].get("occurred_at") or 0), reverse=True)
    for item in blocker_asks[:1]:
        blockers.append(make_bullet(project=project, section="blocker",
                                    subject="ask:blocker:recent",
                                    text="仍在追问：" + clip(item["text"], 32),
                                    refs=[event_ref(item["event"])],
                                    source_adapter="canonical-events"))
    for event in events["errors"][:1]:
        if len(blockers) >= 2:
            break
        text = clean_user_text(event.get("summary"))
        if text:
            blockers.append(make_bullet(project=project, section="blocker",
                                        subject="error:recent",
                                        text="未解决错误：" + clip(text, 38),
                                        refs=[event_ref(event)],
                                        source_adapter="canonical-events"))
    return {"doing": doing, "decision": decided, "blocker": blockers}


def render_brief(project: str, latest: int, sections: dict[str, list[dict[str, Any]]],
                 max_chars: int = MAX_BRIEF_CHARS) -> str:
    labels = (("doing", "在做什么"), ("decision", "已确定决策"), ("blocker", "卡住的问题"))

    def render() -> str:
        parts = [f"{project} · 截至 {fmt_day(latest)}", ""]
        for key, label in labels:
            parts.append(label)
            bullets = sections.get(key) or []
            if bullets:
                for bullet in bullets:
                    refs = "；".join(citation(ref) for ref in bullet["refs"])
                    parts.append(f"- {bullet['display']}（来源：{refs}）")
            else:
                parts.append("- 暂无可证实的结论。")
            parts.append("")
        return "\n".join(parts).rstrip()

    text = render()
    # Drop lower-priority bullets while keeping at least one bullet per section
    # that originally had evidence.
    while len(text) > max_chars:
        candidates = [(key, len(sections.get(key) or [])) for key, _ in labels if len(sections.get(key) or []) > 1]
        if not candidates:
            break
        key = max(candidates, key=lambda item: item[1])[0]
        sections[key].pop()
        text = render()
    if len(text) > max_chars:
        excess = len(text) - max_chars
        visible = [b for key, _ in labels for b in sections.get(key) or []]
        if visible:
            share = max(8, excess // max(1, len(visible)) + 2)
            for bullet in visible:
                bullet["display"] = clip(bullet["display"], max(8, len(bullet["display"]) - share))
        text = render()
    return text


def brief(project: str, store: str | None = None, persist_memory: bool = True,
          max_chars: int = MAX_BRIEF_CHARS) -> dict[str, Any]:
    path, workspaces = choose_store(project, store)
    workspace_ids = [item["workspace_id"] for item in workspaces]
    read_con = open_ro(path)
    try:
        events = load_events(read_con, workspace_ids)
        decisions = load_decisions(read_con, workspace_ids)
    finally:
        read_con.close()
    latest = events["latest"]
    sections = build_claims(project, events, decisions, latest)
    text = render_brief(project, latest, sections, max_chars)

    claims = [bullet for key in ("doing", "decision", "blocker") for bullet in sections.get(key) or []]
    memory_results: list[dict[str, Any]] = []
    if persist_memory:
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        try:
            ensure_memory_schema(con)
            stamp = now_ms()
            for bullet in claims:
                result = record_claim(
                    con,
                    project=project,
                    claim_key=bullet["claim_key"],
                    content=bullet["display"],
                    evidence_refs=bullet["refs"],
                    source_adapter=bullet["source_adapter"],
                    created_at=stamp,
                )
                result.update({"section": bullet["section"], "claim_key": bullet["claim_key"]})
                memory_results.append(result)
            con.commit()
        finally:
            con.close()
    return {
        "project": project,
        "store": str(path),
        "workspace_ids": workspace_ids,
        "latest": latest,
        "brief": text,
        "chars": len(text),
        "sections": {key: len(value) for key, value in sections.items()},
        "claims": [
            {"section": bullet["section"], "text": bullet["display"],
             "evidence_refs": bullet["refs"], "claim_key": bullet["claim_key"],
             "source_adapter": bullet["source_adapter"]}
            for bullet in claims
        ],
        "memory": memory_results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sm", description="sessionmap project memory commands")
    sub = parser.add_subparsers(dest="command", required=True)
    brief_parser = sub.add_parser("brief", help="归纳项目当前状态（600 字以内，带来源引用）")
    brief_parser.add_argument("project", help="项目名或仓库路径，例如 fangzi")
    brief_parser.add_argument("--store", default=None, help="显式指定 canonical store")
    brief_parser.add_argument("--json", action="store_true", help="输出结构化结果")
    brief_parser.add_argument("--no-memory", action="store_true", help="只生成，不写入 memory 表")
    brief_parser.add_argument("--max-chars", type=int, default=MAX_BRIEF_CHARS)
    args = parser.parse_args(argv)
    if args.command == "brief":
        try:
            result = brief(args.project, args.store, not args.no_memory, args.max_chars)
        except SystemExit as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(result["brief"])
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
