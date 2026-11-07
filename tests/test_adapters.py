#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from adapters import (CANONICAL_EVENT_TYPES, ClaudeAdapter, CodexAdapter, DSHAdapter,
                      OpenCodeAdapter, PiAdapter, SOURCE_KINDS, ZCodeAdapter,
                      validate_store_schema)


class AdapterContractTests(unittest.TestCase):
    def test_codex_probe_is_read_only_and_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "sessions"
            root.mkdir()
            path = root / "rollout.jsonl"
            path.write_text(json.dumps({"id": "s1", "cwd": "C:/repo", "type": "session"}) + "\n", encoding="utf-8")
            adapter = CodexAdapter(root)
            first = adapter.probe()
            second = adapter.probe()
            self.assertEqual(first, second)
            self.assertEqual(first.status, "ready")
            sessions = adapter.list_sessions()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].workspace_id, "codex:c:/repo")
            self.assertEqual(path.read_text(encoding="utf-8").count("s1"), 1)

    def test_claude_jsonl_normalizes_message(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "projects" / "C--repo"
            root.mkdir(parents=True)
            path = root / "abc.jsonl"
            path.write_text(json.dumps({"uuid": "m1", "type": "user", "timestamp": "2026-11-07T00:00:00Z", "message": {"role": "user", "content": "hello"}}) + "\n", encoding="utf-8")
            adapter = ClaudeAdapter(root.parent)
            session = adapter.list_sessions()[0]
            record = next(adapter.read_incremental(session))
            event = adapter.normalize(record, session)
            self.assertEqual(event.event_type, "user_message")
            self.assertEqual(event.actor, "user")
            self.assertIn("hello", event.summary)
            self.assertEqual(event.payload["raw_kind"], "user")

    def test_claude_source_kinds_map_to_canonical_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "projects" / "C--repo"
            root.mkdir(parents=True)
            rows = [
                {"uuid": "u1", "type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.py"}}
                ]}},
                {"uuid": "u2", "type": "user", "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}
                ]}},
                {"uuid": "e1", "type": "api_error", "error": {"message": "rate limit"}},
                {"uuid": "s1", "type": "subagent_start", "summary": "child"},
                {"uuid": "t1x", "type": "todo_update", "summary": "ship"},
                {"uuid": "f1", "type": "file-history-delta", "summary": "a.py changed"},
                {"uuid": "x1", "type": "future_kind", "summary": "preserve"},
            ]
            path = root / "abc.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            adapter = ClaudeAdapter(root.parent)
            session = adapter.list_sessions()[0]
            events = [adapter.normalize(record, session) for record in adapter.read_incremental(session)]
            self.assertEqual([event.event_type for event in events], [
                "tool_call", "tool_result", "error", "subagent_event", "todo_change", "file_change", "source_event"
            ])
            self.assertEqual(events[0].actor, "assistant")
            self.assertEqual(events[1].actor, "tool")
            self.assertEqual(events[-1].payload["raw_kind"], "future_kind")
            self.assertTrue(all(event.event_type in CANONICAL_EVENT_TYPES for event in events))

    def test_zcode_sqlite_inventory_is_read_only_and_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "db.sqlite"
            con = sqlite3.connect(path)
            con.executescript("""
                CREATE TABLE session (id TEXT, parent_id TEXT, title TEXT, directory TEXT,
                                      time_created INTEGER, time_updated INTEGER, time_deleted INTEGER);
                CREATE TABLE message (id TEXT);
                CREATE TABLE part (id TEXT);
                CREATE TABLE todo (id TEXT);
                INSERT INTO session VALUES ('s1', NULL, 'demo', 'C:/repo', 1000, 2000, NULL);
            """)
            con.commit()
            con.close()
            adapter = ZCodeAdapter(path)
            status = adapter.probe()
            self.assertEqual(status.status, "ready")
            self.assertEqual(status.ingestion_mode, "sqlite-sync")
            self.assertIn("incremental", status.capabilities)
            workspaces = adapter.discover_workspaces()
            self.assertEqual(len(workspaces), 1)
            sessions = adapter.list_sessions(workspaces[0].workspace_id)
            self.assertEqual(len(sessions), 1)
            with self.assertRaises(RuntimeError):
                next(adapter.read_incremental(sessions[0]))
            check = sqlite3.connect(path)
            self.assertEqual(check.execute("SELECT count(*) FROM session").fetchone()[0], 1)
            check.close()

    def test_pi_v3_jsonl_normalizes_user_and_tool_events(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "sessions" / "--C--repo--"
            root.mkdir(parents=True)
            path = root / "session.jsonl"
            rows = [
                {"type": "session", "version": 3, "id": "pi-1", "timestamp": "2026-11-07T00:00:00Z", "cwd": "C:/repo"},
                {"type": "message", "id": "m1", "timestamp": "2026-11-07T00:00:01Z", "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]}},
                {"type": "message", "id": "m2", "timestamp": "2026-11-07T00:00:02Z", "message": {"role": "assistant", "content": [{"type": "toolCall", "name": "read", "arguments": {"path": "a.py"}}]}},
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            adapter = PiAdapter(root.parent)
            self.assertEqual(adapter.probe().status, "ready")
            session = adapter.list_sessions()[0]
            events = [adapter.normalize(record, session) for record in adapter.read_incremental(session)]
            events = [event for event in events if event]
            self.assertEqual([event.event_type for event in events], ["user_message", "tool_call"])
            self.assertEqual(events[0].actor, "user")

    def test_opencode_sqlite_normalizes_part_and_todo_events(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "opencode.db"
            con = sqlite3.connect(path)
            con.executescript("""
                CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT, title TEXT, directory TEXT,
                    time_created INTEGER, time_updated INTEGER);
                CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT);
                CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT);
                CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT);
                CREATE TABLE todo (session_id TEXT, content TEXT, status TEXT, priority TEXT, position INTEGER, time_created INTEGER, time_updated INTEGER);
                INSERT INTO session VALUES ('s1',NULL,'demo','C:/repo',1000,3000);
                INSERT INTO message VALUES ('m1','s1','{"role":"user"}');
                INSERT INTO part VALUES ('p1','m1','s1',2000,'{"type":"text","text":"hello"}');
                INSERT INTO todo VALUES ('s1','ship','in_progress','high',0,2000,3000);
            """)
            con.commit(); con.close()
            adapter = OpenCodeAdapter(path)
            self.assertEqual(adapter.probe().status, "ready")
            session = adapter.list_sessions()[0]
            events = [adapter.normalize(record, session) for record in adapter.read_incremental(session)]
            self.assertEqual([event.event_type for event in events if event], ["user_message", "todo_change"])
            self.assertEqual(adapter.next_cursor(session), 3000)

    def test_dsh_v4_zstd_stream_is_read_only_and_cursored(self):
        decoder = DSHAdapter._decoder()
        if not decoder:
            self.skipTest("zstd decoder unavailable")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "sessions" / "--C--repo--" / "child"
            root.mkdir(parents=True)
            source = Path(temp) / "plain.jsonl"
            compressed = root / "session.v4.jsonl.zstd"
            rows = [
                {"type": "session", "version": 4, "id": "dsh-1", "createdAt": 1000, "cwd": "C:/repo"},
                {"type": "user/message", "seq": 1, "time": 2000, "content": [{"type": "text", "text": "hello"}]},
                {"type": "tool/call", "seq": 2, "time": 3000, "name": "read", "arguments": {"path": "a.py"}},
            ]
            source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            subprocess.run([decoder, "-q", "-f", "-o", str(compressed), str(source)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            adapter = DSHAdapter(root.parents[1])
            self.assertEqual(adapter.probe().status, "ready")
            self.assertIn("incremental", adapter.probe().capabilities)
            session = adapter.list_sessions()[0]
            events = [adapter.normalize(record, session) for record in adapter.read_incremental(session)]
            events = [event for event in events if event]
            self.assertEqual([event.event_type for event in events], ["user_message", "tool_call"])
            self.assertEqual(adapter.next_cursor(session), 3)

    def test_source_kind_contract(self):
        self.assertEqual(set(SOURCE_KINDS), {"zcode", "codex", "claude", "pi", "dsh", "opencode"})

    def test_store_schema_check(self):
        # The production store is checked by the integration command; this
        # unit test only verifies the checker has a stable result shape.
        with tempfile.TemporaryDirectory() as temp:
            result = validate_store_schema(Path(temp) / "empty.db")
            self.assertFalse(result["ok"])
            self.assertIn("workspace", result["tables"])


if __name__ == "__main__":
    unittest.main()
