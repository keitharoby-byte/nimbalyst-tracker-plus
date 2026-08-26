from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from reader.contracts import ReaderError
from reader.database import NativeTrackerReader
from reader.query import resolve_dispatch_fail_on_policy, resolve_dispatch_scope_policy

ROOT = Path(__file__).resolve().parents[2]


class QueryTraverseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = str(Path(self.tempdir.name).resolve())
        tracker_schema_directory = Path(self.workspace) / ".nimbalyst" / "trackers"
        tracker_schema_directory.mkdir(parents=True)
        self.timeline_item_schema = tracker_schema_directory / "timeline-item.yaml"
        self.timeline_item_schema.write_text(
            "type: timeline-item\n"
            "displayName: Timeline Item\n",
            encoding="utf-8",
        )
        shutil.copyfile(
            ROOT / "examples" / "tracker-plus.queries.json",
            Path(self.workspace) / ".nimbalyst" / "tracker-plus.queries.json",
        )
        self.db_path = Path(self.tempdir.name) / "fixture.sqlite"
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.executescript((ROOT / "fixtures/sql/tracker-schema-current.sql").read_text(encoding="utf-8"))
            self._insert(connection, "launch-1", "LAUNCH-RELEASE-A", "launch", {
                "title": "Feature preview", "launchKey": "RELEASE-A", "status": "active",
                "owner": "Coordinator", "audience": ["internal"], "scopeRevision": "1",
                "entryCriteria": [{}], "exitCriteria": [{}],
            })
            self._insert(connection, "member-1", "ITEM-100", "task", {"title": "Core work", "status": "in-progress", "owner": "coordinator", "tags": ["release-a"]})
            self._insert(connection, "member-2", "ITEM-101", "task", {"title": "Review work", "status": "done", "owner": "engineer"})
            self._insert(connection, "prior", "M-ALPHA", "milestone", {"title": "Prior launch", "status": "active", "targetDate": "2026-07-01"})
            self._insert(connection, "alpha-seed", "ALPHA-1", "task", {"title": "Alpha seed", "status": "open", "tags": ["release-a-tag"]})
            self._insert(connection, "demo-seed", "DEMO-1", "task", {"title": "Demo seed", "status": "open", "tags": ["release-b-tag"]})
            self._insert(connection, "shared-evidence", "EVIDENCE-1", "document", {"title": "Shared native evidence", "status": "active"})
            self._link(connection, "link-member-1", "REL-1", "member-1", "launch-1", "part-of-launch", scope_role="core")
            self._link(connection, "link-member-2", "REL-2", "member-2", "launch-1", "part-of-launch", scope_role="review")
            self._link(connection, "link-blocker", "REL-3", "member-1", "prior", "depends-on", hardness="hard-serial")
            self._link(connection, "link-alpha-evidence", "REL-ALPHA", "alpha-seed", "shared-evidence", "related")
            self._link(connection, "link-demo-evidence", "REL-DEMO", "demo-seed", "shared-evidence", "related")
            for index in range(205):
                self._insert(connection, f"page-{index:03d}", f"PAGE-{index:03d}", "task", {"title": f"Paged {index}", "status": "open", "updated": f"2026-07-16T00:{index % 60:02d}:00Z"})
            connection.commit()
        self.reader = NativeTrackerReader(self.db_path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _insert(self, connection: sqlite3.Connection, item_id: str, issue_key: str, item_type: str, data: dict[str, object]) -> None:
        connection.execute(
            """INSERT INTO tracker_items (id, issue_number, issue_key, type, data, workspace, content, archived, type_tags, deleted_at, created, updated, last_indexed)
               VALUES (?, 1, ?, ?, ?, ?, '', 0, ?, NULL, '2026-07-16T00:00:00Z', ?, '2026-07-16T00:00:00Z')""",
            (item_id, issue_key, item_type, json.dumps(data), self.workspace, json.dumps([item_type]), data.get("updated", "2026-07-16T00:00:00Z")),
        )

    def _link(self, connection: sqlite3.Connection, item_id: str, issue_key: str, source: str, target: str, relationship_type: str, *, scope_role: str | None = None, hardness: str | None = None, status: str = "active", clearing_condition: str | None = None, owner: str | None = None, contribution_role: str | None = None) -> None:
        data = {"title": issue_key, "sourceItem": {"itemId": source}, "targetItem": {"itemId": target}, "relationshipType": relationship_type, "status": status}
        if scope_role: data["scopeRole"] = scope_role
        if hardness: data["hardness"] = hardness
        if clearing_condition: data["clearingCondition"] = clearing_condition
        if owner: data["owner"] = owner
        if contribution_role: data["contributionRole"] = contribution_role
        self._insert(connection, item_id, issue_key, "timeline-link", data)

    def _write_registry_override(self, payload: dict[str, object]) -> None:
        path = Path(self.workspace) / ".nimbalyst" / "tracker-plus.registry.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _write_dispatch_scope_policy(self, scope_policy: dict[str, object]) -> None:
        path = Path(self.workspace) / ".nimbalyst" / "tracker-plus.queries.json"
        catalog = json.loads(path.read_text(encoding="utf-8"))
        query = catalog["queries"]["dispatch-eligible-work-v1"]
        query["version"] = 2
        if "rootKeys" not in query["optionalParams"]:
            query["optionalParams"].append("rootKeys")
        query["definition"]["scopePolicy"] = scope_policy
        path.write_text(json.dumps(catalog), encoding="utf-8")

    def _write_dispatch_fail_on(self, fail_on: dict[str, object]) -> None:
        path = Path(self.workspace) / ".nimbalyst" / "tracker-plus.queries.json"
        catalog = json.loads(path.read_text(encoding="utf-8"))
        query = catalog["queries"]["dispatch-eligible-work-v1"]
        query["version"] = 3
        query["definition"]["failOn"] = fail_on
        path.write_text(json.dumps(catalog), encoding="utf-8")

    @staticmethod
    def _ready_dispatch_fields() -> dict[str, object]:
        return {
            "title": "Ready packet",
            "status": "ready",
            "packetRevision": "revision-1",
            "currentRevision": "revision-1",
            "qaEvidenceRevision": "revision-1",
            "qaStatus": "passed",
            "holdState": "clear",
            "databaseRouteState": "approved",
            "custodyState": "clear",
            "survivorState": "unique",
            "collisionState": "clear",
            "executionConstraint": "clear",
        }

    def _mapped_dispatch_override(self) -> dict[str, object]:
        policy = json.loads(json.dumps(self.reader._registry["dispatchPolicy"]))
        policy["readyStatuses"] = ["to-do"]
        return {
            "dispatchPolicy": policy,
            "dispatchEvidence": {
                "packetRevision": {"sources": [{"kind": "tag-prefix", "prefix": "packet-revision:"}]},
                "currentRevision": {"sources": [{"kind": "tag-prefix", "prefix": "current-revision:"}]},
                "qaEvidenceRevision": {"sources": [{"kind": "field", "field": "qaCheckRevision"}]},
                "qaStatus": {"sources": [{"kind": "tag", "tag": "qa-signed-off", "value": "passed"}]},
                "holdState": {"sources": [{"kind": "tag", "tag": "hold-clear", "value": "clear"}]},
                "databaseRouteState": {"sources": [{"kind": "field", "field": "routeState"}]},
                "custodyState": {"sources": [{"kind": "tag", "tag": "custody-vacant", "value": "vacant"}]},
                "survivorState": {"sources": [{"kind": "field", "field": "survivor"}]},
                "collisionState": {"sources": [{"kind": "tag", "tag": "collision-clear", "value": "clear"}]},
            },
        }

    @staticmethod
    def _mapped_dispatch_fields() -> dict[str, object]:
        return {
            "status": "to-do",
            "tags": [
                "packet-revision:rev-configured",
                "current-revision:rev-configured",
                "qa-signed-off",
                "hold-clear",
                "custody-vacant",
                "collision-clear",
            ],
            "qaCheckRevision": "rev-configured",
            "routeState": "approved",
            "survivor": "unique",
        }

    def _assert_graph_page_signals(self, page: dict[str, object]) -> None:
        self.assertIsInstance(page.get("hasMore"), bool)
        has_more = page["nextCursor"] is not None
        self.assertEqual(page["hasMore"], has_more)
        self.assertEqual(page["continuationRequired"], has_more)
        self.assertEqual(
            page["resultsComplete"],
            not has_more and not bool(page.get("truncated")),
        )

    def test_saved_role_query_and_parameterized_sql_value(self) -> None:
        result = self.reader.query_items({"workspacePath": self.workspace, "savedQuery": {"id": "role-active-work-and-attention", "params": {"roleId": "coordinator"}}})
        self.assertEqual([node["id"] for node in result["nodes"]], ["launch-1", "member-1"])
        self.assertNotIn("timeline-link", {node["type"] for node in result["nodes"]})
        injection = self.reader.query_items({"workspacePath": self.workspace, "where": {"field": "title", "op": "eq", "value": "' OR 1=1 --"}})
        self.assertEqual(injection["page"]["totalCount"], 0)

    def test_predicate_validation_is_query_local(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "unrelated-launch", "LAUNCH-UNRELATED", "launch", {
                "title": "Unrelated incomplete launch",
                "launchKey": "UNRELATED",
                "status": "active",
            })
            connection.commit()

        result = self.reader.query_items({
            "workspacePath": self.workspace,
            "where": {"field": "issueKey", "op": "eq", "value": "PAGE-000"},
        })

        self.assertEqual([node["id"] for node in result["nodes"]], ["page-000"])
        self.assertEqual(result["validation"]["state"], "pass")
        scope = result["query"]["validationScope"]
        self.assertEqual(scope["type"], "query-local")
        self.assertEqual(scope["selectedNodeCount"], 1)
        self.assertEqual(scope["contextNodeCount"], 0)
        self.assertEqual(scope["relationshipCount"], 0)
        self.assertEqual(len(scope["fingerprint"]), 64)

        role_result = self.reader.query_items({
            "workspacePath": self.workspace,
            "savedQuery": {
                "id": "role-active-work-and-attention",
                "params": {"roleId": "coordinator"},
            },
        })
        self.assertEqual(role_result["validation"]["state"], "pass")
        self.assertNotIn(
            "unrelated-launch",
            {
                item_id
                for finding in role_result["validation"]["findings"]
                for item_id in finding["itemIds"]
            },
        )

    def test_selected_launch_validation_carries_membership_context(self) -> None:
        result = self.reader.query_items({
            "workspacePath": self.workspace,
            "where": {"field": "issueKey", "op": "eq", "value": "LAUNCH-RELEASE-A"},
        })

        self.assertEqual(result["validation"]["state"], "pass")
        scope = result["query"]["validationScope"]
        self.assertEqual(scope["type"], "query-local")
        self.assertGreaterEqual(scope["contextNodeCount"], 2)
        self.assertGreaterEqual(scope["relationshipCount"], 2)

        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "selected-incomplete-launch", "LAUNCH-SELECTED-INCOMPLETE", "launch", {
                "title": "Selected incomplete launch",
                "launchKey": "SELECTED-INCOMPLETE",
                "status": "active",
            })
            connection.commit()
        incomplete = self.reader.query_items({
            "workspacePath": self.workspace,
            "where": {
                "field": "issueKey",
                "op": "eq",
                "value": "LAUNCH-SELECTED-INCOMPLETE",
            },
        })
        self.assertEqual(incomplete["validation"]["state"], "fail")
        self.assertIn(
            "launch-fields-incomplete",
            {finding["code"] for finding in incomplete["validation"]["findings"]},
        )

    def test_selected_launch_duplicate_membership_remains_terminal(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._link(
                connection,
                "query-membership-duplicate",
                "REL-QUERY-DUPLICATE",
                "member-1",
                "launch-1",
                "part-of-launch",
                scope_role="core",
            )
            connection.commit()

        result = self.reader.query_items({
            "workspacePath": self.workspace,
            "where": {"field": "issueKey", "op": "eq", "value": "LAUNCH-RELEASE-A"},
        })

        self.assertEqual(result["validation"]["state"], "fail")
        self.assertIn(
            "duplicate-active-membership",
            {finding["code"] for finding in result["validation"]["findings"]},
        )

    def test_saved_role_result_with_legacy_relationships_normalizes_without_crashing(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(
                connection,
                "legacy-role-item",
                "ITEM-LEGACY-ROLE",
                "task",
                {
                    "title": "Legacy relationship fixture",
                    "status": "ready",
                    "owner": "legacy-reviewer",
                    "blockers": [{"itemId": "prior"}],
                },
            )
            connection.commit()
        (Path(self.workspace) / ".nimbalyst" / "tracker-plus.registry.json").write_text(
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

        result = self.reader.query_items({
            "workspacePath": self.workspace,
            "savedQuery": {
                "id": "role-active-work-and-attention",
                "params": {"roleId": "legacy-reviewer"},
            },
        })

        self.assertEqual([node["id"] for node in result["nodes"]], ["legacy-role-item"])
        self.assertEqual(
            [
                (
                    edge["sourceId"],
                    edge["relationshipType"],
                    edge["targetId"],
                    edge["scopeRole"],
                    edge["contributionRole"],
                )
                for edge in result["edges"]
            ],
            [("legacy-role-item", "depends-on", "prior", None, None)],
        )

    def test_workspace_query_catalog_changes_queries_without_code_changes(self) -> None:
        query_catalog = {
            "version": 1,
            "queries": {
                "workspace-open-items": {
                    "version": 1,
                    "kind": "predicate",
                    "params": [],
                    "label": "Workspace-defined open items",
                    "definition": {
                        "where": {
                            "field": "status",
                            "op": "eq",
                            "value": "open",
                        },
                        "sort": [{"field": "id", "direction": "asc"}],
                        "limit": 10,
                    },
                },
            },
        }
        (Path(self.workspace) / ".nimbalyst" / "tracker-plus.queries.json").write_text(
            json.dumps(query_catalog),
            encoding="utf-8",
        )
        result = self.reader.query_items({
            "workspacePath": self.workspace,
            "savedQuery": {
                "id": "workspace-open-items",
                "params": {},
            },
        })
        self.assertTrue(result["nodes"])
        self.assertTrue(all(node["status"] == "open" for node in result["nodes"]))
        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph({
                "workspacePath": self.workspace,
                "savedQuery": {
                    "id": "launch-open-reviews",
                    "params": {"launchKey": "RELEASE-A"},
                },
            })
        self.assertEqual(raised.exception.code, "SAVED_QUERY_NOT_FOUND")

    def test_workspace_catalog_can_define_a_composed_query_without_code_changes(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(
                connection,
                "workspace-composed-root",
                "CONTROL-1",
                "feature",
                {
                    "title": "Workspace selected control root",
                    "status": "active",
                },
            )
            self._link(
                connection,
                "workspace-composed-edge",
                "CONTROL-REL-1",
                "workspace-composed-root",
                "prior",
                "depends-on",
                hardness="hard-serial",
                clearing_condition="Prior control is complete",
                owner="reviewer",
            )
            connection.commit()
        (Path(self.workspace) / ".nimbalyst" / "tracker-plus.queries.json").write_text(
            json.dumps({
                "version": 1,
                "queries": {
                    "workspace-composed-control": {
                        "version": 1,
                        "kind": "composed",
                        "params": [],
                        "definition": {
                            "mode": "composed-v1",
                            "select": {
                                "where": {
                                    "field": "issueKey",
                                    "op": "eq",
                                    "value": "CONTROL-1",
                                },
                                "limit": 1,
                            },
                            "traverse": {
                                "expand": {
                                    "relationshipTypes": ["depends-on"],
                                    "direction": "outgoing",
                                    "maxDepth": 1,
                                    "edgeWhere": {"status": ["active"]},
                                    "externalEndpointBehavior": "boundary",
                                },
                            },
                            "failOn": {"truncation": True, "validation": False},
                        },
                    },
                },
            }),
            encoding="utf-8",
        )

        result = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "savedQuery": {
                "id": "workspace-composed-control",
                "params": {},
            },
        })

        self._assert_graph_page_signals(result["page"])
        self.assertEqual([node["id"] for node in result["nodes"]], ["workspace-composed-root"])
        self.assertEqual([node["id"] for node in result["boundaryNodes"]], ["prior"])
        self.assertEqual([edge["id"] for edge in result["edges"]], ["workspace-composed-edge"])
        self.assertTrue(result["query"]["selection"]["complete"])

    def test_empty_composed_traversal_has_terminal_page_signals(self) -> None:
        (Path(self.workspace) / ".nimbalyst" / "tracker-plus.queries.json").write_text(
            json.dumps({
                "version": 1,
                "queries": {
                    "empty-composed": {
                        "version": 1,
                        "kind": "composed",
                        "params": [],
                        "definition": {
                            "mode": "composed-v1",
                            "select": {
                                "where": {
                                    "field": "id",
                                    "op": "eq",
                                    "value": "absent-root",
                                },
                                "limit": 1,
                            },
                            "traverse": {},
                            "failOn": {"truncation": True, "validation": False},
                        },
                    },
                },
            }),
            encoding="utf-8",
        )

        result = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "savedQuery": {"id": "empty-composed", "params": {}},
        })

        self.assertEqual(result["nodes"], [])
        self._assert_graph_page_signals(result["page"])
        self.assertFalse(result["page"]["hasMore"])
        self.assertTrue(result["page"]["resultsComplete"])

    def test_walk_readiness_is_not_injected_or_required(self) -> None:
        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph({
                "workspacePath": self.workspace,
                "savedQuery": {"id": "walk-ready-milestones", "params": {}},
            })
        self.assertEqual(raised.exception.code, "SAVED_QUERY_NOT_FOUND")

        result = self.reader.query_items({
            "workspacePath": self.workspace,
            "where": {"field": "issueKey", "op": "exists", "value": True},
            "limit": 10,
        })
        self.assertFalse(
            any(
                finding["code"].startswith("walk-")
                for finding in result["validation"]["findings"]
            )
        )

    def test_query_cursor_reconciles_total_count(self) -> None:
        params = {"workspacePath": self.workspace, "where": {"field": "issueKey", "op": "exists", "value": True}, "sort": [{"field": "id", "direction": "asc"}], "limit": 200}
        first = self.reader.query_items(params)
        second = self.reader.query_items({**params, "cursor": first["page"]["nextCursor"]})
        ids = [node["id"] for node in first["nodes"] + second["nodes"]]
        self.assertEqual(len(ids), first["page"]["totalCount"])
        self.assertEqual(len(ids), len(set(ids)))

    def test_query_page_signals_cover_first_middle_terminal_and_empty_pages(self) -> None:
        params = {
            "workspacePath": self.workspace,
            "where": {"field": "issueKey", "op": "exists", "value": True},
            "sort": [{"field": "id", "direction": "asc"}],
            "limit": 100,
        }
        pages: list[dict[str, object]] = []
        cursor: str | None = None
        for _page_number in range(10):
            result = self.reader.query_items({
                **params,
                **({"cursor": cursor} if cursor else {}),
            })
            self._assert_graph_page_signals(result["page"])
            pages.append(result["page"])
            cursor = result["page"]["nextCursor"]
            if cursor is None:
                break
        else:
            self.fail("query cursor continuation did not terminate")

        self.assertGreaterEqual(len(pages), 3)
        self.assertTrue(pages[0]["hasMore"])
        self.assertTrue(pages[1]["hasMore"])
        self.assertFalse(pages[-1]["hasMore"])

        empty = self.reader.query_items({
            "workspacePath": self.workspace,
            "where": {"field": "id", "op": "eq", "value": "absent-item"},
        })
        self._assert_graph_page_signals(empty["page"])
        self.assertFalse(empty["page"]["hasMore"])
        self.assertTrue(empty["page"]["resultsComplete"])

    def test_query_response_truncation_can_be_fully_retrieved_by_cursor(self) -> None:
        params = {
            "workspacePath": self.workspace,
            "where": {"field": "issueKey", "op": "exists", "value": True},
            "sort": [{"field": "id", "direction": "asc"}],
            "limit": 200,
        }
        ids: list[str] = []
        cursor: str | None = None
        total_count: int | None = None
        saw_response_truncation = False

        with patch("reader.database.MAX_RESULT_BYTES", 25_000):
            for _page_number in range(20):
                result = self.reader.query_items({
                    **params,
                    **({"cursor": cursor} if cursor else {}),
                })
                total_count = total_count if total_count is not None else result["page"]["totalCount"]
                ids.extend(node["id"] for node in result["nodes"])
                saw_response_truncation = saw_response_truncation or result["page"]["responseTruncated"]
                cursor = result["page"]["nextCursor"]
                self._assert_graph_page_signals(result["page"])
                if not result["page"]["continuationRequired"]:
                    break
            else:
                self.fail("cursor continuation did not terminate")

        self.assertTrue(saw_response_truncation)
        self.assertEqual(len(ids), total_count)
        self.assertEqual(len(ids), len(set(ids)))

    def test_query_complexity_and_foreign_cursor_fail(self) -> None:
        with self.assertRaises(ReaderError) as raised:
            self.reader.query_items({"workspacePath": self.workspace, "where": {"field": "unknown", "op": "eq", "value": "x"}})
        self.assertEqual(raised.exception.code, "FIELD_NOT_QUERYABLE")
        with self.assertRaises(ReaderError) as raised:
            self.reader.query_items({"workspacePath": self.workspace, "where": {"field": "id", "op": "eq", "value": "x"}, "cursor": "bad"})
        self.assertEqual(raised.exception.code, "CURSOR_INVALID")

    def test_launch_traversal_separates_boundary_and_rolls_up(self) -> None:
        result = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "roots": ["RELEASE-A"],
            "membership": {"relationshipTypes": ["part-of-launch"], "direction": "incoming", "status": ["active"], "maxDepth": 1},
            "expand": {"relationshipTypes": ["depends-on"], "direction": "both", "maxDepth": 1, "edgeWhere": {"status": ["active"]}, "externalEndpointBehavior": "boundary"},
        })
        self.assertEqual({node["id"] for node in result["nodes"]}, {"launch-1", "member-1", "member-2"})
        self.assertEqual([node["id"] for node in result["boundaryNodes"]], ["prior"])
        launch = next(node for node in result["nodes"] if node["id"] == "launch-1")
        self.assertEqual(launch["launchRollup"]["derivedProgress"], 0)
        self.assertEqual(launch["launchRollup"]["activeHardBlockers"], 1)
        self.assertEqual(result["validation"]["state"], "pass")

    def test_legacy_launch_tags_do_not_change_membership_rollups_or_validation(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "launch-2", "LAUNCH-RELEASE-B", "launch", {
                "title": "Second launch", "launchKey": "RELEASE-B", "status": "draft",
            })
            self._insert(connection, "legacy-tag-only", "ITEM-LEGACY-TAG", "task", {
                "title": "Migration-tagged only", "status": "done",
                "tags": ["release-a"],
            })
            self._insert(connection, "cross-launch-member", "ITEM-CROSS-LAUNCH", "task", {
                "title": "Member of A tagged for B", "status": "done",
                "tags": ["release-b"],
            })
            self._link(
                connection,
                "link-cross-launch-member",
                "REL-CROSS-LAUNCH",
                "cross-launch-member",
                "launch-1",
                "part-of-launch",
                scope_role="core",
            )
            connection.commit()

        result = self.reader.query_items({
            "workspacePath": self.workspace,
            "where": {"field": "type", "op": "eq", "value": "launch"},
        })

        launches = {node["id"]: node for node in result["nodes"]}
        self.assertEqual(result["validation"]["state"], "pass")
        self.assertNotIn(
            "tag-membership-mismatch",
            {finding["code"] for finding in result["validation"]["findings"]},
        )
        # Only the two typed core members contribute to RELEASE-A. The
        # completed tag-only migration item must not increase its rollup.
        self.assertEqual(launches["launch-1"]["launchRollup"]["derivedProgress"], 50)
        # Membership in RELEASE-A must not make a RELEASE-B tag authoritative.
        self.assertEqual(launches["launch-2"]["launchRollup"]["derivedProgress"], 0)

        release_b = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "roots": ["RELEASE-B"],
            "membership": {
                "relationshipTypes": ["part-of-launch"],
                "direction": "incoming",
                "status": ["active"],
                "maxDepth": 1,
            },
        })
        self.assertEqual([node["id"] for node in release_b["nodes"]], ["launch-2"])
        self.assertEqual(release_b["validation"]["state"], "pass")

    def test_launch_traversal_stops_at_boundary_nodes(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "beyond-prior", "M-BEFORE-ALPHA", "milestone", {"title": "Earlier launch", "status": "active"})
            self._link(connection, "link-beyond", "REL-BEYOND", "prior", "beyond-prior", "depends-on")
            connection.commit()

        result = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "roots": ["RELEASE-A"],
            "membership": {"relationshipTypes": ["part-of-launch"], "direction": "incoming", "status": ["active"], "maxDepth": 1},
            "expand": {"relationshipTypes": ["depends-on"], "direction": "both", "maxDepth": 2, "edgeWhere": {"status": ["active"]}, "externalEndpointBehavior": "boundary"},
        })

        self.assertEqual([node["id"] for node in result["boundaryNodes"]], ["prior"])
        self.assertNotIn("beyond-prior", {node["id"] for node in [*result["nodes"], *result["boundaryNodes"]]})
        self.assertNotIn("link-beyond", {edge["id"] for edge in result["edges"]})

    def test_launch_rooted_snapshot_reports_members_and_boundaries(self) -> None:
        snapshot = self.reader.timeline_snapshot({"workspacePath": self.workspace, "includeUnscheduled": True, "maxItems": 50, "launch": "RELEASE-A"})
        self.assertEqual(snapshot["source"]["rootLaunch"], "RELEASE-A")
        self.assertEqual(snapshot["source"]["membership"], {"memberCount": 2, "boundaryCount": 1})
        self.assertFalse(snapshot["page"]["queryTruncated"])

    def test_milestone_rooted_snapshot_preserves_member_implementation_edge(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "milestone-root", "MS-ROOT", "milestone", {
                "title": "Milestone root", "status": "active", "targetDate": "2026-09-01",
            })
            self._insert(connection, "milestone-task", "TASK-1", "task", {
                "title": "Milestone task",
                "status": "active",
                "collection": {"itemId": "milestone-root"},
            })
            self._insert(connection, "delivery-plan", "PLAN-1", "plan", {
                "title": "Delivery plan", "status": "active",
            })
            self._link(
                connection,
                "link-implementation",
                "REL-IMPLEMENTATION",
                "milestone-task",
                "delivery-plan",
                "implements",
            )
            connection.commit()

        snapshot = self.reader.timeline_snapshot({
            "workspacePath": self.workspace,
            "includeUnscheduled": True,
            "maxItems": 50,
            "launch": "MS-ROOT",
        })

        relationship = next(
            edge for edge in snapshot["relationships"]
            if edge["id"] == "link-implementation"
        )
        self.assertEqual(
            (relationship["sourceId"], relationship["relationshipType"], relationship["targetId"]),
            ("milestone-task", "implements", "delivery-plan"),
        )
        self.assertFalse(any(finding["severity"] == "error" for finding in snapshot["validation"]))
        receipt = snapshot["source"]["relationshipProjection"]
        self.assertTrue(receipt["reconciled"])
        self.assertEqual(
            receipt["normalizedSourceCount"],
            receipt["emittedCount"] + receipt["excludedCount"],
        )

    def test_launch_snapshot_surfaces_nested_lane_timeline_items_as_boundary(self) -> None:
        # Regression for issue #23: registered timeline-item walk steps that are
        # part-of-launch members of a nested lane (itself a launch container)
        # must still be projected into the launch snapshot as boundary context,
        # not silently dropped.
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "lane-a", "LANE-A", "launch", {
                "title": "Alpha lane", "launchKey": "LANE-A", "status": "active",
            })
            self._link(connection, "link-lane-a", "REL-LANE-A", "lane-a", "launch-1", "part-of-launch", scope_role="core")
            for index in (1, 2, 3):
                self._insert(connection, f"walk-{index}", f"WALK-{index}", "timeline-item", {
                    "title": f"Walk step {index}", "status": "waiting", "priority": "critical",
                })
                self._link(connection, f"link-walk-{index}", f"REL-WALK-{index}", f"walk-{index}", "lane-a", "part-of-launch", scope_role="core")
            connection.commit()

        snapshot = self.reader.timeline_snapshot({
            "workspacePath": self.workspace,
            "includeUnscheduled": True,
            "maxItems": 50,
            "launch": "RELEASE-A",
        })

        walk_ids = {"walk-1", "walk-2", "walk-3"}
        projected = {item["id"] for item in snapshot["items"]}
        self.assertTrue(walk_ids.issubset(projected))
        for item in snapshot["items"]:
            if item["id"] in walk_ids:
                self.assertEqual(item["status"], "waiting")
                self.assertTrue(item["boundary"])
                self.assertFalse(item["launchMember"])
        # Nested lane members are context, not launch members or milestones.
        self.assertNotIn("walk-1", {item["id"] for item in snapshot["milestones"]})
        discovery = snapshot["source"]["schemaDiscovery"]
        self.assertEqual(discovery["state"], "registered")
        self.assertEqual(discovery["liveRowCount"], 3)
        self.assertEqual(discovery["projectedRowCount"], 3)
        self.assertTrue(discovery["allLiveRowsProjected"])

    def test_tag_seeded_snapshots_are_independent_and_include_boundaries(self) -> None:
        alpha = self.reader.timeline_snapshot({
            "workspacePath": self.workspace,
            "includeUnscheduled": True,
            "maxItems": 50,
            "selector": {"launchTags": [" RELEASE-A-TAG "]},
        })
        demo = self.reader.timeline_snapshot({
            "workspacePath": self.workspace,
            "includeUnscheduled": True,
            "maxItems": 50,
            "selector": {"launchTags": ["release-b-tag"]},
        })

        self.assertEqual({item["id"] for item in alpha["items"]}, {"alpha-seed", "shared-evidence"})
        self.assertEqual({edge["id"] for edge in alpha["relationships"]}, {"link-alpha-evidence"})
        self.assertTrue(next(item for item in alpha["items"] if item["id"] == "shared-evidence")["boundary"])
        self.assertEqual({item["id"] for item in demo["items"]}, {"demo-seed", "shared-evidence"})
        self.assertEqual({edge["id"] for edge in demo["relationships"]}, {"link-demo-evidence"})
        self.assertNotEqual(alpha["source"]["outputHash"], demo["source"]["outputHash"])
        self.assertEqual(alpha["source"]["selector"]["launchTags"], ["release-a-tag"])
        self.assertEqual(alpha["source"]["selector"]["seedIds"], ["alpha-seed"])
        self.assertEqual(alpha["source"]["closure"]["strategy"], "active-one-hop-boundary")
        self.assertEqual(alpha["source"]["truncation"], {"source": False, "cap": False, "response": False})

    def test_tag_seeded_multi_tag_union_and_identity_are_deterministic(self) -> None:
        params = {
            "workspacePath": self.workspace,
            "includeUnscheduled": True,
            "maxItems": 50,
            "selector": {"launchTags": ["RELEASE-B-TAG", "release-a-tag"]},
        }
        first = self.reader.timeline_snapshot(params)
        second = self.reader.timeline_snapshot({
            **params,
            "selector": {"launchTags": ["release-a-tag", "release-b-tag"]},
        })
        self.assertEqual(first["source"]["outputHash"], second["source"]["outputHash"])
        self.assertEqual(first["source"]["generationId"], second["source"]["generationId"])
        self.assertEqual(first["source"]["selector"]["launchTags"], ["release-a-tag", "release-b-tag"])
        self.assertEqual(
            {item["id"] for item in first["items"]},
            {"alpha-seed", "demo-seed", "shared-evidence"},
        )
        self.assertEqual(
            {edge["id"] for edge in first["relationships"]},
            {"link-alpha-evidence", "link-demo-evidence"},
        )

        alpha_before = self.reader.timeline_snapshot({
            "workspacePath": self.workspace,
            "selector": {"launchTags": ["release-a-tag"]},
        })["source"]["outputHash"]
        with closing(sqlite3.connect(self.db_path)) as connection:
            demo_data = {"title": "Changed Demo seed", "status": "open", "tags": ["release-b-tag"]}
            connection.execute(
                "UPDATE tracker_items SET data=? WHERE id='demo-seed'",
                (json.dumps(demo_data),),
            )
            connection.commit()
        alpha_after_demo_change = self.reader.timeline_snapshot({
            "workspacePath": self.workspace,
            "selector": {"launchTags": ["release-a-tag"]},
        })["source"]["outputHash"]
        self.assertEqual(alpha_before, alpha_after_demo_change)

        with closing(sqlite3.connect(self.db_path)) as connection:
            alpha_data = {"title": "Changed Alpha seed", "status": "open", "tags": ["release-a-tag"]}
            connection.execute(
                "UPDATE tracker_items SET data=? WHERE id='alpha-seed'",
                (json.dumps(alpha_data),),
            )
            connection.commit()
        alpha_after_alpha_change = self.reader.timeline_snapshot({
            "workspacePath": self.workspace,
            "selector": {"launchTags": ["release-a-tag"]},
        })["source"]["outputHash"]
        self.assertNotEqual(alpha_before, alpha_after_alpha_change)

    def test_tag_selector_rejects_invalid_empty_and_overflowed_projections(self) -> None:
        invalid_params = [
            {"selector": {"launchTags": []}},
            {"selector": {"launchTags": ["Alpha", " alpha "]}},
            {"selector": {"launchTags": ["alpha"], "other": True}},
            {"selector": {"launchTags": ["alpha"]}, "launch": "RELEASE-A"},
        ]
        for extra in invalid_params:
            with self.subTest(extra=extra), self.assertRaises(ReaderError) as raised:
                self.reader.timeline_snapshot({"workspacePath": self.workspace, **extra})
            self.assertEqual(raised.exception.code, "INVALID_PARAMS")

        with self.assertRaises(ReaderError) as raised:
            self.reader.timeline_snapshot({
                "workspacePath": self.workspace,
                "selector": {"launchTags": ["missing-launch"]},
            })
        self.assertEqual(raised.exception.code, "SELECTOR_NO_MATCH")

        with self.assertRaises(ReaderError) as raised:
            self.reader.timeline_snapshot({
                "workspacePath": self.workspace,
                "maxItems": 1,
                "selector": {"launchTags": ["release-a-tag"]},
            })
        self.assertEqual(raised.exception.code, "RESULT_LIMIT_EXCEEDED")

        with patch("reader.database.MAX_TIMELINE_SELECTOR_SOURCE_ITEMS", 1):
            with self.assertRaises(ReaderError) as raised:
                self.reader.timeline_snapshot({
                    "workspacePath": self.workspace,
                    "selector": {"launchTags": ["release-a-tag"]},
                })
        self.assertEqual(raised.exception.code, "SOURCE_LIMIT_EXCEEDED")

        with patch("reader.database.MAX_RESULT_BYTES", 1_000):
            with self.assertRaises(ReaderError) as raised:
                self.reader.timeline_snapshot({
                    "workspacePath": self.workspace,
                    "selector": {"launchTags": ["release-a-tag"]},
                })
        self.assertEqual(raised.exception.code, "RESPONSE_TOO_LARGE")

    def test_tag_selector_fails_on_incident_invalid_or_missing_endpoints(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._link(connection, "link-alpha-missing", "REL-MISSING", "alpha-seed", "missing-id", "related")
            connection.commit()
        with self.assertRaises(ReaderError) as raised:
            self.reader.timeline_snapshot({
                "workspacePath": self.workspace,
                "selector": {"launchTags": ["release-a-tag"]},
            })
        self.assertEqual(raised.exception.code, "VALIDATION_FAILED")

        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("DELETE FROM tracker_items WHERE id='link-alpha-missing'")
            self._link(connection, "link-alpha-invalid", "REL-INVALID", "alpha-seed", "shared-evidence", "unknown-kind")
            connection.commit()
        with self.assertRaises(ReaderError) as raised:
            self.reader.timeline_snapshot({
                "workspacePath": self.workspace,
                "selector": {"launchTags": ["release-a-tag"]},
            })
        self.assertEqual(raised.exception.code, "VALIDATION_FAILED")

    def test_all_registry_relationship_types_normalize(self) -> None:
        existing = {"part-of-launch", "depends-on"}
        with closing(sqlite3.connect(self.db_path)) as connection:
            for index, relationship_type in enumerate(sorted(set(self.reader._registry["relationshipTypes"]) - existing)):
                self._link(connection, f"extra-link-{index}", f"EXTRA-{index}", "member-1", "prior", relationship_type)
            connection.commit()
        snapshot = self.reader.timeline_snapshot({"workspacePath": self.workspace, "includeUnscheduled": True, "maxItems": 100})
        self.assertNotIn("invalid-relationship-type", {finding["code"] for finding in snapshot["validation"]})
        self.assertEqual({edge["relationshipType"] for edge in snapshot["relationships"]}, set(self.reader._registry["relationshipTypes"]))

    def test_launch_lifecycle_findings_are_visible_in_query(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "launch-bad", "LAUNCH-BAD", "launch", {
                "title": "Bad launch", "launchKey": "RELEASE-A", "status": "active",
                "actualDate": "2026-07-16", "progress": 50,
            })
            self._insert(connection, "launch-missing", "LAUNCH-MISSING", "launch", {"title": "Missing key", "status": "draft"})
            connection.commit()
        result = self.reader.query_items({"workspacePath": self.workspace, "where": {"field": "type", "op": "eq", "value": "launch"}})
        codes = {finding["code"] for finding in result["validation"]["findings"]}
        self.assertTrue({"launch-key-missing", "launch-key-duplicate", "launch-fields-incomplete", "launch-actual-date-unreleased", "launch-progress-hand-set"}.issubset(codes))
        self.assertEqual(result["validation"]["state"], "fail")

    # --- Issue #2: relationship registry / schema adapter alignment ---

    def test_valid_membership_projects_clean_and_unknown_type_fails_closed(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._link(connection, "link-unknown", "REL-UNK", "member-1", "prior", "totally-unknown")
            connection.commit()
        snapshot = self.reader.timeline_snapshot({"workspacePath": self.workspace, "includeUnscheduled": True, "maxItems": 100})
        membership_edges = [edge for edge in snapshot["relationships"] if edge["relationshipType"] == "part-of-launch"]
        self.assertIn("REL-1", {edge["issueKey"] for edge in membership_edges})
        # The schema-valid membership row carries no error/warning of its own.
        self.assertEqual([finding for finding in snapshot["validation"] if "link-member-1" in finding["relationshipIds"]], [])
        # A genuinely unknown relationship type still fails closed.
        unknown_findings = [finding for finding in snapshot["validation"] if finding["code"] == "invalid-relationship-type"]
        self.assertEqual([finding["relationshipIds"] for finding in unknown_findings], [["link-unknown"]])

    def test_membership_scope_role_violations_fail_closed(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "member-3", "ITEM-102", "task", {"title": "Scope role bogus", "status": "open"})
            self._insert(connection, "member-4", "ITEM-103", "task", {"title": "Scope conflict", "status": "open"})
            self._link(connection, "link-bad-scope", "REL-BAD", "member-3", "launch-1", "part-of-launch", scope_role="bogus")
            self._link(connection, "link-conflict", "REL-CONFLICT", "member-4", "launch-1", "part-of-launch", scope_role="core", contribution_role="primary")
            connection.commit()
        snapshot = self.reader.timeline_snapshot({"workspacePath": self.workspace, "includeUnscheduled": True, "maxItems": 100})
        by_code = {finding["code"]: finding for finding in snapshot["validation"]}
        self.assertIn("scope-role-invalid", by_code)
        self.assertIn("link-bad-scope", by_code["scope-role-invalid"]["relationshipIds"])
        self.assertIn("scope-role-conflict", by_code)
        self.assertIn("link-conflict", by_code["scope-role-conflict"]["relationshipIds"])

    def test_hard_serial_clearing_condition_is_emitted_and_required(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "gate", "ITEM-110", "task", {"title": "Gate", "status": "open"})
            self._link(connection, "link-cleared", "REL-CLEARED", "member-1", "gate", "depends-on", hardness="hard-serial", clearing_condition="Alpha exit review signed off", owner="engineer")
            connection.commit()
        snapshot = self.reader.timeline_snapshot({"workspacePath": self.workspace, "includeUnscheduled": True, "maxItems": 100})
        cleared = next(edge for edge in snapshot["relationships"] if edge["issueKey"] == "REL-CLEARED")
        self.assertEqual(cleared["clearingCondition"], "Alpha exit review signed off")
        # A fully-controlled hard-serial edge produces no controls-missing finding.
        self.assertNotIn("link-cleared", [rel for finding in snapshot["validation"] if finding["code"] == "hard-serial-controls-missing" for rel in finding["relationshipIds"]])
        # The uncontrolled seed blocker (REL-3) still fails closed.
        missing = [finding for finding in snapshot["validation"] if finding["code"] == "hard-serial-controls-missing"]
        self.assertIn("link-blocker", [rel for finding in missing for rel in finding["relationshipIds"]])

    def test_registry_relationship_vocabulary_matches_rendering_layer(self) -> None:
        registry = json.loads((ROOT / "reader/registry.json").read_text(encoding="utf-8"))
        model_ts = (ROOT / "src/timeline/model.ts").read_text(encoding="utf-8")
        block = model_ts.split("const RELATIONSHIP_TYPES", 1)[1].split("]", 1)[0]
        rendered_types = set(re.findall(r"'([a-z][a-z-]+)'", block))
        self.assertEqual(set(registry["relationshipTypes"]), rendered_types)
        schema_path = ROOT / ".nimbalyst" / "trackers" / "timeline-link.yaml"
        if schema_path.is_file():
            schema_text = schema_path.read_text(encoding="utf-8")
            scope_block = schema_text.split("name: scopeRole", 1)[1].split("- name:", 1)[0]
            schema_roles = set(re.findall(r"value:\s*([a-z][a-z-]+)", scope_block))
            self.assertEqual(set(registry["scopeRoles"]), schema_roles)
            # The adapter reads/emits/requires clearingCondition on hard-serial
            # edges, so the schema must keep a home for it (issue #2 drift guard).
            self.assertIn("name: clearingCondition", schema_text)

    # --- Issue #36: live out-of-selection endpoints are boundary, not orphan ---

    def _orphan_findings(self, result: dict[str, object]) -> list[dict[str, object]]:
        return [
            finding for finding in result["validation"]["findings"]
            if finding["code"] == "orphan-endpoint"
        ]

    def test_live_out_of_selection_collection_target_is_boundary_not_orphan(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "release-live", "REL-LIVE", "release", {"title": "Live release", "status": "active"})
            self._insert(connection, "coll-source", "ITEM-B36", "task", {"title": "Collected role work", "status": "open", "owner": "coordinator", "collection": {"itemId": "release-live"}})
            connection.commit()
        result = self.reader.query_items({
            "workspacePath": self.workspace,
            "where": {"field": "issueKey", "op": "eq", "value": "ITEM-B36"},
        })
        self.assertEqual([node["id"] for node in result["nodes"]], ["coll-source"])
        self.assertEqual(self._orphan_findings(result), [])
        self.assertEqual(result["validation"]["state"], "pass")
        self.assertEqual(result["query"]["validationScope"]["boundaryEndpointCount"], 1)

    def test_role_query_with_cross_scope_collection_targets_stays_authoritative(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "release-live", "REL-LIVE", "release", {"title": "Live release", "status": "active"})
            self._insert(connection, "coll-role-1", "ITEM-B36A", "task", {"title": "Role work one", "status": "open", "owner": "coordinator", "collection": {"itemId": "release-live"}})
            self._insert(connection, "coll-role-2", "ITEM-B36B", "task", {"title": "Role work two", "status": "open", "owner": "coordinator", "collection": {"itemId": "prior"}})
            connection.commit()
        result = self.reader.query_items({
            "workspacePath": self.workspace,
            "savedQuery": {"id": "role-active-work-and-attention", "params": {"roleId": "coordinator"}},
        })
        returned = {node["id"] for node in result["nodes"]}
        self.assertLessEqual({"coll-role-1", "coll-role-2"}, returned)
        self.assertEqual(self._orphan_findings(result), [])
        self.assertEqual(result["validation"]["state"], "pass")

    def test_live_out_of_selection_legacy_edge_endpoints_are_not_orphans(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "legacy-source", "ITEM-B36L", "task", {
                "title": "Legacy-linked work", "status": "open",
                "milestone": {"itemId": "prior"},
                "deliverables": [{"itemId": "alpha-seed"}],
            })
            connection.commit()
        result = self.reader.query_items({
            "workspacePath": self.workspace,
            "where": {"field": "issueKey", "op": "eq", "value": "ITEM-B36L"},
        })
        self.assertEqual(self._orphan_findings(result), [])
        self.assertEqual(result["validation"]["state"], "pass")

    def test_unresolved_endpoint_still_fails_closed_as_orphan(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "ghost-source", "ITEM-B36G", "task", {"title": "Ghost-collected work", "status": "open", "collection": {"itemId": "no-such-item"}})
            connection.commit()
        result = self.reader.query_items({
            "workspacePath": self.workspace,
            "where": {"field": "issueKey", "op": "eq", "value": "ITEM-B36G"},
        })
        orphans = self._orphan_findings(result)
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0]["itemIds"], ["no-such-item"])
        self.assertEqual(result["validation"]["state"], "fail")

    def test_archived_endpoint_still_fails_closed_as_orphan(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "archived-release", "REL-ARCH", "release", {"title": "Archived release", "status": "active"})
            connection.execute("UPDATE tracker_items SET archived = 1 WHERE id = 'archived-release'")
            self._insert(connection, "arch-source", "ITEM-B36X", "task", {"title": "Archived-collected work", "status": "open", "collection": {"itemId": "archived-release"}})
            connection.commit()
        result = self.reader.query_items({
            "workspacePath": self.workspace,
            "where": {"field": "issueKey", "op": "eq", "value": "ITEM-B36X"},
        })
        orphans = self._orphan_findings(result)
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0]["itemIds"], ["archived-release"])
        self.assertEqual(result["validation"]["state"], "fail")

    # --- Issue #35: native collection membership + release containers ---

    def _insert_release_collection_fixture(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "release-1", "REL-CONTAINER", "release", {"title": "Release 1.0", "status": "active", "targetDate": "2026-09-01"})
            self._insert(connection, "coll-task", "ITEM-COLL", "task", {"title": "Collected work", "status": "open", "collection": {"itemId": "release-1"}})
            self._insert(connection, "coll-task-m", "ITEM-COLL-M", "task", {"title": "Milestone-collected work", "status": "open", "collection": {"itemId": "prior"}})
            connection.commit()

    def test_inline_collection_field_synthesizes_native_in_collection_edges(self) -> None:
        self._insert_release_collection_fixture()
        result = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "roots": ["ITEM-COLL"],
            "expand": {"relationshipTypes": ["in-collection"], "direction": "outgoing", "maxDepth": 1},
        })
        edges = [edge for edge in result["edges"] if edge["relationshipType"] == "in-collection"]
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual((edge["sourceId"], edge["targetId"]), ("coll-task", "release-1"))
        self.assertFalse(edge["legacy"])
        self.assertTrue(edge["id"].startswith("native-"))

    def test_release_and_milestone_roots_default_to_in_collection_membership(self) -> None:
        self._insert_release_collection_fixture()
        release_walk = self.reader.traverse_graph({"workspacePath": self.workspace, "roots": ["REL-CONTAINER"]})
        self.assertIn("coll-task", {node["id"] for node in release_walk["nodes"]})
        self.assertIn("release-1", {node["id"] for node in release_walk["nodes"]})
        milestone_walk = self.reader.traverse_graph({"workspacePath": self.workspace, "roots": ["M-ALPHA"]})
        self.assertIn("coll-task-m", {node["id"] for node in milestone_walk["nodes"]})

    def test_release_rooted_snapshot_summarizes_all_milestone_members(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "release-summary", "REL-SUMMARY", "release", {
                "title": "Release summary", "status": "active", "targetDate": "2026-10-01",
            })
            for index in range(10):
                self._insert(connection, f"release-milestone-{index}", f"MS-{index}", "milestone", {
                    "title": f"Milestone {index}",
                    "status": "active",
                    "targetDate": f"2026-09-{index + 1:02d}",
                    "collection": {"itemId": "release-summary"},
                })
            connection.commit()

        snapshot = self.reader.timeline_snapshot({
            "workspacePath": self.workspace,
            "includeUnscheduled": True,
            "maxItems": 50,
            "launch": "REL-SUMMARY",
        })

        milestone_ids = {item["id"] for item in snapshot["milestones"]}
        self.assertEqual(milestone_ids, {f"release-milestone-{index}" for index in range(10)})
        self.assertEqual(snapshot["source"]["milestoneRows"], 10)
        self.assertEqual(snapshot["source"]["membership"], {"memberCount": 10, "boundaryCount": 0})
        receipt = snapshot["source"]["relationshipProjection"]
        self.assertEqual(receipt["emittedCount"], 10)
        self.assertEqual(receipt["normalizedSourceCount"], receipt["emittedCount"] + receipt["excludedCount"])
        self.assertTrue(receipt["reconciled"])

    def test_release_containers_surface_in_timeline_snapshot(self) -> None:
        self._insert_release_collection_fixture()
        snapshot = self.reader.timeline_snapshot({"workspacePath": self.workspace, "includeUnscheduled": True, "maxItems": 300})
        release = next(item for item in snapshot["items"] if item["id"] == "release-1")
        self.assertEqual(release["primaryType"], "release")
        self.assertEqual(release["dueDate"], "2026-09-01")

    def test_explicit_in_collection_timeline_link_validates(self) -> None:
        self._insert_release_collection_fixture()
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._link(connection, "link-coll", "REL-COLL", "member-1", "release-1", "in-collection")
            connection.commit()
        snapshot = self.reader.timeline_snapshot({"workspacePath": self.workspace, "includeUnscheduled": True, "maxItems": 300})
        self.assertNotIn("link-coll", [rel for finding in snapshot["validation"] if finding["code"] == "invalid-relationship-type" for rel in finding["relationshipIds"]])
        edge = next(edge for edge in snapshot["relationships"] if edge["id"] == "link-coll")
        self.assertEqual(edge["relationshipType"], "in-collection")
        self.assertEqual(edge["state"], "active")

    # --- Issue #3: composable role inbox + bounded traversal ---

    def test_role_query_matches_owner_or_attention_and_excludes_terminal(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "attn-open", "ITEM-120", "feature", {"title": "Attention only", "status": "open", "owner": "someone-else", "tags": ["needs-coordination"]})
            self._insert(connection, "attn-done", "ITEM-121", "feature", {"title": "Attention done", "status": "done", "owner": "someone-else", "tags": ["needs-coordination"]})
            connection.commit()
        result = self.reader.query_items({"workspacePath": self.workspace, "savedQuery": {"id": "role-active-work-and-attention", "params": {"roleId": "coordinator"}}})
        ids = {node["id"] for node in result["nodes"]}
        self.assertIn("attn-open", ids)   # matched only by attention tag, not owner
        self.assertIn("member-1", ids)    # matched by owner alias
        self.assertNotIn("attn-done", ids)  # terminal status excluded
        self.assertNotIn("member-2", ids)   # terminal (done) excluded

    def test_type_exclusion_hides_links_but_keeps_them_queryable(self) -> None:
        excluded = self.reader.query_items({"workspacePath": self.workspace, "where": {"field": "issueKey", "op": "eq", "value": "REL-1"}})
        self.assertEqual(excluded["page"]["totalCount"], 0)
        self.assertEqual(excluded["edges"], [])
        included = self.reader.query_items({"workspacePath": self.workspace, "where": {"field": "issueKey", "op": "eq", "value": "REL-1"}, "includeRelationshipRecords": True})
        self.assertEqual([node for node in included["nodes"]], [])
        self.assertEqual({edge["issueKey"] for edge in included["edges"]}, {"REL-1"})

    def test_returned_count_counts_items_not_edges(self) -> None:
        edges_only = self.reader.query_items({"workspacePath": self.workspace, "where": {"field": "issueKey", "op": "eq", "value": "REL-1"}, "includeRelationshipRecords": True})
        self.assertEqual(edges_only["nodes"], [])
        self.assertEqual(len(edges_only["edges"]), 1)
        self.assertEqual(edges_only["page"]["returnedCount"], 0)

        with_edges = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "roots": ["RELEASE-A"],
            "membership": {"relationshipTypes": ["part-of-launch"], "direction": "incoming", "status": ["active"], "maxDepth": 1},
            "expand": {"relationshipTypes": ["depends-on"], "direction": "both", "maxDepth": 1, "edgeWhere": {"status": ["active"]}, "externalEndpointBehavior": "boundary"},
        })
        self.assertTrue(with_edges["edges"])
        self.assertEqual(with_edges["page"]["returnedCount"], len(with_edges["nodes"]) + len(with_edges["boundaryNodes"]))

    def test_missing_timeline_item_schema_with_live_rows_is_explicit_everywhere(self) -> None:
        self.timeline_item_schema.unlink()
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(
                connection,
                "legacy-timeline-item",
                "NIM-LEGACY",
                "timeline-item",
                {"title": "Legacy timeline row", "status": "in-progress"},
            )
            self._link(
                connection,
                "legacy-timeline-link",
                "REL-LEGACY",
                "legacy-timeline-item",
                "prior",
                "related",
            )
            connection.commit()

        query = self.reader.query_items({
            "workspacePath": self.workspace,
            "where": {"field": "type", "op": "eq", "value": "timeline-item"},
        })
        self.assertEqual([node["id"] for node in query["nodes"]], ["legacy-timeline-item"])
        discovery = query["watermark"]["schemaDiscovery"]
        self.assertEqual(discovery["state"], "missing-with-live-rows")
        self.assertEqual(discovery["liveRowCount"], 1)
        self.assertFalse(discovery["registered"])
        self.assertFalse(discovery["repair"]["automaticMutation"])
        self.assertEqual(
            discovery["repair"]["targetRelativePath"],
            ".nimbalyst/trackers/timeline-item.yaml",
        )

        traversal = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "roots": ["NIM-LEGACY"],
        })
        snapshot = self.reader.timeline_snapshot({
            "workspacePath": self.workspace,
            "includeUnscheduled": True,
            "maxItems": 500,
        })
        for result in (query, traversal):
            self.assertIn(
                "timeline-item-schema-missing-with-live-rows",
                {finding["code"] for finding in result["validation"]["findings"]},
            )
        self.assertIn(
            "timeline-item-schema-missing-with-live-rows",
            {finding["code"] for finding in snapshot["validation"]},
        )
        self.assertIn(
            "legacy-timeline-item",
            {item["id"] for item in snapshot["items"]},
        )
        self.assertEqual(
            snapshot["source"]["schemaDiscovery"]["state"],
            "missing-with-live-rows",
        )
        self.assertEqual(
            snapshot["source"]["schemaDiscovery"]["projectedRowCount"],
            1,
        )
        self.assertTrue(
            snapshot["source"]["schemaDiscovery"]["allLiveRowsProjected"],
        )
        self.assertNotIn(self.workspace, json.dumps({
            "query": query["watermark"]["schemaDiscovery"],
            "traversal": traversal["watermark"]["schemaDiscovery"],
            "snapshot": snapshot["source"]["schemaDiscovery"],
        }))

    def test_response_trimming_prioritizes_missing_schema_legacy_rows(self) -> None:
        result = {
            "items": [
                {
                    "id": "ordinary",
                    "primaryType": "task",
                    "title": "Large ordinary row",
                    "payload": "x" * 10_000,
                },
                {
                    "id": "legacy",
                    "primaryType": "timeline-item",
                    "title": "Legacy timeline row",
                },
            ],
            "milestones": [],
            "relationships": [],
            "validation": [],
            "criticalPath": {"itemIds": [], "cycleItemIds": []},
            "page": {"returned": 2, "responseTruncated": False},
            "source": {
                "schemaDiscovery": {
                    "trackerType": "timeline-item",
                    "state": "missing-with-live-rows",
                    "registered": False,
                    "liveRowCount": 1,
                },
            },
        }
        with patch("reader.database.MAX_RESULT_BYTES", 2_500):
            trimmed = self.reader._fit_timeline_result(result)
        self.assertEqual([item["id"] for item in trimmed["items"]], ["legacy"])
        self.assertTrue(trimmed["page"]["responseTruncated"])
        self.assertEqual(
            trimmed["source"]["schemaDiscovery"]["projectedRowCount"],
            1,
        )
        self.assertTrue(
            trimmed["source"]["schemaDiscovery"]["allLiveRowsProjected"],
        )

    def test_query_clause_depth_cap_is_enforced(self) -> None:
        clause: dict[str, object] = {"field": "status", "op": "eq", "value": "open"}
        for _ in range(6):
            clause = {"not": clause}
        with self.assertRaises(ReaderError) as raised:
            self.reader.query_items({"workspacePath": self.workspace, "where": clause})
        self.assertEqual(raised.exception.code, "QUERY_TOO_COMPLEX")

    def test_traversal_distinguishes_unknown_root_from_empty_result(self) -> None:
        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph({"workspacePath": self.workspace, "roots": ["NOPE-999"]})
        self.assertEqual(raised.exception.code, "ROOT_NOT_FOUND")
        empty = self.reader.traverse_graph({"workspacePath": self.workspace, "roots": ["M-ALPHA"]})
        self.assertEqual([node["id"] for node in empty["nodes"]], ["prior"])
        self.assertEqual(empty["boundaryNodes"], [])
        self.assertEqual(empty["validation"]["state"], "pass")

    def test_traversal_membership_cycle_fails_closed(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "launch-a", "LAUNCH-A", "launch", {"title": "A", "launchKey": "LA", "status": "draft"})
            self._insert(connection, "launch-b", "LAUNCH-B", "launch", {"title": "B", "launchKey": "LB", "status": "draft"})
            self._link(connection, "cycle-ab", "CYC-1", "launch-a", "launch-b", "part-of-launch", scope_role="core")
            self._link(connection, "cycle-ba", "CYC-2", "launch-b", "launch-a", "part-of-launch", scope_role="core")
            connection.commit()
        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph({
                "workspacePath": self.workspace,
                "roots": ["LA"],
                "membership": {"relationshipTypes": ["part-of-launch"], "direction": "both", "status": ["active"], "maxDepth": 4},
                "failOn": {"truncation": False, "validation": True},
            })
        self.assertEqual(raised.exception.code, "VALIDATION_FAILED")
        self.assertGreaterEqual(raised.exception.details["validation"]["cycleCount"], 1)

    def test_traversal_cap_truncates_breadth_first_and_can_fail_closed(self) -> None:
        base = {
            "workspacePath": self.workspace,
            "roots": ["RELEASE-A"],
            "membership": {"relationshipTypes": ["part-of-launch"], "direction": "incoming", "status": ["active"], "maxDepth": 1},
            "limits": {"maxNodes": 1},
        }
        truncated = self.reader.traverse_graph({**base, "failOn": {"truncation": False, "validation": False}})
        self.assertTrue(truncated["page"]["truncated"])
        self.assertEqual([node["id"] for node in truncated["nodes"]], ["launch-1"])
        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph({**base, "failOn": {"truncation": True, "validation": False}})
        self.assertEqual(raised.exception.code, "RESULT_TRUNCATED")

        complete = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "roots": ["RELEASE-A"],
            "membership": {
                "relationshipTypes": ["part-of-launch"],
                "direction": "incoming",
                "status": ["active"],
                "maxDepth": 1,
            },
        })
        node_ids: list[str] = []
        edge_ids: list[str] = []
        cursor: str | None = None
        for _page_number in range(10):
            page = self.reader.traverse_graph({
                **base,
                "limits": {"maxNodes": 1, "maxEdges": 1},
                "failOn": {"truncation": True, "validation": False},
                "paginate": True,
                **({"cursor": cursor} if cursor else {}),
            })
            node_ids.extend(
                node["id"] for node in [*page["nodes"], *page["boundaryNodes"]]
            )
            edge_ids.extend(edge["id"] for edge in page["edges"])
            cursor = page["page"]["nextCursor"]
            self._assert_graph_page_signals(page["page"])
            if not page["page"]["continuationRequired"]:
                self.assertTrue(page["page"]["resultsComplete"])
                break
        else:
            self.fail("traversal cursor continuation did not terminate")

        self.assertEqual(
            node_ids,
            [node["id"] for node in [*complete["nodes"], *complete["boundaryNodes"]]],
        )
        self.assertEqual(edge_ids, [edge["id"] for edge in complete["edges"]])

    def test_traversal_cursor_rejects_a_changed_complete_graph(self) -> None:
        request = {
            "workspacePath": self.workspace,
            "roots": ["RELEASE-A"],
            "membership": {
                "relationshipTypes": ["part-of-launch"],
                "direction": "incoming",
                "status": ["active"],
                "maxDepth": 1,
            },
            "limits": {"maxNodes": 1, "maxEdges": 1},
            "paginate": True,
        }
        first = self.reader.traverse_graph(request)
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "member-new", "ITEM-NEW", "task", {
                "title": "New member", "status": "open",
            })
            self._link(
                connection,
                "link-member-new",
                "REL-NEW",
                "member-new",
                "launch-1",
                "part-of-launch",
                scope_role="core",
            )
            connection.commit()

        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph({
                **request,
                "cursor": first["page"]["nextCursor"],
            })
        self.assertEqual(raised.exception.code, "CURSOR_INVALID")

    def test_selected_traversal_filters_retired_and_preserves_role_identity(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._link(
                connection,
                "link-member-supporting",
                "REL-SUPPORTING",
                "member-1",
                "launch-1",
                "part-of-launch",
                scope_role="supporting",
            )
            self._link(
                connection,
                "link-member-retired-duplicate",
                "REL-RETIRED",
                "member-1",
                "launch-1",
                "part-of-launch",
                scope_role="core",
                status="retired",
            )
            self._link(
                connection,
                "link-outside-duplicate-a",
                "REL-OUT-A",
                "alpha-seed",
                "shared-evidence",
                "related",
            )
            self._link(
                connection,
                "link-outside-duplicate-b",
                "REL-OUT-B",
                "alpha-seed",
                "shared-evidence",
                "related",
            )
            stale_target = {
                "title": "REL-STALE-TYPE",
                "sourceItem": {"itemId": "member-1"},
                "targetItem": {
                    "itemId": "prior",
                    "trackerType": "stale-type",
                    "title": "Stale title",
                },
                "relationshipType": "related",
                "status": "active",
            }
            self._insert(
                connection,
                "link-stale-target-type",
                "REL-STALE-TYPE",
                "timeline-link",
                stale_target,
            )
            connection.commit()

        result = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "roots": ["RELEASE-A"],
            "membership": {
                "relationshipTypes": ["part-of-launch"],
                "direction": "incoming",
                "status": ["active"],
                "maxDepth": 1,
            },
            "expand": {
                "relationshipTypes": ["related"],
                "direction": "both",
                "maxDepth": 1,
                "edgeWhere": {"status": ["active"]},
                "externalEndpointBehavior": "boundary",
            },
            "failOn": {"truncation": True, "validation": True},
        })
        relationship_ids = {edge["id"] for edge in result["edges"]}
        self.assertIn("link-member-1", relationship_ids)
        self.assertIn("link-member-supporting", relationship_ids)
        self.assertNotIn("link-member-retired-duplicate", relationship_ids)
        self.assertNotIn("link-outside-duplicate-a", relationship_ids)
        self.assertNotIn("link-outside-duplicate-b", relationship_ids)
        stale = next(
            edge for edge in result["edges"]
            if edge["id"] == "link-stale-target-type"
        )
        self.assertEqual(stale["targetType"], "milestone")
        self.assertEqual(result["validation"]["state"], "pass")

        snapshot = self.reader.timeline_snapshot({
            "workspacePath": self.workspace,
            "includeUnscheduled": True,
            "maxItems": 500,
        })
        retired = next(
            edge for edge in snapshot["relationships"]
            if edge["id"] == "link-member-retired-duplicate"
        )
        self.assertEqual(retired["state"], "retired")

    def test_exact_selected_duplicate_is_terminal_when_validation_is_declared(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._link(
                connection,
                "link-member-exact-duplicate",
                "REL-EXACT-DUP",
                "member-1",
                "launch-1",
                "part-of-launch",
                scope_role="core",
            )
            connection.commit()
        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph({
                "workspacePath": self.workspace,
                "roots": ["RELEASE-A"],
                "membership": {
                    "relationshipTypes": ["part-of-launch"],
                    "direction": "incoming",
                    "status": ["active"],
                    "maxDepth": 1,
                },
                "failOn": {"truncation": True, "validation": True},
            })
        self.assertEqual(raised.exception.code, "VALIDATION_FAILED")
        findings = raised.exception.details["validation"]["findings"]
        duplicate = next(
            finding for finding in findings
            if finding["code"] == "duplicate-active-membership"
        )
        self.assertEqual(
            duplicate["relationshipIds"],
            ["link-member-1", "link-member-exact-duplicate"],
        )

    def test_dispatch_saved_query_is_multi_launch_deterministic_and_auditable(self) -> None:
        ready = {
            "status": "ready",
            "owner": "coordinator",
            "packetRevision": "rev-7",
            "currentRevision": "rev-7",
            "qaEvidenceRevision": "rev-7",
            "qaStatus": "passed",
            "holdState": "clear",
            "databaseRouteState": "approved",
            "custodyState": "vacant",
            "survivorState": "unique",
            "collisionState": "clear",
        }
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "launch-2", "LAUNCH-RELEASE-B", "launch", {
                "title": "Second launch",
                "launchKey": "RELEASE-B",
                "status": "active",
                "owner": "Coordinator",
                "audience": ["internal"],
                "scopeRevision": "2",
                "entryCriteria": [{}],
                "exitCriteria": [{}],
            })
            self._insert(connection, "dispatch-a", "NIM-DISPATCH-A", "task", {
                **ready,
                "title": "Dependent packet",
                "priority": "critical",
            })
            self._insert(connection, "dispatch-b", "NIM-DISPATCH-B", "bug", {
                **ready,
                "title": "Prerequisite packet",
                "priority": "low",
            })
            self._insert(connection, "dispatch-excluded", "NIM-DISPATCH-X", "task", {
                **ready,
                "title": "Collision packet",
                "collisionState": "collision",
            })
            self._link(
                connection,
                "dispatch-membership-a",
                "REL-DISPATCH-A",
                "dispatch-a",
                "launch-1",
                "part-of-launch",
                scope_role="core",
            )
            self._link(
                connection,
                "dispatch-membership-b",
                "REL-DISPATCH-B",
                "dispatch-b",
                "launch-2",
                "part-of-launch",
                scope_role="core",
            )
            self._link(
                connection,
                "dispatch-membership-x",
                "REL-DISPATCH-X",
                "dispatch-excluded",
                "launch-2",
                "part-of-launch",
                scope_role="core",
            )
            self._link(
                connection,
                "dispatch-cleared-dependency",
                "REL-DISPATCH-DEP",
                "dispatch-a",
                "dispatch-b",
                "depends-on",
                hardness="hard-serial",
                status="cleared",
                clearing_condition="QA evidence accepted",
            )
            self._link(
                connection,
                "dispatch-retired-duplicate",
                "REL-DISPATCH-RETIRED",
                "dispatch-a",
                "launch-1",
                "part-of-launch",
                scope_role="core",
                status="retired",
            )
            connection.commit()

        request = {
            "workspacePath": self.workspace,
            "savedQuery": {
                "id": "dispatch-eligible-work-v1",
                "params": {
                    "roleId": "coordinator",
                    "launchKeys": ["RELEASE-B", "RELEASE-A"],
                    "includeUnscoped": False,
                },
            },
        }
        first = self.reader.traverse_graph(request)
        second = self.reader.traverse_graph(request)
        self.assertEqual(
            [node["id"] for node in first["nodes"]],
            ["dispatch-b", "dispatch-a"],
        )
        self.assertEqual(
            first["query"]["queryFingerprint"],
            second["query"]["queryFingerprint"],
        )
        self.assertEqual(
            [receipt["itemId"] for receipt in first["receipts"]],
            [receipt["itemId"] for receipt in second["receipts"]],
        )
        self.assertEqual(first["page"]["candidateCount"], 2)
        self.assertEqual(
            first["page"]["inspectedCount"],
            len(first["receipts"]) + first["page"]["preAdmissionExcludedCount"],
        )
        self.assertEqual(
            first["admission"]["sourceDispatchableCount"],
            first["page"]["inspectedCount"],
        )
        excluded = {
            receipt["itemId"]: receipt["exclusionReasons"]
            for receipt in first["excluded"]
        }
        self.assertIn("collision-or-overlap", excluded["dispatch-excluded"])
        dispatch_a = next(
            receipt for receipt in first["receipts"]
            if receipt["itemId"] == "dispatch-a"
        )
        self.assertEqual(
            dispatch_a["dependencyEvidence"][0]["relationshipId"],
            "dispatch-cleared-dependency",
        )
        self.assertTrue(dispatch_a["dependencyEvidence"][0]["cleared"])
        self.assertEqual(
            dispatch_a["ancestry"]["launches"][0]["relationshipIds"],
            ["dispatch-membership-a"],
        )
        self.assertNotIn(
            "dispatch-retired-duplicate",
            {edge["id"] for edge in first["edges"]},
        )
        self.assertEqual(first["validation"]["state"], "pass")

    def test_dispatch_scope_policy_migrates_authority_to_collection_membership(self) -> None:
        self._write_dispatch_scope_policy({
            "version": 1,
            "rootTypes": ["release"],
            "implicitRootSelection": "all-eligible",
            "ancestryDepth": 3,
            "mechanisms": [{
                "id": "release-membership",
                "relationshipType": "in-collection",
                "direction": "outgoing",
                "authority": "authoritative",
            }],
        })
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "release-root", "ROOT-OLD", "release", {
                "title": "Current delivery collection",
                "status": "active",
                "targetDate": "2026-09-01",
            })
            self._insert(
                connection,
                "collection-packet",
                "PACKET-1",
                "task",
                self._ready_dispatch_fields(),
            )
            self._link(
                connection,
                "current-membership",
                "REL-CURRENT",
                "collection-packet",
                "release-root",
                "in-collection",
            )
            self._link(
                connection,
                "historical-membership",
                "REL-HISTORICAL",
                "collection-packet",
                "launch-1",
                "part-of-launch",
                scope_role="core",
            )
            connection.commit()

        request = {
            "workspacePath": self.workspace,
            "savedQuery": {"id": "dispatch-eligible-work-v1", "params": {}},
        }

        first = self.reader.traverse_graph(request)
        receipt = next(
            value for value in first["receipts"]
            if value["itemId"] == "collection-packet"
        )
        self.assertTrue(receipt["included"])
        self.assertEqual(receipt["scopeAuthority"], "authoritative")
        self.assertEqual(receipt["scopeMechanismIds"], ["release-membership"])
        self.assertEqual(receipt["ancestry"]["roots"][0]["id"], "release-root")
        self.assertEqual(
            receipt["ancestry"]["roots"][0]["mechanismIds"],
            ["release-membership"],
        )
        self.assertNotIn("historical-membership", {edge["id"] for edge in first["edges"]})
        self.assertEqual(first["query"]["scopePolicy"]["source"], "workspace-query")
        self.assertEqual(first["query"]["scopePolicy"]["rootTypes"], ["release"])

        first_scope_fingerprint = receipt["scopeFingerprint"]
        first_evidence = receipt["evidence"]
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE tracker_items SET issue_key = 'ROOT-NEW' WHERE id = 'release-root'"
            )
            connection.commit()
        second = self.reader.traverse_graph(request)
        second_receipt = next(
            value for value in second["receipts"]
            if value["itemId"] == "collection-packet"
        )
        self.assertEqual(second_receipt["scopeFingerprint"], first_scope_fingerprint)
        self.assertEqual(second_receipt["evidence"], first_evidence)

    def test_archived_inline_collection_members_do_not_poison_dispatch_scope(self) -> None:
        self._write_dispatch_scope_policy({
            "version": 1,
            "rootTypes": ["release", "milestone"],
            "implicitRootSelection": "all-eligible",
            "ancestryDepth": 2,
            "mechanisms": [{
                "id": "collection-membership",
                "relationshipType": "in-collection",
                "direction": "outgoing",
                "authority": "authoritative",
            }],
        })
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "scope-release", "ROOT-RELEASE", "release", {
                "title": "Release root", "status": "active", "targetDate": "2026-10-01",
            })
            self._insert(connection, "scope-milestone", "ROOT-MILESTONE", "milestone", {
                "title": "Milestone root", "status": "active", "targetDate": "2026-09-15",
            })
            self._insert(connection, "current-scope-packet", "PACKET-CURRENT", "task", {
                **self._ready_dispatch_fields(),
                "collection": {"itemId": "scope-release"},
            })
            for item_type in ("task", "bug", "plan"):
                for container_id in ("scope-release", "scope-milestone"):
                    item_id = f"archived-{item_type}-{container_id}"
                    self._insert(connection, item_id, f"ARCHIVED-{item_type}-{container_id}", item_type, {
                        "title": f"Archived {item_type}",
                        "status": "active",
                        "collection": {"itemId": container_id},
                    })
                    connection.execute(
                        "UPDATE tracker_items SET archived=1 WHERE id=?",
                        (item_id,),
                    )
            connection.commit()

        request = {
            "workspacePath": self.workspace,
            "savedQuery": {"id": "dispatch-eligible-work-v1", "params": {}},
        }
        result = self.reader.traverse_graph(request)
        self._assert_graph_page_signals(result["page"])
        current = next(
            receipt for receipt in result["receipts"]
            if receipt["itemId"] == "current-scope-packet"
        )
        self.assertTrue(current["included"])
        self.assertEqual(current["scopeMechanismIds"], ["collection-membership"])
        self.assertNotIn(
            "archived-",
            json.dumps({"edges": result["edges"], "receipts": result["receipts"]}),
        )

        with closing(sqlite3.connect(self.db_path)) as connection:
            self._link(
                connection,
                "missing-active-membership",
                "REL-MISSING-ACTIVE",
                "missing-active-item",
                "scope-release",
                "in-collection",
            )
            connection.commit()
        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph(request)
        self.assertEqual(raised.exception.code, "UNRESOLVED_EDGE")
        self.assertEqual(
            raised.exception.details["relationshipId"],
            "missing-active-membership",
        )

    def test_dispatch_scope_policy_preserves_legacy_defaults_when_omitted(self) -> None:
        policy = resolve_dispatch_scope_policy(
            {"mode": "dispatch-eligible-work-v1"},
            self.reader._registry,
        )

        self.assertEqual(policy["source"], "built-in-default")
        self.assertEqual(policy["rootTypes"], ["launch", "milestone"])
        self.assertEqual(policy["implicitRootSelection"], "all-eligible")
        self.assertEqual(
            [mechanism["id"] for mechanism in policy["mechanisms"]],
            ["launch-membership", "milestone-contribution"],
        )
        self.assertEqual(
            resolve_dispatch_fail_on_policy({"mode": "dispatch-eligible-work-v1"}),
            {
                "truncation": True,
                "unresolvedEvidence": True,
                "validation": True,
                "warning": True,
            },
        )

    def test_dispatch_scope_policy_prefers_authority_and_can_use_fallback(self) -> None:
        self._write_dispatch_scope_policy({
            "version": 1,
            "rootTypes": ["launch"],
            "implicitRootSelection": "all-eligible",
            "ancestryDepth": 2,
            "mechanisms": [
                {
                    "id": "current-membership",
                    "relationshipType": "in-collection",
                    "direction": "outgoing",
                    "authority": "authoritative",
                },
                {
                    "id": "legacy-membership",
                    "relationshipType": "part-of-launch",
                    "direction": "outgoing",
                    "authority": "fallback",
                    "scopeRoles": ["core"],
                },
            ],
        })
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "fallback-packet", "PACKET-F", "task", self._ready_dispatch_fields())
            self._insert(connection, "current-packet", "PACKET-C", "task", self._ready_dispatch_fields())
            self._link(connection, "fallback-link", "REL-F", "fallback-packet", "launch-1", "part-of-launch", scope_role="core")
            self._link(connection, "current-link", "REL-C", "current-packet", "launch-1", "in-collection")
            self._link(connection, "current-legacy-link", "REL-C-OLD", "current-packet", "launch-1", "part-of-launch", scope_role="core")
            connection.commit()

        result = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "savedQuery": {"id": "dispatch-eligible-work-v1", "params": {}},
        })
        receipts = {value["itemId"]: value for value in result["receipts"]}
        self.assertEqual(receipts["fallback-packet"]["scopeAuthority"], "fallback")
        self.assertEqual(receipts["fallback-packet"]["scopeMechanismIds"], ["legacy-membership"])
        self.assertEqual(receipts["current-packet"]["scopeAuthority"], "authoritative")
        self.assertEqual(receipts["current-packet"]["scopeMechanismIds"], ["current-membership"])
        self.assertNotIn("current-legacy-link", {edge["id"] for edge in result["edges"]})

    def test_dispatch_scope_policy_invalid_or_missing_roots_fails_closed(self) -> None:
        self._write_dispatch_scope_policy({
            "version": 1,
            "rootTypes": ["release"],
            "implicitRootSelection": "require-explicit",
            "ancestryDepth": 2,
            "mechanisms": [{
                "id": "release-membership",
                "relationshipType": "in-collection",
                "direction": "outgoing",
                "authority": "authoritative",
            }],
        })
        with self.assertRaises(ReaderError) as missing:
            self.reader.traverse_graph({
                "workspacePath": self.workspace,
                "savedQuery": {"id": "dispatch-eligible-work-v1", "params": {}},
            })
        self.assertEqual(missing.exception.code, "DISPATCH_ROOTS_REQUIRED")

        self._write_dispatch_scope_policy({
            "version": 1,
            "rootTypes": ["release"],
            "implicitRootSelection": "all-eligible",
            "ancestryDepth": 2,
            "mechanisms": [
                {
                    "id": "one",
                    "relationshipType": "in-collection",
                    "direction": "outgoing",
                    "authority": "authoritative",
                },
                {
                    "id": "two",
                    "relationshipType": "in-collection",
                    "direction": "outgoing",
                    "authority": "fallback",
                },
            ],
        })
        with self.assertRaises(ReaderError) as invalid:
            self.reader.traverse_graph({
                "workspacePath": self.workspace,
                "savedQuery": {"id": "dispatch-eligible-work-v1", "params": {}},
            })
        self.assertEqual(invalid.exception.code, "DISPATCH_SCOPE_CONFIG_INVALID")
        self.assertEqual(
            invalid.exception.details["path"],
            "definition.scopePolicy.mechanisms[1]",
        )

    def test_dispatch_transitive_milestone_ancestry_admits_without_direct_edge(self) -> None:
        ready = {
            "status": "ready",
            "owner": "coordinator",
            "packetRevision": "rev-9",
            "currentRevision": "rev-9",
            "qaEvidenceRevision": "rev-9",
            "qaStatus": "passed",
            "holdState": "clear",
            "databaseRouteState": "approved",
            "custodyState": "vacant",
            "survivorState": "unique",
            "collisionState": "clear",
        }
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "bridge-milestone", "NIM-BRIDGE-M", "milestone", {
                "title": "Intermediate milestone",
                "status": "active",
                "targetDate": "2026-09-01",
            })
            self._insert(connection, "dispatch-transitive", "NIM-DISPATCH-T", "task", {
                **ready,
                "title": "Transitively scoped packet",
            })
            self._insert(connection, "dispatch-supporting", "NIM-DISPATCH-S", "task", {
                **ready,
                "title": "Supporting-role packet",
            })
            self._link(
                connection,
                "transitive-milestone-membership",
                "REL-TRANSITIVE-MS",
                "dispatch-transitive",
                "bridge-milestone",
                "part-of-launch",
                scope_role="core",
            )
            self._link(
                connection,
                "transitive-launch-membership",
                "REL-TRANSITIVE-LAUNCH",
                "bridge-milestone",
                "launch-1",
                "part-of-launch",
                scope_role="core",
            )
            self._link(
                connection,
                "supporting-launch-membership",
                "REL-SUPPORTING-LAUNCH",
                "dispatch-supporting",
                "launch-1",
                "part-of-launch",
                scope_role="supporting",
            )
            connection.commit()

        result = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "savedQuery": {
                "id": "dispatch-eligible-work-v1",
                "params": {"launchKeys": ["RELEASE-A"]},
            },
        })

        self.assertEqual(
            [node["id"] for node in result["nodes"]],
            ["dispatch-transitive"],
        )
        receipt = next(
            entry for entry in result["receipts"]
            if entry["itemId"] == "dispatch-transitive"
        )
        self.assertTrue(receipt["included"])
        self.assertEqual(
            [(row["id"], row["relationshipIds"]) for row in receipt["ancestry"]["milestones"]],
            [("bridge-milestone", ["transitive-milestone-membership"])],
        )
        self.assertEqual(
            [(row["id"], row["relationshipIds"]) for row in receipt["ancestry"]["launches"]],
            [("launch-1", ["transitive-launch-membership"])],
        )
        # The fixture intentionally has no direct task -> launch edge; admission
        # is proved entirely by the two-hop qualifying ancestry above.
        self.assertFalse([
            edge for edge in result["edges"]
            if edge["sourceId"] == "dispatch-transitive" and edge["targetId"] == "launch-1"
        ])
        candidate_ids = {entry["itemId"] for entry in result["receipts"]}
        self.assertNotIn("dispatch-supporting", candidate_ids)
        self.assertGreaterEqual(
            result["admission"]["preAdmissionReasonCounts"]["scope-not-admitted"],
            1,
        )
        self.assertEqual(result["validation"]["state"], "pass")

    def test_dispatch_large_workspace_summarizes_pre_admission_exclusions(self) -> None:
        ready = {
            "status": "ready",
            "owner": "coordinator",
            "packetRevision": "rev-large",
            "currentRevision": "rev-large",
            "qaEvidenceRevision": "rev-large",
            "qaStatus": "passed",
            "holdState": "clear",
            "databaseRouteState": "approved",
            "custodyState": "vacant",
            "survivorState": "unique",
            "collisionState": "clear",
        }
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(
                connection,
                "dispatch-large-ready",
                "ITEM-LARGE-READY",
                "task",
                {**ready, "title": "Ready packet"},
            )
            self._link(
                connection,
                "dispatch-large-membership",
                "REL-LARGE-READY",
                "dispatch-large-ready",
                "launch-1",
                "part-of-launch",
                scope_role="core",
            )
            for index in range(2_400):
                self._insert(
                    connection,
                    f"bulk-nonready-{index:04d}",
                    f"ITEM-BULK-{index:04d}",
                    "task",
                    {
                        "title": f"Non-ready packet {index}",
                        "status": "in-progress",
                        "owner": "coordinator",
                    },
                )
            connection.commit()

        result = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "savedQuery": {
                "id": "dispatch-eligible-work-v1",
                "params": {
                    "roleId": "coordinator",
                    "launchKeys": ["RELEASE-A"],
                    "includeUnscoped": False,
                },
            },
        })

        self.assertEqual([node["id"] for node in result["nodes"]], ["dispatch-large-ready"])
        self.assertFalse(result["page"]["truncated"])
        self.assertGreaterEqual(result["page"]["inspectedCount"], 2_401)
        self.assertGreaterEqual(result["page"]["preAdmissionExcludedCount"], 2_400)
        self.assertEqual(
            result["admission"]["preAdmissionReasonCounts"]["workflow-not-dispatch-ready"],
            result["page"]["preAdmissionExcludedCount"],
        )
        self.assertEqual(result["page"]["detailedReceiptCount"], 1)
        self.assertLess(self.reader._json_size(result), 500 * 1024)

    def test_dispatch_incomplete_evidence_returns_terminal_empty_receipt(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "dispatch-incomplete", "NIM-DISPATCH-I", "task", {
                "title": "Incomplete packet",
                "status": "ready",
                "owner": "coordinator",
                "packetRevision": "rev-1",
            })
            self._link(
                connection,
                "dispatch-membership-incomplete",
                "REL-DISPATCH-I",
                "dispatch-incomplete",
                "launch-1",
                "part-of-launch",
                scope_role="core",
            )
            connection.commit()
        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph({
                "workspacePath": self.workspace,
                "savedQuery": {
                    "id": "dispatch-eligible-work-v1",
                    "params": {"launchKeys": ["RELEASE-A"]},
                },
            })
        self.assertEqual(
            raised.exception.code,
            "DISPATCH_EVIDENCE_INCOMPLETE",
        )
        receipt = raised.exception.details["receipt"]
        self._assert_graph_page_signals(receipt["page"])
        self.assertEqual(receipt["candidates"], [])
        self.assertNotIn("launchTotals", receipt)
        self.assertNotIn("totalCount", receipt["page"])
        self.assertEqual(
            receipt["incompleteEvidence"][0]["itemId"],
            "dispatch-incomplete",
        )

    def test_dispatch_can_exclude_incomplete_evidence_without_hiding_complete_candidates(self) -> None:
        self._write_dispatch_fail_on({
            "truncation": True,
            "validation": True,
            "warning": True,
            "unresolvedEvidence": False,
        })
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(
                connection,
                "dispatch-complete",
                "ITEM-COMPLETE",
                "task",
                self._ready_dispatch_fields(),
            )
            self._insert(connection, "dispatch-partial", "ITEM-PARTIAL", "task", {
                "title": "Incomplete packet",
                "status": "ready",
                "packetRevision": "revision-1",
            })
            for item_id, relationship_id in (
                ("dispatch-complete", "REL-COMPLETE"),
                ("dispatch-partial", "REL-PARTIAL"),
            ):
                self._link(
                    connection,
                    f"{item_id}-membership",
                    relationship_id,
                    item_id,
                    "launch-1",
                    "part-of-launch",
                    scope_role="core",
                )
            connection.commit()

        request = {
            "workspacePath": self.workspace,
            "savedQuery": {
                "id": "dispatch-eligible-work-v1",
                "params": {"launchKeys": ["RELEASE-A"]},
            },
        }
        first = self.reader.traverse_graph(request)
        second = self.reader.traverse_graph(request)

        self.assertEqual([node["id"] for node in first["nodes"]], ["dispatch-complete"])
        self.assertEqual(first["page"]["candidateCount"], 1)
        self.assertEqual(first["launchTotals"], {"launch-1": 1})
        self.assertEqual(first["validation"]["state"], "pass")
        self.assertIn("generatedAt", first["watermark"])
        self.assertFalse(first["query"]["failOn"]["unresolvedEvidence"])
        self.assertEqual(first["query"]["unresolvedEvidenceDisposition"], "exclude-row")
        self.assertEqual(first["admission"]["incompleteEvidenceCount"], 1)
        receipts = {receipt["itemId"]: receipt for receipt in first["receipts"]}
        self.assertTrue(receipts["dispatch-complete"]["included"])
        self.assertEqual(
            receipts["dispatch-complete"]["evidenceCompleteness"]["state"],
            "complete",
        )
        self.assertFalse(receipts["dispatch-partial"]["included"])
        self.assertEqual(
            receipts["dispatch-partial"]["evidenceCompleteness"]["state"],
            "incomplete",
        )
        self.assertIn(
            "qaStatus",
            receipts["dispatch-partial"]["evidenceCompleteness"]["missingLogicalSignals"],
        )
        self.assertEqual(
            [entry["itemId"] for entry in first["excluded"]],
            ["dispatch-partial"],
        )
        self.assertEqual(first["nodes"], second["nodes"])
        self.assertEqual(first["receipts"], second["receipts"])
        self.assertEqual(first["launchTotals"], second["launchTotals"])
        self.assertEqual(
            first["query"]["queryFingerprint"],
            second["query"]["queryFingerprint"],
        )

        self._write_dispatch_fail_on({
            "truncation": True,
            "validation": True,
            "warning": True,
            "unresolvedEvidence": True,
        })
        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph(request)
        self.assertEqual(raised.exception.code, "DISPATCH_EVIDENCE_INCOMPLETE")
        self.assertEqual(raised.exception.details["receipt"]["candidates"], [])

    def test_dispatch_rejects_non_boolean_unresolved_evidence_policy(self) -> None:
        self._write_dispatch_fail_on({"unresolvedEvidence": "false"})

        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph({
                "workspacePath": self.workspace,
                "savedQuery": {"id": "dispatch-eligible-work-v1", "params": {}},
            })

        self.assertEqual(raised.exception.code, "QUERY_INVALID")
        self.assertEqual(raised.exception.details["path"], "definition.failOn")

    def test_dispatch_role_attention_tag_reaches_detailed_receipt(self) -> None:
        self._write_registry_override({
            "roles": {
                "dispatch-controller": {
                    "ownerAliases": ["dispatch-controller"],
                    "attentionTags": ["needs-dispatch-attention"],
                }
            }
        })
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "dispatch-attention", "NIM-DISPATCH-ATTN", "task", {
                "title": "Attention-routed packet",
                "status": "ready",
                "owner": "project-manager",
                "tags": ["Needs-Dispatch-Attention"],
            })
            self._link(
                connection,
                "dispatch-attention-membership",
                "REL-DISPATCH-ATTN",
                "dispatch-attention",
                "launch-1",
                "part-of-launch",
                scope_role="core",
            )
            connection.commit()

        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph({
                "workspacePath": self.workspace,
                "savedQuery": {
                    "id": "dispatch-eligible-work-v1",
                    "params": {
                        "roleId": "dispatch-controller",
                        "launchKeys": ["RELEASE-A"],
                    },
                },
            })

        self.assertEqual(raised.exception.code, "DISPATCH_EVIDENCE_INCOMPLETE")
        receipt = raised.exception.details["receipt"]
        self.assertEqual(receipt["admission"]["detailedInspectionCount"], 1)
        self.assertEqual(
            receipt["incompleteEvidence"][0]["itemId"],
            "dispatch-attention",
        )
        self.assertEqual(receipt["candidates"], [])
        self.assertNotIn("launchTotals", receipt)

    def test_dispatch_validates_explicit_launch_graph_before_admission(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "launch-container", "NIM-LAUNCH-CONTAINER", "feature", {
                "title": "Non-delivery launch container",
                "status": "active",
                "owner": "project-manager",
            })
            self._link(
                connection,
                "launch-container-membership",
                "REL-LAUNCH-CONTAINER",
                "launch-container",
                "launch-1",
                "part-of-launch",
                scope_role="core",
            )
            connection.commit()

        result = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "savedQuery": {
                "id": "dispatch-eligible-work-v1",
                "params": {
                    "roleId": "coordinator",
                    "launchKeys": ["RELEASE-A"],
                },
            },
        })

        self.assertEqual(result["validation"]["state"], "pass")
        self.assertEqual(result["receipts"], [])
        self.assertEqual(result["nodes"], [])
        self.assertEqual(result["edges"], [])
        self.assertEqual(result["page"]["candidateCount"], 0)

    def test_dispatch_launch_graph_unavailable_endpoint_fails_closed(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._link(
                connection,
                "missing-launch-member",
                "REL-MISSING-LAUNCH-MEMBER",
                "missing-source",
                "launch-1",
                "part-of-launch",
                scope_role="core",
            )
            connection.commit()

        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph({
                "workspacePath": self.workspace,
                "savedQuery": {
                    "id": "dispatch-eligible-work-v1",
                    "params": {"launchKeys": ["RELEASE-A"]},
                },
            })

        self.assertEqual(raised.exception.code, "UNRESOLVED_EDGE")
        self.assertEqual(
            raised.exception.details["relationshipId"],
            "missing-launch-member",
        )

    def test_dispatch_launch_lifecycle_omission_remains_terminal(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "launch-incomplete", "LAUNCH-INCOMPLETE", "launch", {
                "title": "Incomplete launch",
                "launchKey": "INCOMPLETE",
                "status": "active",
            })
            self._insert(connection, "launch-incomplete-member", "ITEM-INCOMPLETE", "feature", {
                "title": "Valid structural member",
                "status": "active",
            })
            self._link(
                connection,
                "launch-incomplete-membership",
                "REL-LAUNCH-INCOMPLETE",
                "launch-incomplete-member",
                "launch-incomplete",
                "part-of-launch",
                scope_role="core",
            )
            connection.commit()

        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph({
                "workspacePath": self.workspace,
                "savedQuery": {
                    "id": "dispatch-eligible-work-v1",
                    "params": {"launchKeys": ["INCOMPLETE"]},
                },
            })

        self.assertEqual(raised.exception.code, "VALIDATION_FAILED")
        receipt = raised.exception.details["receipt"]
        self.assertIn(
            "launch-fields-incomplete",
            {finding["code"] for finding in receipt["validation"]["findings"]},
        )
        self.assertEqual(receipt["candidates"], [])
        self.assertNotIn("launchTotals", receipt)

    def test_dispatch_evidence_mapping_supports_fields_tags_prefixes_and_provenance(self) -> None:
        self._write_registry_override(self._mapped_dispatch_override())
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "dispatch-mapped", "NIM-DISPATCH-MAPPED", "task", {
                "title": "Mapped packet",
                **self._mapped_dispatch_fields(),
            })
            self._link(
                connection,
                "dispatch-mapped-membership",
                "REL-DISPATCH-MAPPED",
                "dispatch-mapped",
                "launch-1",
                "part-of-launch",
                scope_role="core",
            )
            connection.commit()

        result = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "savedQuery": {
                "id": "dispatch-eligible-work-v1",
                "params": {"launchKeys": ["RELEASE-A"]},
            },
        })

        self.assertEqual([node["id"] for node in result["nodes"]], ["dispatch-mapped"])
        receipt = result["receipts"][0]
        self.assertTrue(receipt["included"])
        self.assertEqual(receipt["evidence"]["workflow"]["value"], "to-do")
        self.assertEqual(receipt["evidence"]["packetRevision"]["source"]["kind"], "tag-prefix")
        self.assertEqual(receipt["evidence"]["qaStatus"], {
            "value": "passed",
            "source": {"kind": "tag", "tag": "qa-signed-off"},
        })
        self.assertEqual(receipt["evidence"]["databaseRouteState"]["source"], {
            "kind": "field",
            "field": "routeState",
        })
        self.assertEqual(set(receipt["evidence"]), set(self.reader._registry["dispatchEvidence"]))
        self.assertEqual(len(result["query"]["evidenceMapping"]["fingerprint"]), 64)

    def test_dispatch_evidence_mapping_supports_normalized_relationship_sources(self) -> None:
        self._write_registry_override({
            "dispatchEvidence": {
                "qaStatus": {
                    "sources": [{
                        "kind": "relationship",
                        "relationshipType": "evidences",
                        "direction": "outgoing",
                        "state": "active",
                        "value": "passed",
                    }],
                },
            },
        })
        ready = {
            "status": "ready",
            "packetRevision": "rev-edge",
            "currentRevision": "rev-edge",
            "qaEvidenceRevision": "rev-edge",
            "holdState": "clear",
            "databaseRouteState": "approved",
            "custodyState": "vacant",
            "survivorState": "unique",
            "collisionState": "clear",
        }
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "dispatch-relationship", "NIM-DISPATCH-REL", "task", {
                "title": "Relationship-evidenced packet",
                **ready,
            })
            self._insert(connection, "qa-evidence", "EVIDENCE-QA", "document", {
                "title": "QA evidence",
                "status": "active",
            })
            self._link(
                connection,
                "dispatch-relationship-membership",
                "REL-DISPATCH-REL-MEMBER",
                "dispatch-relationship",
                "launch-1",
                "part-of-launch",
                scope_role="core",
            )
            self._link(
                connection,
                "dispatch-relationship-evidence",
                "REL-DISPATCH-REL-EVIDENCE",
                "dispatch-relationship",
                "qa-evidence",
                "evidences",
            )
            connection.commit()

        result = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "savedQuery": {
                "id": "dispatch-eligible-work-v1",
                "params": {"launchKeys": ["RELEASE-A"]},
            },
        })

        self.assertEqual([node["id"] for node in result["nodes"]], ["dispatch-relationship"])
        self.assertEqual(result["receipts"][0]["evidence"]["qaStatus"], {
            "value": "passed",
            "source": {
                "kind": "relationship",
                "relationshipId": "dispatch-relationship-evidence",
                "relationshipType": "evidences",
                "direction": "outgoing",
                "state": "active",
            },
        })

    def test_dispatch_mapped_missing_signal_returns_terminal_fail_closed_receipt(self) -> None:
        self._write_registry_override(self._mapped_dispatch_override())
        fields = self._mapped_dispatch_fields()
        fields["tags"] = [tag for tag in fields["tags"] if tag != "qa-signed-off"]
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "dispatch-mapped-incomplete", "NIM-DISPATCH-MISSING", "task", {
                "title": "Mapped incomplete packet",
                **fields,
            })
            self._link(
                connection,
                "dispatch-mapped-incomplete-membership",
                "REL-DISPATCH-MISSING",
                "dispatch-mapped-incomplete",
                "launch-1",
                "part-of-launch",
                scope_role="core",
            )
            connection.commit()

        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph({
                "workspacePath": self.workspace,
                "savedQuery": {
                    "id": "dispatch-eligible-work-v1",
                    "params": {"launchKeys": ["RELEASE-A"]},
                },
            })

        self.assertEqual(raised.exception.code, "DISPATCH_EVIDENCE_INCOMPLETE")
        receipt = raised.exception.details["receipt"]
        self.assertEqual(receipt["candidates"], [])
        missing = receipt["incompleteEvidence"][0]
        self.assertIn("qaStatus", missing["missingLogicalSignals"])
        self.assertNotIn("launchTotals", receipt)

    def test_dispatch_currentness_diagnostic_names_configured_sources(self) -> None:
        ready = {
            "title": "Currentness diagnostic packet",
            "status": "ready",
            "packetRevision": "revision-1",
            "qaEvidenceRevision": "revision-1",
            "qaStatus": "passed",
            "holdState": "clear",
            "databaseRouteState": "approved",
            "custodyState": "vacant",
            "survivorState": "unique",
            "collisionState": "clear",
            "revisionCurrentness": "current",
        }
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(
                connection,
                "dispatch-currentness",
                "ITEM-DISPATCH-CURRENTNESS",
                "task",
                ready,
            )
            self._link(
                connection,
                "dispatch-currentness-membership",
                "REL-DISPATCH-CURRENTNESS",
                "dispatch-currentness",
                "launch-1",
                "part-of-launch",
                scope_role="core",
            )
            connection.commit()

        request = {
            "workspacePath": self.workspace,
            "savedQuery": {
                "id": "dispatch-eligible-work-v1",
                "params": {"launchKeys": ["RELEASE-A"]},
            },
        }
        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph(request)
        self.assertEqual(raised.exception.code, "DISPATCH_EVIDENCE_INCOMPLETE")
        missing = raised.exception.details["receipt"]["incompleteEvidence"][0]
        self.assertIn("revision-currentness", missing["missingLogicalSignals"])
        self.assertEqual(missing["unacceptedFieldsPresent"], ["revisionCurrentness"])
        sources = missing["acceptedSources"]["revision-currentness"]
        self.assertEqual(
            {(source["field"], source["type"], source["constraint"]) for source in sources},
            {
                ("currentRevision", "string", "equals packetRevision"),
                ("isCurrentRevision", "boolean", "true"),
            },
        )

        with closing(sqlite3.connect(self.db_path)) as connection:
            ready["currentRevision"] = "revision-1"
            connection.execute(
                "UPDATE tracker_items SET data=? WHERE id='dispatch-currentness'",
                (json.dumps(ready),),
            )
            connection.commit()
        string_result = self.reader.traverse_graph(request)
        self.assertIn(
            "dispatch-currentness",
            {node["id"] for node in string_result["nodes"]},
        )

        with closing(sqlite3.connect(self.db_path)) as connection:
            ready.pop("currentRevision")
            ready["isCurrentRevision"] = True
            connection.execute(
                "UPDATE tracker_items SET data=? WHERE id='dispatch-currentness'",
                (json.dumps(ready),),
            )
            connection.commit()
        boolean_result = self.reader.traverse_graph(request)
        self.assertIn(
            "dispatch-currentness",
            {node["id"] for node in boolean_result["nodes"]},
        )

        with closing(sqlite3.connect(self.db_path)) as connection:
            ready["isCurrentRevision"] = False
            ready["currentRevision"] = "revision-2"
            connection.execute(
                "UPDATE tracker_items SET data=? WHERE id='dispatch-currentness'",
                (json.dumps(ready),),
            )
            connection.commit()
        mismatch = self.reader.traverse_graph(request)
        receipt = next(
            receipt for receipt in mismatch["receipts"]
            if receipt["itemId"] == "dispatch-currentness"
        )
        self.assertFalse(receipt["included"])
        self.assertIn("packet-not-current-revision", receipt["exclusionReasons"])

    def test_dispatch_waiting_roots_require_explicit_policy_and_terminal_roots_stay_excluded(self) -> None:
        policy = json.loads(json.dumps(self.reader._registry["dispatchPolicy"]))
        policy["eligibleLaunchStatuses"].append("waiting")
        self._write_registry_override({"dispatchPolicy": policy})
        ready = {
            "status": "ready",
            "packetRevision": "rev-waiting",
            "currentRevision": "rev-waiting",
            "qaEvidenceRevision": "rev-waiting",
            "qaStatus": "passed",
            "holdState": "clear",
            "databaseRouteState": "approved",
            "custodyState": "vacant",
            "survivorState": "unique",
            "collisionState": "clear",
        }
        with closing(sqlite3.connect(self.db_path)) as connection:
            for item_id, issue_key, launch_key, status in (
                ("launch-waiting", "LAUNCH-WAITING", "WAITING", "waiting"),
                ("launch-done", "LAUNCH-DONE", "DONE", "done"),
                ("launch-retired", "LAUNCH-RETIRED", "RETIRED", "retired"),
                ("launch-archived", "LAUNCH-ARCHIVED", "ARCHIVED", "active"),
            ):
                self._insert(connection, item_id, issue_key, "launch", {
                    "title": launch_key,
                    "launchKey": launch_key,
                    "status": status,
                    "owner": "Coordinator",
                    "audience": ["internal"],
                    "scopeRevision": "1",
                    "entryCriteria": [{}],
                    "exitCriteria": [{}],
                })
            self._insert(connection, "dispatch-waiting", "NIM-DISPATCH-WAITING", "task", {
                "title": "Waiting-root packet",
                **ready,
            })
            self._link(
                connection,
                "dispatch-waiting-membership",
                "REL-DISPATCH-WAITING",
                "dispatch-waiting",
                "launch-waiting",
                "part-of-launch",
                scope_role="core",
            )
            connection.execute(
                "UPDATE tracker_items SET archived=1 WHERE id='launch-archived'"
            )
            connection.commit()

        result = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "savedQuery": {
                "id": "dispatch-eligible-work-v1",
                "params": {"launchKeys": ["WAITING"]},
            },
        })
        self.assertEqual([node["id"] for node in result["nodes"]], ["dispatch-waiting"])
        for launch_key in ("DONE", "RETIRED", "ARCHIVED"):
            with self.subTest(launch_key=launch_key), self.assertRaises(ReaderError) as raised:
                self.reader.traverse_graph({
                    "workspacePath": self.workspace,
                    "savedQuery": {
                        "id": "dispatch-eligible-work-v1",
                        "params": {"launchKeys": [launch_key]},
                    },
                })
            self.assertEqual(raised.exception.code, "ROOT_NOT_FOUND")

    def test_dispatch_unscoped_requires_explicit_registry_admission(self) -> None:
        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph({
                "workspacePath": self.workspace,
                "savedQuery": {
                    "id": "dispatch-eligible-work-v1",
                    "params": {"includeUnscoped": True},
                },
            })
        self.assertEqual(raised.exception.code, "UNSCOPED_WORK_NOT_CONFIGURED")
        policy = dict(self.reader._registry["dispatchPolicy"])
        policy["admittedUnscopedTypes"] = ["bug"]
        override_path = (
            Path(self.workspace)
            / ".nimbalyst"
            / "tracker-plus.registry.json"
        )
        override_path.write_text(
            json.dumps({"dispatchPolicy": policy}),
            encoding="utf-8",
        )
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(connection, "dispatch-unscoped", "NIM-DISPATCH-U", "bug", {
                "title": "Admitted unscoped bug",
                "status": "ready",
                "packetRevision": "rev-u",
                "currentRevision": "rev-u",
                "qaEvidenceRevision": "rev-u",
                "qaStatus": "passed",
                "holdState": "clear",
                "databaseRouteState": "approved",
                "custodyState": "vacant",
                "survivorState": "unique",
                "collisionState": "clear",
            })
            connection.commit()
        excluded = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "savedQuery": {
                "id": "dispatch-eligible-work-v1",
                "params": {"includeUnscoped": False},
            },
        })
        self.assertNotIn(
            "dispatch-unscoped",
            {node["id"] for node in excluded["nodes"]},
        )
        included = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "savedQuery": {
                "id": "dispatch-eligible-work-v1",
                "params": {"includeUnscoped": True},
            },
        })
        self.assertIn(
            "dispatch-unscoped",
            {node["id"] for node in included["nodes"]},
        )


if __name__ == "__main__":
    unittest.main()
