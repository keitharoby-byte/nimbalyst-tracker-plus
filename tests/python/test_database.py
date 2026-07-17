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

    def test_imported_github_pull_request_urn_projects_number_and_url(self) -> None:
        number, url = self.reader._pull_request_fields(
            {"origin": {"external": {"urn": "github://example/repo#73"}}},
            ["task", "pull-request"],
        )
        self.assertEqual(number, 73)
        self.assertEqual(url, "https://github.com/example/repo/pull/73")

    def test_timeline_snapshot_projects_normalized_relationships_and_dimensions(self) -> None:
        self._insert_timeline_items()

        result = self.reader.timeline_snapshot(
            {
                "workspacePath": "C:\\Workspace\\One",
                "includeUnscheduled": True,
                "maxItems": 100,
            }
        )

        self.assertEqual(
            {item["id"] for item in result["items"]},
            {"milestone-one", "timeline-one", "tracker-one"},
        )
        self.assertEqual(result["milestones"][0]["issueKey"], "NIM-10")
        self.assertEqual(result["milestones"][0]["progress"], 25)
        self.assertEqual(result["source"]["milestoneProgressSource"], "active primary deliverables")
        relationship_types = {edge["relationshipType"] for edge in result["relationships"]}
        self.assertEqual(
            relationship_types,
            {"depends-on", "contributes-to", "reviews", "evidences", "implements", "related"},
        )
        self.assertEqual(len(result["relationships"]), 6)
        timeline = next(item for item in result["items"] if item["id"] == "timeline-one")
        self.assertEqual(timeline["workflow"], "in-progress")
        self.assertEqual(timeline["executionConstraint"], "blocked")
        self.assertEqual(timeline["riskLevel"], "critical")
        self.assertEqual(timeline["primaryMilestoneId"], "milestone-one")
        self.assertTrue(timeline["isCritical"])
        self.assertEqual(timeline["pullRequestNumber"], 42)
        self.assertEqual(
            timeline["pullRequestUrl"],
            "https://github.com/example/repo/pull/42",
        )
        self.assertEqual(result["source"]["projectStateRevision"], "fixture-r7")
        self.assertFalse(any(finding["severity"] == "error" for finding in result["validation"]))
        serialized = json.dumps(result)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("Old synthetic comment", serialized)
        self.assertNotIn("Most recent synthetic comment", serialized)

    def test_timeline_snapshot_resolves_explicit_link_endpoints_before_row_limit(self) -> None:
        self._insert_timeline_items()
        with closing(sqlite3.connect(self.db_path)) as connection:
            for index in range(20):
                self._insert_tracker(
                    connection,
                    f"decoy-{index:02d}",
                    f"NIM-{100 + index}",
                    "C:\\Workspace\\One",
                )
            connection.execute("UPDATE tracker_items SET archived = 1 WHERE id = 'tracker-one'")
            connection.commit()

        result = self.reader.timeline_snapshot(
            {
                "workspacePath": "C:\\Workspace\\One",
                "includeUnscheduled": True,
                "maxItems": 10,
            }
        )

        self.assertEqual(
            {item["id"] for item in result["items"]},
            {"milestone-one", "timeline-one", "tracker-one"},
        )
        self.assertEqual(len(result["relationships"]), 6)
        self.assertTrue(all(edge["legacy"] is False for edge in result["relationships"]))
        self.assertEqual(result["source"]["relationshipRows"], 6)
        self.assertEqual(result["source"]["endpointItems"], 3)
        self.assertEqual(result["source"]["legacyRelationships"], 0)
        self.assertTrue(result["source"]["includeArchivedLinkedEvidence"])
        self.assertEqual(sum(item["launchScoped"] for item in result["items"]), 3)

    def test_intentional_secondary_contribution_does_not_require_primary_milestone(self) -> None:
        self._insert_timeline_items()
        secondary = {
            "title": "Intentional supporting item",
            "status": "in-progress",
            "projectStateRevision": "fixture-r7",
        }
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO tracker_items (
                  id, issue_number, issue_key, type, data, workspace, content,
                  archived, type_tags, deleted_at, created, updated, last_indexed
                ) VALUES ('secondary-only', 30, 'NIM-30', 'task', ?, ?, '', 0, '["task"]', NULL, ?, ?, ?)
                """,
                (
                    json.dumps(secondary),
                    "C:\\Workspace\\One",
                    "2026-07-04T00:00:00.000Z",
                    "2026-07-04T00:00:00.000Z",
                    "2026-07-04T00:00:00.000Z",
                ),
            )
            connection.commit()
        self._insert_link(
            "link-secondary-only",
            "NIM-31",
            "secondary-only",
            "milestone-one",
            "contributes-to",
            contribution_role="secondary",
        )

        result = self.reader.timeline_snapshot(
            {
                "workspacePath": "C:\\Workspace\\One",
                "includeUnscheduled": True,
                "maxItems": 100,
            }
        )

        cardinality = [
            finding for finding in result["validation"]
            if finding["code"] == "primary-milestone-cardinality"
            and "secondary-only" in finding["itemIds"]
        ]
        self.assertEqual(cardinality, [])

    def test_cleared_review_evidence_is_not_orphaned(self) -> None:
        items = [
            {
                "id": "review-source",
                "issueKey": "NIM-40",
                "primaryType": "task",
                "typeTags": ["task"],
                "workflow": "done",
                "_launchScopeExplicit": False,
                "ownerLabel": "QA",
            },
            {
                "id": "milestone-target",
                "issueKey": "NIM-41",
                "primaryType": "milestone",
                "typeTags": ["milestone"],
                "workflow": "achieved",
                "_launchScopeExplicit": False,
                "ownerLabel": "PM",
            },
            {
                "id": "cleared-evidence",
                "issueKey": "NIM-42",
                "primaryType": "task",
                "typeTags": ["task", "evidence"],
                "workflow": "done",
                "_launchScopeExplicit": False,
                "ownerLabel": "QA",
            },
        ]
        edges = [
            {
                "id": "cleared-review",
                "sourceId": "review-source",
                "targetId": "milestone-target",
                "relationshipType": "reviews",
                "state": "cleared",
                "entryEvidenceIds": ["cleared-evidence"],
                "exitEvidenceIds": ["review-source"],
                "evidenceSourceIds": [],
                "primaryContribution": False,
            }
        ]

        findings = self.reader._validate_timeline(items, edges)

        orphaned = [
            finding for finding in findings
            if finding["code"] == "orphan-item"
            and "cleared-evidence" in finding["itemIds"]
        ]
        self.assertEqual(orphaned, [])

    def test_tagged_standalone_seed_is_distinct_from_broken_orphan(self) -> None:
        base = {
            "primaryType": "task",
            "typeTags": ["task"],
            "workflow": "open",
            "ownerLabel": "PM",
        }
        findings = self.reader._validate_timeline([
            {**base, "id": "seed", "issueKey": "NIM-SEED", "tags": ["alpha-launch"], "_launchScopeExplicit": False},
            {**base, "id": "orphan", "issueKey": "NIM-ORPHAN", "tags": [], "_launchScopeExplicit": True},
        ], [])

        by_code = {finding["code"]: finding for finding in findings}
        self.assertEqual(by_code["standalone-seed"]["severity"], "info")
        self.assertEqual(by_code["standalone-seed"]["itemIds"], ["seed"])
        self.assertEqual(by_code["orphan-item"]["severity"], "warning")
        self.assertEqual(by_code["orphan-item"]["itemIds"], ["orphan"])

    def test_milestone_report_surfaces_overdue_and_blocked_work(self) -> None:
        self._insert_timeline_items()

        result = self.reader.milestone_report(
            {
                "workspacePath": "C:\\Workspace\\One",
                "milestoneId": "NIM-10",
                "asOf": "2026-08-01",
                "lookaheadDays": 30,
                "maxItems": 100,
            }
        )

        section = result["milestones"][0]
        self.assertEqual(section["health"], "late")
        self.assertEqual(section["scheduleHealth"], "late")
        self.assertEqual(section["riskLevel"], "critical")
        self.assertEqual(section["deliverableCount"], 1)
        self.assertEqual(section["progress"], 25)
        self.assertEqual([item["id"] for item in section["overdue"]], ["timeline-one"])
        self.assertEqual([item["id"] for item in section["blockedItems"]], ["timeline-one"])
        self.assertEqual(len(section["activeDependencies"]), 1)
        self.assertIn("# Milestone report", result["markdown"])
        self.assertIn("Launch milestone", result["markdown"])
        self.assertIn("Schedule health: **late**", result["markdown"])

    def test_hard_dependency_cycles_are_validation_errors(self) -> None:
        self._insert_timeline_items()
        self._insert_link(
            "link-cycle",
            "NIM-18",
            "tracker-one",
            "timeline-one",
            "depends-on",
            hardness="hard-serial",
            clearing_condition="Timeline proof is accepted.",
        )

        result = self.reader.timeline_snapshot(
            {
                "workspacePath": "C:\\Workspace\\One",
                "includeUnscheduled": True,
                "maxItems": 100,
            }
        )

        self.assertEqual(set(result["criticalPath"]["cycleItemIds"]), {"tracker-one", "timeline-one"})
        cycles = [finding for finding in result["validation"] if finding["code"] == "hard-dependency-cycle"]
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0]["severity"], "error")

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

    def _insert_timeline_items(self) -> None:
        milestone = {
            "title": "Launch milestone",
            "status": "in-progress",
            "startDate": "2026-07-01",
            "targetDate": "2026-08-15",
            "forecastDate": "2026-08-20",
            "progress": 50,
            "scheduleHealth": "on-track",
            "executionConstraint": "clear",
            "impact": 2,
            "likelihood": 2,
            "projectStateRevision": "fixture-r7",
        }
        timeline = {
            "title": "Ship timeline",
            "status": "in-progress",
            "startDate": "2026-07-10",
            "dueDate": "2026-07-20",
            "forecastDate": "2026-07-23",
            "progress": 25,
            "scheduleHealth": "on-track",
            "executionConstraint": "blocked",
            "impact": 4,
            "likelihood": 3,
            "riskDurability": "structural",
            "recoverability": "hard",
            "launchScope": "launch",
            "projectStateRevision": "fixture-r7",
            "pullRequestUrl": "https://github.com/example/repo/pull/42",
        }
        with closing(sqlite3.connect(self.db_path)) as connection:
            for tracker_id, issue_key, tracker_type, data, tags in (
                ("milestone-one", "NIM-10", "milestone", milestone, '["milestone"]'),
                ("timeline-one", "NIM-11", "timeline-item", timeline, '["timeline-item"]'),
            ):
                connection.execute(
                    """
                    INSERT INTO tracker_items (
                      id, issue_number, issue_key, type, data, workspace, content,
                      archived, type_tags, deleted_at, created, updated, last_indexed
                    ) VALUES (?, 10, ?, ?, ?, ?, '', 0, ?, NULL, ?, ?, ?)
                    """,
                    (
                        tracker_id,
                        issue_key,
                        tracker_type,
                        json.dumps(data),
                        "C:\\Workspace\\One",
                        tags,
                        "2026-07-01T00:00:00.000Z",
                        "2026-07-02T00:00:00.000Z",
                        "2026-07-02T00:00:00.000Z",
                    ),
                )
            connection.commit()
        self._insert_link(
            "link-dependency", "NIM-12", "timeline-one", "tracker-one", "depends-on",
            hardness="hard-serial", clearing_condition="Harness proof is accepted.",
        )
        self._insert_link(
            "link-contribution", "NIM-13", "timeline-one", "milestone-one", "contributes-to",
            contribution_role="primary",
        )
        self._insert_link("link-review", "NIM-14", "timeline-one", "milestone-one", "reviews", with_review_evidence=True)
        self._insert_link("link-evidence", "NIM-15", "tracker-one", "timeline-one", "evidences")
        self._insert_link("link-implementation", "NIM-16", "timeline-one", "tracker-one", "implements")
        self._insert_link("link-related", "NIM-17", "timeline-one", "tracker-one", "related", directedness="symmetric")

    def _insert_link(
        self,
        tracker_id: str,
        issue_key: str,
        source_id: str,
        target_id: str,
        relationship_type: str,
        *,
        hardness: str = "soft-coordination",
        clearing_condition: str | None = None,
        contribution_role: str = "secondary",
        directedness: str = "directed",
        with_review_evidence: bool = False,
    ) -> None:
        data = {
            "title": f"{source_id} {relationship_type} {target_id}",
            "status": "active",
            "sourceItem": {"itemId": source_id},
            "targetItem": {"itemId": target_id},
            "relationshipType": relationship_type,
            "directedness": directedness,
            "dependencyMode": "finish-to-start",
            "hardness": hardness,
            "leadLagDays": 0,
            "clearingCondition": clearing_condition,
            "owner": "Fixture Owner",
            "contributionRole": contribution_role,
            "effectiveRevision": "fixture-r7",
            "projectStateRevision": "fixture-r7",
        }
        if with_review_evidence:
            data["entryEvidence"] = [{"itemId": "tracker-one"}]
            data["exitEvidence"] = [{"itemId": "timeline-one"}]
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO tracker_items (
                  id, issue_number, issue_key, type, data, workspace, content,
                  archived, type_tags, deleted_at, created, updated, last_indexed
                ) VALUES (?, 20, ?, 'timeline-link', ?, ?, '', 0, '["timeline-link"]', NULL, ?, ?, ?)
                """,
                (
                    tracker_id,
                    issue_key,
                    json.dumps(data),
                    "C:\\Workspace\\One",
                    "2026-07-03T00:00:00.000Z",
                    "2026-07-03T00:00:00.000Z",
                    "2026-07-03T00:00:00.000Z",
                ),
            )
            connection.commit()


if __name__ == "__main__":
    unittest.main()
