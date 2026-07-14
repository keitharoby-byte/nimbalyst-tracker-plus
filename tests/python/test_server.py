from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ReaderServerTests(unittest.TestCase):
    def test_ndjson_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "fixture.sqlite"
            schema = (ROOT / "fixtures" / "sql" / "tracker-schema-current.sql").read_text(
                encoding="utf-8"
            )
            with closing(sqlite3.connect(db_path)) as connection:
                connection.executescript(schema)
                data = json.dumps({"title": "Protocol fixture", "comments": []})
                connection.execute(
                    """
                    INSERT INTO tracker_items (
                      id, issue_key, type, data, workspace, content, archived,
                      type_tags, deleted_at, created, updated, last_indexed
                    ) VALUES ('id-1', 'NIM-9', 'task', ?, ?, 'Body', 0, '[]', NULL, ?, ?, ?)
                    """,
                    (
                        data,
                        "C:\\Workspace\\Protocol",
                        "2026-07-14T00:00:00.000Z",
                        "2026-07-14T00:00:00.000Z",
                        "2026-07-14T00:00:00.000Z",
                    ),
                )
                connection.commit()

            request = {
                "id": "request-1",
                "method": "list_comments",
                "params": {
                    "trackerId": "NIM-9",
                    "workspacePath": "C:\\Workspace\\Protocol",
                    "limit": 20,
                    "order": "newest",
                },
            }
            env = {**os.environ, "NIMBALYST_SQLITE_PATH": str(db_path), "PYTHONUTF8": "1"}
            process = subprocess.run(
                [sys.executable, "-I", str(ROOT / "reader" / "server.py")],
                input=json.dumps(request) + "\n",
                text=True,
                capture_output=True,
                env=env,
                timeout=5,
                check=True,
            )
            response = json.loads(process.stdout)
            self.assertTrue(response["ok"])
            self.assertEqual(response["result"]["tracker"]["issueKey"], "NIM-9")
            self.assertNotIn("Body", process.stderr)


if __name__ == "__main__":
    unittest.main()
