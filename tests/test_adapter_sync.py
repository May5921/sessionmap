#!/usr/bin/env python3
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from adapter_sync import import_adapter
from adapters import (AdapterStatus, CanonicalEvent, ClaudeAdapter, OpenCodeAdapter,
                       SessionRef, SourceAdapter, WorkspaceRef)
from sync import sync_workspace


class MutableAdapter(SourceAdapter):
    source_kind = "fake"

    def __init__(self, value="one", fail=False):
        super().__init__("C:/fake-source")
        self.value = value
        self.fail = fail

    def probe(self):
        return AdapterStatus("fake", "ready", str(self.root))

    def discover_workspaces(self):
        return [WorkspaceRef("fake:w", "fake", "C:/repo", "repo")]

    def list_sessions(self, workspace_id=None):
        return [SessionRef("s1", "fake:w", "fake", "C:/fake.jsonl", "s1")]

    def read_incremental(self, session, cursor=0):
        if self.fail:
            raise RuntimeError("decoder unavailable")
        yield {"value": self.value}

    def next_cursor(self, session, cursor=0):
        return 0

    def normalize(self, record, session):
        return CanonicalEvent(
            "fake:e1", session.workspace_id, session.session_id, self.source_kind,
            "e1", "source_event", "system", 1000, "value " + self.value,
            {"value": self.value}, "fake:e1",
        )


class AdapterSyncTests(unittest.TestCase):
    def test_claude_adapter_import_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "projects" / "C--repo"
            root.mkdir(parents=True)
            path = root / "abc.jsonl"
            path.write_text(json.dumps({"uuid": "m1", "type": "user", "timestamp": "2026-11-07T00:00:00Z", "message": {"role": "user", "content": "hello"}}) + "\n", encoding="utf-8")
            store = Path(temp) / "store.db"
            adapter = ClaudeAdapter(root.parent)
            first = import_adapter(adapter, str(store))
            second = import_adapter(adapter, str(store))
            con = sqlite3.connect(store)
            self.assertEqual(first["events"], 1)
            self.assertEqual(second["events"], 0)
            self.assertEqual(con.execute("select count(*) from event").fetchone()[0], 1)
            self.assertEqual(con.execute("select count(*) from session").fetchone()[0], 1)
            con.close()

    def test_opencode_adapter_import_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "opencode.db"
            con = sqlite3.connect(source)
            con.executescript("""
                CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT, title TEXT, directory TEXT,
                    time_created INTEGER, time_updated INTEGER);
                CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT);
                CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT);
                CREATE TABLE todo (session_id TEXT, content TEXT, status TEXT, priority TEXT, position INTEGER,
                    time_created INTEGER, time_updated INTEGER);
                INSERT INTO session VALUES ('s1',NULL,'demo','C:/repo',1000,3000);
                INSERT INTO message VALUES ('m1','s1','{"role":"user"}');
                INSERT INTO part VALUES ('p1','m1','s1',2000,'{"type":"text","text":"hello"}');
            """)
            con.commit()
            con.close()
            store = Path(temp) / "store.db"
            adapter = OpenCodeAdapter(source)
            first = import_adapter(adapter, str(store))
            second = import_adapter(adapter, str(store))
            result = sqlite3.connect(store)
            self.assertEqual(first["events"], 1)
            self.assertEqual(second["events"], 0)
            self.assertEqual(result.execute("select count(*) from event").fetchone()[0], 1)
            result.close()

    def test_event_fingerprint_detects_payload_only_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "store.db"
            adapter = MutableAdapter("one")
            first = import_adapter(adapter, str(store))
            second = import_adapter(adapter, str(store))
            adapter.value = "two"
            changed = import_adapter(adapter, str(store))
            con = sqlite3.connect(store)
            fp = con.execute("SELECT fingerprint FROM event WHERE event_id='fake:e1'").fetchone()[0]
            payload = json.loads(con.execute("SELECT payload_json FROM event WHERE event_id='fake:e1'").fetchone()[0])
            con.close()
            self.assertEqual(first["events"], 1)
            self.assertEqual(second["changed_events"], 0)
            self.assertEqual(changed["changed_events"], 1)
            self.assertEqual(payload["value"], "two")
            self.assertTrue(fp)

    def test_runtime_error_is_reported_as_partial_import(self):
        with tempfile.TemporaryDirectory() as temp:
            result = import_adapter(MutableAdapter(fail=True), str(Path(temp) / "store.db"))
            self.assertEqual(result["status"], "partial")
            self.assertEqual(len(result["skipped_sessions"]), 1)
            self.assertIn("decoder unavailable", result["errors"][0])

    def test_zcode_upsert_refreshes_fingerprint_when_payload_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.db"
            store = Path(temp) / "store.db"
            con = sqlite3.connect(source)
            con.executescript("""
                CREATE TABLE session (
                    id TEXT, project_id TEXT, parent_id TEXT, title TEXT, directory TEXT,
                    time_created INTEGER, time_updated INTEGER, time_deleted INTEGER,
                    task_type TEXT, last_outcome TEXT, summary_files TEXT,
                    summary_additions INTEGER, summary_deletions INTEGER
                );
                CREATE TABLE message (id TEXT, session_id TEXT, data TEXT);
                CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT,
                    time_created INTEGER, data TEXT, sequence INTEGER);
                CREATE TABLE todo (session_id TEXT, status TEXT, priority TEXT,
                    position INTEGER, content TEXT, time_created INTEGER, time_updated INTEGER);
                INSERT INTO session VALUES ('s1','p1',NULL,'demo','C:/repo',1000,2000,NULL,'main',NULL,'[]',0,0);
                INSERT INTO message VALUES ('m1','s1','{"role":"user"}');
                INSERT INTO part VALUES ('p1','m1','s1',2000,'{"type":"text","text":"hello"}',1);
            """)
            con.commit(); con.close()
            sync_workspace("repo", str(source), str(store))
            con = sqlite3.connect(store)
            first = con.execute("SELECT fingerprint,summary FROM event WHERE event_id='zcode:p1'").fetchone()
            con.close()
            con = sqlite3.connect(source)
            con.execute("UPDATE part SET data=? WHERE id='p1'", ('{"type":"text","text":"updated"}',))
            con.commit(); con.close()
            sync_workspace("repo", str(source), str(store))
            con = sqlite3.connect(store)
            second = con.execute("SELECT fingerprint,summary FROM event WHERE event_id='zcode:p1'").fetchone()
            con.close()
            self.assertNotEqual(first[0], second[0])
            self.assertEqual(second[1], "updated")


if __name__ == "__main__":
    unittest.main()
