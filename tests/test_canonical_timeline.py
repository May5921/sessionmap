import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from canonical_timeline import build_canonical_timeline
from sync import SCHEMA
from view import build_view


class CanonicalTimelineTests(unittest.TestCase):
    def make_store(self, path: str) -> None:
        con = sqlite3.connect(path)
        con.executescript(SCHEMA)
        con.execute("INSERT INTO workspace(workspace_id,name,source_kind,source_path,updated_at) VALUES(?,?,?,?,?)",
                     ("w1", "alpha", "claude", "C:/alpha", 2))
        con.execute("INSERT INTO workspace(workspace_id,name,source_kind,source_path,updated_at) VALUES(?,?,?,?,?)",
                     ("w2", "beta", "zcode", "C:/beta", 1))
        con.execute("INSERT INTO session(session_id,workspace_id,source_kind,source_id,title,started_at) VALUES(?,?,?,?,?,?)",
                     ("s1", "w1", "claude", "s1", "first", 1000))
        con.execute("INSERT INTO event(event_id,workspace_id,session_id,source_kind,source_id,event_type,actor,occurred_at,summary,payload_json,evidence_text,raw_ref,fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("e1", "w1", "s1", "claude", "e1", "user_message", "user", 2000, "choose line", "{}", "choose line", "file:x", "f1", 1))
        con.execute("INSERT INTO analysis_run(run_id,workspace_id,semantic_source,input_hash,status,started_at,completed_at) VALUES(?,?,?,?,?,?,?)",
                     ("r1", "w1", "timeline.json", "hash", "succeeded", 1, 3))
        con.execute("INSERT INTO decision(decision_id,run_id,workspace_id,external_id,ordinal,occurred_at,title,track,what,why,status,source_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("r1:D1", "r1", "w1", "D1", 1, 2000, "Old", "主干", "old", "because", "open", json.dumps({"evidence": "choose line"}), 1, 1))
        con.execute("INSERT INTO evidence_link(link_id,decision_id,event_id,session_id,quote,confidence,rank,source_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     ("l1", "r1:D1", "e1", "s1", "choose line", .9, 1, json.dumps({"session_title": "first", "at": 2000}), 1))
        con.execute("INSERT INTO decision_override(decision_id,title,updated_at) VALUES(?,?,?)", ("r1:D1", "New", 4))
        con.commit()
        con.close()

    def test_latest_workspace_and_semantic_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "store.db")
            self.make_store(path)
            result = build_canonical_timeline(path)
            self.assertEqual(result["workspace"]["id"], "w1")
            self.assertEqual(result["semantic"]["mode"], "semantic")
            self.assertEqual(result["decisions"][0]["title"], "New")
            self.assertEqual(result["decisions"][0]["evidence_refs"][0]["quote"], "choose line")

    def test_workspace_selection_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "store.db")
            self.make_store(path)
            result = build_canonical_timeline(path, "w2")
            self.assertEqual(result["workspace"]["id"], "w2")
            self.assertEqual(result["semantic"]["mode"], "facts-only")

    def test_facts_only_view_still_has_inspectable_milestones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "store.db")
            self.make_store(path)
            con = sqlite3.connect(path)
            con.execute("DELETE FROM evidence_link")
            con.execute("DELETE FROM decision")
            con.execute("DELETE FROM analysis_run")
            con.commit()
            con.close()
            result = build_view(path)
            self.assertEqual(result["analysis"]["semantic_source"], "facts-only")
            self.assertEqual(result["decisions"][0]["track"], "事实")

    def test_change_projection_keeps_facts_and_status_reversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "store.db")
            self.make_store(path)
            con = sqlite3.connect(path)
            con.execute("INSERT INTO event(event_id,workspace_id,session_id,source_kind,source_id,event_type,actor,occurred_at,summary,payload_json,evidence_text,raw_ref,fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         ("todo-1", "w1", "s1", "claude", "todo-1", "todo_change", "system", 3000, "[open] follow up", json.dumps({"status": "open", "content": "follow up"}), "follow up", "todo:1", "f2", 1))
            con.execute("INSERT INTO analysis_run(run_id,workspace_id,semantic_source,input_hash,status,started_at,completed_at) VALUES(?,?,?,?,?,?,?)",
                         ("r2", "w1", "timeline.json", "hash2", "succeeded", 4, 5))
            con.execute("INSERT INTO decision(decision_id,run_id,workspace_id,external_id,ordinal,occurred_at,title,track,what,why,status,source_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         ("r2:D1", "r2", "w1", "D1", 1, 2000, "Old", "主干", "old", "because", "done", json.dumps({"evidence": "choose line"}), 4, 4))
            con.commit()
            con.close()
            result = build_canonical_timeline(path, "w1")
            kinds = {item["kind"] for item in result["changes"]}
            self.assertIn("todo", kinds)
            self.assertIn("status_reversal", kinds)


if __name__ == "__main__":
    unittest.main()
