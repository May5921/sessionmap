import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sync import SCHEMA
from workspace_merge import merge_stores


class WorkspaceMergeTests(unittest.TestCase):
    def make_source(self, path: Path, source_kind: str, event_id: str) -> None:
        con = sqlite3.connect(path)
        con.executescript(SCHEMA)
        con.execute(
            "INSERT INTO workspace(workspace_id,name,source_kind,source_path,first_seen,last_seen,updated_at) VALUES(?,?,?,?,?,?,?)",
            (f"{source_kind}:w", "C:/repo", source_kind, "C:/repo", 1, 3, 3),
        )
        con.execute(
            "INSERT INTO session(session_id,workspace_id,source_kind,source_id,title,directory,started_at,updated_at,raw_json) VALUES(?,?,?,?,?,?,?,?,?)",
            (f"{source_kind}:s1", f"{source_kind}:w", source_kind, "s1", source_kind, "C:/repo", 1, 3, "{}"),
        )
        con.execute(
            "INSERT INTO event(event_id,workspace_id,session_id,source_kind,source_id,event_type,actor,occurred_at,summary,payload_json,evidence_text,raw_ref,fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, f"{source_kind}:w", f"{source_kind}:s1", source_kind, event_id, "user_message", "user", 2,
             source_kind, json.dumps({"source": source_kind}), source_kind, f"raw:{event_id}", f"fp:{source_kind}", 3),
        )
        con.commit()
        con.close()

    def test_same_root_merges_sources_without_losing_source_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zcode = root / "zcode.db"
            codex = root / "codex.db"
            merged = root / "merged.db"
            self.make_source(zcode, "zcode", "z1")
            self.make_source(codex, "codex", "c1")
            result = merge_stores([zcode, codex], merged, "C:/repo")
            self.assertEqual(result["workspaces"], 1)
            con = sqlite3.connect(merged)
            self.assertEqual(con.execute("SELECT count(*) FROM workspace").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM session").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT count(*) FROM event").fetchone()[0], 2)
            self.assertEqual({row[0] for row in con.execute("SELECT source_kind FROM event")}, {"zcode", "codex"})
            self.assertTrue(all(str(row[0]).startswith(("zcode:", "codex:")) for row in con.execute("SELECT event_id FROM event")))
            con.close()

            # Rebuilding the same output is idempotent.
            merge_stores([zcode, codex], merged, "C:/repo")
            con = sqlite3.connect(merged)
            self.assertEqual(con.execute("SELECT count(*) FROM event").fetchone()[0], 2)
            con.close()


if __name__ == "__main__":
    unittest.main()
