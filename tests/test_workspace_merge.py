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

    def test_git_commit_is_collapsed_across_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zcode = root / "zcode.db"
            codex = root / "codex.db"
            merged = root / "merged.db"
            self.make_source(zcode, "zcode", "z1")
            self.make_source(codex, "codex", "c1")
            for path, source in ((zcode, "zcode"), (codex, "codex")):
                con = sqlite3.connect(path)
                con.execute(
                    "INSERT INTO event(event_id,workspace_id,source_kind,source_id,event_type,actor,occurred_at,summary,payload_json,evidence_text,raw_ref,fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"{source}:git", f"{source}:w", source, "abc123", "git_commit", "git", 4,
                     "ship", json.dumps({"sha": "abc123", "subject": "ship"}), "ship", "git:abc123", f"{source}-fp", 4),
                )
                con.commit()
                con.close()
            merge_stores([zcode, codex], merged, "C:/repo")
            con = sqlite3.connect(merged)
            self.assertEqual(con.execute("SELECT count(*) FROM event WHERE event_type='git_commit'").fetchone()[0], 1)
            payload = json.loads(con.execute("SELECT payload_json FROM event WHERE event_type='git_commit'").fetchone()[0])
            self.assertEqual(payload["source_kinds"], ["codex", "zcode"])
            con.close()

    def test_latest_semantic_notes_survive_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zcode = root / "zcode.db"
            merged = root / "merged.db"
            self.make_source(zcode, "zcode", "z1")
            con = sqlite3.connect(zcode)
            con.execute(
                "INSERT INTO analysis_run(run_id,workspace_id,semantic_source,input_hash,status,started_at,completed_at,summary_json) VALUES(?,?,?,?,?,?,?,?)",
                ("r1", "zcode:w", "timeline.json", "h1", "succeeded", 1, 10, "{}"),
            )
            con.execute(
                "INSERT INTO decision(decision_id,run_id,workspace_id,external_id,ordinal,occurred_at,track,title,what,why,alternative,status,confidence,source_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("r1:D1", "r1", "zcode:w", "D1", 1, 2, "主干", "方向", "确定方向", "用户确认", None, "open", 0.9, "{}", 1, 10),
            )
            con.execute(
                "INSERT INTO analysis_note(note_id,run_id,workspace_id,kind,content_json,created_at) VALUES(?,?,?,?,?,?)",
                ("r1:unfinished", "r1", "zcode:w", "unfinished", '["继续验证"]', 10),
            )
            con.commit()
            con.close()
            merge_stores([zcode], merged, "C:/repo")
            con = sqlite3.connect(merged)
            self.assertEqual(con.execute("SELECT content_json FROM analysis_note WHERE kind='unfinished'").fetchone()[0], '["继续验证"]')
            con.close()


if __name__ == "__main__":
    unittest.main()
