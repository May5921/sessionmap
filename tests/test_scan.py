import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scan import fetch_child_user_texts


class ScanTests(unittest.TestCase):
    def test_child_user_texts_are_bounded_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = sqlite3.connect(Path(tmp) / "scan.db")
            con.executescript("""
                CREATE TABLE session (
                    id TEXT PRIMARY KEY, title TEXT, parent_id TEXT, directory TEXT,
                    time_created INTEGER, time_updated INTEGER, time_deleted INTEGER,
                    summary_files INTEGER, summary_additions INTEGER, summary_deletions INTEGER
                );
                CREATE TABLE message (
                    id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT
                );
                CREATE TABLE part (
                    id TEXT PRIMARY KEY, message_id TEXT, sequence INTEGER, data TEXT
                );
            """)
            con.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?)",
                        ("child", "child", "main", "C:/repo", 1, 2, None, 0, 0, 0))
            for index, text in enumerate(("决定保留事件线", "决定保留事件线", "补充证据"), 1):
                con.execute("INSERT INTO message VALUES (?,?,?,?)",
                            (f"m{index}", "child", index, json.dumps({"role": "user"})))
                con.execute("INSERT INTO part VALUES (?,?,?,?)",
                            (f"p{index}", f"m{index}", 0,
                             json.dumps({"type": "text", "text": text})))
            con.commit()
            rows = fetch_child_user_texts(con, ["child"], trunc=220, max_msgs=2, budget=1000)
            con.close()
            self.assertEqual([row[1] for row in rows], ["决定保留事件线", "补充证据"])


if __name__ == "__main__":
    unittest.main()
