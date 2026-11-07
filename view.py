#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a human-facing project view from the canonical event store.

The view is a projection, not a source of truth.  Facts are read from
sessionmap.db; semantic decisions are read from the existing timeline JSON
and receive heuristic evidence links back to the canonical events.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from canonical_timeline import build_canonical_timeline


def parse_time(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(dt.datetime.strptime(value, "%Y-%m-%d %H:%M").timestamp() * 1000)
    except Exception:
        return None


def fmt_ms(value: int | None, fmt: str = "%m-%d %H:%M") -> str:
    if not value:
        return "—"
    return dt.datetime.fromtimestamp(value / 1000).strftime(fmt)


def compact(text: Any, limit: int = 180) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def tokens(text: str) -> set[str]:
    text = str(text or "").lower()
    words = set(re.findall(r"[a-z0-9_./+-]{2,}", text))
    han = re.sub(r"[^\u3400-\u9fff]", "", text)
    words.update(han[i : i + 2] for i in range(max(0, len(han) - 1)))
    return words


def load_rows(con: sqlite3.Connection, query: str, args: tuple = ()) -> list[dict[str, Any]]:
    con.row_factory = sqlite3.Row
    return [dict(row) for row in con.execute(query, args).fetchall()]


def evidence_for(decision: dict[str, Any], events: list[dict[str, Any]], sessions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    when = parse_time(decision.get("when"))
    source_text = " ".join(str(decision.get(k) or "") for k in ("title", "what", "why", "evidence"))
    target = tokens(source_text)
    quoted: list[str] = []
    for match in re.findall(r"「([^」]+)」|“([^”]+)”|\"([^\"]+)\"", source_text):
        quoted.extend(" ".join(part.split()) for part in match if part)
    candidates = []
    for event in events:
        if event.get("event_type") not in ("user_message", "assistant_message", "tool_call", "tool_result",
                                            "file_change", "todo_change", "memory_break", "git_commit",
                                            "git_status", "error"):
            continue
        at = event.get("occurred_at") or 0
        distance = abs(at - when) if when else 10**18
        if when and distance > 12 * 3600 * 1000:
            continue
        text = event.get("evidence_text") or event.get("summary") or ""
        overlap = len(target & tokens(text))
        # Human intent is the strongest evidence for a decision.  Assistant
        # prose remains a fallback, because long research responses otherwise
        # crowd the drawer with model narration instead of the user's choice.
        actor_bonus = {"user_message": 6, "todo_change": 3, "file_change": 3,
                       "git_commit": 3, "git_status": 2, "error": 2,
                       "tool_call": 1, "tool_result": 1, "assistant_message": 0,
                       "memory_break": 0}.get(event.get("event_type"), 0)
        time_score = max(0.0, 2.0 - distance / (6 * 3600 * 1000)) if when else 0.0
        normalized_text = " ".join(str(text).split())
        exact_quote_bonus = max((18 for quote in quoted if quote and quote in normalized_text), default=0)
        score = overlap * 2 + actor_bonus + time_score + exact_quote_bonus
        candidates.append((score, overlap, distance, event))
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
    exact_matches = [item for item in candidates if any(
        quote and quote in " ".join(str(item[3].get("evidence_text") or item[3].get("summary") or "").split())
        for quote in quoted
    )]
    exact_matches.sort(key=lambda item: (
        -max((len(quote) for quote in quoted if quote and quote in " ".join(str(item[3].get("evidence_text") or item[3].get("summary") or "").split())), default=0),
        item[2],
    ))
    human = [item for item in candidates if item[3].get("event_type") == "user_message"]
    supporting = [item for item in candidates if item[3].get("event_type") in
                  ("tool_call", "file_change", "todo_change", "memory_break", "git_commit", "git_status", "error")]
    # If we found a human statement near the decision, use it as the primary
    # evidence set.  Supporting facts may add one extra item; assistant prose
    # is used only when no human statement can be matched.
    if exact_matches:
        candidates = exact_matches[:2] + [item for item in supporting[:1] if item not in exact_matches]
    elif human:
        candidates = human[:2] + supporting[:1]
    picked: list[dict[str, Any]] = []
    seen_sessions: set[str] = set()
    seen_evidence: set[str] = set()
    for score, overlap, _, event in candidates:
        sid = event.get("session_id") or ""
        try:
            payload = json.loads(event.get("payload_json") or "{}")
        except Exception:
            payload = event.get("payload") or {}
        evidence_key = " ".join(str(event.get("evidence_text") or event.get("summary") or "").split()).strip()
        if evidence_key in seen_evidence:
            continue
        # Keep more than one event from a session only when it improves the
        # evidence set substantially; this avoids a drawer full of duplicates.
        if sid in seen_sessions and len(picked) >= 1:
            continue
        seen_sessions.add(sid)
        seen_evidence.add(evidence_key)
        session = sessions.get(sid, {})
        picked.append({
            "event_id": event.get("event_id"),
            "session_id": sid,
            "session_title": session.get("title") or "未命名会话",
            "at": event.get("occurred_at"),
            "when": fmt_ms(event.get("occurred_at"), "%Y-%m-%d %H:%M"),
            "type": event.get("event_type"),
            "summary": compact(event.get("summary"), 240),
            "quote": compact(event.get("evidence_text") or event.get("summary"), 420),
            "confidence": round(min(0.99, 0.35 + score / 20), 2),
            "source_kind": event.get("source_kind"),
            "raw_ref": event.get("raw_ref"),
            "event_type": event.get("event_type"),
            "fact": {key: payload[key] for key in
                     ("tool", "command", "path", "filePath", "file_path", "status", "content", "sha", "subject")
                     if key in payload and payload[key] not in (None, "", [])},
        })
        if len(picked) >= 3:
            break
    return picked


def fact_decisions(events: list[dict[str, Any]], *, id_prefix: str = "fact:",
                   track: str = "事实", why: str = "来自统一事实层；尚未完成语义提炼") -> list[dict[str, Any]]:
    """Create a small honest projection from high-signal factual events."""
    priority = {"memory_break": 10, "error": 9, "git_commit": 8, "git_status": 7,
                "todo_change": 5, "user_message": 3}
    candidates = [event for event in events if event.get("event_type") in priority]
    candidates.sort(key=lambda event: (-priority[event.get("event_type")], event.get("occurred_at") or 0))
    picked = candidates[:12]
    picked.sort(key=lambda event: (event.get("occurred_at") or 0, event.get("event_id") or ""))
    result = []
    for event in picked:
        summary = compact(event.get("summary") or event.get("event_type"), 120)
        result.append({
            "id": f"{id_prefix}{event.get('event_id')}",
            "when": fmt_ms(event.get("occurred_at"), "%Y-%m-%d %H:%M"),
            "when_ms": event.get("occurred_at"),
            "title": summary,
            "track": track,
            "status": "open",
            "what": event.get("summary") or event.get("event_type"),
            "why": why,
            "evidence": event.get("evidence_text") or event.get("summary") or "",
            "evidence_refs": [{
                "event_id": event.get("event_id"), "session_id": event.get("session_id"),
                "session_title": event.get("session_title") or "未命名会话",
                "when": fmt_ms(event.get("occurred_at"), "%Y-%m-%d %H:%M"), "type": event.get("event_type"),
                "quote": compact(event.get("evidence_text") or event.get("summary"), 420),
                "confidence": 1.0, "source_kind": event.get("source_kind"),
                "raw_ref": event.get("raw_ref"), "event_type": event.get("event_type"),
                "fact": event.get("payload") or {},
            }],
            "evidence_count": 1,
        })
    return result


def small_events(events: list[dict[str, Any]], linked_event_ids: set[str], limit: int = 18) -> list[dict[str, Any]]:
    """Return a restrained factual supplement around semantic decision bones."""
    priority = {
        "memory_break": 10, "error": 9, "git_commit": 8, "subagent_event": 7,
        "todo_change": 6, "git_status": 5, "user_message": 3,
    }
    def is_harness_noise(event: dict[str, Any]) -> bool:
        if event.get("event_type") != "user_message":
            return False
        text = " ".join(str(event.get(key) or "") for key in ("summary", "evidence_text")).strip().lower()
        markers = (
            "<system-reminder>", "current runtime context", "time sampled",
            "browser time zone", "available skills", "workspace instructions",
            "model changed", "approval policy changed",
        )
        return any(marker in text for marker in markers)

    candidates = [event for event in events
                  if event.get("event_id") not in linked_event_ids
                  and event.get("event_type") in priority
                  and not is_harness_noise(event)]
    candidates.sort(key=lambda event: (event.get("occurred_at") or 0, event.get("event_id") or ""))
    # A merged workspace should show that several agents contributed.  Seed
    # one high-signal fact per source before filling the remaining slots by
    # time, while keeping the total small.
    source_seed: list[dict[str, Any]] = []
    for source in sorted({str(event.get("source_kind") or "") for event in candidates}):
        source_events = [event for event in candidates if str(event.get("source_kind") or "") == source]
        source_events.sort(key=lambda event: (-priority[event.get("event_type")], event.get("occurred_at") or 0,
                                               event.get("event_id") or ""))
        if source_events:
            source_seed.append(source_events[0])
    candidates = source_seed + [event for event in candidates if event not in source_seed]
    picked: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for event in candidates:
        summary = compact(event.get("summary") or event.get("event_type"), 140)
        # Consecutive identical todo/status facts add noise; keep one per
        # session and summary while preserving distinct turns and breaks.
        key = (str(event.get("source_kind") or ""), str(event.get("session_id") or ""), summary)
        if key in seen:
            continue
        seen.add(key)
        picked.append({
            "id": f"small:{event.get('event_id')}",
            "when": fmt_ms(event.get("occurred_at"), "%Y-%m-%d %H:%M"),
            "when_ms": event.get("occurred_at"),
            "title": summary,
            "track": "补充",
            "status": "fact",
            "what": summary,
            "why": "来自统一事实层；作为项目转折的上下文补充",
            "evidence": event.get("evidence_text") or summary,
            "small": True,
            "event_type": event.get("event_type"),
            "evidence_refs": [{
                "event_id": event.get("event_id"), "session_id": event.get("session_id"),
                "session_title": event.get("session_title") or "未命名会话",
                "when": fmt_ms(event.get("occurred_at"), "%Y-%m-%d %H:%M"), "type": event.get("event_type"),
                "quote": compact(event.get("evidence_text") or summary, 420),
                "confidence": 1.0, "source_kind": event.get("source_kind"),
                "raw_ref": event.get("raw_ref"), "event_type": event.get("event_type"),
                "fact": event.get("payload") or {},
            }],
            "evidence_count": 1,
        })
        if len(picked) >= limit:
            break
    return picked


def build_view(store_path: str, timeline_path: str | None = None, out_path: str | None = None,
               workspace_id: str | None = None, include_pending_facts: bool = False) -> dict[str, Any]:
    canonical = build_canonical_timeline(store_path, workspace_id)
    workspace = canonical["workspace"]
    sessions = canonical["sessions"]
    events = canonical["events"]
    repository = canonical["repository"]
    changes = canonical.get("changes", [])
    semantic_meta = canonical.get("semantic", {})
    session_map = {s["session_id"]: s for s in sessions}
    timeline = json.loads(Path(timeline_path).read_text(encoding="utf-8")) if timeline_path and Path(timeline_path).exists() else {}
    # The SQLite semantic projection is the display source once available.
    # The JSON file remains a compatibility fallback for older workspaces and
    # for the first facts-only run before a semantic pass exists.
    semantic_decisions = canonical["decisions"]
    decisions_source = "canonical.sqlite" if semantic_decisions else "timeline.json"
    decision_items = semantic_decisions or timeline.get("decisions", [])
    facts_only = not decision_items
    if facts_only:
        decision_items = fact_decisions(events)
        decisions_source = "facts-only"
    elif include_pending_facts:
        # A watcher can expose new high-signal facts immediately while the
        # last semantic run remains authoritative.  These nodes are explicitly
        # marked as pending and never pretend to be model decisions.
        linked_event_ids = {
            ref.get("event_id")
            for decision in semantic_decisions
            for ref in (decision.get("evidence_refs") or [])
            if ref.get("event_id")
        }
        cutoff = int(semantic_meta.get("completed_at") or 0)
        pending_events = [
            event for event in events
            if (event.get("occurred_at") or 0) > cutoff
            and event.get("event_id") not in linked_event_ids
        ]
        pending = fact_decisions(
            pending_events,
            id_prefix="pending:",
            track="待提炼",
            why="来自语义运行之后的统一事实；等待局部语义提炼",
        )
        if pending:
            decision_items = semantic_decisions + pending
            decisions_source = "canonical.sqlite+facts"

    linked_event_ids = {
        ref.get("event_id")
        for decision in semantic_decisions
        for ref in (decision.get("evidence_refs") or [])
        if ref.get("event_id")
    }
    supplement = small_events(events, linked_event_ids)

    by_type = Counter(event["event_type"] for event in events)
    times = [e["occurred_at"] for e in events if e.get("occurred_at")]
    t0, t1 = min(times) if times else None, max(times) if times else None
    span_h = ((t1 - t0) / 3600000) if t0 and t1 else 0
    bucket_count = max(1, math.ceil(span_h) + 1)
    buckets = [{"count": 0, "user": 0, "tool": 0, "todo": 0, "break": 0} for _ in range(bucket_count)]
    for event in events:
        if not event.get("occurred_at") or not t0:
            continue
        idx = min(bucket_count - 1, max(0, int((event["occurred_at"] - t0) / 3600000)))
        buckets[idx]["count"] += 1
        if event["event_type"] == "user_message":
            buckets[idx]["user"] += 1
        elif event["event_type"] == "tool_call":
            buckets[idx]["tool"] += 1
        elif event["event_type"] == "todo_change":
            buckets[idx]["todo"] += 1
        elif event["event_type"] == "memory_break":
            buckets[idx]["break"] += 1

    decisions = []
    for decision in decision_items:
        item = dict(decision)
        item["when_ms"] = item.get("when_ms") or parse_time(item.get("when"))
        if not item.get("evidence_refs"):
            item["evidence_refs"] = evidence_for(item, events, session_map)
        item["evidence_count"] = len(item["evidence_refs"])
        decisions.append(item)

    linked_decisions = sum(1 for item in decisions if item.get("evidence_count"))
    evidence_coverage = round(linked_decisions / len(decisions), 3) if decisions else 0

    tracks: dict[str, dict[str, Any]] = {}
    for item in decisions:
        track = item.get("track") or "其他"
        track_view = tracks.setdefault(track, {"name": track, "decisions": [], "open": 0, "done": 0, "abandoned": 0})
        track_view["decisions"].append(item["id"])
        status = item.get("status") if item.get("status") in ("open", "done", "abandoned") else "open"
        track_view[status] += 1
    track_order = sorted(tracks, key=lambda name: (name != "主干", name))
    for index, name in enumerate(track_order):
        tracks[name]["index"] = index
        tracks[name]["state"] = "abandoned" if tracks[name]["abandoned"] and not tracks[name]["open"] and not tracks[name]["done"] else ("open" if tracks[name]["open"] else "done")

    recent = []
    for event in reversed(events):
        if event["event_type"] not in ("user_message", "todo_change", "memory_break", "error"):
            continue
        session = session_map.get(event.get("session_id"), {})
        recent.append({
            "event_id": event["event_id"], "type": event["event_type"], "at": event.get("occurred_at"),
            "when": fmt_ms(event.get("occurred_at")), "summary": compact(event.get("summary"), 260),
            "session_title": session.get("title") or "未命名会话", "session_id": event.get("session_id"),
        })
        if len(recent) >= 8:
            break
    recent.reverse()

    breaks = []
    for event in events:
        if event.get("event_type") != "memory_break":
            continue
        payload = event.get("payload") or {}
        if not payload:
            payload = json.loads(event.get("payload_json") or "{}")
        breaks.append({
            "event_id": event.get("event_id"),
            "at": event.get("occurred_at"),
            "when": fmt_ms(event.get("occurred_at")),
            "loss_pct": payload.get("loss_pct"),
            "pre_tokens": payload.get("pre_tokens"),
            "post_tokens": payload.get("post_tokens"),
        })

    session_views = []
    for session in sessions:
        session_events = [e for e in events if e.get("session_id") == session["session_id"]]
        session_views.append({
            "id": session["session_id"], "title": session["title"] or "未命名会话", "parent_id": session["parent_id"],
            "started_at": session["started_at"], "updated_at": session["updated_at"],
            "when": fmt_ms(session["started_at"]), "event_count": len(session_events),
        })

    notes = canonical.get("notes") or {}
    unfinished_values = notes.get("unfinished") if semantic_decisions else timeline.get("unfinished", [])
    unfinished = []
    for index, text in enumerate(unfinished_values or [], 1):
        unfinished.append({"id": f"todo-{index}", "text": text, "status": "open"})

    recurring = notes.get("recurring") if semantic_decisions else timeline.get("recurring", [])
    lost_after_compaction = notes.get("lost_after_compaction") if semantic_decisions else timeline.get("lost_after_compaction")

    result = {
        "schema_version": 1,
        "generated_at": int(dt.datetime.now().timestamp() * 1000),
        "workspace": {"id": workspace["id"], "name": workspace["name"], "source": workspace["source"],
                      "source_path": workspace["source_path"]},
        "repository": repository,
        "stats": {"sessions": len(sessions), "main_sessions": sum(1 for s in sessions if not s["parent_id"]),
                  "child_sessions": sum(1 for s in sessions if s["parent_id"]), "events": len(events),
                  "user_messages": by_type["user_message"], "tool_calls": by_type["tool_call"],
                  "todo_changes": by_type["todo_change"], "memory_breaks": by_type["memory_break"],
                  "change_count": len(changes),
                  "span_hours": round(span_h, 1), "active_hours": sum(1 for b in buckets if b["count"]),
                  "first_at": t0, "last_at": t1, "first_when": fmt_ms(t0), "last_when": fmt_ms(t1)},
        "analysis": {"facts_version": semantic_meta.get("facts_version") or 1,
                     "semantic_source": decisions_source,
                     "semantic_run_id": semantic_meta.get("run_id"),
                     "semantic_model": semantic_meta.get("model"),
                     "semantic_prompt_version": semantic_meta.get("prompt_version"),
                     "semantic_completed_at": semantic_meta.get("completed_at"),
                     "affected_nodes": semantic_meta.get("affected_nodes"),
                     "previous_run_id": semantic_meta.get("previous_run_id"),
                     "pending_fact_count": sum(1 for item in decisions if str(item.get("id", "")).startswith("pending:")),
                     "decision_count": len(decisions), "linked_decisions": linked_decisions,
                     "evidence_coverage": evidence_coverage},
        "headline": timeline.get("headline") or ("事实已同步，等待项目语义提炼" if facts_only else "等待项目理解结果"),
        "story": timeline.get("story") or timeline.get("headline") or ("当前展示的是可回溯事实，完成语义提炼后会出现项目转折。" if facts_only else "项目正在形成可回溯的工作记忆。"),
        "recurring": recurring or [],
        "unfinished": unfinished,
        "lost_after_compaction": lost_after_compaction,
        "activity": {"start": t0, "buckets": buckets, "max": max((b["count"] for b in buckets), default=0)},
        "recent_changes": recent,
        "changes": changes,
        "breaks": breaks,
        "decisions": decisions,
        "small_events": supplement,
        "tracks": [tracks[name] for name in track_order],
        "sessions": session_views,
    }
    if out_path:
        Path(out_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="生成证据驱动的项目视图")
    ap.add_argument("store", help="sync.py 生成的 sessionmap.db")
    ap.add_argument("timeline", nargs="?", default=None, help="兼容旧流程的 timeline.json，可省略")
    ap.add_argument("--out", default="project-view.json")
    ap.add_argument("--workspace-id", default=None)
    ap.add_argument("--include-pending-facts", action="store_true",
                    help="在已有语义运行后显示等待提炼的新增高信号事实")
    args = ap.parse_args()
    result = build_view(args.store, args.timeline, args.out, args.workspace_id, args.include_pending_facts)
    print(json.dumps({"out": args.out, "decisions": len(result["decisions"]),
                      "evidence_links": sum(d["evidence_count"] for d in result["decisions"]),
                      "events": result["stats"]["events"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
