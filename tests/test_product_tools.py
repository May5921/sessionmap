import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from compare import compare_views
from consistency import check_view
from override import set_override
from search import search_store
from privacy import make_share_view
from sync import SCHEMA
from view import build_view, small_events
from eventline_dashboard import render


class ProductToolTests(unittest.TestCase):
    def test_eventline_first_view_is_quiet_and_incremental_badge_is_muted(self):
        template = (ROOT / "eventline_dashboard.py").read_text(encoding="utf-8")
        self.assertIn("const fresh=new Set(previous?items.filter", template)
        self.assertIn(".recent{color:var(--muted)", template)
        self.assertNotIn(".event.new .copy{border-left:2px solid var(--rust)", template)
        self.assertIn('id="sourceFilter"', template)
        self.assertIn('id="toolFilter"', template)
        self.assertIn('id="stageToggle"', template)
        self.assertIn('class="ev-ref"', template)
        self.assertIn('curatedExtras=allExtras.filter', template)
        self.assertIn('attribution_score||0)>=0.55', template)

    def test_compare_reports_decision_fact_and_git_changes(self):
        before = {"workspace": {"id": "w"}, "generated_at": 1,
                  "decisions": [{"id": "D1", "title": "Old", "status": "open"}],
                  "changes": [{"id": "f1"}], "repository": {"head_sha": "a"}}
        after = {"workspace": {"id": "w"}, "generated_at": 2,
                 "decisions": [{"id": "D1", "title": "New", "status": "done"},
                                {"id": "D2", "title": "Added", "status": "open"}],
                 "changes": [{"id": "f1"}, {"id": "f2"}], "repository": {"head_sha": "b"}}
        result = compare_views(before, after)
        self.assertEqual(result["summary"]["decisions_added"], 1)
        self.assertEqual(result["summary"]["decisions_changed"], 1)
        self.assertEqual(result["summary"]["facts_added"], 1)
        self.assertEqual(result["repository"]["head_sha"]["after"], "b")

    def test_consistency_only_reports_explicit_missing_evidence(self):
        result = check_view({"workspace": {"id": "w"}, "decisions": [
            {"id": "D1", "title": "Done", "status": "done", "evidence_refs": []}
        ], "repository": {}})
        self.assertEqual(result["summary"]["error_count"], 1)
        self.assertEqual(result["warnings"][0]["kind"], "decision_without_evidence")

    def test_override_is_persisted_and_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "store.db")
            set_override(path, "r1:D1", {"title": "Corrected", "status": "done"})
            con = sqlite3.connect(path)
            row = con.execute("SELECT title,status FROM decision_override WHERE decision_id=?", ("r1:D1",)).fetchone()
            con.close()
            self.assertEqual(row, ("Corrected", "done"))

    def test_search_returns_ranked_canonical_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.db"
            con = sqlite3.connect(path)
            con.executescript("""
                CREATE TABLE event (event_id TEXT PRIMARY KEY, workspace_id TEXT, session_id TEXT,
                    source_kind TEXT, event_type TEXT, occurred_at INTEGER, summary TEXT,
                    evidence_text TEXT, raw_ref TEXT);
            """)
            con.execute("INSERT INTO event VALUES (?,?,?,?,?,?,?,?,?)",
                        ("e1", "w", "s", "pi", "user_message", 2, "切换工作区", "切换工作区", "raw"))
            con.commit()
            con.close()
            result = search_store(str(path), "工作区", "w")
            self.assertEqual(result[0]["event_id"], "e1")

    def test_privacy_view_removes_paths_and_identifiers(self):
        result = make_share_view({"workspace": {"source_path": r"C:\secret\repo", "id": "w"},
                                  "decisions": [{"quote": "read .env", "session_id": "s1"}]})
        self.assertTrue(result["privacy"]["redacted"])
        self.assertNotIn("source_path", result["workspace"])
        self.assertNotIn("session_id", result["decisions"][0])
        self.assertEqual(result["decisions"][0]["quote"], "<redacted>")

    def test_pending_facts_are_explicit_after_semantic_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.db"
            con = sqlite3.connect(path)
            con.executescript(SCHEMA)
            con.execute(
                "INSERT INTO workspace(workspace_id,name,source_kind,source_path,updated_at) VALUES(?,?,?,?,?)",
                ("w", "demo", "test", "C:/demo", 1),
            )
            con.execute(
                "INSERT INTO analysis_run(run_id,workspace_id,semantic_source,input_hash,status,started_at,completed_at) VALUES(?,?,?,?,?,?,?)",
                ("r1", "w", "timeline.json", "h1", "succeeded", 1, 1000),
            )
            con.execute(
                "INSERT INTO decision(decision_id,run_id,workspace_id,external_id,ordinal,occurred_at,title,status,source_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("r1:D1", "r1", "w", "D1", 1, 900, "已确认方向", "done", "{}", 1, 1),
            )
            con.execute(
                "INSERT INTO event(event_id,workspace_id,source_kind,source_id,event_type,actor,occurred_at,summary,payload_json,evidence_text,raw_ref,fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("e2", "w", "test", "e2", "git_commit", "git", 2000, "补上增量事实", "{}", "补上增量事实", "git:e2", "f2", 2),
            )
            con.commit()
            con.close()
            result = build_view(str(path), workspace_id="w", include_pending_facts=True)
            self.assertEqual(result["analysis"]["pending_fact_count"], 1)
            self.assertEqual(result["decisions"][-1]["id"], "pending:e2")
            self.assertEqual(result["decisions"][-1]["track"], "待提炼")
            self.assertNotIn("small:e2", {item["id"] for item in result["small_events"]})

    def test_small_events_keep_unlinked_high_signal_facts(self):
        events = [
            {"event_id": "e1", "event_type": "user_message", "occurred_at": 1,
             "summary": "用户补充方向", "evidence_text": "用户补充方向", "session_id": "s1"},
            {"event_id": "e2", "event_type": "tool_call", "occurred_at": 2,
             "summary": "read noisy", "evidence_text": "read noisy", "session_id": "s1"},
            {"event_id": "e3", "event_type": "error", "occurred_at": 3,
             "summary": "失败后重试", "evidence_text": "失败后重试", "session_id": "s2"},
        ]
        result = small_events(events, {"e3"})
        self.assertEqual([item["id"] for item in result], ["small:e1"])
        self.assertTrue(result[0]["small"])

    def test_small_event_attribution_explains_anchor(self):
        events = [{"event_id": "e1", "event_type": "user_message", "occurred_at": 1000,
                   "summary": "补充方向", "evidence_text": "补充方向", "session_id": "s1",
                   "source_kind": "pi"}]
        decisions = [{"id": "D1", "when_ms": 1000, "title": "方向", "what": "补充方向",
                      "why": "用户确认", "evidence_refs": [{"session_id": "s1", "source_kind": "pi"}]}]
        result = small_events(events, set(), decisions)
        self.assertEqual(result[0]["anchor_id"], "D1")
        self.assertGreaterEqual(result[0]["attribution_score"], 0.8)
        self.assertIn("同一会话", result[0]["attribution_reasons"])

    def test_weak_small_event_stays_unanchored(self):
        events = [{"event_id": "e1", "event_type": "error", "occurred_at": 1,
                   "summary": "无关失败", "evidence_text": "无关失败", "session_id": "s2",
                   "source_kind": "dsh"}]
        decisions = [{"id": "D1", "when_ms": 10**15, "title": "方向", "what": "确定路线",
                      "why": "用户确认", "evidence_refs": [{"session_id": "s1", "source_kind": "pi"}]}]
        result = small_events(events, set(), decisions)
        self.assertIsNone(result[0]["anchor_id"])
        self.assertLess(result[0]["attribution_score"], 0.55)

    def test_render_creates_local_evidence_viewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            view = root / "view.json"
            report = root / "report.html"
            view.write_text(json.dumps({"workspace": {"name": "demo", "source": "merged"},
                                        "stats": {}, "decisions": [], "small_events": []}), encoding="utf-8")
            render(str(view), str(report))
            self.assertTrue((root / "evidence.html").exists())
            self.assertIn("原始事实载荷", (root / "evidence.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
