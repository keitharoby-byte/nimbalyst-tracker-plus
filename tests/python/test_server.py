from __future__ import annotations

import json
import os
import shutil
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

    def test_saved_role_query_with_legacy_edges_is_stable_across_helper_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = str(Path(tempdir).resolve())
            db_path = Path(tempdir) / "fixture.sqlite"
            schema = (ROOT / "fixtures" / "sql" / "tracker-schema-current.sql").read_text(
                encoding="utf-8"
            )
            with closing(sqlite3.connect(db_path)) as connection:
                connection.executescript(schema)
                rows = [
                    (
                        "prior",
                        "MILESTONE-1",
                        "milestone",
                        {"title": "Prior milestone", "status": "active"},
                    ),
                    (
                        "legacy-role-item",
                        "ITEM-1",
                        "task",
                        {
                            "title": "Legacy relationship fixture",
                            "status": "ready",
                            "owner": "legacy-reviewer",
                            "blockers": [{"itemId": "prior"}],
                        },
                    ),
                ]
                for item_id, issue_key, item_type, data in rows:
                    connection.execute(
                        """
                        INSERT INTO tracker_items (
                          id, issue_key, type, data, workspace, content, archived,
                          type_tags, deleted_at, created, updated, last_indexed
                        ) VALUES (?, ?, ?, ?, ?, '', 0, ?, NULL, ?, ?, ?)
                        """,
                        (
                            item_id,
                            issue_key,
                            item_type,
                            json.dumps(data),
                            workspace,
                            json.dumps([item_type]),
                            "2026-07-14T00:00:00.000Z",
                            "2026-07-14T00:00:00.000Z",
                            "2026-07-14T00:00:00.000Z",
                        ),
                    )
                connection.commit()
            registry_dir = Path(workspace) / ".nimbalyst"
            registry_dir.mkdir()
            (registry_dir / "tracker-plus.registry.json").write_text(
                json.dumps({
                    "roles": {
                        "legacy-reviewer": {
                            "ownerAliases": ["legacy-reviewer"],
                            "attentionTags": ["needs-legacy-review"],
                        }
                    }
                }),
                encoding="utf-8",
            )
            shutil.copyfile(
                ROOT / "examples" / "tracker-plus.queries.json",
                registry_dir / "tracker-plus.queries.json",
            )
            requests = [
                {
                    "id": "request-large",
                    "method": "query_items",
                    "params": {
                        "workspacePath": workspace,
                        "savedQuery": {
                            "id": "role-active-work-and-attention",
                            "params": {"roleId": "legacy-reviewer"},
                        },
                        "limit": 100,
                        "includeTotalCount": True,
                    },
                },
                {
                    "id": "request-small",
                    "method": "query_items",
                    "params": {
                        "workspacePath": workspace,
                        "savedQuery": {
                            "id": "role-active-work-and-attention",
                            "params": {"roleId": "legacy-reviewer"},
                        },
                        "limit": 25,
                        "includeTotalCount": False,
                    },
                },
            ]
            env = {**os.environ, "NIMBALYST_SQLITE_PATH": str(db_path), "PYTHONUTF8": "1"}
            process = subprocess.run(
                [sys.executable, "-I", str(ROOT / "reader" / "server.py")],
                input="".join(json.dumps(request) + "\n" for request in requests),
                text=True,
                capture_output=True,
                env=env,
                timeout=5,
                check=True,
            )
            responses = [json.loads(line) for line in process.stdout.splitlines()]
            self.assertEqual(len(responses), 2)
            self.assertTrue(all(response["ok"] for response in responses))
            results = [response["result"] for response in responses]
            self.assertTrue(all(
                [node["id"] for node in result["nodes"]] == ["legacy-role-item"]
                for result in results
            ))
            self.assertTrue(all(result["page"]["truncated"] is False for result in results))
            self.assertEqual(
                results[0]["watermark"]["registryHash"],
                results[1]["watermark"]["registryHash"],
            )
            self.assertEqual(
                results[0]["watermark"]["schemaFingerprint"],
                results[1]["watermark"]["schemaFingerprint"],
            )
            self.assertTrue(all(
                isinstance(result["query"]["queryFingerprint"], str)
                and len(result["query"]["queryFingerprint"]) == 64
                for result in results
            ))
            self.assertNotIn("READER_INTERNAL_ERROR", process.stdout)


if __name__ == "__main__":
    unittest.main()
