from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from reader.contracts import ReaderError  # noqa: E402
from reader.database import NativeTrackerReader  # noqa: E402


class NativeTrackerReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "fixture.sqlite"
        schema = (ROOT / "fixtures" / "sql" / "tracker-schema-current.sql").read_text(
            encoding="utf-8"
        )
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.executescript(schema)
            self._insert_tracker(connection, "tracker-one", "NIM-1", "C:\\Workspace\\One")
            self._insert_tracker(connection, "tracker-two", "NIM-1", "C:\\Workspace\\Two")
            connection.commit()
        self.reader = NativeTrackerReader(self.db_path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _insert_tracker(
        self,
        connection: sqlite3.Connection,
        tracker_id: str,
        issue_key: str,
        workspace: str,
    ) -> None:
        data = {
            "title": "Synthetic tracker",
            "status": "in-progress",
            "priority": "high",
            "owner": "Fixture Owner",
            "comments": [
                {
                    "id": "comment-old",
                    "body": "Old synthetic comment",
                    "createdAt": 1_752_494_400_000,
                    "updatedAt": None,
                    "deleted": False,
                    "authorIdentity": {
                        "displayName": "Fixture Author",
                        "email": "must-not-leak@example.test",
                    },
                },
                {
                    "id": "comment-new",
                    "body": "Most recent synthetic comment",
                    "createdAt": 1_752_494_460_000,
                    "updatedAt": None,
                    "deleted": False,
                    "authorIdentity": {
                        "gitName": "Fixture Git Author",
                        "gitEmail": "must-not-leak@example.test",
                    },
                },
                {
                    "id": "comment-deleted",
                    "body": "Deleted body",
                    "createdAt": 1_752_494_520_000,
                    "deleted": True,
                },
            ],
        }
        connection.execute(
            """
            INSERT INTO tracker_items (
              id, issue_number, issue_key, type, data, workspace, content,
              archived, type_tags, deleted_at, created, updated, last_indexed
            ) VALUES (?, 1, ?, 'task', ?, ?, ?, 0, '["task"]', NULL, ?, ?, ?)
            """,
            (
                tracker_id,
                issue_key,
                json.dumps(data),
                workspace,
                "Synthetic durable body",
                "2026-07-14T12:00:00.000Z",
                "2026-07-14T12:01:00.000Z",
                "2026-07-14T12:01:00.000Z",
            ),
        )

    def _params(self, **overrides: object) -> dict[str, object]:
        return {
            "trackerId": "NIM-1",
            "workspacePath": "C:\\Workspace\\One",
            "limit": 20,
            "order": "newest",
            **overrides,
        }

    def test_lists_only_current_workspace_non_deleted_comments(self) -> None:
        result = self.reader.list_comments(self._params())

        self.assertEqual(result["tracker"]["id"], "tracker-one")
        self.assertEqual([comment["id"] for comment in result["comments"]], ["comment-new", "comment-old"])
        serialized = json.dumps(result)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("comment-deleted", serialized)
        self.assertEqual(result["comments"][0]["authorLabel"], "Fixture Git Author")

    def test_cursor_pagination_is_deterministic(self) -> None:
        first = self.reader.list_comments(self._params(limit=1))
        second = self.reader.list_comments(
            self._params(limit=1, cursor=first["page"]["nextCursor"])
        )

        self.assertEqual(first["comments"][0]["id"], "comment-new")
        self.assertEqual(second["comments"][0]["id"], "comment-old")
        self.assertFalse(second["page"]["hasMore"])

    def test_oldest_order_cursor_stays_oldest(self) -> None:
        first = self.reader.list_comments(self._params(limit=1, order="oldest"))
        second = self.reader.list_comments(
            self._params(limit=1, order="oldest", cursor=first["page"]["nextCursor"])
        )

        self.assertEqual(first["comments"][0]["id"], "comment-old")
        self.assertEqual(second["comments"][0]["id"], "comment-new")

    def test_equal_timestamps_use_comment_id_as_tie_breaker(self) -> None:
        comments = [
            {"id": "comment-a", "body": "A", "createdAt": 1_752_494_400_000},
            {"id": "comment-b", "body": "B", "createdAt": 1_752_494_400_000},
        ]
        self._replace_comments(comments)

        result = self.reader.list_comments(self._params())

        self.assertEqual([item["id"] for item in result["comments"]], ["comment-b", "comment-a"])

    def test_missing_id_gets_stable_response_only_id(self) -> None:
        comments = [{"body": "No persisted id", "createdAt": 1_752_494_400_000}]
        self._replace_comments(comments)

        first = self.reader.list_comments(self._params())
        second = self.reader.list_comments(self._params())

        generated = first["comments"][0]["id"]
        self.assertTrue(generated.startswith("generated-"))
        self.assertEqual(generated, second["comments"][0]["id"])

    def test_comment_and_response_limits_are_enforced(self) -> None:
        comments = [
            {
                "id": f"comment-{index:03d}",
                "body": "x" * 25_000,
                "createdAt": 1_752_494_400_000 + index,
            }
            for index in range(40)
        ]
        self._replace_comments(comments)

        result = self.reader.list_comments(self._params(limit=100))

        self.assertTrue(result["page"]["hasMore"])
        self.assertIsNotNone(result["page"]["nextCursor"])
        self.assertLess(len(json.dumps(result).encode("utf-8")), 500 * 1024)
        self.assertTrue(all(item["truncated"] for item in result["comments"]))
        self.assertTrue(all(len(item["body"]) == 20_000 for item in result["comments"]))

    def test_get_returns_orientation_fields_and_body(self) -> None:
        result = self.reader.get_with_comments(self._params())

        tracker = result["tracker"]
        self.assertEqual(tracker["primaryType"], "task")
        self.assertEqual(tracker["status"], "in-progress")
        self.assertEqual(tracker["body"], "Synthetic durable body")
        self.assertFalse(tracker["bodyTruncated"])

    def test_sql_shaped_tracker_id_is_only_a_value(self) -> None:
        with self.assertRaises(ReaderError) as raised:
            self.reader.list_comments(self._params(trackerId="NIM-1' OR 1=1 --"))
        self.assertEqual(raised.exception.code, "TRACKER_NOT_FOUND")

    def test_schema_drift_fails_honestly(self) -> None:
        drift_path = Path(self.tempdir.name) / "drift.sqlite"
        schema = (ROOT / "fixtures" / "sql" / "tracker-schema-missing-data.sql").read_text(
            encoding="utf-8"
        )
        with closing(sqlite3.connect(drift_path)) as connection:
            connection.executescript(schema)

        with self.assertRaises(ReaderError) as raised:
            NativeTrackerReader(drift_path).schema_fingerprint()
        self.assertEqual(raised.exception.code, "SCHEMA_INCOMPATIBLE")
        self.assertIn("data", raised.exception.details["missingColumns"])

    def test_connection_rejects_writes(self) -> None:
        with self.assertRaises(ReaderError) as raised:
            with self.reader._connect() as connection:
                connection.execute("DELETE FROM tracker_items")
        self.assertEqual(raised.exception.code, "READ_ONLY_GUARD_FAILED")
        with closing(sqlite3.connect(self.db_path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM tracker_items").fetchone()[0]
        self.assertEqual(count, 2)

    def _replace_comments(self, comments: list[dict[str, object]]) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            raw = connection.execute(
                "SELECT data FROM tracker_items WHERE id = 'tracker-one'"
            ).fetchone()[0]
            data = json.loads(raw)
            data["comments"] = comments
            connection.execute(
                "UPDATE tracker_items SET data = ? WHERE id = 'tracker-one'",
                (json.dumps(data),),
            )
            connection.commit()


if __name__ == "__main__":
    unittest.main()
