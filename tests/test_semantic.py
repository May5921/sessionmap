import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from semantic import compute_semantic_manifest, ensure_schema, sync_semantic
from sync import SCHEMA


class SemanticManifestTests(unittest.TestCase):
    def test_pure_manifest_tracks_new_reused_and_changed_nodes(self) -> None:
        decisions = [{"id": "D1", "title": "选择主线", "status": "open"}]
        view = {"D1": {"evidence_refs": [{"event_id": "e1", "quote": "选择主线", "confidence": .9}]}}
        first = compute_semantic_manifest(decisions, view, {"e1": "f1"})
        self.assertEqual(first["affected_ids"], ["D1"])
        self.assertEqual(first["nodes"][0]["reason"], "new_node")

        previous = {row["external_id"]: row for row in first["nodes"]}
        matched = compute_semantic_manifest(decisions, view, {"e1": "f1"}, previous)
        self.assertEqual(matched["affected_count"], 0)
        self.assertEqual(matched["reused_count"], 1)
        self.assertEqual(matched["nodes"][0]["reason"], "unchanged")

        changed = compute_semantic_manifest(decisions, view, {"e1": "f2"}, previous)
        self.assertEqual(changed["affected_ids"], ["D1"])
        self.assertEqual(changed["nodes"][0]["reason"], "evidence_changed")

    def test_sync_persists_baseline_match_and_fact_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store.db"
            timeline = root / "timeline.json"
            view = root / "view.json"
            con = sqlite3.connect(store)
            con.executescript(SCHEMA)
            con.execute(
                "INSERT INTO workspace(workspace_id,name,source_kind,source_path,updated_at) VALUES(?,?,?,?,?)",
                ("w1", "alpha", "test", "C:/alpha", 1),
            )
            con.execute(
                "INSERT INTO session(session_id,workspace_id,source_kind,source_id,title,started_at) VALUES(?,?,?,?,?,?)",
                ("s1", "w1", "test", "s1", "first", 1),
            )
            con.execute(
                "INSERT INTO event(event_id,workspace_id,session_id,source_kind,source_id,event_type,actor,occurred_at,summary,payload_json,evidence_text,raw_ref,fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("e1", "w1", "s1", "test", "e1", "user_message", "user", 2,
                 "选择主线", "{}", "选择主线", "raw", "f1", 1),
            )
            con.commit()
            con.close()

            def write_timeline(title: str) -> None:
                timeline.write_text(json.dumps({"decisions": [{
                    "id": "D1", "when": "1970-01-01 08:00", "track": "主干",
                    "title": title, "what": "选择主线", "why": "依据", "status": "open",
                }]}), encoding="utf-8")
                view.write_text(json.dumps({"decisions": [{
                    "id": "D1", "evidence_refs": [{"event_id": "e1", "quote": "选择主线", "confidence": .9}],
                }]}), encoding="utf-8")

            write_timeline("选择主线")
            first = sync_semantic(str(store), str(timeline), str(view), workspace_id="w1")
            self.assertEqual(first["node_manifest"]["comparison"], "baseline")
            self.assertEqual(first["node_manifest"]["affected_ids"], ["D1"])

            same = sync_semantic(str(store), str(timeline), str(view), workspace_id="w1")
            self.assertEqual(same["node_manifest"]["comparison"], "matched")
            self.assertEqual(same["node_manifest"]["affected_count"], 0)

            con = sqlite3.connect(store)
            con.execute("UPDATE event SET fingerprint=? WHERE event_id=?", ("f2", "e1"))
            con.commit()
            con.close()
            write_timeline("选择另一条主线")
            changed = sync_semantic(str(store), str(timeline), str(view), workspace_id="w1")
            self.assertEqual(changed["node_manifest"]["comparison"], "changed")
            self.assertEqual(changed["node_manifest"]["affected_ids"], ["D1"])
            self.assertEqual(changed["affected_nodes"]["comparison"], "changed")

            con = sqlite3.connect(store)
            snapshots = con.execute(
                "SELECT fingerprint FROM analysis_event_snapshot WHERE run_id=?", (changed["run_id"],)
            ).fetchone()
            manifests = con.execute(
                "SELECT state,reason,affected FROM analysis_node_manifest WHERE run_id=?",
                (changed["run_id"],),
            ).fetchone()
            con.close()
            self.assertEqual(snapshots[0], "f2")
            self.assertEqual(manifests, ("refresh", "evidence_changed", 1))


if __name__ == "__main__":
    unittest.main()
