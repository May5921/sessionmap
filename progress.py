#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic project-progress spine derived from canonical facts.

The spine answers "how did this project get here" without asking a model:
work items come from session boundaries and file overlap, milestones from git
release commits, and status from TODO state and session outcomes.  Semantic
decisions stay attached to the spine as annotations.

Design absorbed from Looma (WorkItem spine, git as ground truth, file-overlap
resolution, confidence), Waystone (structured facts + supersession) and
Graphiti/Zep (bi-temporal facts, LLM as helper not driver).  Everything here
is a pure function of the canonical store, so it is reproducible and cheap.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from collections import Counter, defaultdict
from typing import Any

RELEASE_RE = re.compile(r"发布\s*(v?\d+(?:\.\d+)+)", re.IGNORECASE)
VERSION_RE = re.compile(r"v?\d+(?:\.\d+)+")
KIND_RULES = (
    ("investigation", re.compile(r"调研|研究|审计|分析|排查|评估|推广|获客|前景|规划|benchmark|research|audit", re.IGNORECASE)),
    ("bugfix", re.compile(r"修复|修\s*bug|回归|问题|故障|无法|不能|出错|异常|fix|bug|regression", re.IGNORECASE)),
    ("refactor", re.compile(r"重构|整理|refactor", re.IGNORECASE)),
    ("release", re.compile(r"发布|上线|release|ship", re.IGNORECASE)),
)


def fmt_ms(value: int | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not value:
        return "—"
    return dt.datetime.fromtimestamp(value / 1000).strftime(fmt)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _norm_path(path: str, root: str | None) -> str:
    text = str(path or "").replace("\\", "/")
    if root:
        root_norm = str(root).replace("\\", "/").rstrip("/")
        if root_norm and text.lower().startswith(root_norm.lower() + "/"):
            text = text[len(root_norm) + 1:]
    return text


def _kind_for(title: str) -> str:
    for kind, pattern in KIND_RULES:
        if pattern.search(title or ""):
            return kind
    return "feature"


def _children_index(sessions: list[dict[str, Any]]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = defaultdict(list)
    for session in sessions:
        parent = session.get("parent_id")
        if parent:
            children[parent].append(session["session_id"])
    return children


def _descendants(session_id: str, children: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    stack = [session_id]
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            out.append(child)
            stack.append(child)
    return out


def _events_by_session(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[event.get("session_id")].append(event)
    return grouped


def _files_for(session_ids: list[str], events_by_session: dict[str, list[dict[str, Any]]],
               root: str | None) -> Counter:
    files: Counter = Counter()
    for session_id in session_ids:
        for event in events_by_session.get(session_id, []):
            if event.get("event_type") != "tool_call":
                continue
            payload = event.get("payload") or {}
            if payload.get("tool") not in ("Edit", "Read", "Write"):
                continue
            inp = payload.get("input") if isinstance(payload.get("input"), dict) else {}
            path = inp.get("file_path") or inp.get("path") or inp.get("filePath")
            if path:
                files[_norm_path(path, root)] += 1
    return files


def _todos_for(session_ids: list[str],
               events_by_session: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for session_id in session_ids:
        for event in events_by_session.get(session_id, []):
            if event.get("event_type") != "todo_change":
                continue
            payload = event.get("payload") or {}
            content = payload.get("content")
            if not content:
                continue
            latest[content] = {
                "status": payload.get("status"),
                "at": event.get("occurred_at"),
                "session_id": session_id,
            }
    return latest


def _ref(event: dict[str, Any], quote: str | None = None, fact: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = event.get("payload") or {}
    return {
        "event_id": event.get("event_id"),
        "session_id": event.get("session_id"),
        "session_title": event.get("session_title") or "未命名会话",
        "at": event.get("occurred_at"),
        "when": event.get("when"),
        "type": event.get("event_type"),
        "summary": event.get("summary"),
        "quote": quote if quote is not None else (event.get("evidence_text") or event.get("summary") or ""),
        "confidence": 1.0,
        "source_kind": event.get("source_kind"),
        "raw_ref": event.get("raw_ref"),
        "event_type": event.get("event_type"),
        "fact": fact if fact is not None else {},
        "payload": payload,
    }


def _commits(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    commits: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "git_commit":
            continue
        payload = event.get("payload") or {}
        sha = payload.get("sha") or event.get("source_id")
        commits.append({
            "id": f"commit:{sha}",
            "sha": sha,
            "when": event.get("when"),
            "when_ms": event.get("occurred_at"),
            "title": payload.get("subject") or event.get("summary") or sha,
            "kind": "commit",
            "status": "done",
            "evidence_refs": [_ref(event)],
        })
    commits.sort(key=lambda item: (item.get("when_ms") or 0, item.get("id") or ""))
    return commits


def _milestones(commits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    milestones: list[dict[str, Any]] = []
    for commit in commits:
        if not RELEASE_RE.search(commit.get("title") or ""):
            continue
        version = VERSION_RE.search(commit.get("title") or "")
        milestones.append({
            "id": f"milestone:{commit.get('sha')}",
            "when": commit.get("when"),
            "when_ms": commit.get("when_ms"),
            "title": version.group(0) if version else commit.get("title"),
            "kind": "release",
            "status": "done",
            "what": commit.get("title"),
            "commit_id": commit.get("id"),
            "evidence_refs": commit.get("evidence_refs") or [],
        })
    return milestones


def _confidence(files: Counter, commit_linked: bool, session_count: int, source_count: int,
                span_hours: float) -> float:
    file_overlap = _clamp01(len(files) / 8.0)
    commit_linkage = 1.0 if commit_linked else 0.0
    session_breadth = 1.0 - math.exp(-0.5 * max(0, session_count - 1))
    agent_breadth = 1.0 - math.exp(-0.5 * max(0, source_count - 1))
    temporal = _clamp01(span_hours / 4.0)
    raw = (0.30 * file_overlap + 0.25 * commit_linkage + 0.20 * session_breadth
           + 0.15 * agent_breadth + 0.10 * temporal)
    return round(_clamp01(raw), 2)


def _band(confidence: float) -> str:
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.40:
        return "medium"
    return "low"


def _work_items(main_sessions: list[dict[str, Any]], children: dict[str, list[str]],
                events_by_session: dict[str, list[dict[str, Any]]], commits: list[dict[str, Any]],
                root: str | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for session in main_sessions:
        descendants = _descendants(session["session_id"], children)
        scope = [session["session_id"], *descendants]
        files = _files_for(scope, events_by_session, root)
        todos = _todos_for(scope, events_by_session)
        open_todos = [
            {"text": content, "status": info.get("status"), "when": fmt_ms(info.get("at"))}
            for content, info in sorted(todos.items(), key=lambda kv: kv[1].get("at") or 0)
            if info.get("status") in ("in_progress", "pending")
        ]
        end = None
        for event in events_by_session.get(session["session_id"], []):
            if event.get("event_type") == "session_end":
                end = event.get("payload") or {}
        kind = _kind_for(session.get("title") or "")
        span_hours = 0.0
        if session.get("started_at") and session.get("updated_at"):
            span_hours = max(0.0, (session["updated_at"] - session["started_at"]) / 3600000)
        # Commits are linked when they happen inside the effort window; release
        # commits after a session still belong to the nearest preceding effort.
        linked = [c for c in commits
                  if session.get("started_at") and session.get("updated_at")
                  and session["started_at"] <= (c.get("when_ms") or 0) <= session["updated_at"]]
        if open_todos:
            status = "open"
        elif kind == "bugfix":
            status = "done" if any(str(c.get("title") or "").lower().startswith("fix") for c in linked) else "open"
        elif end and end.get("completed"):
            status = "done"
        else:
            status = "open"
        sources = sorted({str(event.get("source_kind") or "") for event_id in scope
                          for event in events_by_session.get(event_id, [])} - {""})
        evidence: list[dict[str, Any]] = []
        # The first and last thing the person actually typed are the only two
        # sentences that answer "why did this start" and "why did it stop".
        # Without them a work item is just a file list.
        human: list[dict[str, Any]] = sorted(
            (event for session_id in scope for event in events_by_session.get(session_id, [])
             if event.get("event_type") == "user_message"
             and (event.get("evidence_text") or event.get("summary") or "").strip()),
            key=lambda event: event.get("occurred_at") or 0,
        )
        opening = human[0] if human else None
        closing = human[-1] if len(human) > 1 else None
        if opening:
            evidence.append(_ref(opening))
        for event in events_by_session.get(session["session_id"], []):
            if event.get("event_type") == "session_start":
                evidence.append(_ref(event))
        if closing:
            evidence.append(_ref(closing))
        for path, _count in files.most_common(6):
            evidence.append({
                "event_id": None, "session_id": None, "session_title": "文件重叠",
                "when": session.get("started_at") and fmt_ms(session.get("started_at")),
                "type": "file", "summary": path, "quote": path, "confidence": 1.0,
                "source_kind": "git", "raw_ref": "", "event_type": "file",
                "fact": {"path": path}, "payload": {"path": path},
            })
        for commit in linked[:3]:
            evidence.extend(commit.get("evidence_refs") or [])
        items.append({
            "id": f"wi:{session['session_id']}",
            "session_id": session["session_id"],
            "when": fmt_ms(session.get("started_at")),
            "when_ms": session.get("started_at"),
            "end_ms": session.get("updated_at"),
            "title": session.get("title") or "未命名工作",
            "kind": kind,
            "status": status,
            "track": "主干",
            "files": [path for path, _c in files.most_common(12)],
            "file_count": len(files),
            "session_count": len(scope),
            "child_count": len(descendants),
            "sources": sources,
            "span_hours": round(span_hours, 1),
            "confidence": _confidence(files, bool(linked), len(scope), len(sources), span_hours),
            "confidence_band": _band(_confidence(files, bool(linked), len(scope), len(sources), span_hours)),
            "open_todos": open_todos,
            "todo_total": len(todos),
            "opening_quote": (opening.get("evidence_text") or opening.get("summary") or "").strip() if opening else "",
            "opening_at": opening.get("occurred_at") if opening else None,
            "closing_quote": (closing.get("evidence_text") or closing.get("summary") or "").strip() if closing else "",
            "closing_at": closing.get("occurred_at") if closing else None,
            "commits": [{"id": c["id"], "title": c["title"], "when": c["when"], "when_ms": c["when_ms"]} for c in linked],
            "evidence_refs": evidence[:12],
            "evidence_count": len(evidence[:12]),
        })
    items.sort(key=lambda item: (item.get("when_ms") or 0, item.get("id") or ""))
    return items


def _phases(milestones: list[dict[str, Any]], work_items: list[dict[str, Any]],
              first_ms: int | None, last_ms: int | None) -> list[dict[str, Any]]:
    if not milestones:
        return []
    ordered = sorted(milestones, key=lambda m: m.get("when_ms") or 0)
    bounds: list[tuple[str, int | None, int | None]] = []
    start = first_ms
    for index, milestone in enumerate(ordered):
        bounds.append((milestone.get("title") or "阶段", start, milestone.get("when_ms")))
        start = milestone.get("when_ms")
    bounds.append(("当前", start, last_ms))
    phases: list[dict[str, Any]] = []
    for index, (name, begin, end) in enumerate(bounds):
        members = []
        for item in work_items:
            when = item.get("when_ms") or 0
            if (begin is None or when >= begin) and (end is None or when < end):
                members.append(item["id"])
        phases.append({
            "id": f"phase:{index}",
            "title": name,
            "start_ms": begin,
            "end_ms": end,
            "work_item_ids": members,
            "milestone_id": ordered[index]["id"] if index < len(ordered) else None,
        })
    return phases


def build_progress(canonical: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic spine for one canonical timeline."""
    sessions = canonical.get("sessions") or []
    events = canonical.get("events") or []
    repository = canonical.get("repository") or {}
    root = repository.get("root_path")
    children = _children_index(sessions)
    events_by_session = _events_by_session(events)
    main_sessions = [s for s in sessions if not s.get("parent_id")]
    commits = _commits(events)
    milestones = _milestones(commits)
    work_items = _work_items(main_sessions, children, events_by_session, commits, root)
    times = [e.get("occurred_at") for e in events if e.get("occurred_at")]
    first_ms = min(times) if times else None
    last_ms = max(times) if times else None
    phases = _phases(milestones, work_items, first_ms, last_ms)
    return {
        "schema_version": 1,
        "milestones": milestones,
        "work_items": work_items,
        "commits": commits,
        "phases": phases,
        "stats": {
            "work_items": len(work_items),
            "open_work_items": sum(1 for item in work_items if item.get("status") == "open"),
            "done_work_items": sum(1 for item in work_items if item.get("status") == "done"),
            "milestones": len(milestones),
            "commits": len(commits),
        },
    }


def main() -> None:
    import argparse
    import json
    from pathlib import Path

    from canonical_timeline import build_canonical_timeline

    ap = argparse.ArgumentParser(description="生成确定性的项目推进骨架")
    ap.add_argument("store")
    ap.add_argument("--workspace-id", default=None)
    ap.add_argument("--out", default="progress.json")
    args = ap.parse_args()
    canonical = build_canonical_timeline(args.store, args.workspace_id)
    result = build_progress(canonical)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": args.out, **result["stats"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
