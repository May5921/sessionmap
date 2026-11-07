#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persist the semantic projection of a timeline into sessionmap.db.

The canonical event store is factual and append-friendly.  This module keeps
the model-derived layer reproducible by recording the input hash, source
analysis, decisions, evidence links, notes, and a per-node refresh manifest.
The manifest is a cache/scope decision; it does not invoke a model itself.

Usage:
    python semantic.py sessionmap.db timeline.json --view project-view.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def now_ms() -> int:
    return int(dt.datetime.now().timestamp() * 1000)


def parse_when(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(dt.datetime.strptime(value, "%Y-%m-%d %H:%M").timestamp() * 1000)
    except Exception:
        return None


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stable_hash(value: Any) -> str:
    """Hash a JSON-compatible value without depending on key order.

    The semantic layer is derived data, so its cache keys must be stable across
    Python runs and SQLite row order.  Keep this helper small and explicit so
    the manifest never accidentally includes timestamps or other volatile
    fields.
    """
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_json(value: Any, default: Any) -> Any:
    """Parse persisted JSON while keeping older or malformed rows readable."""
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def compute_semantic_manifest(
    decisions: list[dict[str, Any]],
    view_decisions: dict[str, dict[str, Any]],
    fingerprints: dict[str, str],
    previous: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Plan the smallest semantic refresh set for the current decisions.

    A node is a decision plus its linked factual evidence.  Reusing a node is
    safe only when both the decision payload and every linked event fingerprint
    are unchanged.  This function is deliberately pure: callers can inspect
    or test the plan before persisting it or invoking a model.
    """
    previous = previous or {}
    rows: list[dict[str, Any]] = []
    current_ids: set[str] = set()
    for ordinal, item in enumerate(decisions, 1):
        external_id = str(item.get("id") or f"D{ordinal}")
        current_ids.add(external_id)
        refs = view_decisions.get(external_id, {}).get("evidence_refs", []) or []
        normalized_refs = []
        event_ids: list[str] = []
        event_hashes: dict[str, str] = {}
        for ref in refs:
            event_id = str(ref.get("event_id") or "")
            if event_id:
                event_ids.append(event_id)
                event_hashes[event_id] = fingerprints.get(event_id, "missing")
            normalized_refs.append({
                "event_id": event_id,
                "quote": ref.get("quote") or "",
                "confidence": ref.get("confidence"),
                "rank": ref.get("rank"),
            })
        event_ids = sorted(set(event_ids))
        event_hashes = {key: event_hashes[key] for key in event_ids}
        node_payload = {
            "external_id": external_id,
            "decision": {
                key: item.get(key)
                for key in ("when", "track", "stage", "title", "what", "why", "alternative", "status", "confidence", "evidence")
            },
            "evidence": sorted(normalized_refs,
                                key=lambda ref: (ref["event_id"], ref["quote"], ref["rank"] or 0)),
            "event_hashes": event_hashes,
        }
        node_hash = stable_hash(node_payload)
        old = previous.get(external_id)
        if not old:
            state, reason, affected = "refresh", "new_node", True
        elif old.get("node_hash") == node_hash:
            state, reason, affected = "reuse", "unchanged", False
        else:
            old_event_hashes = old.get("event_hashes") or {}
            changed_events = sorted(
                event_id for event_id in set(event_hashes) | set(old_event_hashes)
                if event_hashes.get(event_id) != old_event_hashes.get(event_id)
            )
            state, reason, affected = "refresh", (
                "evidence_changed" if changed_events else "node_changed"
            ), True
        rows.append({
            "external_id": external_id,
            "node_hash": node_hash,
            "event_ids": event_ids,
            "event_hashes": event_hashes,
            "state": state,
            "reason": reason,
            "affected": affected,
        })

    removed = []
    for external_id, old in previous.items():
        if external_id in current_ids:
            continue
        removed.append({
            "external_id": external_id,
            "node_hash": old.get("node_hash", ""),
            "event_ids": old.get("event_ids", []),
            "event_hashes": old.get("event_hashes", {}),
            "state": "removed",
            "reason": "missing_from_current_run",
            "affected": False,
        })
    rows.extend(removed)
    return {
        "nodes": rows,
        "current_count": len(decisions),
        "affected_count": sum(1 for row in rows if row["affected"]),
        "reused_count": sum(1 for row in rows if row["state"] == "reuse"),
        "removed_count": len(removed),
        "affected_ids": [row["external_id"] for row in rows if row["affected"]],
    }


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS analysis_run (
            run_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, semantic_source TEXT NOT NULL,
            input_hash TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', prompt_version TEXT NOT NULL DEFAULT '',
            facts_version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'succeeded',
            started_at INTEGER NOT NULL, completed_at INTEGER NOT NULL, summary_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(workspace_id, input_hash)
        );
        CREATE TABLE IF NOT EXISTS decision (
            decision_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
            external_id TEXT NOT NULL, ordinal INTEGER NOT NULL, occurred_at INTEGER,
            track TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', what TEXT NOT NULL DEFAULT '',
            why TEXT NOT NULL DEFAULT '', alternative TEXT, status TEXT NOT NULL DEFAULT 'open', confidence REAL,
            source_json TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            UNIQUE(run_id, external_id)
        );
        CREATE TABLE IF NOT EXISTS evidence_link (
            link_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL, event_id TEXT, session_id TEXT,
            quote TEXT NOT NULL DEFAULT '', confidence REAL, rank INTEGER NOT NULL DEFAULT 0,
            source_json TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL,
            UNIQUE(decision_id, event_id, quote)
        );
        CREATE TABLE IF NOT EXISTS decision_override (
            decision_id TEXT PRIMARY KEY, title TEXT, track TEXT, status TEXT, what TEXT, why TEXT,
            override_json TEXT NOT NULL DEFAULT '{}', updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS analysis_note (
            note_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, workspace_id TEXT NOT NULL, kind TEXT NOT NULL,
            content_json TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL,
            UNIQUE(run_id, kind)
        );
        -- A factual snapshot per semantic run makes the expensive part
        -- explainable: a later run can name the event fingerprints that
        -- actually changed instead of reprocessing the whole project blindly.
        CREATE TABLE IF NOT EXISTS analysis_event_snapshot (
            run_id TEXT NOT NULL, event_id TEXT NOT NULL, session_id TEXT,
            fingerprint TEXT NOT NULL, PRIMARY KEY(run_id, event_id)
        );
        CREATE INDEX IF NOT EXISTS idx_analysis_event_snapshot_event
            ON analysis_event_snapshot(event_id, run_id);
        CREATE TABLE IF NOT EXISTS analysis_node_manifest (
            run_id TEXT NOT NULL, workspace_id TEXT NOT NULL, external_id TEXT NOT NULL,
            node_hash TEXT NOT NULL, event_ids_json TEXT NOT NULL DEFAULT '[]',
            event_hashes_json TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL,
            reason TEXT NOT NULL, affected INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL, PRIMARY KEY(run_id, external_id)
        );
        CREATE INDEX IF NOT EXISTS idx_analysis_node_manifest_workspace
            ON analysis_node_manifest(workspace_id, external_id, run_id);
        CREATE INDEX IF NOT EXISTS idx_decision_workspace_time ON decision(workspace_id, occurred_at);
        CREATE INDEX IF NOT EXISTS idx_evidence_decision ON evidence_link(decision_id, rank);
        """
    )


def load_view(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def event_fingerprint_map(con: sqlite3.Connection, workspace_id: str) -> dict[str, tuple[str, str | None]]:
    """Return the current factual event snapshot for one workspace.

    The event table is append-friendly, but a sync may update an existing
    event's payload in place.  Comparing the stable event id and its
    fingerprint catches both new/deleted events and changed facts.
    """
    return {
        row["event_id"]: (row["fingerprint"], row["session_id"])
        for row in con.execute(
            "SELECT event_id,fingerprint,session_id FROM event WHERE workspace_id=?",
            (workspace_id,),
        )
    }


def previous_successful_run(con: sqlite3.Connection, workspace_id: str, run_id: str | None = None) -> sqlite3.Row | None:
    """Find the latest completed run before ``run_id`` (or the latest run)."""
    if run_id:
        return con.execute(
            "SELECT * FROM analysis_run WHERE workspace_id=? AND status='succeeded' AND run_id<>? "
            "ORDER BY completed_at DESC, run_id DESC LIMIT 1",
            (workspace_id, run_id),
        ).fetchone()
    return con.execute(
        "SELECT * FROM analysis_run WHERE workspace_id=? AND status='succeeded' "
        "ORDER BY completed_at DESC, run_id DESC LIMIT 1",
        (workspace_id,),
    ).fetchone()


def affected_nodes(
    con: sqlite3.Connection,
    workspace_id: str,
    previous_run_id: str | None,
    current_events: dict[str, tuple[str, str | None]] | None = None,
) -> dict[str, Any]:
    """Compute the smallest semantic scope touched by factual changes.

    ``analysis_event_snapshot`` is deliberately separate from model output.
    It lets callers ask for a scoped re-refinement before invoking a model and
    gives the UI an honest explanation of why a node was reconsidered.  A
    missing snapshot is reported as ``unavailable`` instead of pretending the
    whole workspace is affected.
    """
    if not previous_run_id:
        return {"comparison": "baseline", "event_ids": [], "session_ids": [], "decision_ids": []}
    current_events = current_events or event_fingerprint_map(con, workspace_id)
    old_rows = con.execute(
        "SELECT event_id,session_id,fingerprint FROM analysis_event_snapshot WHERE run_id=?",
        (previous_run_id,),
    ).fetchall()
    if not old_rows:
        return {"comparison": "unavailable", "event_ids": [], "session_ids": [], "decision_ids": []}
    old = {row["event_id"]: (row["fingerprint"], row["session_id"]) for row in old_rows}
    changed = sorted(
        event_id for event_id in set(old) | set(current_events)
        if old.get(event_id) != current_events.get(event_id)
    )
    sessions = sorted({old.get(event_id, current_events.get(event_id, ("", None)))[1]
                       for event_id in changed if old.get(event_id, current_events.get(event_id, ("", None)))[1]})
    if not changed:
        return {"comparison": "matched", "event_ids": [], "session_ids": [], "decision_ids": []}
    placeholders = ",".join("?" for _ in changed)
    decision_rows = con.execute(
        "SELECT DISTINCT d.external_id FROM decision d JOIN evidence_link l ON l.decision_id=d.decision_id "
        "WHERE d.run_id=? AND (l.event_id IN (%s) OR l.session_id IN (%s)) ORDER BY d.ordinal"
        % (placeholders, ",".join("?" for _ in sessions) if sessions else "NULL"),
        (previous_run_id, *changed, *sessions),
    ).fetchall()
    return {
        "comparison": "changed",
        "event_ids": changed,
        "session_ids": sessions,
        "decision_ids": [row[0] for row in decision_rows],
    }


def load_node_manifest(con: sqlite3.Connection, run_id: str | None) -> dict[str, dict[str, Any]]:
    """Load a prior node manifest, tolerating stores created before the table."""
    if not run_id:
        return {}
    try:
        rows = con.execute(
            "SELECT external_id,node_hash,event_ids_json,event_hashes_json,state,reason,affected "
            "FROM analysis_node_manifest WHERE run_id=?", (run_id,)
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    result = {}
    for row in rows:
        result[row["external_id"]] = {
            "node_hash": row["node_hash"],
            "event_ids": parse_json(row["event_ids_json"], []),
            "event_hashes": parse_json(row["event_hashes_json"], {}),
            "state": row["state"], "reason": row["reason"],
            "affected": bool(row["affected"]),
        }
    return result


def build_incremental_plan(store_path: str, timeline_path: str | None = None,
                           workspace_id: str | None = None,
                           max_events: int = 240) -> dict[str, Any]:
    """Build a read-only package for a scoped semantic refresh.

    This is intentionally separate from :func:`sync_semantic`: planning must
    happen before a new semantic run stores its event snapshot, otherwise the
    newly arrived facts would look unchanged.  The package contains only the
    changed evidence and the prior decision context needed by a local CLI.
    It never claims that a model was called.
    """
    con = sqlite3.connect(store_path)
    con.row_factory = sqlite3.Row
    try:
        if workspace_id:
            workspace = con.execute(
                "SELECT * FROM workspace WHERE workspace_id=?", (workspace_id,)
            ).fetchone()
        else:
            workspace = con.execute(
                "SELECT * FROM workspace ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        if not workspace:
            raise SystemExit("sessionmap.db 里还没有匹配的工作区")
        wid = workspace["workspace_id"]
        previous = previous_successful_run(con, wid)
        if not previous:
            return {
                "schema_version": 1, "mode": "baseline", "workspace_id": wid,
                "previous_run_id": None,
                "scope": {"comparison": "baseline", "event_ids": [],
                           "session_ids": [], "decision_ids": []},
                "events": [], "decisions": [], "decision_catalog": [],
                "limits": {"max_events": max_events, "truncated": False},
            }
        current_events = event_fingerprint_map(con, wid)
        try:
            scope = affected_nodes(con, wid, previous["run_id"], current_events)
        except sqlite3.OperationalError:
            scope = {"comparison": "unavailable", "event_ids": [],
                     "session_ids": [], "decision_ids": []}
        mode = scope.get("comparison")
        if mode not in {"changed", "matched"}:
            mode = "unavailable"

        changed_ids = list(scope.get("event_ids") or [])
        session_ids = list(scope.get("session_ids") or [])
        events: list[dict[str, Any]] = []
        if changed_ids or session_ids:
            event_parts: list[str] = []
            params: list[Any] = [wid]
            if changed_ids:
                event_parts.append("event_id IN (%s)" % ",".join("?" for _ in changed_ids))
                params.extend(changed_ids)
            if session_ids:
                event_parts.append("session_id IN (%s)" % ",".join("?" for _ in session_ids))
                params.extend(session_ids)
            query = (
                "SELECT event_id,session_id,source_kind,event_type,actor,occurred_at,"
                "summary,evidence_text,raw_ref,payload_json FROM event "
                "WHERE workspace_id=? AND (" + " OR ".join(event_parts) + ") "
                "ORDER BY occurred_at,event_id LIMIT ?"
            )
            params.append(max(1, int(max_events)))
            rows = con.execute(query, params).fetchall()
            for row in rows:
                item = dict(row)
                item["payload"] = parse_json(item.pop("payload_json", "{}"), {})
                events.append(item)
        decisions: list[dict[str, Any]] = []
        decision_ids = list(scope.get("decision_ids") or [])
        if decision_ids:
            placeholders = ",".join("?" for _ in decision_ids)
            rows = con.execute(
                "SELECT external_id,ordinal,occurred_at,track,title,what,why,alternative,status,"
                "confidence,source_json FROM decision WHERE run_id=? AND external_id IN (" + placeholders + ") "
                "ORDER BY ordinal", (previous["run_id"], *decision_ids)
            ).fetchall()
            for row in rows:
                item = dict(row)
                item["source"] = parse_json(item.pop("source_json", "{}"), {})
                decisions.append(item)
        catalog = [dict(row) for row in con.execute(
            "SELECT external_id,track,title,status FROM decision WHERE run_id=? ORDER BY ordinal",
            (previous["run_id"],)
        )]
        return {
            "schema_version": 1,
            "mode": mode,
            "workspace_id": wid,
            "workspace": {"name": workspace["name"], "source_kind": workspace["source_kind"]},
            "timeline_path": str(timeline_path or ""),
            "previous_run_id": previous["run_id"],
            "scope": scope,
            "events": events,
            "decisions": decisions,
            "decision_catalog": catalog,
            "limits": {"max_events": max_events, "truncated": len(events) >= max(1, int(max_events))},
        }
    finally:
        con.close()


def sync_semantic(store_path: str, timeline_path: str, view_path: str | None = None,
                  model: str = "local-cli", prompt_version: str = "timeline-v1",
                  workspace_id: str | None = None) -> dict[str, Any]:
    timeline_file = Path(timeline_path)
    timeline = json.loads(timeline_file.read_text(encoding="utf-8"))
    view = load_view(Path(view_path) if view_path else None)
    con = sqlite3.connect(store_path)
    con.row_factory = sqlite3.Row
    ensure_schema(con)
    if workspace_id:
        workspace = con.execute("SELECT workspace_id FROM workspace WHERE workspace_id=?", (workspace_id,)).fetchone()
    else:
        workspace = con.execute("SELECT workspace_id FROM workspace ORDER BY updated_at DESC LIMIT 1").fetchone()
    if not workspace:
        raise SystemExit("sessionmap.db 里还没有工作区，请先运行 sync.py")
    workspace_id = workspace["workspace_id"]
    input_hash = sha256(timeline_file)
    run_id = f"semantic:{workspace_id}:{input_hash[:20]}"
    previous = previous_successful_run(con, workspace_id, run_id)
    # Re-running the same input reuses its own persisted snapshot.  Without
    # this fallback every idempotent refresh would look like a new baseline
    # because the deterministic run_id is excluded by the query above.
    if previous is None:
        previous = con.execute(
            "SELECT * FROM analysis_run WHERE workspace_id=? AND run_id=? AND status='succeeded'",
            (workspace_id, run_id),
        ).fetchone()
    current_events = event_fingerprint_map(con, workspace_id)
    scope = affected_nodes(con, workspace_id, previous["run_id"] if previous else None, current_events)
    started = now_ms()
    con.execute(
        """INSERT INTO analysis_run(run_id,workspace_id,semantic_source,input_hash,model,prompt_version,
           facts_version,status,started_at,completed_at,summary_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(run_id) DO UPDATE SET completed_at=excluded.completed_at,
           summary_json=excluded.summary_json,status=excluded.status""",
        (run_id, workspace_id, str(timeline_file), input_hash, model, prompt_version, 1,
         "succeeded", started, started, "{}"),
    )

    view_decisions = {d.get("id"): d for d in view.get("decisions", [])}
    decisions = timeline.get("decisions", [])
    previous_manifest = load_node_manifest(con, previous["run_id"] if previous else None)
    node_manifest = compute_semantic_manifest(decisions, view_decisions,
                                              {event_id: fingerprint for event_id, (fingerprint, _session_id) in current_events.items()},
                                              previous_manifest)
    if previous and not previous_manifest:
        manifest_comparison = "unavailable"
    elif not previous:
        manifest_comparison = "baseline"
    else:
        manifest_comparison = "changed" if node_manifest["affected_count"] else "matched"
    # Event snapshots identify changed facts; node manifests add semantic
    # changes (new/edited decisions) that have no new event yet.
    scope["decision_ids"] = sorted(set(scope.get("decision_ids", [])) |
                                    set(node_manifest["affected_ids"]))
    if manifest_comparison == "changed" and scope.get("comparison") == "matched":
        scope["comparison"] = "changed"
    scope["node_manifest"] = {
        "comparison": manifest_comparison,
        "current_count": node_manifest["current_count"],
        "affected_count": node_manifest["affected_count"],
        "reused_count": node_manifest["reused_count"],
        "removed_count": node_manifest["removed_count"],
        "affected_ids": node_manifest["affected_ids"],
    }
    for ordinal, item in enumerate(decisions, 1):
        external_id = str(item.get("id") or f"D{ordinal}")
        decision_id = f"{run_id}:{external_id}"
        source = json.dumps(item, ensure_ascii=False, sort_keys=True)
        refs = view_decisions.get(external_id, {}).get("evidence_refs", [])
        confidence = item.get("confidence")
        if confidence is None and refs:
            values = [float(ref.get("confidence")) for ref in refs if ref.get("confidence") is not None]
            confidence = round(sum(values) / len(values), 2) if values else None
        con.execute(
            """INSERT INTO decision(decision_id,run_id,workspace_id,external_id,ordinal,occurred_at,track,title,
               what,why,alternative,status,confidence,source_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(decision_id) DO UPDATE SET ordinal=excluded.ordinal,occurred_at=excluded.occurred_at,
               track=excluded.track,title=excluded.title,what=excluded.what,why=excluded.why,
               alternative=excluded.alternative,status=excluded.status,confidence=excluded.confidence,
               source_json=excluded.source_json,
               updated_at=excluded.updated_at""",
            (decision_id, run_id, workspace_id, external_id, ordinal, parse_when(item.get("when")),
             item.get("track") or "", item.get("title") or "", item.get("what") or "", item.get("why") or "",
             item.get("alternative"), item.get("status") or "open", confidence, source, started, started),
        )
        # Evidence is a projection of the current view.  Remove stale links
        # before inserting the current ranked set so re-running after an
        # evidence matcher change remains idempotent.
        con.execute("DELETE FROM evidence_link WHERE decision_id=?", (decision_id,))
        for rank, ref in enumerate(refs, 1):
            event_id = ref.get("event_id")
            quote = ref.get("quote") or ""
            link_id = hashlib.sha256(f"{decision_id}\x1f{event_id}\x1f{quote}".encode("utf-8")).hexdigest()
            con.execute(
                """INSERT INTO evidence_link(link_id,decision_id,event_id,session_id,quote,confidence,rank,
                   source_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(link_id) DO UPDATE SET confidence=excluded.confidence,rank=excluded.rank,
                   source_json=excluded.source_json""",
                (link_id, decision_id, event_id, ref.get("session_id"), quote, ref.get("confidence"), rank,
                 json.dumps(ref, ensure_ascii=False), started),
            )

    notes = {
        "recurring": timeline.get("recurring", []),
        "unfinished": timeline.get("unfinished", []),
        "lost_after_compaction": timeline.get("lost_after_compaction"),
    }
    for kind, content in notes.items():
        note_id = f"{run_id}:{kind}"
        con.execute(
            """INSERT INTO analysis_note(note_id,run_id,workspace_id,kind,content_json,created_at)
               VALUES(?,?,?,?,?,?) ON CONFLICT(note_id) DO UPDATE SET content_json=excluded.content_json""",
            (note_id, run_id, workspace_id, kind, json.dumps(content, ensure_ascii=False), started),
        )
    # Persist the factual snapshot used to derive ``scope``.  This is cheap
    # compared with another model call and makes the next refresh auditable.
    con.execute("DELETE FROM analysis_event_snapshot WHERE run_id=?", (run_id,))
    con.executemany(
        "INSERT INTO analysis_event_snapshot(run_id,event_id,session_id,fingerprint) VALUES(?,?,?,?)",
        [(run_id, event_id, session_id, fingerprint)
         for event_id, (fingerprint, session_id) in current_events.items()],
    )
    con.execute("DELETE FROM analysis_node_manifest WHERE run_id=?", (run_id,))
    con.executemany(
        """INSERT INTO analysis_node_manifest(
           run_id,workspace_id,external_id,node_hash,event_ids_json,event_hashes_json,
           state,reason,affected,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        [(run_id, workspace_id, row["external_id"], row["node_hash"],
          json.dumps(row["event_ids"], ensure_ascii=False),
          json.dumps(row["event_hashes"], ensure_ascii=False, sort_keys=True),
          row["state"], row["reason"], int(row["affected"]), started)
         for row in node_manifest["nodes"]],
    )
    evidence_count = sum(len(view_decisions.get(d.get("id"), {}).get("evidence_refs", [])) for d in decisions)
    summary = {
        "decisions": len(decisions), "evidence_links": evidence_count,
        "notes": len(notes), "input_hash": input_hash,
        "affected_nodes": scope,
        "node_manifest": scope["node_manifest"],
        "previous_run_id": previous["run_id"] if previous else None,
    }
    con.execute("UPDATE analysis_run SET summary_json=?, completed_at=? WHERE run_id=?",
                (json.dumps(summary, ensure_ascii=False), now_ms(), run_id))
    con.commit()
    counts = {
        "run_id": run_id,
        "workspace_id": workspace_id,
        "decisions": con.execute("SELECT count(*) FROM decision WHERE run_id=?", (run_id,)).fetchone()[0],
        "evidence_links": con.execute("SELECT count(*) FROM evidence_link WHERE decision_id LIKE ?", (run_id + ":%",)).fetchone()[0],
        "notes": con.execute("SELECT count(*) FROM analysis_note WHERE run_id=?", (run_id,)).fetchone()[0],
        "affected_nodes": scope,
        "node_manifest": scope["node_manifest"],
    }
    con.close()
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description="把 timeline.json 持久化到 sessionmap 的语义层")
    ap.add_argument("store")
    ap.add_argument("timeline")
    ap.add_argument("--plan", action="store_true",
                    help="只生成受影响节点的增量语义包，不写入分析运行")
    ap.add_argument("--plan-out", default=None,
                    help="增量语义包输出路径；默认写到 timeline 同目录")
    ap.add_argument("--view", default=None, help="可选 project-view.json，用于写入证据链接")
    ap.add_argument("--model", default="local-cli")
    ap.add_argument("--prompt-version", default="timeline-v1")
    ap.add_argument("--workspace-id", default=None)
    args = ap.parse_args()
    if args.plan:
        result = build_incremental_plan(args.store, args.timeline, args.workspace_id)
        output = Path(args.plan_out) if args.plan_out else Path(args.timeline).with_name("semantic-incremental-plan.json")
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"plan": str(output), "mode": result.get("mode"),
                          "events": len(result.get("events", [])),
                          "decision_ids": result.get("scope", {}).get("decision_ids", [])},
                         ensure_ascii=False, indent=2))
        return
    print(json.dumps(sync_semantic(args.store, args.timeline, args.view, args.model, args.prompt_version,
                                   args.workspace_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
