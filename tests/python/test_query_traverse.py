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
            self._insert(connection, "launch-1", "LAUNCH-FFP-1", "launch", {
                "title": "Feature preview", "launchKey": "FFP-1", "status": "active",
                "owner": "PM", "audience": ["internal"], "scopeRevision": "1",
                "entryCriteria": [{}], "exitCriteria": [{}],
            })
            self._insert(connection, "member-1", "NIM-1550", "task", {"title": "Core work", "status": "in-progress", "owner": "pm", "tags": ["ffp-1"]})
            self._insert(connection, "member-2", "NIM-1551", "task", {"title": "Review work", "status": "done", "owner": "engineer"})
            self._insert(connection, "prior", "M-ALPHA", "milestone", {"title": "Prior launch", "status": "active", "targetDate": "2026-07-01"})
            self._insert(connection, "alpha-seed", "ALPHA-1", "task", {"title": "Alpha seed", "status": "open", "tags": ["Alpha-Launch"]})
            self._insert(connection, "demo-seed", "DEMO-1", "task", {"title": "Demo seed", "status": "open", "tags": ["demo-launch"]})
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

    def test_saved_role_query_and_parameterized_sql_value(self) -> None:
        result = self.reader.query_items({"workspacePath": self.workspace, "savedQuery": {"id": "role-active-work-and-attention", "params": {"roleId": "project-manager"}}})
        self.assertEqual([node["id"] for node in result["nodes"]], ["launch-1", "member-1"])
        self.assertNotIn("timeline-link", {node["type"] for node in result["nodes"]})
        injection = self.reader.query_items({"workspacePath": self.workspace, "where": {"field": "title", "op": "eq", "value": "' OR 1=1 --"}})
        self.assertEqual(injection["page"]["totalCount"], 0)

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
            "roots": ["FFP-1"],
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
            "roots": ["FFP-1"],
            "membership": {"relationshipTypes": ["part-of-launch"], "direction": "incoming", "status": ["active"], "maxDepth": 1},
            "expand": {"relationshipTypes": ["depends-on"], "direction": "both", "maxDepth": 2, "edgeWhere": {"status": ["active"]}, "externalEndpointBehavior": "boundary"},
        })

        self.assertEqual([node["id"] for node in result["boundaryNodes"]], ["prior"])
        self.assertNotIn("beyond-prior", {node["id"] for node in [*result["nodes"], *result["boundaryNodes"]]})
        self.assertNotIn("link-beyond", {edge["id"] for edge in result["edges"]})

    def test_launch_rooted_snapshot_reports_members_and_boundaries(self) -> None:
        snapshot = self.reader.timeline_snapshot({"workspacePath": self.workspace, "includeUnscheduled": True, "maxItems": 50, "launch": "FFP-1"})
        self.assertEqual(snapshot["source"]["rootLaunch"], "FFP-1")
        self.assertEqual(snapshot["source"]["membership"], {"memberCount": 2, "boundaryCount": 1})
        self.assertFalse(snapshot["page"]["queryTruncated"])

    def test_tag_seeded_snapshots_are_independent_and_include_boundaries(self) -> None:
        alpha = self.reader.timeline_snapshot({
            "workspacePath": self.workspace,
            "includeUnscheduled": True,
            "maxItems": 50,
            "selector": {"launchTags": [" ALPHA-LAUNCH "]},
        })
        demo = self.reader.timeline_snapshot({
            "workspacePath": self.workspace,
            "includeUnscheduled": True,
            "maxItems": 50,
            "selector": {"launchTags": ["demo-launch"]},
        })

        self.assertEqual({item["id"] for item in alpha["items"]}, {"alpha-seed", "shared-evidence"})
        self.assertEqual({edge["id"] for edge in alpha["relationships"]}, {"link-alpha-evidence"})
        self.assertTrue(next(item for item in alpha["items"] if item["id"] == "shared-evidence")["boundary"])
        self.assertEqual({item["id"] for item in demo["items"]}, {"demo-seed", "shared-evidence"})
        self.assertEqual({edge["id"] for edge in demo["relationships"]}, {"link-demo-evidence"})
        self.assertNotEqual(alpha["source"]["outputHash"], demo["source"]["outputHash"])
        self.assertEqual(alpha["source"]["selector"]["launchTags"], ["alpha-launch"])
        self.assertEqual(alpha["source"]["selector"]["seedIds"], ["alpha-seed"])
        self.assertEqual(alpha["source"]["closure"]["strategy"], "active-one-hop-boundary")
        self.assertEqual(alpha["source"]["truncation"], {"source": False, "cap": False, "response": False})

    def test_tag_seeded_multi_tag_union_and_identity_are_deterministic(self) -> None:
        params = {
            "workspacePath": self.workspace,
            "includeUnscheduled": True,
            "maxItems": 50,
            "selector": {"launchTags": ["DEMO-LAUNCH", "alpha-launch"]},
        }
        first = self.reader.timeline_snapshot(params)
        second = self.reader.timeline_snapshot({
            **params,
            "selector": {"launchTags": ["alpha-launch", "demo-launch"]},
        })
        self.assertEqual(first["source"]["outputHash"], second["source"]["outputHash"])
        self.assertEqual(first["source"]["generationId"], second["source"]["generationId"])
        self.assertEqual(first["source"]["selector"]["launchTags"], ["alpha-launch", "demo-launch"])
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
            "selector": {"launchTags": ["alpha-launch"]},
        })["source"]["outputHash"]
        with closing(sqlite3.connect(self.db_path)) as connection:
            demo_data = {"title": "Changed Demo seed", "status": "open", "tags": ["demo-launch"]}
            connection.execute(
                "UPDATE tracker_items SET data=? WHERE id='demo-seed'",
                (json.dumps(demo_data),),
            )
            connection.commit()
        alpha_after_demo_change = self.reader.timeline_snapshot({
            "workspacePath": self.workspace,
            "selector": {"launchTags": ["alpha-launch"]},
        })["source"]["outputHash"]
        self.assertEqual(alpha_before, alpha_after_demo_change)

        with closing(sqlite3.connect(self.db_path)) as connection:
            alpha_data = {"title": "Changed Alpha seed", "status": "open", "tags": ["alpha-launch"]}
            connection.execute(
                "UPDATE tracker_items SET data=? WHERE id='alpha-seed'",
                (json.dumps(alpha_data),),
            )
            connection.commit()
        alpha_after_alpha_change = self.reader.timeline_snapshot({
            "workspacePath": self.workspace,
            "selector": {"launchTags": ["alpha-launch"]},
        })["source"]["outputHash"]
        self.assertNotEqual(alpha_before, alpha_after_alpha_change)

    def test_tag_selector_rejects_invalid_empty_and_overflowed_projections(self) -> None:
        invalid_params = [
            {"selector": {"launchTags": []}},
            {"selector": {"launchTags": ["Alpha", " alpha "]}},
            {"selector": {"launchTags": ["alpha"], "other": True}},
            {"selector": {"launchTags": ["alpha"]}, "launch": "FFP-1"},
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
                "selector": {"launchTags": ["alpha-launch"]},
            })
        self.assertEqual(raised.exception.code, "RESULT_LIMIT_EXCEEDED")

        with patch("reader.database.MAX_TIMELINE_SELECTOR_SOURCE_ITEMS", 1):
            with self.assertRaises(ReaderError) as raised:
                self.reader.timeline_snapshot({
                    "workspacePath": self.workspace,
                    "selector": {"launchTags": ["alpha-launch"]},
                })
        self.assertEqual(raised.exception.code, "SOURCE_LIMIT_EXCEEDED")

        with patch("reader.database.MAX_RESULT_BYTES", 1_000):
            with self.assertRaises(ReaderError) as raised:
                self.reader.timeline_snapshot({
                    "workspacePath": self.workspace,
                    "selector": {"launchTags": ["alpha-launch"]},
                })
        self.assertEqual(raised.exception.code, "RESPONSE_TOO_LARGE")

    def test_tag_selector_fails_on_incident_invalid_or_missing_endpoints(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            self._link(connection, "link-alpha-missing", "REL-MISSING", "alpha-seed", "missing-id", "related")
            connection.commit()
        with self.assertRaises(ReaderError) as raised:
            self.reader.timeline_snapshot({
                "workspacePath": self.workspace,
                "selector": {"launchTags": ["alpha-launch"]},
            })
        self.assertEqual(raised.exception.code, "VALIDATION_FAILED")

        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("DELETE FROM tracker_items WHERE id='link-alpha-missing'")
            self._link(connection, "link-alpha-invalid", "REL-INVALID", "alpha-seed", "shared-evidence", "unknown-kind")
            connection.commit()
        with self.assertRaises(ReaderError) as raised:
            self.reader.timeline_snapshot({
                "workspacePath": self.workspace,
                "selector": {"launchTags": ["alpha-launch"]},
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
                "title": "Bad launch", "launchKey": "FFP-1", "status": "active",
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
            self._insert(connection, "member-3", "NIM-1552", "task", {"title": "Scope role bogus", "status": "open"})
            self._insert(connection, "member-4", "NIM-1553", "task", {"title": "Scope conflict", "status": "open"})
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
            self._insert(connection, "gate", "NIM-1560", "task", {"title": "Gate", "status": "open"})
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
        schema_path = Path("C:/Development/PrediClear/.nimbalyst/trackers/timeline-link.json.yaml")
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
            self._insert(connection, "attn-open", "NIM-1570", "feature", {"title": "Attention only", "status": "open", "owner": "someone-else", "tags": ["needs-pm-attention"]})
            self._insert(connection, "attn-done", "NIM-1571", "feature", {"title": "Attention done", "status": "done", "owner": "someone-else", "tags": ["needs-pm-attention"]})
            connection.commit()
        result = self.reader.query_items({"workspacePath": self.workspace, "savedQuery": {"id": "role-active-work-and-attention", "params": {"roleId": "project-manager"}}})
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
            "roots": ["FFP-1"],
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
            "roots": ["FFP-1"],
            "membership": {"relationshipTypes": ["part-of-launch"], "direction": "incoming", "status": ["active"], "maxDepth": 1},
            "limits": {"maxNodes": 1},
        }
        truncated = self.reader.traverse_graph({**base, "failOn": {"truncation": False, "validation": False}})
        self.assertTrue(truncated["page"]["truncated"])
        self.assertEqual([node["id"] for node in truncated["nodes"]], ["launch-1"])
        with self.assertRaises(ReaderError) as raised:
            self.reader.traverse_graph({**base, "failOn": {"truncation": True, "validation": False}})
        self.assertEqual(raised.exception.code, "RESULT_TRUNCATED")


if __name__ == "__main__":
    unittest.main()
