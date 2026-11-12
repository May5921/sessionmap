import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_memory import (
    active_memories,
    ensure_memory_schema,
    record_claim,
    search_memory,
)
from sm import brief
from sync import SCHEMA


def insert_event(con, event_id, workspace_id, session_id, event_type, occurred_at, summary,
                 payload=None, source_kind="test"):
    con.execute(
        """INSERT INTO event(event_id,workspace_id,session_id,source_kind,source_id,event_type,actor,
                             occurred_at,summary,payload_json,evidence_text,raw_ref,fingerprint,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (event_id, workspace_id, session_id, source_kind, event_id, event_type, "user",
         occurred_at, summary, json.dumps(payload or {}, ensure_ascii=False), summary,
         "test:" + event_id, "fp-" + event_id, occurred_at),
    )


class MemorySchemaTests(unittest.TestCase):
    def test_schema_has_memory_and_fts5_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.db"
            con = sqlite3.connect(path)
            con.executescript(SCHEMA)
            schema_tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("memory_fts", schema_tables)
            ensure_memory_schema(con)
            columns = {row[1] for row in con.execute("PRAGMA table_info(memory)")}
            self.assertEqual(columns, {
                "id", "project", "claim_key", "content", "evidence_refs",
                "source_adapter", "created_at", "superseded_by", "status",
            })
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("memory_fts", tables)
            con.close()

            con = sqlite3.connect(path)
            record_claim(con, project="alpha", claim_key="k1", content="fix(login): clarify worker entry",
                         evidence_refs=[{"event_id": "e1", "session_id": "s1"}], source_adapter="test")
            con.commit()
            self.assertEqual(len(search_memory(con, "alpha", "clarify")), 1)
            con.close()


class SupersedeTests(unittest.TestCase):
    def test_new_claim_supersedes_old_and_same_content_reuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = sqlite3.connect(Path(tmp) / "store.db")
            first = record_claim(con, project="alpha", claim_key="same-key", content="旧结论",
                                 evidence_refs=[{"event_id": "e1"}], source_adapter="test", created_at=1)
            second = record_claim(con, project="alpha", claim_key="same-key", content="新结论",
                                  evidence_refs=[{"event_id": "e2"}], source_adapter="test", created_at=2)
            third = record_claim(con, project="alpha", claim_key="same-key", content="新结论",
                                 evidence_refs=[{"event_id": "e3"}], source_adapter="test", created_at=3)
            self.assertIsNone(first["superseded"])
            self.assertEqual(second["superseded"], first["id"])
            self.assertTrue(third["reused"])
            self.assertEqual(third["id"], second["id"])

            rows = {row["id"]: dict(row) for row in con.execute("SELECT * FROM memory")}
            self.assertEqual(rows[first["id"]]["status"], "superseded")
            self.assertEqual(rows[first["id"]]["superseded_by"], second["id"])
            self.assertEqual(rows[second["id"]]["status"], "active")
            self.assertEqual([row["content"] for row in active_memories(con, "alpha")], ["新结论"])
            con.close()


class BriefTests(unittest.TestCase):
    def make_store(self, path: Path) -> None:
        base = 1_800_000_000_000
        con = sqlite3.connect(path)
        con.executescript(SCHEMA)
        con.execute(
            "INSERT INTO workspace(workspace_id,name,source_kind,source_path,updated_at) VALUES(?,?,?,?,?)",
            ("w1", "alpha", "claude", "C:/alpha", base),
        )
        con.execute(
            "INSERT INTO session(session_id,workspace_id,source_kind,source_id,title,started_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("s1", "w1", "claude", "s1", "alpha session", base - 1000, base),
        )
        insert_event(con, "e1", "w1", "s1", "user_message", base - 60_000,
                     "登录页面有歧义，请统一角色入口和文案")
        insert_event(con, "e2", "w1", "s1", "user_message", base - 120_000,
                     "首页精选内容需要补上，顺便检查其他页面")
        insert_event(con, "e3", "w1", "s1", "git_commit", base - 180_000,
                     "fix(login): clarify worker entry",
                     {"sha": "abc123def456", "subject": "fix(login): clarify worker entry"})
        insert_event(con, "e4", "w1", "s1", "todo_change", base - 240_000,
                     "[pending] 待定：隐私指引是否重提", {"content": "待定：隐私指引是否重提", "status": "pending"})
        insert_event(con, "e5", "w1", "s1", "todo_change", base - 300_000,
                     "[completed] 已核对云端 8 个云函数一致",
                     {"content": "已核对云端 8 个云函数一致", "status": "completed"})
        con.commit()
        con.close()

    def test_brief_has_three_sections_citations_and_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.db"
            self.make_store(path)
            result = brief("alpha", str(path), persist_memory=True)
            text = result["brief"]
            self.assertLessEqual(len(text), 600)
            self.assertLessEqual(result["chars"], 600)
            for heading in ("在做什么", "已确定决策", "卡住的问题"):
                self.assertIn(heading, text)
            bullet_lines = [line for line in text.splitlines() if line.startswith("- ")]
            self.assertGreaterEqual(len(bullet_lines), 3)
            for line in bullet_lines:
                if "暂无可证实的结论" not in line:
                    self.assertIn("（来源：", line)
            self.assertTrue(result["claims"])
            for claim in result["claims"]:
                self.assertTrue(claim["evidence_refs"])
                self.assertTrue(claim["evidence_refs"][0].get("event_id") or
                                claim["evidence_refs"][0].get("session_id"))
            self.assertTrue(any(item["reused"] is False for item in result["memory"]))

            con = sqlite3.connect(path)
            self.assertEqual(len(active_memories(con, "alpha")), len(result["claims"]))
            con.close()

    def test_running_brief_again_supersedes_changed_asks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.db"
            self.make_store(path)
            first = brief("alpha", str(path), persist_memory=True)
            asks_key = next(claim["claim_key"] for claim in first["claims"] if claim["section"] == "doing")
            con = sqlite3.connect(path)
            old_id = con.execute(
                "SELECT id FROM memory WHERE project=? AND claim_key=? AND status='active'",
                ("alpha", asks_key),
            ).fetchone()[0]
            con.execute("UPDATE event SET summary=? WHERE event_id=?",
                        ("全系统文案需要重新排查，角色入口仍有歧义", "e1"))
            con.commit()
            con.close()

            second = brief("alpha", str(path), persist_memory=True)
            con = sqlite3.connect(path)
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT id,content,status,superseded_by FROM memory WHERE project=? AND claim_key=? "
                "ORDER BY created_at,id", ("alpha", asks_key)
            ).fetchall()
            con.close()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["id"], old_id)
            self.assertEqual(rows[0]["status"], "superseded")
            self.assertEqual(rows[0]["superseded_by"], rows[1]["id"])
            self.assertEqual(rows[1]["status"], "active")
            self.assertIn("全系统文案", rows[1]["content"])
            self.assertNotIn("旧结论", second["brief"])


if __name__ == "__main__":
    unittest.main()
