import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from extract import build_incremental_prompt, merge_chunk_results, split_timeline, merge_incremental_result
from semantic import build_incremental_plan, sync_semantic
from sync import SCHEMA


class IncrementalExecutionTests(unittest.TestCase):
    def test_chunk_split_preserves_timeline_and_merge_relabels_ids(self):
        timeline = "# 工作区\n\n## 2026-01-01\n\n### 00:00 · A\n- 用户决定 A\n\n## 2026-01-02\n\n### 00:00 · B\n- 用户决定 B\n"
        chunks = split_timeline(timeline, 60)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual("".join(chunks), timeline)
        merged = merge_chunk_results([
            {"headline": "old", "decisions": [{"id": "D1", "when": "2026-01-01 00:00", "track": "主干", "title": "A", "evidence": "a"}], "recurring": ["r1"], "unfinished": ["u1"]},
            {"headline": "new", "decisions": [{"id": "D1", "when": "2026-01-01 00:00", "track": "主干", "title": "A", "evidence": "longer evidence"}, {"id": "D1", "when": "2026-01-02 00:00", "track": "主干", "title": "B", "evidence": "b"}], "recurring": ["r2"], "unfinished": ["u2"]},
        ])
        self.assertEqual([item["id"] for item in merged["decisions"]], ["D1", "D2"])
        self.assertEqual(merged["decisions"][0]["evidence"], "longer evidence")
        self.assertEqual(merged["chunked"]["chunks"], 2)

    def test_merge_preserves_unaffected_nodes_and_applies_patch(self):
        previous = {
            "headline": "old",
            "decisions": [
                {"id": "D1", "when": "2026-01-01 00:00", "title": "old one", "status": "open"},
                {"id": "D2", "when": "2026-01-02 00:00", "title": "keep", "status": "done"},
            ],
            "unfinished": ["old"],
        }
        plan = {"previous_run_id": "r1", "scope": {"decision_ids": ["D1"], "event_ids": ["e1"]}}
        patch = {
            "decisions": [{"id": "D1", "when": "2026-01-03 00:00", "title": "new one", "status": "done"},
                          {"id": "D3", "when": "2026-01-04 00:00", "title": "new", "status": "open"}],
            "removed_ids": [], "headline": "new headline", "unfinished": ["next"],
        }
        result = merge_incremental_result(previous, patch, plan)
        self.assertEqual([item["id"] for item in result["decisions"]], ["D2", "D1", "D3"])
        self.assertEqual(result["decisions"][0]["title"], "keep")
        self.assertEqual(result["headline"], "new headline")
        self.assertEqual(result["incremental"]["affected_decision_ids"], ["D1"])

    def test_plan_is_read_only_and_contains_only_changed_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store.db"
            timeline = root / "timeline.json"
            view = root / "view.json"
            con = sqlite3.connect(store)
            con.executescript(SCHEMA)
            con.execute("INSERT INTO workspace(workspace_id,name,source_kind,source_path,updated_at) VALUES(?,?,?,?,?)",
                        ("w1", "alpha", "test", "C:/alpha", 1))
            con.execute("INSERT INTO event(event_id,workspace_id,session_id,source_kind,source_id,event_type,actor,occurred_at,summary,payload_json,evidence_text,raw_ref,fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("e1", "w1", "s1", "test", "e1", "user_message", "user", 2, "old", "{}", "old", "raw", "f1", 1))
            con.commit(); con.close()
            timeline.write_text(json.dumps({"decisions": [{"id": "D1", "when": "1970-01-01 08:00", "title": "old", "status": "open"}]}), encoding="utf-8")
            view.write_text(json.dumps({"decisions": [{"id": "D1", "evidence_refs": [{"event_id": "e1", "session_id": "s1", "quote": "old"}]}]}), encoding="utf-8")
            self.assertEqual(build_incremental_plan(str(store), str(timeline), "w1")["mode"], "baseline")
            sync_semantic(str(store), str(timeline), str(view), workspace_id="w1")
            matched = build_incremental_plan(str(store), str(timeline), "w1")
            self.assertEqual(matched["mode"], "matched")
            con = sqlite3.connect(store)
            before = con.execute("SELECT count(*) FROM analysis_run").fetchone()[0]
            con.close()
            con = sqlite3.connect(store)
            con.execute("UPDATE event SET fingerprint=?,summary=? WHERE event_id='e1'", ("f2", "new"))
            con.commit(); con.close()
            plan = build_incremental_plan(str(store), str(timeline), "w1")
            con = sqlite3.connect(store)
            after = con.execute("SELECT count(*) FROM analysis_run").fetchone()[0]
            con.close()
            self.assertEqual(plan["mode"], "changed")
            self.assertEqual(plan["scope"]["event_ids"], ["e1"])
            self.assertEqual(plan["events"][0]["summary"], "new")
            self.assertEqual(before, after)
            self.assertIn("D1", build_incremental_prompt(json.loads(timeline.read_text()), plan))

    def test_pipeline_no_longer_exposes_incremental_or_chunked(self):
        # The incremental path is a performance optimisation for a scale we are
        # not at.  It stays out of the CLI until a real project needs it.
        source = (ROOT / "pipeline.py").read_text(encoding="utf-8")
        self.assertNotIn("--incremental", source)
        self.assertNotIn("--chunked", source)
        self.assertNotIn("incremental-from", source)
        self.assertIn("timeline_view.py", source)

    def test_extract_rejects_truncation_without_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            timeline = root / "timeline.md"
            output = root / "timeline.json"
            timeline.write_text("# 工作区\n\n" + ("事实 " * 40), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "extract.py"), str(timeline), "--max-chars", "20",
                 "--cli", "pi", "--out", str(output)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--chunked", result.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
