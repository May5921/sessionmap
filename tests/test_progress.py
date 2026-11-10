import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from progress import build_progress


def _event(eid, sid, etype, at, summary="", payload=None, source="zcode"):
    return {
        "event_id": eid, "session_id": sid, "event_type": etype,
        "occurred_at": at, "summary": summary, "payload": payload or {},
        "source_kind": source, "when": "", "session_title": "",
        "evidence_text": summary, "raw_ref": "",
    }


class ProgressTests(unittest.TestCase):
    def _canonical(self):
        sessions = [
            {"session_id": "s1", "parent_id": None, "title": "做登录功能",
             "started_at": 1000, "updated_at": 2000},
            {"session_id": "s1a", "parent_id": "s1", "title": "子任务",
             "started_at": 1100, "updated_at": 1900},
            {"session_id": "s2", "parent_id": None, "title": "修复登录 bug",
             "started_at": 3000, "updated_at": 4000},
            {"session_id": "s3", "parent_id": None, "title": "对话时无法切换工作区",
             "started_at": 6000, "updated_at": 7000},
        ]
        events = [
            _event("e1", "s1", "session_start", 1000),
            _event("e2", "s1", "session_end", 2000,
                   payload={"completed": True, "last_outcome": "completed"}),
            _event("e3", "s1a", "tool_call", 1500,
                   payload={"tool": "Edit", "input": {"file_path": "/repo/src/login.js"}}),
            _event("e4", "s2", "session_start", 3000),
            _event("e5", "s2", "session_end", 4000,
                   payload={"completed": True, "last_outcome": "completed"}),
            _event("e6", "s2", "git_commit", 3500,
                   payload={"sha": "abc123", "subject": "fix: 登录校验"}),
            _event("e7", None, "git_commit", 5000,
                   payload={"sha": "def456", "subject": "chore: 发布 v1.0.0"}),
            _event("e8", "s3", "session_start", 6000),
            _event("e9", "s3", "session_end", 7000,
                   payload={"completed": True, "last_outcome": "completed"}),
        ]
        return {"sessions": sessions, "events": events,
                "repository": {"root_path": "/repo"}}

    def test_work_items_come_from_main_sessions_with_files_and_status(self):
        result = build_progress(self._canonical())
        items = {item["session_id"]: item for item in result["work_items"]}
        self.assertEqual(set(items), {"s1", "s2", "s3"})
        self.assertEqual(items["s1"]["kind"], "feature")
        self.assertEqual(items["s1"]["status"], "done")
        self.assertEqual(items["s1"]["files"], ["src/login.js"])
        self.assertEqual(items["s1"]["child_count"], 1)
        # A bugfix is only done when a fix commit exists in its window.
        self.assertEqual(items["s2"]["kind"], "bugfix")
        self.assertEqual(items["s2"]["status"], "done")
        self.assertEqual(items["s3"]["status"], "open")
        self.assertGreaterEqual(items["s1"]["confidence"], 0.0)
        self.assertIn(items["s1"]["confidence_band"], {"high", "medium", "low"})

    def test_open_todos_keep_a_work_item_open(self):
        canonical = self._canonical()
        canonical["events"].append(
            _event("e10", "s1", "todo_change", 1800,
                   payload={"status": "in_progress", "content": "接入 OAuth"})
        )
        result = build_progress(canonical)
        item = {x["session_id"]: x for x in result["work_items"]}["s1"]
        self.assertEqual(item["status"], "open")
        self.assertEqual(item["open_todos"][0]["text"], "接入 OAuth")

    def test_release_commits_become_milestones_and_phases(self):
        result = build_progress(self._canonical())
        self.assertEqual([m["title"] for m in result["milestones"]], ["v1.0.0"])
        self.assertEqual(result["milestones"][0]["kind"], "release")
        self.assertEqual(result["stats"]["commits"], 2)
        phases = result["phases"]
        self.assertEqual([p["title"] for p in phases], ["v1.0.0", "当前"])
        # s1 and s2 landed before the release; the later bug report is current.
        self.assertEqual(len(phases[0]["work_item_ids"]), 2)
        self.assertEqual(len(phases[1]["work_item_ids"]), 1)

    def test_commits_outside_sessions_are_not_linked_to_work_items(self):
        result = build_progress(self._canonical())
        s1 = {x["session_id"]: x for x in result["work_items"]}["s1"]
        self.assertEqual(s1["commits"], [])
        # The release commit at 5000 is outside every effort window.
        self.assertEqual(result["commits"][-1]["title"], "chore: 发布 v1.0.0")


if __name__ == "__main__":
    unittest.main()
