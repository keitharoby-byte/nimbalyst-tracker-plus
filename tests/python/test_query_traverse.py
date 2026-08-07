from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from reader.contracts import ReaderError
from reader.database import NativeTrackerReader

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

    def test_saved_role_query_and_parameterized_sql_value(self) -> None:
        result = self.reader.query_items({"workspacePath": self.workspace, "savedQuery": {"id": "role-active-work-and-attention", "params": {"roleId": "coordinator"}}})
        self.assertEqual([node["id"] for node in result["nodes"]], ["launch-1", "member-1"])
        self.assertNotIn("timeline-link", {node["type"] for node in result["nodes"]})
        injection = self.reader.query_items({"workspacePath": self.workspace, "where": {"field": "title", "op": "eq", "value": "' OR 1=1 --"}})
        self.assertEqual(injection["page"]["totalCount"], 0)

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
                "launch-open-reviews": None,
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
                    "walkStage": "local-verifiable",
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

        self.assertEqual([node["id"] for node in result["nodes"]], ["workspace-composed-root"])
        self.assertEqual([node["id"] for node in result["boundaryNodes"]], ["prior"])
        self.assertEqual([edge["id"] for edge in result["edges"]], ["workspace-composed-edge"])
        self.assertTrue(result["query"]["selection"]["complete"])

    def test_walk_ready_composition_is_evidence_backed_and_terminal_authoritative(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._insert(
                connection,
                "walk-blocked",
                "CONTROL-20",
                "feature",
                {
                    "title": "Blocked walk control",
                    "status": "active",
                    "buildState": "build-complete",
                    "walkStage": "local-verifiable",
                    "requiredRuntimeAvailable": True,
                    "gate": "Walk the completed behavior",
                },
            )
            self._insert(
                connection,
                "walk-terminal",
                "CONTROL-21",
                "feature",
                {
                    "title": "Completed walk control",
                    "status": "done",
                    "buildState": "build-complete",
                    "walkStage": "production-only",
                    "requiredRuntimeAvailable": False,
                },
            )
            self._insert(
                connection,
                "walk-unverified",
                "CONTROL-22",
                "feature",
                {
                    "title": "Unverified walk control",
                    "status": "active",
                    "buildState": "build-complete",
                    "walkStage": "mixed",
                    "requiredRuntimeAvailable": True,
                    "gate": "Verify the merged behavior",
                },
            )
            self._insert(
                connection,
                "implementing-artifact",
                "ARTIFACT-20",
                "document",
                {"title": "Merged implementation evidence", "status": "active"},
            )
            self._insert(
                connection,
                "walk-predecessor",
                "CONTROL-19",
                "task",
                {"title": "Required predecessor", "status": "active"},
            )
            self._link(
                connection,
                "walk-implements",
                "CONTROL-REL-20",
                "implementing-artifact",
                "walk-blocked",
                "implements",
            )
            self._link(
                connection,
                "walk-blocker",
                "CONTROL-REL-21",
                "walk-blocked",
                "walk-predecessor",
                "depends-on",
                hardness="hard-serial",
                clearing_condition="Predecessor acceptance is complete",
                owner="reviewer",
            )
            self._link(
                connection,
                "walk-terminal-stale-blocker",
                "CONTROL-REL-22",
                "walk-terminal",
                "walk-predecessor",
                "depends-on",
                hardness="hard-serial",
                clearing_condition="Stale child condition",
                owner="reviewer",
            )
            connection.commit()

        result = self.reader.traverse_graph({
            "workspacePath": self.workspace,
            "savedQuery": {"id": "walk-ready-milestones", "params": {}},
        })
        roots = {node["id"]: node for node in result["nodes"]}

        self.assertEqual(set(roots), {"walk-blocked", "walk-terminal", "walk-unverified"})
        self.assertEqual(roots["walk-blocked"]["buildState"], "build-complete")
        self.assertEqual(roots["walk-blocked"]["readiness"], "blocked")
        self.assertEqual(
            roots["walk-blocked"]["serialPredecessor"]["relationshipId"],
            "walk-blocker",
        )
        self.assertEqual(
            roots["walk-blocked"]["walkReadinessProvenance"]["implementingEvidence"],
            [{
                "itemId": "implementing-artifact",
                "issueKey": "ARTIFACT-20",
                "relationshipId": "walk-implements",
                "relationshipType": "implements",
            }],
        )
        self.assertEqual(roots["walk-terminal"]["readiness"], "walk-ready")
        self.assertEqual(roots["walk-terminal"]["walkReadiness"]["percentage"], 100)
        self.assertIsNone(roots["walk-terminal"]["serialPredecessor"])
        self.assertEqual(roots["walk-unverified"]["buildState"], "unknown")
        self.assertEqual(roots["walk-unverified"]["readiness"], "unknown")
        self.assertIn(
            "walk-build-evidence-missing",
            {finding["code"] for finding in result["validation"]["findings"]},
        )
        self.assertFalse(result["page"]["truncated"])
        self.assertTrue(result["query"]["selection"]["complete"])

    def test_walk_ready_composition_fails_closed_when_root_selection_overflows(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            for index in range(9):
                self._insert(
                    connection,
                    f"walk-overflow-{index}",
                    f"CONTROL-{100 + index}",
                    "feature",
                    {
                        "title": f"Walk control {index}",
                        "status": "active",
                        "buildState": "build-complete",
                        "walkStage": "local-verifiable",
                    },
                )
            connection.commit()

        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph({
                "workspacePath": self.workspace,
                "savedQuery": {"id": "walk-ready-milestones", "params": {}},
            })

        self.assertEqual(raised.exception.code, "RESULT_TRUNCATED")

    def test_query_cursor_reconciles_total_count(self) -> None:
        params = {"workspacePath": self.workspace, "where": {"field": "issueKey", "op": "exists", "value": True}, "sort": [{"field": "id", "direction": "asc"}], "limit": 200}
        first = self.reader.query_items(params)
        second = self.reader.query_items({**params, "cursor": first["page"]["nextCursor"]})
        ids = [node["id"] for node in first["nodes"] + second["nodes"]]
        self.assertEqual(len(ids), first["page"]["totalCount"])
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
        self.assertEqual(receipt["candidates"], [])
        self.assertNotIn("launchTotals", receipt)
        self.assertNotIn("totalCount", receipt["page"])
        self.assertEqual(
            receipt["incompleteEvidence"][0]["itemId"],
            "dispatch-incomplete",
        )

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
