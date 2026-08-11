"""Strict, workspace-scoped, read-only access to native tracker comments."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

try:
    from .contracts import (
        DEFAULT_LIMIT,
        DEFAULT_REPORT_LOOKAHEAD_DAYS,
        DEFAULT_TIMELINE_ITEMS,
        MAX_COMMENT_CHARS,
        MAX_LIMIT,
        MAX_REPORT_LOOKAHEAD_DAYS,
        MAX_RESULT_BYTES,
        MAX_TIMELINE_ITEMS,
        MAX_TIMELINE_SELECTOR_SOURCE_ITEMS,
        MAX_TIMELINE_SELECTOR_SOURCE_RELATIONSHIPS,
        MAX_TIMELINE_SELECTOR_TAG_CHARS,
        MAX_TIMELINE_SELECTOR_TAGS,
        MAX_TRACKER_BODY_CHARS,
        REQUIRED_COLUMNS,
        SCHEMA_ADAPTER,
        ReaderError,
    )
    from .query import PredicateCompiler, decode_cursor, encode_cursor, expand_saved_query, predicate_matches, sort_sql, validate_sort
    from .registry import bundled_diagnostics, effective_registry
    from .traverse import archived_explicitly_allowed, edge_matches, neighbor, validate_stage
except ImportError:  # pragma: no cover - used when server.py runs as a script
    from contracts import (  # type: ignore[no-redef]
        DEFAULT_LIMIT,
        DEFAULT_REPORT_LOOKAHEAD_DAYS,
        DEFAULT_TIMELINE_ITEMS,
        MAX_COMMENT_CHARS,
        MAX_LIMIT,
        MAX_REPORT_LOOKAHEAD_DAYS,
        MAX_RESULT_BYTES,
        MAX_TIMELINE_ITEMS,
        MAX_TIMELINE_SELECTOR_SOURCE_ITEMS,
        MAX_TIMELINE_SELECTOR_SOURCE_RELATIONSHIPS,
        MAX_TIMELINE_SELECTOR_TAG_CHARS,
        MAX_TIMELINE_SELECTOR_TAGS,
        MAX_TRACKER_BODY_CHARS,
        REQUIRED_COLUMNS,
        SCHEMA_ADAPTER,
        ReaderError,
    )
    from query import PredicateCompiler, decode_cursor, encode_cursor, expand_saved_query, predicate_matches, sort_sql, validate_sort  # type: ignore[no-redef]
    from registry import bundled_diagnostics, effective_registry  # type: ignore[no-redef]
    from traverse import archived_explicitly_allowed, edge_matches, neighbor, validate_stage  # type: ignore[no-redef]


class NativeTrackerReader:
    """Open a fresh read-only SQLite connection for each bounded operation."""

    def __init__(self, database_path: Path | None = None) -> None:
        self._database_path = database_path
        self._registry, self._registry_override_active, self._registry_override_error, self._registry_hash = effective_registry(Path.cwd())
        self._bundle_diagnostics = bundled_diagnostics()

    def _load_registry(self, workspace_path: str) -> None:
        (
            self._registry,
            self._registry_override_active,
            self._registry_override_error,
            self._registry_hash,
        ) = effective_registry(workspace_path)

    def list_comments(self, params: Mapping[str, Any]) -> dict[str, Any]:
        parsed = self._validated_params(params)
        self._load_registry(parsed["workspacePath"])
        row, schema_fingerprint = self._find_tracker(
            parsed["workspacePath"], parsed["trackerId"]
        )
        data = self._parse_data(row)
        result = {
            "tracker": self._tracker_summary(row, data),
            **self._comment_page(row["id"], data.get("comments"), parsed),
            "source": self._source(schema_fingerprint),
        }
        return self._fit_result(result)

    def get_with_comments(self, params: Mapping[str, Any]) -> dict[str, Any]:
        parsed = self._validated_params(params)
        self._load_registry(parsed["workspacePath"])
        row, schema_fingerprint = self._find_tracker(
            parsed["workspacePath"], parsed["trackerId"]
        )
        data = self._parse_data(row)
        body = row["content"] if isinstance(row["content"], str) else data.get("description", "")
        if not isinstance(body, str):
            body = ""
        bounded_body, body_truncated = self._truncate(body, MAX_TRACKER_BODY_CHARS)

        tracker = {
            **self._tracker_summary(row, data),
            "primaryType": row["type"],
            "typeTags": self._parse_type_tags(row["type_tags"]),
            "status": self._optional_string(data.get("status")),
            "priority": self._optional_string(data.get("priority")),
            "owner": self._optional_string(data.get("owner")),
            "created": self._optional_string(row["created"]),
            "updated": self._optional_string(row["updated"]),
            "body": bounded_body,
            "bodyTruncated": body_truncated,
        }
        result = {
            "tracker": tracker,
            **self._comment_page(row["id"], data.get("comments"), parsed),
            "source": self._source(schema_fingerprint),
        }
        return self._fit_result(result)

    def schema_fingerprint(self) -> str:
        with self._connect() as connection:
            return self._validate_schema(connection)

    def timeline_snapshot(self, params: Mapping[str, Any]) -> dict[str, Any]:
        parsed = self._validated_timeline_params(params)
        self._load_registry(parsed["workspacePath"])
        if parsed.get("selector"):
            return self._tag_seeded_timeline_snapshot(parsed)
        if parsed.get("launch"):
            return self._launch_timeline_snapshot(parsed)
        link_rows: list[tuple[sqlite3.Row, dict[str, Any]]] = []
        try:
            with self._connect() as connection:
                fingerprint = self._validate_schema(connection)
                schema_discovery = self._schema_discovery(
                    parsed["workspacePath"], connection
                )
                raw_link_rows = connection.execute(
                    """
                    SELECT id, issue_key, type, data, content, archived, type_tags, created, updated
                    FROM tracker_items
                    WHERE workspace = ?
                      AND deleted_at IS NULL
                      AND archived = 0
                      AND type = 'timeline-link'
                    ORDER BY updated DESC, id ASC
                    LIMIT ?
                    """,
                    (parsed["workspacePath"], parsed["maxItems"] + 1),
                ).fetchall()

                link_query_truncated = len(raw_link_rows) > parsed["maxItems"]
                for row in raw_link_rows[: parsed["maxItems"]]:
                    data = self._parse_data(row)
                    link_rows.append((row, self._flatten_custom_fields(data)))

                if link_rows:
                    endpoint_ids: set[str] = set()
                    for _row, fields in link_rows:
                        endpoint_ids.update(
                            target["itemId"]
                            for field_name in ("sourceItem", "targetItem")
                            for target in self._relationship_targets(fields.get(field_name))
                        )
                    ordered_endpoint_ids = sorted(endpoint_ids)
                    if ordered_endpoint_ids:
                        placeholders = ",".join("?" for _item_id in ordered_endpoint_ids)
                        rows = connection.execute(
                            f"""
                            SELECT id, issue_key, type, data, content, archived, type_tags, created, updated
                            FROM tracker_items
                            WHERE workspace = ?
                              AND deleted_at IS NULL
                              AND type <> 'timeline-link'
                              AND (
                                id IN ({placeholders})
                                OR (type = 'milestone' AND archived = 0)
                                OR (type = 'timeline-item' AND archived = 0)
                              )
                            ORDER BY updated DESC, id ASC
                            LIMIT ?
                            """,
                            (
                                parsed["workspacePath"],
                                *ordered_endpoint_ids,
                                parsed["maxItems"] + 1,
                            ),
                        ).fetchall()
                    else:
                        rows = connection.execute(
                            """
                            SELECT id, issue_key, type, data, content, archived, type_tags, created, updated
                            FROM tracker_items
                            WHERE workspace = ?
                              AND deleted_at IS NULL
                              AND archived = 0
                              AND type IN ('milestone', 'timeline-item')
                            ORDER BY updated DESC, id ASC
                            LIMIT ?
                            """,
                            (parsed["workspacePath"], parsed["maxItems"] + 1),
                        ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT id, issue_key, type, data, content, archived, type_tags, created, updated
                        FROM tracker_items
                        WHERE workspace = ?
                          AND deleted_at IS NULL
                          AND archived = 0
                          AND type <> 'timeline-link'
                        ORDER BY updated DESC, id ASC
                        LIMIT ?
                        """,
                        (parsed["workspacePath"], parsed["maxItems"] + 1),
                    ).fetchall()
        except ReaderError:
            raise
        except sqlite3.OperationalError as error:
            message = str(error).lower()
            if "locked" in message or "busy" in message:
                raise ReaderError(
                    "DATABASE_BUSY",
                    "The Nimbalyst tracker database is busy. Retry the read shortly.",
                ) from None
            raise ReaderError(
                "DATABASE_READ_FAILED",
                "The Nimbalyst tracker database could not be read safely.",
            ) from None

        query_truncated = link_query_truncated or len(rows) > parsed["maxItems"]
        rows = rows[: parsed["maxItems"]]
        items: list[dict[str, Any]] = []
        raw_fields_by_id: dict[str, dict[str, Any]] = {}
        source_revision: str | None = None

        for _row, fields in link_rows:
            source_revision = source_revision or self._bounded_string(
                fields.get("sourceRevision"), 200
            )

        for row in rows:
            data = self._parse_data(row)
            fields = self._flatten_custom_fields(data)
            source_revision = source_revision or self._bounded_string(
                fields.get("sourceRevision"), 200
            )
            item = self._timeline_item(row, fields)
            if not parsed["includeUnscheduled"] and not self._is_scheduled(item):
                continue
            if not self._in_timeline_range(item, parsed.get("fromMs"), parsed.get("toMs")):
                continue
            items.append(item)
            raw_fields_by_id[item["id"]] = fields

        edges, relationship_findings = self._normalized_relationships(
            items, raw_fields_by_id, link_rows
        )
        self._apply_derived_milestone_progress(items, edges)
        self._apply_launch_rollups(items, edges)
        critical_path, analysis_findings = self._apply_timeline_analysis(items, edges)
        validation = [
            *self._registry_findings(),
            *self._schema_discovery_findings(schema_discovery),
            *relationship_findings,
            *analysis_findings,
            *self._launch_findings(items, edges),
            *self._validate_timeline(items, edges),
        ]
        item_ids = {item["id"] for item in items}
        for edge in edges:
            edge["targetInSnapshot"] = edge["targetId"] in item_ids

        source = self._source(fingerprint, schema_discovery)
        source["sourceRevision"] = source_revision or "unavailable"
        source["relationshipSource"] = (
            "native timeline-link rows" if link_rows else "legacy tracker fields"
        )
        source["relationshipRows"] = len(link_rows)
        source["sourceItemCount"] = len(rows)
        source["sourceRelationshipCount"] = len(link_rows)
        source["endpointItems"] = len(items)
        source["milestoneRows"] = sum(item["primaryType"] == "milestone" for item in items)
        source["legacyRelationships"] = sum(edge.get("legacy") is True for edge in edges)
        source["includeArchivedLinkedEvidence"] = bool(link_rows)
        undated_items = [
            item for item in items
            if not item.get("startDate")
            and not item.get("dueDate")
            and not item.get("forecastDate")
        ]
        source["activeUnscheduledItems"] = sum(
            self._is_active_executable(item) for item in undated_items
        )
        source["excludedUndatedEvidence"] = sum(
            not self._is_active_executable(item) for item in undated_items
        )
        source["milestoneProgressSource"] = "active primary deliverables"

        for item in items:
            for key in [entry for entry in item if entry.startswith("_")]:
                item.pop(key, None)

        result = {
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "items": items,
            "milestones": [item for item in items if item["primaryType"] == "milestone"],
            "relationships": edges,
            "validation": validation,
            "criticalPath": critical_path,
            "page": {
                "maxItems": parsed["maxItems"],
                "returned": len(items),
                "queryTruncated": query_truncated,
                "responseTruncated": False,
            },
            "source": source,
        }
        return self._fit_timeline_result(result)

    def _tag_seeded_timeline_snapshot(self, parsed: Mapping[str, Any]) -> dict[str, Any]:
        """Build a complete tag-seeded projection with one-hop boundary closure."""
        selector = parsed["selector"]
        launch_tags = list(selector["launchTags"])
        started = time.perf_counter()
        try:
            with self._connect() as connection:
                fingerprint = self._validate_schema(connection)
                schema_discovery = self._schema_discovery(parsed["workspacePath"], connection)
                item_rows = connection.execute(
                    """
                    SELECT id, issue_key, type, data, content, archived, type_tags, created, updated
                    FROM tracker_items
                    WHERE workspace = ?
                      AND deleted_at IS NULL
                      AND type <> 'timeline-link'
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (parsed["workspacePath"], MAX_TIMELINE_SELECTOR_SOURCE_ITEMS + 1),
                ).fetchall()
                link_db_rows = connection.execute(
                    """
                    SELECT id, issue_key, type, data, content, archived, type_tags, created, updated
                    FROM tracker_items
                    WHERE workspace = ?
                      AND deleted_at IS NULL
                      AND type = 'timeline-link'
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (parsed["workspacePath"], MAX_TIMELINE_SELECTOR_SOURCE_RELATIONSHIPS + 1),
                ).fetchall()
        except ReaderError:
            raise
        except sqlite3.OperationalError as error:
            message = str(error).lower()
            if "locked" in message or "busy" in message:
                raise ReaderError("DATABASE_BUSY", "The Nimbalyst tracker database is busy. Retry the read shortly.") from None
            raise ReaderError("DATABASE_READ_FAILED", "The Nimbalyst tracker database could not be read safely.") from None

        if len(item_rows) > MAX_TIMELINE_SELECTOR_SOURCE_ITEMS or len(link_db_rows) > MAX_TIMELINE_SELECTOR_SOURCE_RELATIONSHIPS:
            raise ReaderError(
                "SOURCE_LIMIT_EXCEEDED",
                "The tag selector source exceeded its safe scan limit; no timeline file was replaced.",
                {
                    "itemLimit": MAX_TIMELINE_SELECTOR_SOURCE_ITEMS,
                    "relationshipLimit": MAX_TIMELINE_SELECTOR_SOURCE_RELATIONSHIPS,
                },
            )

        all_items: list[dict[str, Any]] = []
        raw_fields_by_id: dict[str, dict[str, Any]] = {}
        row_by_id: dict[str, sqlite3.Row] = {}
        for row in item_rows:
            fields = self._flatten_custom_fields(self._parse_data(row))
            item = self._timeline_item(row, fields)
            item["archived"] = bool(row["archived"])
            all_items.append(item)
            raw_fields_by_id[item["id"]] = fields
            row_by_id[item["id"]] = row
        items_by_id = {item["id"]: item for item in all_items}
        tag_set = set(launch_tags)
        seed_ids = {
            item["id"]
            for item in all_items
            if not item.get("archived")
            and tag_set.intersection(str(tag).strip().casefold() for tag in item.get("tags", []))
            and (parsed["includeUnscheduled"] or self._is_scheduled(item))
            and self._in_timeline_range(item, parsed.get("fromMs"), parsed.get("toMs"))
        }
        if not seed_ids:
            raise ReaderError(
                "SELECTOR_NO_MATCH",
                "No active tracker items matched selector.launchTags; no timeline file was replaced.",
                {"selector": {"type": "launchTags", "launchTags": launch_tags}},
            )

        link_rows = [
            (row, self._flatten_custom_fields(self._parse_data(row)))
            for row in link_db_rows
        ]
        candidate_link_ids: set[str] = set()
        for row, fields in link_rows:
            if (self._bounded_string(fields.get("status"), 40) or "active") != "active":
                continue
            endpoint_ids = {
                target["itemId"]
                for field_name in ("sourceItem", "targetItem")
                for target in self._relationship_targets(fields.get(field_name))
            }
            if endpoint_ids.intersection(seed_ids):
                candidate_link_ids.add(str(row["id"]))

        edges, normalization_findings = self._normalized_relationships(
            all_items, raw_fields_by_id, link_rows
        )
        candidate_edges = [
            edge for edge in edges
            if edge["id"] in candidate_link_ids and edge.get("state") == "active"
        ]
        closure_ids = set(seed_ids)
        for edge in candidate_edges:
            closure_ids.update((edge["sourceId"], edge["targetId"]))

        relevant_findings = [
            finding for finding in normalization_findings
            if candidate_link_ids.intersection(finding.get("relationshipIds", []))
        ]
        missing_endpoint_ids: set[str] = set()
        tolerated_archived_ids: set[str] = set()
        for edge in candidate_edges:
            for endpoint in (edge["sourceId"], edge["targetId"]):
                item = items_by_id.get(endpoint)
                tolerated = bool(
                    item
                    and item.get("archived")
                    and edge.get("relationshipType") == "evidences"
                    and edge.get("effectiveRevision")
                )
                if tolerated:
                    tolerated_archived_ids.add(endpoint)
                if item is None or (item.get("archived") and not tolerated):
                    missing_endpoint_ids.add(endpoint)
                    relevant_findings.append(self._finding(
                        "orphan-endpoint",
                        "error",
                        f"Relationship {edge.get('issueKey') or edge['id']} has an unavailable endpoint.",
                        item_ids=[endpoint],
                        relationship_ids=[edge["id"]],
                    ))

        if missing_endpoint_ids:
            raise ReaderError(
                "VALIDATION_FAILED",
                "The selected relationship closure has unavailable endpoints; no timeline file was replaced.",
                {"missingEndpointIds": sorted(missing_endpoint_ids)},
            )
        if len(closure_ids) > parsed["maxItems"]:
            raise ReaderError(
                "RESULT_LIMIT_EXCEEDED",
                "The complete selected relationship closure exceeds maxItems; no timeline file was replaced.",
                {"discoveredItems": len(closure_ids), "maxItems": parsed["maxItems"]},
            )
        max_edges = min(self._registry["caps"]["traverseEdgesMax"], parsed["maxItems"] * 2)
        if len(candidate_edges) > max_edges:
            raise ReaderError(
                "RESULT_LIMIT_EXCEEDED",
                "The complete selected relationship closure exceeds the relationship cap; no timeline file was replaced.",
                {"discoveredRelationships": len(candidate_edges), "maxRelationships": max_edges},
            )

        boundary_ids = closure_ids - seed_ids
        items = [items_by_id[item_id] for item_id in sorted(closure_ids)]
        for item in items:
            item["boundary"] = item["id"] in boundary_ids
            item["selectorSeed"] = item["id"] in seed_ids
        selected_edges = sorted(candidate_edges, key=lambda edge: edge["id"])
        self._apply_derived_milestone_progress(items, selected_edges)
        self._apply_launch_rollups(items, selected_edges)
        critical_path, analysis_findings = self._apply_timeline_analysis(items, selected_edges)
        validation = [
            *self._registry_findings(),
            *self._schema_discovery_findings(schema_discovery),
            *relevant_findings,
            *analysis_findings,
            *self._launch_findings(items, selected_edges),
            *self._validate_timeline(items, selected_edges),
        ]
        validation_block = self._validation_block(validation)
        if validation_block["state"] == "fail":
            raise ReaderError(
                "VALIDATION_FAILED",
                "The selected timeline failed validation; no timeline file was replaced.",
                {"validation": validation_block},
            )

        source_revision: str | None = None
        for item_id in sorted(closure_ids):
            source_revision = source_revision or self._bounded_string(
                raw_fields_by_id[item_id].get("sourceRevision"), 200
            )
        link_fields_by_id = {str(row["id"]): fields for row, fields in link_rows}
        for edge in selected_edges:
            source_revision = source_revision or self._bounded_string(
                link_fields_by_id[edge["id"]].get("sourceRevision"), 200
            )
        for item in items:
            item.pop("archived", None)
            for key in [entry for entry in item if entry.startswith("_")]:
                item.pop(key, None)

        semantic_projection = {
            "selector": {"type": "launchTags", "launchTags": launch_tags},
            "items": items,
            "relationships": selected_edges,
            "criticalPath": critical_path,
            "schemaAdapter": SCHEMA_ADAPTER,
            "schemaFingerprint": fingerprint,
            "registryVersion": self._registry["version"],
            "registryHash": self._registry_hash,
            "readerBundle": copy.deepcopy(self._bundle_diagnostics),
            "sourceRevision": source_revision or "unavailable",
        }
        output_hash = hashlib.sha256(
            json.dumps(semantic_projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        source = self._source(fingerprint, schema_discovery)
        source.update({
            "sourceRevision": source_revision or "unavailable",
            "relationshipSource": "native timeline-link rows",
            "relationshipRows": len(link_db_rows),
            "sourceItemCount": len(item_rows),
            "sourceRelationshipCount": len(link_db_rows),
            "endpointItems": len(items),
            "milestoneRows": sum(item.get("primaryType") == "milestone" for item in items),
            "selector": {
                "type": "launchTags",
                "launchTags": launch_tags,
                "seedCount": len(seed_ids),
                "seedIds": sorted(seed_ids),
            },
            "closure": {
                "strategy": "active-one-hop-boundary",
                "seedCount": len(seed_ids),
                "boundaryCount": len(boundary_ids),
                "itemCount": len(items),
                "relationshipCount": len(selected_edges),
            },
            "emitted": {
                "itemCount": len(items),
                "relationshipCount": len(selected_edges),
                "milestoneCount": sum(item.get("primaryType") == "milestone" for item in items),
            },
            "validationCounts": {
                "total": len(validation),
                "error": sum(finding.get("severity") == "error" for finding in validation),
                "warning": sum(finding.get("severity") == "warning" for finding in validation),
                "info": sum(finding.get("severity") == "info" for finding in validation),
            },
            "truncation": {"source": False, "cap": False, "response": False},
            "generatedAt": generated_at,
            "generationId": f"tag-selector:{output_hash}",
            "outputHash": output_hash,
        })
        result = {
            "generatedAt": generated_at,
            "items": items,
            "milestones": [
                item for item in items
                if item.get("primaryType") == "milestone" and not item.get("boundary")
            ],
            "relationships": selected_edges,
            "validation": validation,
            "criticalPath": critical_path,
            "page": {
                "maxItems": parsed["maxItems"],
                "returned": len(items),
                "queryTruncated": False,
                "responseTruncated": False,
            },
            "source": source,
        }
        fitted = self._fit_timeline_result(result)
        if fitted["page"]["responseTruncated"]:
            raise ReaderError(
                "RESPONSE_TOO_LARGE",
                "The complete selected timeline exceeded the safe response size; no timeline file was replaced.",
            )
        return fitted

    def _launch_timeline_snapshot(self, parsed: Mapping[str, Any]) -> dict[str, Any]:
        # Membership pulls direct part-of-launch members of the root launch at
        # depth one. Expand keeps part-of-launch as well so that members which
        # are themselves launch containers (e.g. lanes) surface their own
        # nested part-of-launch members as one-hop boundary context instead of
        # silently dropping registered timeline-item walk steps. Nested members
        # remain boundary nodes: they are excluded from launch rollups and never
        # promoted to direct launch membership.
        relationship_types = list(self._registry["relationshipTypes"])
        graph = self.traverse_graph({
            "workspacePath": parsed["workspacePath"],
            "roots": [parsed["launch"]],
            "membership": {"relationshipTypes": ["part-of-launch"], "direction": "incoming", "status": ["active"], "maxDepth": 1},
            "expand": {"relationshipTypes": relationship_types, "direction": "both", "maxDepth": 1, "edgeWhere": {"status": ["active"]}, "externalEndpointBehavior": "boundary"},
            "limits": {"maxNodes": parsed["maxItems"], "maxEdges": min(self._registry["caps"]["traverseEdgesMax"], parsed["maxItems"] * 2)},
            "failOn": {"truncation": False, "validation": False},
        })
        nodes = list(graph["nodes"])
        boundary_nodes = list(graph["boundaryNodes"])
        root_ids = set(graph["query"].get("resolvedRoots", []))
        for item in nodes:
            item["boundary"] = False
            item["launchMember"] = item["id"] not in root_ids
        for item in boundary_nodes:
            item["boundary"] = True
            item["launchMember"] = False
        items = nodes + boundary_nodes
        edges = list(graph["edges"])
        self._apply_derived_milestone_progress(items, edges)
        self._apply_launch_rollups(items, edges)
        critical_path, analysis_findings = self._apply_timeline_analysis(items, edges)
        validation = [*graph["validation"]["findings"], *analysis_findings, *self._validate_timeline(items, edges)]
        watermark = graph["watermark"]
        schema_discovery = watermark.get("schemaDiscovery")
        source = self._source(
            watermark["schemaFingerprint"],
            schema_discovery if isinstance(schema_discovery, Mapping) else None,
        )
        source.update({
            "sourceRevision": "unavailable",
            "relationshipSource": "native timeline-link rows",
            "relationshipRows": watermark["sourceRelationshipCount"],
            "sourceItemCount": watermark["sourceItemCount"],
            "sourceRelationshipCount": watermark["sourceRelationshipCount"],
            "endpointItems": len(items),
            "milestoneRows": sum(item.get("primaryType") == "milestone" for item in items),
            "rootLaunch": parsed["launch"],
            "membership": {"memberCount": sum(bool(item.get("launchMember")) for item in items), "boundaryCount": len(boundary_nodes)},
        })
        result = {
            "generatedAt": watermark["generatedAt"],
            "items": items,
            "milestones": [item for item in items if item.get("primaryType") == "milestone" and not item.get("boundary")],
            "relationships": edges,
            "validation": validation,
            "criticalPath": critical_path,
            "page": {"maxItems": parsed["maxItems"], "returned": len(items), "queryTruncated": graph["page"]["truncated"], "responseTruncated": False},
            "source": source,
        }
        return self._fit_timeline_result(result)

    def query_items(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Run one bounded, cursor-paged predicate query over tracker items."""
        started = time.perf_counter()
        allowed = {
            "workspacePath", "where", "savedQuery", "sort", "limit", "cursor",
            "includeArchived", "includeRelationshipRecords", "includeTotalCount",
        }
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise ReaderError("INVALID_PARAMS", f"Unknown parameter(s): {', '.join(unknown)}.")
        workspace_path = params.get("workspacePath")
        if not isinstance(workspace_path, str) or not Path(workspace_path).is_absolute():
            raise ReaderError("WORKSPACE_UNAVAILABLE", "The query requires an open local workspace.")
        self._load_registry(workspace_path)
        has_where = "where" in params
        has_saved = "savedQuery" in params
        if has_where == has_saved:
            raise ReaderError("QUERY_INVALID", "Exactly one of where or savedQuery is required.")
        expanded: dict[str, Any] = dict(params)
        query_echo: dict[str, Any] = {}
        if has_saved:
            definition, query_echo = expand_saved_query(params["savedQuery"], self._registry, "predicate")
            expanded = {**definition, **{key: value for key, value in params.items() if key not in {"savedQuery"}}}
        where = expanded.get("where")
        compiler = PredicateCompiler(self._registry)
        compiler.validate(where)
        where_sql, values = compiler.compile(where)
        sort = validate_sort(expanded.get("sort"))
        cursor_id = decode_cursor(expanded.get("cursor"), sort)
        caps = self._registry["caps"]
        limit = expanded.get("limit", caps["queryLimitDefault"])
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= caps["queryLimitMax"]:
            raise ReaderError("QUERY_INVALID", f"limit must be an integer from 1 through {caps['queryLimitMax']}.")
        include_archived = expanded.get("includeArchived", False)
        include_links = expanded.get("includeRelationshipRecords", False)
        include_total = expanded.get("includeTotalCount", True)
        if not all(isinstance(value, bool) for value in (include_archived, include_links, include_total)):
            raise ReaderError("QUERY_INVALID", "Query include flags must be booleans.")
        implicit = ["workspace=current", "deleted_at IS NULL"]
        sql_parts = ["workspace = ?", "deleted_at IS NULL", where_sql]
        bindings: list[Any] = [workspace_path, *values]
        if not include_archived:
            sql_parts.append("archived = 0")
            implicit.append("archived = false")
        if not include_links:
            sql_parts.append("type <> 'timeline-link'")
            implicit.append("type != timeline-link")
        predicate_sql = " AND ".join(f"({part})" for part in sql_parts)
        try:
            with self._connect() as connection:
                fingerprint = self._validate_schema(connection)
                schema_discovery = self._schema_discovery(
                    workspace_path, connection
                )
                source_counts = connection.execute(
                    "SELECT COUNT(*), SUM(CASE WHEN type='timeline-link' THEN 1 ELSE 0 END) FROM tracker_items WHERE workspace=? AND deleted_at IS NULL",
                    (workspace_path,),
                ).fetchone()
                rows = connection.execute(
                    f"SELECT id, issue_key, type, data, content, archived, type_tags, created, updated FROM tracker_items WHERE {predicate_sql} ORDER BY {sort_sql(sort)}",
                    bindings,
                ).fetchall()
                total_count = len(rows) if include_total else None
                validation_rows = connection.execute(
                    """SELECT id, issue_key, type, data, content, archived, type_tags, created, updated
                       FROM tracker_items
                       WHERE workspace=? AND deleted_at IS NULL
                         AND (type <> 'timeline-link' OR json_extract(data,'$.relationshipType')='part-of-launch')
                       ORDER BY id ASC""",
                    (workspace_path,),
                ).fetchall()
        except ReaderError:
            raise
        except sqlite3.OperationalError:
            raise ReaderError("DATABASE_READ_FAILED", "The Nimbalyst tracker database could not be queried safely.") from None
        start_index = 0
        if cursor_id is not None:
            matching = [index for index, row in enumerate(rows) if str(row["id"]) == cursor_id]
            if len(matching) != 1:
                raise ReaderError("CURSOR_INVALID", "The query cursor no longer identifies a result row.")
            start_index = matching[0] + 1
        page_rows = list(rows[start_index:start_index + limit + 1])
        has_more = len(page_rows) > limit
        page_rows = page_rows[:limit]
        nodes: list[dict[str, Any]] = []
        link_rows: list[tuple[sqlite3.Row, dict[str, Any]]] = []
        raw_fields: dict[str, dict[str, Any]] = {}
        for row in page_rows:
            fields = self._flatten_custom_fields(self._parse_data(row))
            if row["type"] == "timeline-link":
                link_rows.append((row, fields))
                continue
            item = self._timeline_item(row, fields)
            item["type"] = item["primaryType"]
            nodes.append(item)
            raw_fields[item["id"]] = fields
        edges, edge_findings = self._normalized_relationships(nodes, raw_fields, link_rows)
        validation_items: list[dict[str, Any]] = []
        validation_fields: dict[str, dict[str, Any]] = {}
        validation_links: list[tuple[sqlite3.Row, dict[str, Any]]] = []
        for row in validation_rows:
            fields = self._flatten_custom_fields(self._parse_data(row))
            if row["type"] == "timeline-link":
                validation_links.append((row, fields))
            elif not row["archived"]:
                item = self._timeline_item(row, fields)
                validation_items.append(item)
                validation_fields[item["id"]] = fields
        validation_edges, validation_edge_findings = self._normalized_relationships(validation_items, validation_fields, validation_links)
        rollup_source_ids = sorted({
            edge["sourceId"] for edge in validation_edges
            if edge.get("relationshipType") == "part-of-launch" and edge.get("state") == "active" and edge.get("scopeRole") in {"core", "acceptance"}
        } | {item["id"] for item in validation_items if item.get("primaryType") == "launch"})
        if rollup_source_ids:
            placeholders = ",".join("?" for _ in rollup_source_ids)
            with self._connect() as connection:
                dependency_rows = connection.execute(
                    f"""SELECT id, issue_key, type, data, content, archived, type_tags, created, updated
                        FROM tracker_items WHERE workspace=? AND deleted_at IS NULL AND type='timeline-link'
                          AND json_extract(data,'$.relationshipType')='depends-on'
                          AND json_extract(data,'$.sourceItem.itemId') IN ({placeholders})
                        ORDER BY id ASC""",
                    (workspace_path, *rollup_source_ids),
                ).fetchall()
            if dependency_rows:
                validation_links.extend((row, self._flatten_custom_fields(self._parse_data(row))) for row in dependency_rows)
                validation_edges, validation_edge_findings = self._normalized_relationships(validation_items, validation_fields, validation_links)
        self._apply_launch_rollups(validation_items, validation_edges)
        rollups = {item["id"]: item.get("launchRollup") for item in validation_items if item.get("launchRollup")}
        for node in nodes:
            if node["id"] in rollups:
                node["launchRollup"] = rollups[node["id"]]
                node["progress"] = rollups[node["id"]]["derivedProgress"]
        findings = [
            *self._registry_findings(),
            *self._schema_discovery_findings(schema_discovery),
            *edge_findings,
            *validation_edge_findings,
            *self._launch_findings(validation_items, validation_edges),
        ]
        result = {
            "nodes": nodes,
            "edges": sorted(edges, key=lambda edge: edge["id"]),
            "boundaryNodes": [],
            "page": {
                "totalCount": total_count,
                "returnedCount": len(page_rows),
                "nextCursor": encode_cursor(sort, str(page_rows[-1]["id"])) if has_more and page_rows else None,
                "truncated": False,
                "resultsComplete": not has_more,
                "continuationRequired": has_more,
                "responseTruncated": False,
            },
            "validation": self._validation_block(findings),
            "watermark": self._watermark(
                fingerprint,
                int(source_counts[0] or 0),
                int(source_counts[1] or 0),
                started,
                schema_discovery,
            ),
            "query": {
                **query_echo,
                "where": where,
                "sort": sort,
                "implicit": implicit,
                "limit": limit,
                "cursor": expanded.get("cursor"),
                "includeArchived": include_archived,
                "includeRelationshipRecords": include_links,
                "includeTotalCount": include_total,
            },
        }
        result["query"]["queryFingerprint"] = self._stable_fingerprint(
            {
                "query": result["query"],
                "schemaAdapter": SCHEMA_ADAPTER,
                "schemaFingerprint": fingerprint,
                "registryVersion": self._registry["version"],
                "registryHash": self._registry_hash,
                "sourceItemCount": int(source_counts[0] or 0),
                "sourceRelationshipCount": int(source_counts[1] or 0),
            }
        )
        return self._fit_graph_result(result, sort)

    def traverse_graph(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Traverse normalized relationships from one or more bounded roots."""
        started = time.perf_counter()
        allowed = {"workspacePath", "roots", "membership", "expand", "nodeWhere", "limits", "failOn", "savedQuery", "paginate", "cursor"}
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise ReaderError("INVALID_PARAMS", f"Unknown parameter(s): {', '.join(unknown)}.")
        workspace_path = params.get("workspacePath")
        if not isinstance(workspace_path, str) or not Path(workspace_path).is_absolute():
            raise ReaderError("WORKSPACE_UNAVAILABLE", "Traversal requires an open local workspace.")
        self._load_registry(workspace_path)
        expanded: dict[str, Any] = dict(params)
        query_echo: dict[str, Any] = {}
        if "savedQuery" in params:
            if "roots" in params:
                raise ReaderError("QUERY_INVALID", "savedQuery and roots cannot be combined.")
            definition, query_echo = expand_saved_query(params["savedQuery"], self._registry, "traversal")
            expanded = {
                **definition,
                "workspacePath": workspace_path,
                **{
                    key: params[key]
                    for key in ("paginate", "cursor")
                    if key in params
                },
            }
            if definition.get("mode") == "dispatch-eligible-work-v1":
                if params.get("paginate") or params.get("cursor") is not None:
                    raise ReaderError("QUERY_INVALID", "Dispatch traversal does not support pagination.")
                return self._dispatch_eligible_work(
                    workspace_path,
                    query_echo,
                    started,
                )
            if definition.get("mode") == "composed-v1":
                if params.get("paginate") or params.get("cursor") is not None:
                    raise ReaderError("QUERY_INVALID", "Composed traversal does not support pagination.")
                return self._composed_saved_traversal(
                    workspace_path,
                    definition,
                    query_echo,
                    started,
                )
        roots = expanded.get("roots")
        caps = self._registry["caps"]
        if not isinstance(roots, list) or not 1 <= len(roots) <= caps["traverseRootsMax"] or not all(isinstance(value, str) and value.strip() for value in roots):
            raise ReaderError("QUERY_TOO_COMPLEX", f"roots must contain 1 through {caps['traverseRootsMax']} non-empty identifiers.")
        node_where = expanded.get("nodeWhere")
        if node_where is not None:
            predicate_compiler = PredicateCompiler(self._registry)
            predicate_compiler.validate(node_where, "nodeWhere")
        limits = expanded.get("limits", {})
        if not isinstance(limits, Mapping) or set(limits) - {"maxNodes", "maxEdges"}:
            raise ReaderError("QUERY_INVALID", "limits is invalid.")
        max_nodes = limits.get("maxNodes", caps["traverseNodesMax"])
        max_edges = limits.get("maxEdges", caps["traverseEdgesMax"])
        if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or not 1 <= max_nodes <= caps["traverseNodesMax"]:
            raise ReaderError("QUERY_TOO_COMPLEX", f"maxNodes must be at most {caps['traverseNodesMax']}.")
        if isinstance(max_edges, bool) or not isinstance(max_edges, int) or not 1 <= max_edges <= caps["traverseEdgesMax"]:
            raise ReaderError("QUERY_TOO_COMPLEX", f"maxEdges must be at most {caps['traverseEdgesMax']}.")
        fail_on = expanded.get("failOn", {})
        if not isinstance(fail_on, Mapping) or set(fail_on) - {"truncation", "validation"} or not all(isinstance(value, bool) for value in fail_on.values()):
            raise ReaderError("QUERY_INVALID", "failOn is invalid.")
        paginate = expanded.get("paginate", False)
        cursor = expanded.get("cursor")
        if not isinstance(paginate, bool):
            raise ReaderError("QUERY_INVALID", "paginate must be a boolean.")
        if cursor is not None and (
            not paginate or not isinstance(cursor, str) or not cursor
        ):
            raise ReaderError(
                "CURSOR_INVALID",
                "Traversal cursor requires paginate=true and an opaque non-empty cursor.",
            )
        try:
            with self._connect() as connection:
                fingerprint = self._validate_schema(connection)
                schema_discovery = self._schema_discovery(
                    workspace_path, connection
                )
                rows = connection.execute(
                    "SELECT id, issue_key, type, data, content, archived, type_tags, created, updated FROM tracker_items WHERE workspace=? AND deleted_at IS NULL ORDER BY id ASC",
                    (workspace_path,),
                ).fetchall()
        except ReaderError:
            raise
        except sqlite3.OperationalError:
            raise ReaderError("DATABASE_READ_FAILED", "The Nimbalyst tracker database could not be traversed safely.") from None
        item_rows = [row for row in rows if row["type"] != "timeline-link"]
        link_db_rows = [row for row in rows if row["type"] == "timeline-link"]
        all_items: list[dict[str, Any]] = []
        fields_by_id: dict[str, dict[str, Any]] = {}
        row_by_id: dict[str, dict[str, Any]] = {}
        for row in item_rows:
            fields = self._flatten_custom_fields(self._parse_data(row))
            item = self._timeline_item(row, fields)
            item["type"] = item["primaryType"]
            item["archived"] = bool(row["archived"])
            all_items.append(item)
            fields_by_id[item["id"]] = fields
            row_by_id[item["id"]] = {key: row[key] for key in row.keys()}
        items_by_id = {item["id"]: item for item in all_items}
        link_rows = [(row, self._flatten_custom_fields(self._parse_data(row))) for row in link_db_rows]
        edges, normalization_findings = self._normalized_relationships(all_items, fields_by_id, link_rows)
        traversal_edges = [
            edge for edge in edges
            if edge.get("state") not in {"retired", "unknown"}
        ]
        edges_by_source: dict[str, list[dict[str, Any]]] = {}
        edges_by_target: dict[str, list[dict[str, Any]]] = {}
        for edge in traversal_edges:
            edges_by_source.setdefault(edge["sourceId"], []).append(edge)
            edges_by_target.setdefault(edge["targetId"], []).append(edge)
        for values in [*edges_by_source.values(), *edges_by_target.values()]:
            values.sort(key=lambda value: value["id"])

        def incident(node_id: str, direction: str) -> list[dict[str, Any]]:
            values: dict[str, dict[str, Any]] = {}
            if direction in {"outgoing", "both"}:
                values.update((edge["id"], edge) for edge in edges_by_source.get(node_id, []))
            if direction in {"incoming", "both"}:
                values.update((edge["id"], edge) for edge in edges_by_target.get(node_id, []))
            return [values[key] for key in sorted(values)]

        resolved_roots: list[str] = []
        allow_archived_root = archived_explicitly_allowed(node_where)
        for raw_root in roots:
            matches = [item for item in all_items if raw_root in {item["id"], item.get("issueKey"), item.get("launchKey")}]
            if not allow_archived_root:
                matches = [item for item in matches if not item.get("archived")]
            if not matches:
                raise ReaderError("ROOT_NOT_FOUND", "A traversal root was not found in the current workspace.", {"root": raw_root})
            if len(matches) > 1:
                raise ReaderError("ROOT_AMBIGUOUS", "A traversal root matched more than one item.", {"root": raw_root})
            resolved_roots.append(matches[0]["id"])
        resolved_roots = sorted(set(resolved_roots))
        default_membership = any(items_by_id[root].get("primaryType") == "launch" for root in resolved_roots)
        membership_raw = expanded.get("membership")
        if membership_raw is None and default_membership:
            membership_raw = {"relationshipTypes": ["part-of-launch"], "direction": "incoming", "status": ["active"], "maxDepth": 1}
        membership = validate_stage(membership_raw, "membership", self._registry, membership=True)
        expand = validate_stage(expanded.get("expand"), "expand", self._registry, membership=False)

        member_ids: set[str] = set()
        selected_edge_ids: set[str] = set()
        node_depth: dict[str, int] = {root: 0 for root in resolved_roots}
        frontier = list(resolved_roots)
        if membership:
            for depth in range(1, membership["maxDepth"] + 1):
                next_frontier: set[str] = set()
                for node_id in sorted(frontier):
                    for edge in incident(node_id, membership["direction"]):
                        if not edge_matches(edge, membership, membership=True):
                            continue
                        next_id = neighbor(edge, node_id, membership["direction"])
                        if next_id:
                            selected_edge_ids.add(edge["id"])
                        if next_id and next_id in items_by_id and next_id not in resolved_roots and next_id not in member_ids:
                            next_frontier.add(next_id)
                            node_depth.setdefault(next_id, depth)
                member_ids.update(next_frontier)
                frontier = sorted(next_frontier)
                if not frontier:
                    break

        boundary_ids: set[str] = set()
        if expand:
            context_seen = set(resolved_roots) | member_ids
            frontier = sorted(context_seen)
            stop_at_boundary = membership is not None and expand["externalEndpointBehavior"] == "boundary"
            for depth in range(1, expand["maxDepth"] + 1):
                next_frontier: set[str] = set()
                for node_id in frontier:
                    for edge in incident(node_id, expand["direction"]):
                        if not edge_matches(edge, expand, membership=False):
                            continue
                        next_id = neighbor(edge, node_id, expand["direction"])
                        if not next_id:
                            continue
                        selected_edge_ids.add(edge["id"])
                        if next_id in items_by_id:
                            if stop_at_boundary and next_id not in context_seen:
                                boundary_ids.add(next_id)
                            elif next_id not in context_seen:
                                next_frontier.add(next_id)
                                node_depth.setdefault(next_id, depth + 10)
                context_seen.update(next_frontier)
                boundary_ids.update(next_frontier - member_ids - set(resolved_roots))
                frontier = sorted(next_frontier)
                if not frontier:
                    break
            if expand["externalEndpointBehavior"] == "exclude":
                boundary_ids.clear()

        kept_member_ids = set(member_ids)
        if node_where is not None:
            kept_member_ids = {
                item_id for item_id in member_ids
                if predicate_matches(row_by_id[item_id], fields_by_id[item_id], node_where, self._registry)
            }
        kept_ids = set(resolved_roots) | kept_member_ids | boundary_ids
        candidate_edges = [edge for edge in traversal_edges if edge["id"] in selected_edge_ids]
        findings = [
            *self._registry_findings(),
            *self._schema_discovery_findings(schema_discovery),
        ]
        tolerated_archived_ids: set[str] = set()
        for edge in candidate_edges:
            for endpoint in (edge["sourceId"], edge["targetId"]):
                item = items_by_id.get(endpoint)
                tolerated = item and item.get("archived") and edge.get("relationshipType") == "evidences" and edge.get("state") == "active" and edge.get("effectiveRevision")
                if tolerated:
                    tolerated_archived_ids.add(endpoint)
                if item is None or (item.get("archived") and not tolerated):
                    raise ReaderError(
                        "UNRESOLVED_EDGE",
                        "A selected traversal relationship has an unavailable endpoint.",
                        {
                            "relationshipId": edge["id"],
                            "endpointId": endpoint,
                            "resolvedRoots": resolved_roots,
                        },
                    )
        kept_ids = {item_id for item_id in kept_ids if not items_by_id[item_id].get("archived") or item_id in tolerated_archived_ids}
        selected_edges = [edge for edge in candidate_edges if edge["sourceId"] in kept_ids and edge["targetId"] in kept_ids]
        selected_edge_ids = {edge["id"] for edge in selected_edges}
        findings.extend(
            finding for finding in normalization_findings
            if selected_edge_ids.intersection(finding.get("relationshipIds", []))
            or kept_ids.intersection(finding.get("itemIds", []))
        )
        result_items = [items_by_id[item_id] for item_id in sorted(kept_ids)]
        findings.extend(self._semantic_duplicate_findings(selected_edges))
        findings.extend(self._launch_findings(result_items, selected_edges))
        self._apply_launch_rollups(result_items, selected_edges)
        validation = self._validation_block(findings)

        if validation["state"] != "pass" and fail_on.get("validation", False):
            raise ReaderError("VALIDATION_FAILED", "Traversal validation failed.", {"validation": validation})
        query_receipt = {
            **query_echo,
            "roots": roots,
            "resolvedRoots": resolved_roots,
            "membership": membership,
            "expand": expand,
            "nodeWhere": node_where,
            "limits": {"maxNodes": max_nodes, "maxEdges": max_edges},
            "failOn": dict(fail_on),
            "paginate": paginate,
        }
        query_receipt["queryFingerprint"] = self._stable_fingerprint(
            {
                "query": query_receipt,
                "schemaAdapter": SCHEMA_ADAPTER,
                "schemaFingerprint": fingerprint,
                "registryVersion": self._registry["version"],
                "registryHash": self._registry_hash,
                "sourceItemCount": len(item_rows),
                "sourceRelationshipCount": len(link_db_rows),
            }
        )

        if paginate:
            ordinary_ids = sorted(kept_ids - boundary_ids)
            ordered_boundary_ids = sorted(kept_ids & boundary_ids)
            node_stream = [
                ("node", items_by_id[item_id]) for item_id in ordinary_ids
            ] + [
                ("boundary", items_by_id[item_id])
                for item_id in ordered_boundary_ids
            ]
            edge_stream = sorted(selected_edges, key=lambda edge: edge["id"])
            result_fingerprint = self._stable_fingerprint({
                "queryFingerprint": query_receipt["queryFingerprint"],
                "nodeIds": [item["id"] for _kind, item in node_stream],
                "edgeIds": [edge["id"] for edge in edge_stream],
            })
            node_offset, edge_offset = self._decode_traversal_cursor(
                cursor,
                result_fingerprint,
                len(node_stream),
                len(edge_stream),
            )
            node_page = node_stream[node_offset:node_offset + max_nodes]
            edge_page = edge_stream[edge_offset:edge_offset + max_edges]
            result = {
                "nodes": [item for kind, item in node_page if kind == "node"],
                "edges": edge_page,
                "boundaryNodes": [
                    item for kind, item in node_page if kind == "boundary"
                ],
                "page": {
                    "totalCount": len(node_stream),
                    "totalEdgeCount": len(edge_stream),
                    "returnedCount": len(node_page),
                    "returnedEdgeCount": len(edge_page),
                    "nextCursor": None,
                    "truncated": False,
                    "resultsComplete": False,
                    "continuationRequired": False,
                    "responseTruncated": False,
                },
                "validation": validation,
                "watermark": self._watermark(
                    fingerprint,
                    len(item_rows),
                    len(link_db_rows),
                    started,
                    schema_discovery,
                ),
                "query": {
                    **query_receipt,
                    "resultFingerprint": result_fingerprint,
                },
            }
            return self._fit_paginated_traversal_result(
                result,
                node_offset,
                edge_offset,
                len(node_stream),
                len(edge_stream),
                result_fingerprint,
            )

        ordered_levels: dict[int, list[str]] = {}
        for item_id in kept_ids:
            ordered_levels.setdefault(node_depth.get(item_id, 99), []).append(item_id)
        retained_ids: set[str] = set()
        truncated = False
        for depth in sorted(ordered_levels):
            level = sorted(ordered_levels[depth])
            if len(retained_ids) + len(level) > max_nodes:
                truncated = True
                break
            retained_ids.update(level)
        selected_edges = [edge for edge in selected_edges if edge["sourceId"] in retained_ids and edge["targetId"] in retained_ids]
        if len(selected_edges) > max_edges:
            selected_edges = sorted(selected_edges, key=lambda edge: edge["id"])[:max_edges]
            truncated = True
        if truncated and fail_on.get("truncation", False):
            raise ReaderError("RESULT_TRUNCATED", "Traversal caps prevented a complete graph response. Retry with paginate=true and follow every page.nextCursor.")
        nodes = [items_by_id[item_id] for item_id in sorted(retained_ids - boundary_ids)]
        boundary_nodes = [items_by_id[item_id] for item_id in sorted(retained_ids & boundary_ids)]
        result = {
            "nodes": nodes,
            "edges": sorted(selected_edges, key=lambda edge: edge["id"]),
            "boundaryNodes": boundary_nodes,
            "page": {"totalCount": len(kept_ids), "returnedCount": len(nodes) + len(boundary_nodes), "nextCursor": None, "truncated": truncated},
            "validation": validation,
            "watermark": self._watermark(
                fingerprint,
                len(item_rows),
                len(link_db_rows),
                started,
                schema_discovery,
            ),
            "query": query_receipt,
        }
        fitted = self._fit_graph_result(result)
        if fitted["page"]["truncated"] and fail_on.get("truncation", False):
            raise ReaderError("RESULT_TRUNCATED", "Traversal caps prevented a complete graph response.")
        if fitted["validation"]["state"] != "pass" and fail_on.get("validation", False):
            raise ReaderError("VALIDATION_FAILED", "Traversal validation failed.", {"validation": fitted["validation"]})
        return fitted

    def _composed_saved_traversal(
        self,
        workspace_path: str,
        definition: Mapping[str, Any],
        query_echo: Mapping[str, Any],
        started: float,
    ) -> dict[str, Any]:
        """Select bounded roots, then traverse them as one saved-query result."""
        allowed = {"mode", "select", "traverse", "projection", "failOn"}
        if set(definition) - allowed:
            raise ReaderError("QUERY_INVALID", "The composed saved query contains unknown fields.")
        select = definition.get("select")
        traverse = definition.get("traverse", {})
        projection = definition.get("projection")
        fail_on = definition.get("failOn", {})
        if not isinstance(select, Mapping) or not isinstance(traverse, Mapping):
            raise ReaderError("QUERY_INVALID", "The composed saved query requires select and traverse objects.")
        if (
            not isinstance(fail_on, Mapping)
            or set(fail_on) - {"truncation", "validation"}
            or not all(isinstance(value, bool) for value in fail_on.values())
        ):
            raise ReaderError("QUERY_INVALID", "The composed saved query failOn policy is invalid.")
        allowed_select = {
            "where", "sort", "limit", "includeArchived",
            "includeRelationshipRecords", "includeTotalCount",
        }
        if set(select) - allowed_select or "where" not in select:
            raise ReaderError("QUERY_INVALID", "The composed root selector is invalid.")
        allowed_traverse = {"membership", "expand", "nodeWhere", "limits"}
        if set(traverse) - allowed_traverse:
            raise ReaderError("QUERY_INVALID", "The composed traversal stage is invalid.")

        selection = self.query_items(
            {
                "workspacePath": workspace_path,
                **dict(select),
                "includeArchived": False,
                "includeRelationshipRecords": False,
                "includeTotalCount": True,
            }
        )
        root_ids = [str(node["id"]) for node in selection["nodes"]]
        root_cap = self._registry["caps"]["traverseRootsMax"]
        selection_incomplete = bool(
            selection["page"].get("nextCursor")
            or selection["page"].get("truncated")
            or len(root_ids) > root_cap
        )
        if selection_incomplete and fail_on.get("truncation", False):
            raise ReaderError(
                "RESULT_TRUNCATED",
                "The composed root selection exceeded its bounded traversal capacity.",
                {
                    "selectedRootCount": len(root_ids),
                    "rootLimit": root_cap,
                    "selectionComplete": False,
                },
            )
        if (
            selection["validation"]["state"] != "pass"
            and fail_on.get("validation", False)
        ):
            raise ReaderError(
                "VALIDATION_FAILED",
                "Composed root selection validation failed.",
                {"validation": selection["validation"]},
            )

        selection_receipt = {
            "totalCount": selection["page"].get("totalCount"),
            "selectedRootCount": len(root_ids),
            "complete": not selection_incomplete,
            "validationState": selection["validation"]["state"],
            "queryFingerprint": selection["query"].get("queryFingerprint"),
        }
        if not root_ids:
            result = {
                "nodes": [],
                "edges": [],
                "boundaryNodes": [],
                "page": {
                    "totalCount": 0,
                    "returnedCount": 0,
                    "nextCursor": None,
                    "truncated": False,
                },
                "validation": selection["validation"],
                "watermark": selection["watermark"],
                "query": {
                    **dict(query_echo),
                    "mode": "composed-v1",
                    "selection": selection_receipt,
                    "resolvedRoots": [],
                    "failOn": dict(fail_on),
                },
            }
            result["query"]["queryFingerprint"] = self._stable_fingerprint(
                {
                    "query": result["query"],
                    "registryHash": self._registry_hash,
                    "schemaFingerprint": result["watermark"]["schemaFingerprint"],
                }
            )
            return result

        traversal = self.traverse_graph(
            {
                "workspacePath": workspace_path,
                "roots": root_ids[:root_cap],
                **dict(traverse),
                "failOn": {
                    "truncation": bool(fail_on.get("truncation", False)),
                    "validation": bool(fail_on.get("validation", False)),
                },
            }
        )
        if projection == "walk-readiness-v1":
            self._apply_walk_readiness_projection(
                traversal,
                set(root_ids[:root_cap]),
            )
        elif projection is not None:
            raise ReaderError("QUERY_INVALID", "The composed saved query projection is unsupported.")

        traversal["query"] = {
            **dict(query_echo),
            "mode": "composed-v1",
            "selection": selection_receipt,
            "resolvedRoots": traversal["query"]["resolvedRoots"],
            "membership": traversal["query"]["membership"],
            "expand": traversal["query"]["expand"],
            "nodeWhere": traversal["query"]["nodeWhere"],
            "limits": traversal["query"]["limits"],
            "failOn": dict(fail_on),
            "projection": projection,
        }
        traversal["query"]["queryFingerprint"] = self._stable_fingerprint(
            {
                "query": traversal["query"],
                "schemaAdapter": SCHEMA_ADAPTER,
                "schemaFingerprint": traversal["watermark"]["schemaFingerprint"],
                "registryVersion": self._registry["version"],
                "registryHash": self._registry_hash,
            }
        )
        traversal["watermark"]["durationMs"] = round(
            (time.perf_counter() - started) * 1000,
            2,
        )
        fitted = self._fit_graph_result(traversal)
        if fitted["page"]["truncated"] and fail_on.get("truncation", False):
            raise ReaderError("RESULT_TRUNCATED", "The composed graph response was truncated.")
        if fitted["validation"]["state"] != "pass" and fail_on.get("validation", False):
            raise ReaderError(
                "VALIDATION_FAILED",
                "Composed traversal validation failed.",
                {"validation": fitted["validation"]},
            )
        return fitted

    def _apply_walk_readiness_projection(
        self,
        result: dict[str, Any],
        root_ids: set[str],
    ) -> None:
        """Attach evidence-backed walk controls without inferring from labels."""
        items = {
            str(item["id"]): item
            for item in [*result["nodes"], *result["boundaryNodes"]]
        }
        findings = list(result["validation"]["findings"])
        terminal = {
            str(value).casefold()
            for value in self._registry["terminalStatuses"]
        }
        for root_id in sorted(root_ids):
            root = items.get(root_id)
            if root is None:
                continue
            workflow_terminal = str(root.get("status") or "").casefold() in terminal
            predecessor_edges = sorted(
                (
                    edge
                    for edge in result["edges"]
                    if edge.get("sourceId") == root_id
                    and edge.get("relationshipType") == "depends-on"
                    and edge.get("hardness") == "hard-serial"
                    and edge.get("state") in {"active", "blocked"}
                ),
                key=lambda edge: edge["id"],
            )
            implementing_edges = sorted(
                (
                    edge
                    for edge in result["edges"]
                    if edge.get("targetId") == root_id
                    and edge.get("relationshipType") in {"implements", "evidences"}
                    and edge.get("state") == "active"
                    and edge.get("sourceId") in items
                ),
                key=lambda edge: edge["id"],
            )
            provenance = dict(root.get("walkReadinessProvenance") or {})
            stored_build = (
                (provenance.get("buildState") or {}).get("storedValue")
                if isinstance(provenance.get("buildState"), Mapping)
                else None
            )
            walk_stage = root.get("walkStage")
            acceptance_present = bool(provenance.get("acceptanceContentPresent"))
            runtime_available = root.get("requiredRuntimeAvailable")

            if workflow_terminal:
                build_state = "build-complete"
                readiness = "walk-ready"
                predecessor_rows: list[dict[str, Any]] = []
                numerator, denominator = 1, 1
            else:
                predecessor_rows = [
                    {
                        "itemId": edge["targetId"],
                        "issueKey": items.get(edge["targetId"], {}).get("issueKey"),
                        "relationshipId": edge["id"],
                        "state": edge["state"],
                        "clearingCondition": edge.get("clearingCondition"),
                        "ownerLabel": edge.get("ownerLabel"),
                    }
                    for edge in predecessor_edges
                ]
                if stored_build == "build-complete" and implementing_edges:
                    build_state = "build-complete"
                elif stored_build in {"in-build", "not-started"}:
                    build_state = stored_build
                else:
                    build_state = "unknown"
                gates = [
                    build_state == "build-complete",
                    not predecessor_rows,
                    runtime_available is True,
                ]
                numerator, denominator = sum(gates), len(gates)
                if (
                    walk_stage == "unknown"
                    or build_state == "unknown"
                    or runtime_available is None
                    or not acceptance_present
                ):
                    readiness = "unknown"
                elif predecessor_rows:
                    readiness = "blocked"
                elif build_state != "build-complete" or runtime_available is not True:
                    readiness = "not-ready"
                else:
                    readiness = "walk-ready"

            root["buildState"] = build_state
            root["readiness"] = readiness
            root["serialPredecessor"] = predecessor_rows[0] if predecessor_rows else None
            root["serialPredecessors"] = predecessor_rows
            root["blockingCondition"] = (
                predecessor_rows[0].get("clearingCondition")
                if predecessor_rows
                else None
            )
            root["blockingOwner"] = (
                predecessor_rows[0].get("ownerLabel")
                if predecessor_rows
                else None
            )
            root["walkReadiness"] = {
                "numerator": numerator,
                "denominator": denominator,
                "percentage": round((numerator / denominator) * 100, 2),
                "fraction": f"{numerator}/{denominator}",
            }
            root["walkReadinessProvenance"] = {
                **provenance,
                "workflowTerminal": workflow_terminal,
                "implementingEvidence": [
                    {
                        "itemId": edge["sourceId"],
                        "issueKey": items[edge["sourceId"]].get("issueKey"),
                        "relationshipId": edge["id"],
                        "relationshipType": edge["relationshipType"],
                    }
                    for edge in implementing_edges
                ],
                "buildState": {
                    "storedValue": stored_build,
                    "derivedValue": build_state,
                    "derived": build_state != stored_build,
                },
                "readiness": {
                    "storedValue": (
                        (provenance.get("readiness") or {}).get("storedValue")
                        if isinstance(provenance.get("readiness"), Mapping)
                        else None
                    ),
                    "derivedValue": readiness,
                    "derived": True,
                },
                "metricBasis": [
                    "build-complete",
                    "hard-serial-predecessors-cleared",
                    "required-runtime-available",
                ] if not workflow_terminal else ["terminal-root"],
            }
            if workflow_terminal:
                continue
            if walk_stage == "unknown":
                findings.append(self._finding(
                    "walk-stage-unknown",
                    "warning",
                    "A selected walk root has no supported native walkStage value.",
                    item_ids=[root_id],
                ))
            if not acceptance_present:
                findings.append(self._finding(
                    "walk-acceptance-content-missing",
                    "warning",
                    "A selected walk root has no native gate or acceptance content.",
                    item_ids=[root_id],
                ))
            if stored_build == "build-complete" and not implementing_edges:
                findings.append(self._finding(
                    "walk-build-evidence-missing",
                    "warning",
                    "Stored build-complete state has no resolved active implementing evidence.",
                    item_ids=[root_id],
                ))
            if runtime_available is None:
                findings.append(self._finding(
                    "walk-runtime-availability-unknown",
                    "warning",
                    "Required runtime availability is not explicitly recorded.",
                    item_ids=[root_id],
                ))
        result["validation"] = self._validation_block(findings)

    def _dispatch_eligible_work(
        self,
        workspace_path: str,
        query_echo: Mapping[str, Any],
        started: float,
    ) -> dict[str, Any]:
        """Resolve one deterministic, fail-closed dispatch candidate set."""
        policy = self._registry["dispatchPolicy"]
        evidence_mapping = self._registry["dispatchEvidence"]
        evidence_mapping_fingerprint = self._stable_fingerprint(evidence_mapping)
        saved = query_echo["savedQuery"]
        params = dict(saved.get("params", {}))
        launch_keys = [str(value) for value in params.get("launchKeys", [])]
        include_unscoped = bool(params.get("includeUnscoped", False))
        role_id = params.get("roleId")
        if include_unscoped and not policy["admittedUnscopedTypes"]:
            raise ReaderError(
                "UNSCOPED_WORK_NOT_CONFIGURED",
                "includeUnscoped requires at least one dispatchPolicy.admittedUnscopedTypes entry.",
                {
                    "includeUnscoped": True,
                    "admittedUnscopedTypes": [],
                },
            )
        try:
            with self._connect() as connection:
                fingerprint = self._validate_schema(connection)
                schema_discovery = self._schema_discovery(workspace_path, connection)
                rows = connection.execute(
                    """SELECT id, issue_key, type, data, content, archived, type_tags,
                              created, updated
                       FROM tracker_items
                       WHERE workspace=? AND deleted_at IS NULL
                       ORDER BY id ASC""",
                    (workspace_path,),
                ).fetchall()
        except ReaderError:
            raise
        except sqlite3.OperationalError:
            raise ReaderError(
                "DATABASE_READ_FAILED",
                "The Nimbalyst tracker database could not be queried safely.",
            ) from None

        item_rows = [row for row in rows if row["type"] != "timeline-link"]
        link_db_rows = [row for row in rows if row["type"] == "timeline-link"]
        items: list[dict[str, Any]] = []
        fields_by_id: dict[str, dict[str, Any]] = {}
        for row in item_rows:
            fields = self._flatten_custom_fields(self._parse_data(row))
            item = self._timeline_item(row, fields)
            item["type"] = item["primaryType"]
            item["archived"] = bool(row["archived"])
            items.append(item)
            fields_by_id[item["id"]] = fields
        items_by_id = {item["id"]: item for item in items}
        link_rows = [
            (row, self._flatten_custom_fields(self._parse_data(row)))
            for row in link_db_rows
        ]
        edges, normalization_findings = self._normalized_relationships(
            items,
            fields_by_id,
            link_rows,
        )
        live_edges = [
            edge for edge in edges
            if edge.get("state") not in {"retired", "unknown"}
        ]
        evidence_edges_by_item: dict[str, list[dict[str, Any]]] = {}
        for edge in live_edges:
            evidence_edges_by_item.setdefault(str(edge["sourceId"]), []).append(edge)
            evidence_edges_by_item.setdefault(str(edge["targetId"]), []).append(edge)
        for values in evidence_edges_by_item.values():
            values.sort(key=lambda edge: str(edge["id"]))
        evidence_by_item: dict[str, dict[str, dict[str, Any]]] = {}

        def dispatch_evidence(item_id: str) -> dict[str, dict[str, Any]]:
            if item_id not in evidence_by_item:
                evidence_by_item[item_id] = self._resolve_dispatch_evidence(
                    item_id,
                    fields_by_id[item_id],
                    evidence_edges_by_item.get(item_id, []),
                    evidence_mapping,
                )
            return evidence_by_item[item_id]

        eligible_launches = [
            item for item in items
            if not item.get("archived")
            and item.get("primaryType") in {"launch", "milestone"}
            and str(dispatch_evidence(item["id"])["workflow"]["value"] or "").casefold()
            in {value.casefold() for value in policy["eligibleLaunchStatuses"]}
        ]
        launch_by_key: dict[str, list[dict[str, Any]]] = {}
        for launch in eligible_launches:
            key = str(launch.get("launchKey") or "").strip()
            if key:
                launch_by_key.setdefault(key.casefold(), []).append(launch)
        selected_root_ids: set[str] = set()
        for raw_key in launch_keys:
            matches = launch_by_key.get(raw_key.casefold(), [])
            if not matches:
                raise ReaderError(
                    "ROOT_NOT_FOUND",
                    "A dispatch launch root was not found or is not eligible.",
                    {"root": raw_key},
                )
            if len(matches) > 1:
                raise ReaderError(
                    "ROOT_AMBIGUOUS",
                    "A dispatch launch root matched more than one eligible item.",
                    {"root": raw_key},
                )
            selected_root_ids.add(matches[0]["id"])
        if not launch_keys:
            selected_root_ids = {item["id"] for item in eligible_launches}

        active_scope_edges = [
            edge for edge in live_edges
            if edge.get("state") == "active"
            and (
                (
                    edge.get("relationshipType") == "part-of-launch"
                    and edge.get("scopeRole") in policy["membershipRoles"]
                )
                or (
                    edge.get("relationshipType") == "contributes-to"
                    and edge.get("contributionRole") in policy["contributionRoles"]
                )
            )
        ]
        outgoing_scope: dict[str, list[dict[str, Any]]] = {}
        for edge in active_scope_edges:
            outgoing_scope.setdefault(edge["sourceId"], []).append(edge)
        for values in outgoing_scope.values():
            values.sort(key=lambda edge: edge["id"])

        def ancestry(item_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            ancestry_edges: dict[str, dict[str, Any]] = {}
            ancestry_nodes: dict[str, dict[str, Any]] = {}
            frontier = [item_id]
            seen = {item_id}
            for _depth in range(4):
                next_frontier: list[str] = []
                for source_id in sorted(frontier):
                    for edge in outgoing_scope.get(source_id, []):
                        target_id = edge["targetId"]
                        target = items_by_id.get(target_id)
                        if target is None or target.get("archived"):
                            ancestry_edges[edge["id"]] = edge
                            continue
                        ancestry_edges[edge["id"]] = edge
                        ancestry_nodes[target_id] = target
                        if target_id not in seen:
                            seen.add(target_id)
                            next_frontier.append(target_id)
                frontier = next_frontier
                if not frontier:
                    break
            return (
                [ancestry_nodes[key] for key in sorted(ancestry_nodes)],
                [ancestry_edges[key] for key in sorted(ancestry_edges)],
            )

        dispatch_types = set(policy["dispatchableTypes"])
        source_items = [
            item for item in items
            if item.get("primaryType") in dispatch_types
        ]
        selected_relationships: dict[str, dict[str, Any]] = {}
        validation_item_ids: set[str] = set()
        receipts: list[dict[str, Any]] = []
        pre_admission_exclusions: list[dict[str, Any]] = []
        incomplete: list[dict[str, Any]] = []
        role_definition = (
            self._registry.get("roles", {}).get(str(role_id), {})
        )
        role_aliases = {
            alias.casefold()
            for alias in role_definition.get("ownerAliases", [])
        }
        role_attention_tags = {
            tag.casefold()
            for tag in role_definition.get("attentionTags", [])
        }
        hard_dependencies_by_source: dict[str, list[dict[str, Any]]] = {}
        for edge in live_edges:
            if (
                edge.get("relationshipType") == "depends-on"
                and edge.get("hardness") == "hard-serial"
            ):
                hard_dependencies_by_source.setdefault(edge["sourceId"], []).append(edge)
        for item in sorted(source_items, key=lambda value: value["id"]):
            fields = fields_by_id[item["id"]]
            evidence = dispatch_evidence(item["id"])
            ancestry_nodes, ancestry_edges = ancestry(item["id"])
            launches = [
                node for node in ancestry_nodes
                if node.get("primaryType") == "launch"
            ]
            milestones = [
                node for node in ancestry_nodes
                if node.get("primaryType") == "milestone"
            ]
            trains = [
                node for node in ancestry_nodes
                if node.get("primaryType") == "train"
            ]
            scoped_to_selected = any(
                node["id"] in selected_root_ids
                for node in [*launches, *milestones]
            )
            unscoped_admitted = (
                include_unscoped
                and item["primaryType"] in policy["admittedUnscopedTypes"]
                and not launches
                and not milestones
            )
            admission_reasons: list[str] = []
            if item.get("archived"):
                admission_reasons.append("archived")
            if str(evidence["workflow"]["value"] or "").casefold() not in {
                value.casefold() for value in policy["readyStatuses"]
            }:
                admission_reasons.append("workflow-not-dispatch-ready")
            item_tags = {
                str(tag).strip().casefold()
                for tag in item.get("tags", [])
                if isinstance(tag, str) and tag.strip()
            }
            role_matches = (
                str(item.get("ownerLabel") or "").casefold() in role_aliases
                or bool(item_tags.intersection(role_attention_tags))
            )
            if role_id and not role_matches:
                admission_reasons.append("role-mismatch")
            if not scoped_to_selected and not unscoped_admitted:
                admission_reasons.append("scope-not-admitted")
            if admission_reasons:
                pre_admission_exclusions.append({
                    "itemId": item["id"],
                    "issueKey": item.get("issueKey"),
                    "reasons": sorted(set(admission_reasons)),
                })
                continue

            packet_revision = evidence["packetRevision"]["value"]
            current_revision = evidence["currentRevision"]["value"]
            is_current = evidence["isCurrentRevision"]["value"]
            qa_revision = evidence["qaEvidenceRevision"]["value"]
            qa_status = evidence["qaStatus"]["value"]
            hold_state = evidence["holdState"]["value"]
            database_route = evidence["databaseRouteState"]["value"]
            custody_state = evidence["custodyState"]["value"]
            survivor_state = evidence["survivorState"]["value"]
            collision_state = evidence["collisionState"]["value"]
            execution_constraint = evidence["executionConstraint"]["value"]
            failure_state = evidence["failureState"]["value"]
            superseded_by = evidence["supersededBy"]["value"]
            reasons: list[str] = []
            if packet_revision is None:
                reasons.append("packet-revision-missing")
            if not (
                is_current is True
                or (
                    packet_revision is not None
                    and current_revision is not None
                    and packet_revision == current_revision
                )
            ):
                reasons.append("packet-not-current-revision")
            if qa_revision is None:
                reasons.append("qa-evidence-revision-missing")
            elif packet_revision is not None and qa_revision != packet_revision:
                reasons.append("qa-evidence-revision-mismatch")
            if str(qa_status or "").casefold() not in {
                value.casefold() for value in policy["qaPassStatuses"]
            }:
                reasons.append("qa-not-passed")
            if str(hold_state or "").casefold() not in {
                value.casefold() for value in policy["clearHoldStates"]
            }:
                reasons.append("hold-not-clear")
            if execution_constraint in {"blocked", "paused", "waiting"}:
                reasons.append(f"execution-{execution_constraint}")
            if str(database_route or "").casefold() not in {
                value.casefold() for value in policy["admissibleDatabaseRoutes"]
            }:
                reasons.append("database-route-inadmissible")
            if str(custody_state or "").casefold() not in {
                value.casefold() for value in policy["clearCustodyStates"]
            }:
                reasons.append("custody-conflict-or-unknown")
            if str(survivor_state or "").casefold() not in {
                value.casefold() for value in policy["survivorStates"]
            }:
                reasons.append("survivor-state-ineligible")
            if str(collision_state or "").casefold() not in {
                value.casefold() for value in policy["clearCollisionStates"]
            }:
                reasons.append("collision-or-overlap")
            if failure_state and failure_state.casefold() not in {"clear", "none", "passed"}:
                reasons.append("failure-present")
            if superseded_by:
                reasons.append("superseded")
            dependencies = sorted(
                hard_dependencies_by_source.get(item["id"], []),
                key=lambda edge: edge["id"],
            )
            if any(edge.get("state") in {"active", "blocked"} for edge in dependencies):
                reasons.append("hard-dependency-unsatisfied")

            evidence_required = True
            missing_fields = [
                name for name, value in {
                    "packetRevision": packet_revision,
                    "qaEvidenceRevision": qa_revision,
                    "qaStatus": qa_status,
                    "holdState": hold_state,
                    "databaseRouteState": database_route,
                    "custodyState": custody_state,
                    "survivorState": survivor_state,
                    "collisionState": collision_state,
                }.items()
                if value is None
            ]
            missing_signals = list(missing_fields)
            if not (is_current is True or current_revision is not None):
                missing_signals.append("revisionCurrentness")
            if evidence_required and missing_signals:
                incomplete.append(
                    {
                        "itemId": item["id"],
                        "issueKey": item.get("issueKey"),
                        "missingFields": missing_fields,
                        "missingLogicalSignals": sorted(missing_signals),
                    }
                )
            if evidence_required:
                validation_item_ids.add(item["id"])
                for edge in [*ancestry_edges, *dependencies]:
                    target = items_by_id.get(edge["targetId"])
                    if target is None or target.get("archived"):
                        raise ReaderError(
                            "UNRESOLVED_EDGE",
                            "A selected dispatch relationship has an unavailable endpoint.",
                            {
                                "relationshipId": edge["id"],
                                "endpointId": edge["targetId"],
                            },
                        )
                    selected_relationships[edge["id"]] = edge
            relationship_ids = sorted(edge["id"] for edge in ancestry_edges)
            scope_fingerprint = self._stable_fingerprint(
                {
                    "itemId": item["id"],
                    "relationshipIds": relationship_ids,
                    "launchKeys": launch_keys,
                    "includeUnscoped": include_unscoped,
                }
            )
            receipt = {
                "itemId": item["id"],
                "issueKey": item.get("issueKey"),
                "included": not reasons,
                "evidence": evidence,
                "packetRevision": packet_revision,
                "qaEvidenceRevision": qa_revision,
                "ancestry": {
                    "launches": self._dispatch_ancestry_rows(launches, ancestry_edges),
                    "milestones": self._dispatch_ancestry_rows(milestones, ancestry_edges),
                    "trains": self._dispatch_ancestry_rows(trains, ancestry_edges),
                },
                "dependencyEvidence": [
                    {
                        "relationshipId": edge["id"],
                        "targetId": edge["targetId"],
                        "state": edge.get("state"),
                        "clearingCondition": edge.get("clearingCondition"),
                        "cleared": edge.get("state") == "cleared",
                    }
                    for edge in dependencies
                ],
                "holdState": hold_state,
                "databaseRouteState": database_route,
                "custody": {
                    "state": custody_state,
                    "pullRequest": fields.get("pullRequestCustody"),
                    "session": fields.get("sessionCustody"),
                    "worktree": fields.get("worktreeCustody"),
                },
                "survivorState": survivor_state,
                "collisionState": collision_state,
                "scopeFingerprint": scope_fingerprint,
                "eligibilityReasons": ["eligible"] if not reasons else [],
                "exclusionReasons": sorted(set(reasons)),
                "frontier": {
                    "branch": self._bounded_string(fields.get("branch"), 500),
                    "train": self._bounded_string(fields.get("train"), 200),
                },
            }
            receipts.append(receipt)

        for edge in live_edges:
            if (
                edge.get("relationshipType") == "precedes"
                and edge.get("state") == "active"
                and edge.get("sourceId") in validation_item_ids
                and edge.get("targetId") in validation_item_ids
            ):
                selected_relationships[edge["id"]] = edge
        selected_edge_list = [
            selected_relationships[key] for key in sorted(selected_relationships)
        ]

        # Candidate edges are intentionally admission-scoped, but launch
        # lifecycle validation must inspect the selected roots' actual active
        # membership graph. Keep that graph separate so non-dispatch members
        # can prove a launch is structurally valid without leaking into the
        # candidate response.
        incoming_memberships: dict[str, list[dict[str, Any]]] = {}
        for edge in live_edges:
            if (
                edge.get("relationshipType") == "part-of-launch"
                and edge.get("state") == "active"
            ):
                incoming_memberships.setdefault(str(edge["targetId"]), []).append(edge)
        for values in incoming_memberships.values():
            values.sort(key=lambda edge: str(edge["id"]))
        launch_validation_relationships: dict[str, dict[str, Any]] = {}
        validation_frontier = sorted(selected_root_ids)
        inspected_validation_nodes: set[str] = set()
        while validation_frontier:
            target_id = validation_frontier.pop(0)
            if target_id in inspected_validation_nodes:
                continue
            inspected_validation_nodes.add(target_id)
            for edge in incoming_memberships.get(target_id, []):
                for endpoint_id in (str(edge["sourceId"]), str(edge["targetId"])):
                    endpoint = items_by_id.get(endpoint_id)
                    if endpoint is None or endpoint.get("archived"):
                        raise ReaderError(
                            "UNRESOLVED_EDGE",
                            "A selected launch membership has an unavailable endpoint.",
                            {
                                "relationshipId": edge["id"],
                                "endpointId": endpoint_id,
                                "resolvedRoots": sorted(selected_root_ids),
                            },
                        )
                launch_validation_relationships[str(edge["id"])] = edge
                source_id = str(edge["sourceId"])
                if source_id not in inspected_validation_nodes:
                    validation_frontier.append(source_id)
            validation_frontier.sort()
        launch_validation_edge_list = [
            launch_validation_relationships[key]
            for key in sorted(launch_validation_relationships)
        ]
        selected_item_ids = {
            receipt["itemId"] for receipt in receipts
        } | {
            endpoint
            for edge in selected_edge_list
            for endpoint in (edge["sourceId"], edge["targetId"])
        }
        validation_relationships = {
            **selected_relationships,
            **launch_validation_relationships,
        }
        scoped_normalization_findings = [
            finding for finding in normalization_findings
            if set(finding.get("relationshipIds", [])).intersection(validation_relationships)
        ]
        validation_root_ids = selected_root_ids
        validation_node_ids = validation_item_ids | validation_root_ids | {
            endpoint
            for edge in selected_edge_list
            for endpoint in (edge["sourceId"], edge["targetId"])
        }
        selected_nodes = [
            items_by_id[item_id]
            for item_id in sorted(validation_node_ids)
            if item_id in items_by_id
        ]
        launch_validation_node_ids = validation_root_ids | {
            endpoint
            for edge in launch_validation_edge_list
            for endpoint in (edge["sourceId"], edge["targetId"])
        }
        launch_validation_nodes = [
            items_by_id[item_id]
            for item_id in sorted(launch_validation_node_ids)
            if item_id in items_by_id
        ]
        validation_edge_list = [
            validation_relationships[key]
            for key in sorted(validation_relationships)
        ]
        findings = [
            *self._registry_findings(),
            *self._schema_discovery_findings(schema_discovery),
            *scoped_normalization_findings,
            *self._semantic_duplicate_findings(validation_edge_list),
            *self._validate_timeline(selected_nodes, selected_edge_list),
            *self._launch_findings(
                launch_validation_nodes,
                launch_validation_edge_list,
            ),
            *self._dispatch_topology_findings(receipts, selected_edge_list),
        ]
        validation = self._validation_block(findings)
        admission_reason_counts: dict[str, int] = {}
        for exclusion in pre_admission_exclusions:
            for reason in exclusion["reasons"]:
                admission_reason_counts[reason] = admission_reason_counts.get(reason, 0) + 1
        admission_receipt = {
            "sourceDispatchableCount": len(source_items),
            "detailedInspectionCount": len(receipts),
            "preAdmissionExcludedCount": len(pre_admission_exclusions),
            "preAdmissionReasonCounts": dict(sorted(admission_reason_counts.items())),
            "preAdmissionFingerprint": self._stable_fingerprint(pre_admission_exclusions),
            "rule": (
                "Detailed receipts are materialized only after workflow, role, "
                "scope, and archive admission."
            ),
        }
        query_receipt = {
            **query_echo,
            "expandedParameters": {
                "roleId": role_id,
                "launchKeys": launch_keys,
                "includeUnscoped": include_unscoped,
            },
            "resolvedRoots": sorted(selected_root_ids),
            "boundaryRules": {
                "ancestryDepth": 4,
                "membershipRoles": list(policy["membershipRoles"]),
                "contributionRoles": list(policy["contributionRoles"]),
                "retiredRelationshipsExcluded": True,
                "archivedRecordsExcluded": True,
            },
            "evidenceMapping": {
                "fingerprint": evidence_mapping_fingerprint,
                "signals": sorted(evidence_mapping),
            },
            "pagination": {"cursor": None, "truncated": False},
            "failOn": dict(query_echo.get("expanded", {}).get("failOn", {})),
        }
        query_receipt["queryFingerprint"] = self._stable_fingerprint(
            {
                "savedQuery": query_receipt["savedQuery"],
                "expandedParameters": query_receipt["expandedParameters"],
                "resolvedRoots": query_receipt["resolvedRoots"],
                "boundaryRules": query_receipt["boundaryRules"],
                "evidenceMapping": query_receipt["evidenceMapping"],
                "schemaAdapter": SCHEMA_ADAPTER,
                "schemaFingerprint": fingerprint,
                "registryVersion": self._registry["version"],
                "registryHash": self._registry_hash,
                "sourceItemCount": len(item_rows),
                "sourceRelationshipCount": len(link_db_rows),
                "preAdmissionFingerprint": admission_receipt["preAdmissionFingerprint"],
            }
        )
        watermark = self._watermark(
            fingerprint,
            len(item_rows),
            len(link_db_rows),
            started,
            schema_discovery,
        )
        terminal_receipt = {
            "savedQuery": query_receipt["savedQuery"],
            "queryFingerprint": query_receipt["queryFingerprint"],
            "resolvedRoots": query_receipt["resolvedRoots"],
            "page": {"truncated": False},
            "validation": validation,
            "watermark": watermark,
            "candidates": [],
            "admission": admission_receipt,
        }
        if validation["state"] != "pass":
            raise ReaderError(
                "VALIDATION_FAILED",
                "Dispatch eligibility validation did not pass; no candidates or totals are trustworthy.",
                {"receipt": terminal_receipt},
            )
        if incomplete:
            terminal_receipt["incompleteEvidence"] = incomplete
            raise ReaderError(
                "DISPATCH_EVIDENCE_INCOMPLETE",
                "Required dispatch evidence is incomplete; no candidates or totals are trustworthy.",
                {"receipt": terminal_receipt},
            )

        included_receipts = [
            receipt for receipt in receipts if receipt["included"]
        ]
        ordered_ids = self._dispatch_order(
            included_receipts,
            selected_edge_list,
            fields_by_id,
            items_by_id,
        )
        receipt_by_id = {receipt["itemId"]: receipt for receipt in receipts}
        nodes: list[dict[str, Any]] = []
        for item_id in ordered_ids:
            node = dict(items_by_id[item_id])
            node["dispatchReceipt"] = receipt_by_id[item_id]
            nodes.append(node)
        boundary_ids = sorted(
            selected_item_ids - {receipt["itemId"] for receipt in receipts}
        )
        launch_totals: dict[str, int] = {}
        for receipt in included_receipts:
            launch_ids = [
                entry["id"] for entry in receipt["ancestry"]["launches"]
            ]
            key = launch_ids[0] if launch_ids else "unscoped"
            launch_totals[key] = launch_totals.get(key, 0) + 1
        result = {
            "nodes": nodes,
            "edges": selected_edge_list,
            "boundaryNodes": [
                items_by_id[item_id]
                for item_id in boundary_ids
                if item_id in items_by_id
            ],
            "receipts": [
                receipt_by_id[item_id]
                for item_id in ordered_ids
            ] + sorted(
                [
                    receipt for receipt in receipts
                    if not receipt["included"]
                ],
                key=lambda receipt: (
                    str(receipt.get("issueKey") or ""),
                    receipt["itemId"],
                ),
            ),
            "excluded": sorted(
                [
                    {
                        "itemId": receipt["itemId"],
                        "issueKey": receipt.get("issueKey"),
                        "exclusionReasons": receipt["exclusionReasons"],
                    }
                    for receipt in receipts
                    if not receipt["included"]
                ],
                key=lambda receipt: (
                    str(receipt.get("issueKey") or ""),
                    receipt["itemId"],
                ),
            ),
            "launchTotals": dict(sorted(launch_totals.items())),
            "admission": admission_receipt,
            "page": {
                "totalCount": len(nodes),
                "candidateCount": len(nodes),
                "inspectedCount": len(source_items),
                "detailedReceiptCount": len(receipts),
                "preAdmissionExcludedCount": len(pre_admission_exclusions),
                "returnedCount": len(nodes),
                "nextCursor": None,
                "truncated": False,
            },
            "validation": validation,
            "watermark": watermark,
            "query": query_receipt,
        }
        if self._json_size(result) > MAX_RESULT_BYTES:
            terminal_receipt["page"]["truncated"] = True
            raise ReaderError(
                "RESULT_TRUNCATED",
                "Dispatch eligibility response size invalidated the candidate set.",
                {"receipt": terminal_receipt},
            )
        fitted = self._fit_graph_result(result)
        if fitted["page"]["truncated"]:
            terminal_receipt["page"]["truncated"] = True
            raise ReaderError(
                "RESULT_TRUNCATED",
                "Dispatch eligibility response truncation invalidated the candidate set.",
                {"receipt": terminal_receipt},
            )
        return fitted

    def _resolve_dispatch_evidence(
        self,
        item_id: str,
        fields: Mapping[str, Any],
        incident_edges: list[Mapping[str, Any]],
        mapping: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Resolve only allowlisted dispatch evidence sources with provenance."""
        tags = sorted(
            {
                value.strip()[:100]
                for value in fields.get("tags", [])
                if isinstance(value, str) and value.strip()
            },
            key=lambda value: (value.casefold(), value),
        ) if isinstance(fields.get("tags"), list) else []
        resolved: dict[str, dict[str, Any]] = {}
        for signal in sorted(mapping):
            sources = mapping[signal]["sources"]
            result: dict[str, Any] = {"value": None, "source": None}
            for source in sources:
                kind = source["kind"]
                if kind == "field":
                    raw = fields.get(source["field"])
                    value = (
                        raw if isinstance(raw, bool) else None
                    ) if signal == "isCurrentRevision" else self._bounded_string(raw, 200)
                    if value is not None:
                        result = {
                            "value": value,
                            "source": {"kind": "field", "field": source["field"]},
                        }
                        break
                elif kind == "tag":
                    match = next(
                        (tag for tag in tags if tag.casefold() == source["tag"].casefold()),
                        None,
                    )
                    if match is not None:
                        result = {
                            "value": source["value"],
                            "source": {"kind": "tag", "tag": match},
                        }
                        break
                elif kind == "tag-prefix":
                    prefix = source["prefix"]
                    match = next(
                        (
                            tag for tag in tags
                            if tag.casefold().startswith(prefix.casefold())
                            and self._bounded_string(tag[len(prefix):], 200) is not None
                        ),
                        None,
                    )
                    if match is not None:
                        result = {
                            "value": self._bounded_string(match[len(prefix):], 200),
                            "source": {
                                "kind": "tag-prefix",
                                "prefix": prefix,
                                "tag": match,
                            },
                        }
                        break
                elif kind == "relationship":
                    match = next(
                        (
                            edge for edge in incident_edges
                            if edge.get("relationshipType") == source["relationshipType"]
                            and edge.get("state") == source["state"]
                            and (
                                source["direction"] == "either"
                                or (
                                    source["direction"] == "outgoing"
                                    and edge.get("sourceId") == item_id
                                )
                                or (
                                    source["direction"] == "incoming"
                                    and edge.get("targetId") == item_id
                                )
                            )
                        ),
                        None,
                    )
                    if match is not None:
                        result = {
                            "value": source["value"],
                            "source": {
                                "kind": "relationship",
                                "relationshipId": match["id"],
                                "relationshipType": match["relationshipType"],
                                "direction": source["direction"],
                                "state": match["state"],
                            },
                        }
                        break
            resolved[signal] = result
        return resolved

    @staticmethod
    def _dispatch_ancestry_rows(
        nodes: list[Mapping[str, Any]],
        edges: list[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        endpoint_relationships: dict[str, list[str]] = {}
        for edge in edges:
            endpoint_relationships.setdefault(
                str(edge["targetId"]),
                [],
            ).append(str(edge["id"]))
        return [
            {
                "id": node["id"],
                "issueKey": node.get("issueKey"),
                "launchKey": node.get("launchKey"),
                "relationshipIds": sorted(endpoint_relationships.get(str(node["id"]), [])),
            }
            for node in sorted(nodes, key=lambda value: str(value["id"]))
        ]

    def _dispatch_topology_findings(
        self,
        receipts: list[Mapping[str, Any]],
        edges: list[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        candidate_ids = {
            str(receipt["itemId"])
            for receipt in receipts
            if receipt.get("included")
        }
        adjacency: dict[str, set[str]] = {item_id: set() for item_id in candidate_ids}
        indegree = {item_id: 0 for item_id in candidate_ids}
        for edge in edges:
            source = str(edge.get("sourceId"))
            target = str(edge.get("targetId"))
            if source not in candidate_ids or target not in candidate_ids:
                continue
            before, after = (
                (target, source)
                if edge.get("relationshipType") == "depends-on"
                and edge.get("hardness") == "hard-serial"
                and edge.get("state") == "cleared"
                else (source, target)
                if edge.get("relationshipType") == "precedes"
                and edge.get("state") == "active"
                else (None, None)
            )
            if before is not None and after not in adjacency[before]:
                adjacency[before].add(after)
                indegree[after] += 1
        frontier = sorted(item_id for item_id, degree in indegree.items() if degree == 0)
        visited: set[str] = set()
        while frontier:
            current = frontier.pop(0)
            visited.add(current)
            for target in sorted(adjacency[current]):
                indegree[target] -= 1
                if indegree[target] == 0:
                    frontier.append(target)
                    frontier.sort()
        cycle_ids = sorted(candidate_ids - visited)
        if not cycle_ids:
            return []
        return [
            self._finding(
                "dispatch-order-cycle",
                "error",
                "Dispatch dependency and precedes evidence contains a cycle.",
                item_ids=cycle_ids,
            )
        ]

    def _dispatch_order(
        self,
        receipts: list[Mapping[str, Any]],
        edges: list[Mapping[str, Any]],
        fields_by_id: Mapping[str, Mapping[str, Any]],
        items_by_id: Mapping[str, Mapping[str, Any]],
    ) -> list[str]:
        candidate_ids = {str(receipt["itemId"]) for receipt in receipts}
        receipt_by_id = {str(receipt["itemId"]): receipt for receipt in receipts}
        adjacency: dict[str, set[str]] = {item_id: set() for item_id in candidate_ids}
        indegree = {item_id: 0 for item_id in candidate_ids}
        for edge in edges:
            source = str(edge.get("sourceId"))
            target = str(edge.get("targetId"))
            if source not in candidate_ids or target not in candidate_ids:
                continue
            before, after = (
                (target, source)
                if edge.get("relationshipType") == "depends-on"
                and edge.get("hardness") == "hard-serial"
                and edge.get("state") == "cleared"
                else (source, target)
                if edge.get("relationshipType") == "precedes"
                and edge.get("state") == "active"
                else (None, None)
            )
            if before is not None and after not in adjacency[before]:
                adjacency[before].add(after)
                indegree[after] += 1
        priority_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}

        def numeric(value: Any) -> float:
            return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0

        def tie_key(item_id: str) -> tuple[float, float, float, str, str]:
            fields = fields_by_id[item_id]
            launch_priority = max(
                (
                    numeric(fields_by_id.get(entry["id"], {}).get("criticalPathPriority"))
                    for entry in receipt_by_id[item_id]["ancestry"]["launches"]
                ),
                default=0.0,
            )
            critical_priority = numeric(fields.get("criticalPathPriority"))
            native_priority = float(
                priority_rank.get(
                    str(items_by_id[item_id].get("priority") or "").casefold(),
                    0,
                )
            )
            return (
                -launch_priority,
                -critical_priority,
                -native_priority,
                str(items_by_id[item_id].get("issueKey") or ""),
                item_id,
            )

        frontier = sorted(
            [item_id for item_id, degree in indegree.items() if degree == 0],
            key=tie_key,
        )
        ordered: list[str] = []
        while frontier:
            current = frontier.pop(0)
            ordered.append(current)
            for target in sorted(adjacency[current]):
                indegree[target] -= 1
                if indegree[target] == 0:
                    frontier.append(target)
                    frontier.sort(key=tie_key)
        return ordered

    def milestone_report(self, params: Mapping[str, Any]) -> dict[str, Any]:
        parsed = self._validated_report_params(params)
        snapshot = self.timeline_snapshot(
            {
                "workspacePath": parsed["workspacePath"],
                "includeUnscheduled": True,
                "maxItems": parsed["maxItems"],
            }
        )
        milestones = snapshot["milestones"]
        milestone_id = parsed.get("milestoneId")
        if milestone_id:
            milestones = [
                item
                for item in milestones
                if item["id"] == milestone_id or item.get("issueKey") == milestone_id
            ]
            if not milestones:
                raise ReaderError(
                    "MILESTONE_NOT_FOUND",
                    "No matching milestone exists in the current workspace.",
                    {"milestoneId": milestone_id},
                )

        items_by_id = {item["id"]: item for item in snapshot["items"]}
        as_of = datetime.fromtimestamp(parsed["asOfMs"] / 1000, timezone.utc)
        lookahead = as_of + timedelta(days=parsed["lookaheadDays"])
        sections: list[dict[str, Any]] = []
        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        schedule_order = {"on-track": 0, "at-risk": 1, "late": 2}

        for milestone in milestones:
            contribution_edges = [
                edge
                for edge in snapshot["relationships"]
                if edge["relationshipType"] == "contributes-to"
                and edge["targetId"] == milestone["id"]
                and edge["state"] == "active"
                and edge.get("primaryContribution")
            ]
            deliverable_ids = {edge["sourceId"] for edge in contribution_edges}
            deliverables = [items_by_id[item_id] for item_id in deliverable_ids if item_id in items_by_id]
            complete = [item for item in deliverables if self._is_complete(item)]
            overdue = [
                item
                for item in deliverables
                if (item.get("forecastDate") or item.get("dueDate"))
                and self._parse_iso_ms(item.get("forecastDate") or item["dueDate"], "dueDate") < parsed["asOfMs"]
                and not self._is_complete(item)
            ]
            upcoming = [
                item
                for item in deliverables
                if (item.get("forecastDate") or item.get("dueDate"))
                and parsed["asOfMs"] <= self._parse_iso_ms(item.get("forecastDate") or item["dueDate"], "dueDate") <= int(lookahead.timestamp() * 1000)
                and not self._is_complete(item)
            ]
            relevant_ids = {milestone["id"], *deliverable_ids}
            active_dependencies = [
                edge
                for edge in snapshot["relationships"]
                if edge["relationshipType"] == "depends-on"
                and edge["state"] == "active"
                and edge["sourceId"] in relevant_ids
            ]
            relevant_items = [milestone, *deliverables]
            blocked_items = [
                item for item in relevant_items if item.get("executionConstraint") == "blocked"
            ]
            waiting_items = [
                item for item in relevant_items if item.get("executionConstraint") == "waiting"
            ]
            progress = self._derived_milestone_progress(deliverables)
            workflow = str(milestone.get("workflow", "planned"))
            schedule_health = max(
                (str(item.get("scheduleHealth", "on-track")) for item in relevant_items),
                key=lambda value: schedule_order.get(value, 0),
            )
            if overdue:
                schedule_health = "late"
            risk_level = max(
                (str(item.get("riskLevel", "low")) for item in relevant_items),
                key=lambda value: risk_order.get(value, 0),
            )
            health = "achieved" if workflow == "achieved" else schedule_health
            milestone_findings = [
                finding
                for finding in snapshot["validation"]
                if relevant_ids.intersection(finding.get("itemIds", []))
            ]
            sections.append(
                {
                    "milestone": milestone,
                    "health": health,
                    "scheduleHealth": schedule_health,
                    "riskLevel": risk_level,
                    "progress": progress,
                    "deliverableCount": len(deliverables),
                    "completeCount": len(complete),
                    "overdue": overdue,
                    "upcoming": upcoming,
                    "activeDependencies": active_dependencies,
                    "blockedItems": blocked_items,
                    "waitingItems": waiting_items,
                    "validation": milestone_findings,
                }
            )

        markdown = self._render_milestone_markdown(sections, as_of, parsed["lookaheadDays"])
        result = {
            "generatedAt": snapshot["generatedAt"],
            "asOf": as_of.date().isoformat(),
            "lookaheadDays": parsed["lookaheadDays"],
            "milestones": sections,
            "markdown": markdown,
            "source": snapshot["source"],
        }
        if self._json_size(result) > MAX_RESULT_BYTES:
            raise ReaderError("RESPONSE_TOO_LARGE", "The milestone report exceeded the safe response limit.")
        return result

    def _validated_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"trackerId", "workspacePath", "limit", "cursor", "since", "order"}
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise ReaderError(
                "INVALID_PARAMS",
                f"Unknown parameter(s): {', '.join(unknown)}.",
            )

        tracker_id = params.get("trackerId")
        workspace_path = params.get("workspacePath")
        if not isinstance(tracker_id, str) or not tracker_id.strip():
            raise ReaderError("INVALID_PARAMS", "trackerId is required.")
        if not isinstance(workspace_path, str) or not Path(workspace_path).is_absolute():
            raise ReaderError(
                "WORKSPACE_UNAVAILABLE",
                "The native tracker reader requires an open local workspace.",
            )

        limit = params.get("limit", DEFAULT_LIMIT)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
            raise ReaderError("INVALID_PARAMS", "limit must be an integer from 1 through 100.")

        order = params.get("order", "newest")
        if order not in {"newest", "oldest"}:
            raise ReaderError("INVALID_PARAMS", "order must be newest or oldest.")
        cursor = params.get("cursor")
        if cursor is not None and not isinstance(cursor, str):
            raise ReaderError("INVALID_PARAMS", "cursor must be a string.")

        since = params.get("since")
        since_ms = None
        if since is not None:
            if not isinstance(since, str):
                raise ReaderError("INVALID_PARAMS", "since must be an ISO-8601 string.")
            since_ms = self._parse_iso_ms(since, "since")

        return {
            "trackerId": tracker_id.strip(),
            "workspacePath": workspace_path,
            "limit": limit,
            "order": order,
            "cursor": cursor,
            "sinceMs": since_ms,
        }

    def _validated_timeline_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"workspacePath", "includeUnscheduled", "maxItems", "from", "to", "launch", "selector"}
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise ReaderError("INVALID_PARAMS", f"Unknown parameter(s): {', '.join(unknown)}.")
        workspace_path = params.get("workspacePath")
        if not isinstance(workspace_path, str) or not Path(workspace_path).is_absolute():
            raise ReaderError("WORKSPACE_UNAVAILABLE", "The timeline requires an open local workspace.")
        include_unscheduled = params.get("includeUnscheduled", True)
        if not isinstance(include_unscheduled, bool):
            raise ReaderError("INVALID_PARAMS", "includeUnscheduled must be a boolean.")
        max_items = params.get("maxItems", DEFAULT_TIMELINE_ITEMS)
        if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= MAX_TIMELINE_ITEMS:
            raise ReaderError("INVALID_PARAMS", f"maxItems must be an integer from 1 through {MAX_TIMELINE_ITEMS}.")
        from_value = params.get("from")
        to_value = params.get("to")
        from_ms = self._optional_date_ms(from_value, "from")
        to_ms = self._optional_date_ms(to_value, "to")
        if from_ms is not None and to_ms is not None and from_ms > to_ms:
            raise ReaderError("INVALID_PARAMS", "from must be on or before to.")
        launch = params.get("launch")
        if launch is not None and (not isinstance(launch, str) or not launch.strip() or len(launch.strip()) > 100):
            raise ReaderError("INVALID_PARAMS", "launch must be a non-empty key of at most 100 characters.")
        selector = params.get("selector")
        if launch is not None and selector is not None:
            raise ReaderError("INVALID_PARAMS", "launch and selector cannot be combined.")
        normalized_selector: dict[str, Any] | None = None
        if selector is not None:
            if not isinstance(selector, Mapping) or set(selector) != {"launchTags"}:
                raise ReaderError("INVALID_PARAMS", "selector must contain only launchTags.")
            launch_tags = selector.get("launchTags")
            if not isinstance(launch_tags, list) or not 1 <= len(launch_tags) <= MAX_TIMELINE_SELECTOR_TAGS:
                raise ReaderError("INVALID_PARAMS", f"selector.launchTags must contain 1 through {MAX_TIMELINE_SELECTOR_TAGS} tags.")
            normalized_tags: list[str] = []
            for tag in launch_tags:
                if not isinstance(tag, str) or not tag.strip() or len(tag.strip()) > MAX_TIMELINE_SELECTOR_TAG_CHARS:
                    raise ReaderError("INVALID_PARAMS", f"selector.launchTags values must be non-empty strings of at most {MAX_TIMELINE_SELECTOR_TAG_CHARS} characters.")
                normalized_tags.append(tag.strip().casefold())
            if len(set(normalized_tags)) != len(normalized_tags):
                raise ReaderError("INVALID_PARAMS", "selector.launchTags must be unique after normalization.")
            normalized_selector = {"launchTags": sorted(normalized_tags)}
        return {
            "workspacePath": workspace_path,
            "includeUnscheduled": include_unscheduled,
            "maxItems": max_items,
            "fromMs": from_ms,
            "toMs": to_ms,
            "launch": launch.strip() if isinstance(launch, str) else None,
            "selector": normalized_selector,
        }

    def _validated_report_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"workspacePath", "milestoneId", "asOf", "lookaheadDays", "maxItems"}
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise ReaderError("INVALID_PARAMS", f"Unknown parameter(s): {', '.join(unknown)}.")
        workspace_path = params.get("workspacePath")
        if not isinstance(workspace_path, str) or not Path(workspace_path).is_absolute():
            raise ReaderError("WORKSPACE_UNAVAILABLE", "The report requires an open local workspace.")
        milestone_id = params.get("milestoneId")
        if milestone_id is not None and (not isinstance(milestone_id, str) or not milestone_id.strip()):
            raise ReaderError("INVALID_PARAMS", "milestoneId must be a non-empty string.")
        as_of = params.get("asOf")
        as_of_ms = self._optional_date_ms(as_of, "asOf") if as_of is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
        lookahead = params.get("lookaheadDays", DEFAULT_REPORT_LOOKAHEAD_DAYS)
        if isinstance(lookahead, bool) or not isinstance(lookahead, int) or not 1 <= lookahead <= MAX_REPORT_LOOKAHEAD_DAYS:
            raise ReaderError("INVALID_PARAMS", f"lookaheadDays must be an integer from 1 through {MAX_REPORT_LOOKAHEAD_DAYS}.")
        max_items = params.get("maxItems", MAX_TIMELINE_ITEMS)
        if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= MAX_TIMELINE_ITEMS:
            raise ReaderError("INVALID_PARAMS", f"maxItems must be an integer from 1 through {MAX_TIMELINE_ITEMS}.")
        return {
            "workspacePath": workspace_path,
            "milestoneId": milestone_id.strip() if isinstance(milestone_id, str) else None,
            "asOfMs": as_of_ms,
            "lookaheadDays": lookahead,
            "maxItems": max_items,
        }

    def _find_tracker(self, workspace_path: str, tracker_id: str) -> tuple[sqlite3.Row, str]:
        try:
            with self._connect() as connection:
                fingerprint = self._validate_schema(connection)
                rows = connection.execute(
                    """
                    SELECT id, issue_key, type, data, content, archived, type_tags, created, updated
                    FROM tracker_items
                    WHERE workspace = ?
                      AND deleted_at IS NULL
                      AND (issue_key = ? OR id = ?)
                    LIMIT 2
                    """,
                    (workspace_path, tracker_id, tracker_id),
                ).fetchall()
        except ReaderError:
            raise
        except sqlite3.OperationalError as error:
            message = str(error).lower()
            if "locked" in message or "busy" in message:
                raise ReaderError(
                    "DATABASE_BUSY",
                    "The Nimbalyst tracker database is busy. Retry the read shortly.",
                ) from None
            raise ReaderError(
                "DATABASE_READ_FAILED",
                "The Nimbalyst tracker database could not be read safely.",
            ) from None

        if not rows:
            raise ReaderError(
                "TRACKER_NOT_FOUND",
                "No matching tracker item exists in the current workspace.",
                {"trackerId": tracker_id},
            )
        if len(rows) > 1:
            raise ReaderError(
                "TRACKER_AMBIGUOUS",
                "More than one matching tracker item exists in the current workspace.",
                {"trackerId": tracker_id},
            )
        return rows[0], fingerprint

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        database_path = self._database_path or self._discover_database_path()
        if not database_path.is_file():
            raise ReaderError(
                "DATABASE_NOT_FOUND",
                "The Nimbalyst SQLite database was not found.",
            )

        connection: sqlite3.Connection | None = None
        try:
            uri = f"{database_path.resolve().as_uri()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=1.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA busy_timeout = 1000")
            query_only = connection.execute("PRAGMA query_only").fetchone()[0]
            if query_only != 1:
                raise ReaderError(
                    "READ_ONLY_GUARD_FAILED",
                    "SQLite did not accept the required read-only guard.",
                )
            yield connection
        except sqlite3.OperationalError as error:
            message = str(error).lower()
            if "locked" in message or "busy" in message:
                raise ReaderError(
                    "DATABASE_BUSY",
                    "The Nimbalyst tracker database is busy. Retry the read shortly.",
                ) from None
            if "readonly" in message:
                raise ReaderError(
                    "READ_ONLY_GUARD_FAILED",
                    "The read-only database guard rejected an unsupported operation.",
                ) from None
            raise ReaderError(
                "DATABASE_OPEN_FAILED",
                "The Nimbalyst SQLite database could not be opened in read-only mode.",
            ) from None
        finally:
            if connection is not None:
                connection.close()

    def _discover_database_path(self) -> Path:
        override = os.environ.get("NIMBALYST_SQLITE_PATH")
        if override:
            return Path(override)

        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise ReaderError(
                "PLATFORM_UNSUPPORTED",
                "Version 0.4.1 supports Windows installations with APPDATA available.",
            )
        root = Path(appdata) / "@nimbalyst" / "electron"
        config_path = root / "database-backend.json"
        if not config_path.is_file():
            raise ReaderError(
                "BACKEND_CONFIG_NOT_FOUND",
                "Nimbalyst's database backend configuration was not found.",
            )
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise ReaderError(
                "BACKEND_CONFIG_INVALID",
                "Nimbalyst's database backend configuration is invalid.",
            ) from None
        if config.get("backend") != "sqlite":
            raise ReaderError(
                "DATABASE_NOT_SQLITE",
                "Tracker+ requires Nimbalyst's SQLite backend.",
            )
        return root / "sqlite-db" / "nimbalyst.sqlite"

    def _validate_schema(self, connection: sqlite3.Connection) -> str:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'tracker_items'"
        ).fetchone()
        if table is None:
            raise ReaderError(
                "SCHEMA_INCOMPATIBLE",
                "The installed Nimbalyst tracker schema is not supported by this add-on.",
                {"adapter": SCHEMA_ADAPTER, "missingTable": "tracker_items"},
            )

        columns = connection.execute("PRAGMA table_info(tracker_items)").fetchall()
        names = {str(row[1]) for row in columns}
        missing = [name for name in REQUIRED_COLUMNS if name not in names]
        if missing:
            raise ReaderError(
                "SCHEMA_INCOMPATIBLE",
                "The installed Nimbalyst tracker schema is not supported by this add-on.",
                {"adapter": SCHEMA_ADAPTER, "missingColumns": missing},
            )
        fingerprint_input = "|".join(f"{row[1]}:{row[2]}" for row in columns)
        return hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()

    def _parse_data(self, row: sqlite3.Row) -> dict[str, Any]:
        raw = row["data"]
        if not isinstance(raw, str):
            raise ReaderError(
                "DATA_JSON_INVALID",
                "The tracker item's data field is not valid JSON.",
                {"trackerId": row["issue_key"] or row["id"]},
            )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raise ReaderError(
                "DATA_JSON_INVALID",
                "The tracker item's data field is not valid JSON.",
                {"trackerId": row["issue_key"] or row["id"]},
            ) from None
        if not isinstance(parsed, dict):
            raise ReaderError(
                "DATA_JSON_INVALID",
                "The tracker item's data field is not a JSON object.",
                {"trackerId": row["issue_key"] or row["id"]},
            )
        return parsed

    @staticmethod
    def _flatten_custom_fields(data: Mapping[str, Any]) -> dict[str, Any]:
        fields = dict(data)
        custom = data.get("customFields")
        if isinstance(custom, dict):
            for key, value in custom.items():
                fields.setdefault(key, value)
        return fields

    @classmethod
    def _pull_request_fields(
        cls,
        fields: Mapping[str, Any],
        type_tags: list[str],
    ) -> tuple[int | None, str | None]:
        nested = fields.get("pullRequest")
        nested_fields = nested if isinstance(nested, Mapping) else {}
        origin = fields.get("origin")
        origin_fields = origin if isinstance(origin, Mapping) else {}
        external = origin_fields.get("external")
        external_fields = external if isinstance(external, Mapping) else {}
        is_pull_request = bool(
            {str(value).lower() for value in type_tags}.intersection(
                {"mr", "merge-request", "pull-request", "change-request"}
            )
        )
        url = next(
            (
                cls._bounded_string(value, 2048)
                for value in (
                    fields.get("pullRequestUrl"),
                    fields.get("prUrl"),
                    fields.get("githubPullRequestUrl"),
                    nested_fields.get("url"),
                    external_fields.get("url") if is_pull_request else None,
                )
                if cls._bounded_string(value, 2048)
            ),
            None,
        )
        number_raw = next(
            (
                value
                for value in (
                    fields.get("pullRequestNumber"),
                    fields.get("prNumber"),
                    fields.get("githubPullRequestNumber"),
                    nested_fields.get("number"),
                )
                if value is not None
            ),
            None,
        )
        number = cls._bounded_integer(number_raw, 1, 999_999_999)
        if number is None and isinstance(number_raw, str):
            match = re.fullmatch(r"#?(\d+)", number_raw.strip())
            number = int(match.group(1)) if match else None
        if number is None and url:
            match = re.search(r"/pull/(\d+)(?:[/?#]|$)", url, re.IGNORECASE)
            number = int(match.group(1)) if match else None
        if is_pull_request and (number is None or url is None):
            urn = cls._bounded_string(external_fields.get("urn"), 1024)
            match = re.fullmatch(r"github://([^/]+)/([^#]+)#(\d+)", urn or "")
            if match:
                number = number or int(match.group(3))
                url = url or f"https://github.com/{match.group(1)}/{match.group(2)}/pull/{match.group(3)}"
        return number, url

    def _is_complete(self, item: Mapping[str, Any]) -> bool:
        return str(item.get("workflow", "")).lower() in {
            str(value).lower() for value in self._registry["terminalStatuses"]
        }

    def _effective_deliverable_progress(self, item: Mapping[str, Any]) -> float:
        if self._is_complete(item):
            return 100.0
        progress = item.get("progress")
        if isinstance(progress, bool) or not isinstance(progress, (int, float)):
            return 0.0
        return max(0.0, min(100.0, float(progress)))

    def _derived_milestone_progress(self, deliverables: list[Mapping[str, Any]]) -> int:
        if not deliverables:
            return 0
        average = sum(self._effective_deliverable_progress(item) for item in deliverables) / len(deliverables)
        return int(average + 0.5)

    def _apply_derived_milestone_progress(
        self,
        items: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> None:
        items_by_id = {item["id"]: item for item in items}
        milestone_ids = {
            item["id"] for item in items if item.get("primaryType") == "milestone"
        }
        deliverables_by_milestone: dict[str, dict[str, dict[str, Any]]] = {
            milestone_id: {} for milestone_id in milestone_ids
        }
        for edge in edges:
            if (
                edge.get("relationshipType") != "contributes-to"
                or edge.get("state") != "active"
                or not edge.get("primaryContribution")
                or edge.get("targetId") not in milestone_ids
            ):
                continue
            source = items_by_id.get(str(edge.get("sourceId")))
            if source is not None:
                deliverables_by_milestone[str(edge["targetId"])][source["id"]] = source
        for milestone_id in milestone_ids:
            items_by_id[milestone_id]["progress"] = self._derived_milestone_progress(
                list(deliverables_by_milestone[milestone_id].values())
            )

    def _timeline_item(self, row: sqlite3.Row, fields: Mapping[str, Any]) -> dict[str, Any]:
        title = self._bounded_string(fields.get("title"), 500) or "Untitled tracker item"
        primary_type = str(row["type"])
        type_tags = self._parse_type_tags(row["type_tags"])
        if primary_type not in type_tags:
            type_tags.insert(0, primary_type)
        due_date = self._date_string(
            fields.get("targetDate") if primary_type == "milestone" else fields.get("dueDate")
        )
        if due_date is None:
            due_date = self._date_string(fields.get("deadline") or fields.get("targetDate"))
        progress_raw = fields.get("progress")
        progress = None
        if not isinstance(progress_raw, bool) and isinstance(progress_raw, (int, float)):
            progress = max(0, min(100, round(float(progress_raw), 2)))
        owner = fields.get("owner")
        owner_label = self._identity_label(owner)
        workflow = self._bounded_string(fields.get("status"), 100)
        schedule_health = self._bounded_string(fields.get("scheduleHealth"), 40)
        execution_constraint = self._bounded_string(fields.get("executionConstraint"), 40)
        if workflow == "blocked":
            workflow = "in-progress"
            execution_constraint = execution_constraint or "blocked"
        elif workflow == "at-risk":
            workflow = "in-progress"
            schedule_health = schedule_health or "at-risk"
        impact = self._bounded_integer(fields.get("impact"), 1, 5)
        likelihood = self._bounded_integer(fields.get("likelihood"), 1, 5)
        start_date = self._date_string(fields.get("startDate"))
        duration_days = 1
        if start_date and due_date:
            start_ms = self._parse_iso_ms(start_date, "startDate")
            due_ms = self._parse_iso_ms(due_date, "dueDate")
            duration_days = max(1, round((due_ms - start_ms) / 86_400_000) + 1)
        launch_scope = self._bounded_string(fields.get("launchScope"), 40)
        pull_request_number, pull_request_url = self._pull_request_fields(fields, type_tags)
        stored_walk_stage = self._bounded_string(fields.get("walkStage"), 40)
        walk_stage = (
            stored_walk_stage
            if stored_walk_stage in {"local-verifiable", "production-only", "mixed"}
            else "unknown"
        )
        stored_build_state = self._bounded_string(fields.get("buildState"), 40)
        build_state = (
            stored_build_state
            if stored_build_state in {"build-complete", "in-build", "not-started"}
            else "unknown"
        )
        stored_readiness = self._bounded_string(fields.get("readiness"), 40)
        readiness = (
            stored_readiness
            if stored_readiness in {"walk-ready", "blocked", "not-ready"}
            else "unknown"
        )
        runtime_available = fields.get("requiredRuntimeAvailable")
        if not isinstance(runtime_available, bool):
            runtime_available = None
        acceptance_content_present = bool(
            self._bounded_string(fields.get("gate"), 200)
            or (
                isinstance(fields.get("exitCriteria"), list)
                and fields.get("exitCriteria")
            )
            or (
                isinstance(fields.get("acceptanceCriteria"), list)
                and fields.get("acceptanceCriteria")
            )
            or self._bounded_string(fields.get("acceptanceCriteria"), 2_000)
        )
        return {
            "id": str(row["id"]),
            "issueKey": row["issue_key"] if isinstance(row["issue_key"], str) else None,
            "primaryType": primary_type,
            "typeTags": type_tags,
            "title": title,
            "workflow": workflow,
            "status": workflow,
            "priority": self._bounded_string(fields.get("priority"), 100),
            "ownerLabel": owner_label,
            "startDate": start_date,
            "dueDate": due_date,
            "forecastDate": self._date_string(fields.get("forecastDate")),
            "progress": progress,
            "_storedProgress": progress_raw is not None,
            "scheduleHealth": schedule_health if schedule_health in {"on-track", "at-risk", "late"} else "on-track",
            "scheduleHealthReasons": [],
            "executionConstraint": execution_constraint if execution_constraint in {"clear", "waiting", "blocked", "paused"} else "clear",
            "impact": impact,
            "likelihood": likelihood,
            "riskScore": impact * likelihood if impact is not None and likelihood is not None else None,
            "riskLevel": "low",
            "riskReasons": [],
            "riskDurability": self._bounded_string(fields.get("riskDurability"), 40),
            "recoverability": self._bounded_string(fields.get("recoverability"), 40),
            "evidenceConfidence": self._bounded_string(fields.get("evidenceConfidence"), 40),
            "technicalUncertainty": self._bounded_string(fields.get("technicalUncertainty"), 40),
            "capacityPressure": self._bounded_string(fields.get("capacityPressure"), 40),
            "gate": self._bounded_string(fields.get("gate"), 200),
            "walkStage": walk_stage,
            "buildState": build_state,
            "readiness": readiness,
            "requiredRuntimeAvailable": runtime_available,
            "walkReadinessProvenance": {
                "walkStage": {
                    "sourceField": "walkStage",
                    "storedValue": stored_walk_stage,
                    "derived": False,
                },
                "buildState": {
                    "sourceField": "buildState",
                    "storedValue": stored_build_state,
                    "derived": False,
                },
                "readiness": {
                    "sourceField": "readiness",
                    "storedValue": stored_readiness,
                    "derived": False,
                },
                "requiredRuntime": {
                    "sourceField": "requiredRuntimeAvailable",
                    "storedValue": runtime_available,
                    "derived": False,
                },
                "acceptanceContentPresent": acceptance_content_present,
            },
            "launchKey": self._bounded_string(fields.get("launchKey"), 100),
            "tags": [self._bounded_string(value, 100) for value in fields.get("tags", []) if self._bounded_string(value, 100)] if isinstance(fields.get("tags"), list) else [],
            "actualDate": self._date_string(fields.get("actualDate")),
            "_launchOwnerPresent": owner_label is not None,
            "_launchAudienceCount": len(fields.get("audience", [])) if isinstance(fields.get("audience"), list) else 0,
            "_launchScopeRevisionPresent": self._bounded_string(fields.get("scopeRevision"), 200) is not None,
            "_launchEntryCriteriaCount": len(fields.get("entryCriteria", [])) if isinstance(fields.get("entryCriteria"), list) else 0,
            "_launchExitCriteriaCount": len(fields.get("exitCriteria", [])) if isinstance(fields.get("exitCriteria"), list) else 0,
            "launchScoped": launch_scope == "launch",
            "_launchScopeExplicit": launch_scope == "launch",
            "primaryMilestoneId": None,
            "scheduleSlackDays": None,
            "criticalPathSlackDays": None,
            "durationDays": duration_days,
            "isCritical": False,
            "pullRequestNumber": pull_request_number,
            "pullRequestUrl": pull_request_url,
            "updated": self._date_time_string(row["updated"]),
        }

    def _normalized_relationships(
        self,
        items: list[dict[str, Any]],
        raw_fields_by_id: Mapping[str, Mapping[str, Any]],
        link_rows: list[tuple[sqlite3.Row, dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        items_by_id = {item["id"]: item for item in items}
        edges: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        for row, fields in link_rows:
            if bool(row["archived"]):
                continue
            sources = self._relationship_targets(fields.get("sourceItem"))
            targets = self._relationship_targets(fields.get("targetItem"))
            link_id = str(row["id"])
            if len(sources) != 1 or len(targets) != 1:
                findings.append(
                    self._finding(
                        "invalid-link-endpoints",
                        "error",
                        f"Relationship {row['issue_key'] or link_id} must have exactly one source and one target.",
                        relationship_ids=[link_id],
                    )
                )
                continue
            source = sources[0]
            target = targets[0]
            relationship_type = self._bounded_string(fields.get("relationshipType"), 60)
            if relationship_type not in self._registry["relationshipTypes"]:
                findings.append(
                    self._finding(
                        "invalid-relationship-type",
                        "error",
                        f"Relationship {row['issue_key'] or link_id} has no supported relationship type.",
                        relationship_ids=[link_id],
                    )
                )
                continue
            source_id = source["itemId"]
            target_id = target["itemId"]
            status = self._bounded_string(fields.get("status"), 40)
            allowed_states = {"active", "cleared", "blocked", "retired", "superseded"}
            normalized_state = status if status in allowed_states else "unknown"
            if normalized_state == "unknown":
                findings.append(
                    self._finding(
                        "relationship-state-unknown",
                        "error",
                        f"Relationship {row['issue_key'] or link_id} has an unknown lifecycle state.",
                        item_ids=[source_id, target_id],
                        relationship_ids=[link_id],
                    )
                )
            directedness = self._bounded_string(fields.get("directedness"), 40)
            entry_evidence = self._relationship_targets(fields.get("entryEvidence"))
            exit_evidence = self._relationship_targets(fields.get("exitEvidence"))
            evidence_sources = self._relationship_targets(fields.get("evidenceSources"))
            edge = {
                "id": link_id,
                "issueKey": row["issue_key"] if isinstance(row["issue_key"], str) else None,
                "sourceId": source_id,
                "sourceIssueKey": source.get("issueKey"),
                "sourceTitle": self._bounded_string(source.get("title"), 300),
                "targetId": target_id,
                "targetIssueKey": target.get("issueKey"),
                "targetTitle": self._bounded_string(target.get("title"), 300),
                "targetType": items_by_id[target_id]["primaryType"]
                if target_id in items_by_id
                else self._bounded_string(target.get("trackerType"), 100),
                "relationshipType": relationship_type,
                "directed": directedness != "symmetric" and relationship_type != "related",
                "state": normalized_state,
                "storedState": status,
                "dependencyMode": self._bounded_string(fields.get("dependencyMode"), 40)
                if relationship_type == "depends-on" else None,
                "hardness": self._bounded_string(fields.get("hardness"), 40)
                if relationship_type == "depends-on" else None,
                "leadLagDays": self._bounded_number(fields.get("leadLagDays"), -365, 365) or 0,
                "clearingCondition": self._bounded_string(fields.get("clearingCondition"), 2_000),
                "ownerLabel": self._identity_label(fields.get("owner")),
                "primaryContribution": fields.get("contributionRole") == "primary",
                "contributionRole": self._bounded_string(fields.get("contributionRole"), 60),
                "scopeRole": self._bounded_string(fields.get("scopeRole"), 60),
                "entryEvidenceIds": [entry["itemId"] for entry in entry_evidence],
                "exitEvidenceIds": [entry["itemId"] for entry in exit_evidence],
                "evidenceSourceIds": [entry["itemId"] for entry in evidence_sources],
                "effectiveRevision": self._bounded_string(fields.get("effectiveRevision"), 200),
                "created": self._date_time_string(row["created"]),
                "updated": self._date_time_string(row["updated"]),
                "targetInSnapshot": target_id in items_by_id,
                "legacy": False,
            }
            if relationship_type == "part-of-launch":
                if edge["scopeRole"] not in self._registry["scopeRoles"]:
                    findings.append(self._finding(
                        "scope-role-invalid", "error",
                        f"Launch membership {row['issue_key'] or link_id} requires a registered scopeRole.",
                        item_ids=[source_id, target_id], relationship_ids=[link_id],
                    ))
                if edge["contributionRole"] is not None or self._bounded_string(fields.get("hardness"), 40) is not None:
                    findings.append(self._finding(
                        "scope-role-conflict", "error",
                        f"Launch membership {row['issue_key'] or link_id} cannot carry contributionRole or hardness.",
                        item_ids=[source_id, target_id], relationship_ids=[link_id],
                    ))
            if source_id in items_by_id:
                edge["sourceTitle"] = items_by_id[source_id]["title"]
                edge["sourceIssueKey"] = items_by_id[source_id].get("issueKey")
            if target_id in items_by_id:
                edge["targetTitle"] = items_by_id[target_id]["title"]
                edge["targetIssueKey"] = items_by_id[target_id].get("issueKey")
                edge["targetType"] = items_by_id[target_id]["primaryType"]
            edges.append(edge)

        explicit_keys = {
            (
                str(edge.get("sourceId") or ""),
                str(edge.get("relationshipType") or ""),
                str(edge.get("targetId") or ""),
                str(edge.get("scopeRole") or ""),
                str(edge.get("contributionRole") or ""),
            )
            for edge in edges
        }

        legacy_specs = {
            "blockers": ("depends-on", False, "hard-serial"),
            "waitingOn": ("depends-on", False, "soft-coordination"),
            "related": ("related", False, None),
            "sourceItems": ("evidences", True, None),
            "milestone": ("contributes-to", False, None),
            "deliverables": ("contributes-to", True, None),
        }
        seen_symmetric: set[tuple[str, str, str]] = set()
        for item in items:
            fields = raw_fields_by_id[item["id"]]
            for field, (relationship_type, reverse, hardness) in legacy_specs.items():
                for target in self._relationship_targets(fields.get(field)):
                    source_id, target_id = (
                        (target["itemId"], item["id"])
                        if reverse
                        else (item["id"], target["itemId"])
                    )
                    contribution_role = "primary" if field == "milestone" else None
                    key = (
                        source_id,
                        relationship_type,
                        target_id,
                        "",
                        contribution_role or "",
                    )
                    if key in explicit_keys:
                        continue
                    if relationship_type == "related":
                        symmetric_key = (relationship_type, *sorted((source_id, target_id)))
                        if symmetric_key in seen_symmetric:
                            continue
                        seen_symmetric.add(symmetric_key)
                    target_item = items_by_id.get(target_id)
                    source_item = items_by_id.get(source_id)
                    stable_seed = f"{source_id}|{relationship_type}|{target_id}"
                    edges.append(
                        {
                            "id": f"legacy-{hashlib.sha256(stable_seed.encode('utf-8')).hexdigest()[:20]}",
                            "issueKey": None,
                            "sourceId": source_id,
                            "sourceIssueKey": source_item.get("issueKey") if source_item else None,
                            "sourceTitle": source_item.get("title") if source_item else None,
                            "targetId": target_id,
                            "targetIssueKey": target_item.get("issueKey") if target_item else target.get("issueKey"),
                            "targetTitle": target_item.get("title") if target_item else self._bounded_string(target.get("title"), 300),
                            "targetType": target_item.get("primaryType") if target_item else self._bounded_string(target.get("trackerType"), 100),
                            "relationshipType": relationship_type,
                            "directed": relationship_type != "related",
                            "state": "active",
                            "dependencyMode": "finish-to-start" if relationship_type == "depends-on" else None,
                            "hardness": hardness,
                            "leadLagDays": 0,
                            "clearingCondition": None,
                            "ownerLabel": None,
                            "primaryContribution": field == "milestone",
                            "contributionRole": contribution_role,
                            "scopeRole": None,
                            "entryEvidenceIds": [],
                            "exitEvidenceIds": [],
                            "evidenceSourceIds": [],
                            "effectiveRevision": None,
                            "created": None,
                            "updated": item.get("updated"),
                            "targetInSnapshot": target_id in items_by_id,
                            "legacy": True,
                        }
                    )
        return edges, findings

    def _apply_timeline_analysis(
        self,
        items: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        items_by_id = {item["id"]: item for item in items}
        hard_edges = [
            edge
            for edge in edges
            if edge["relationshipType"] == "depends-on"
            and edge["state"] == "active"
            and edge.get("hardness") == "hard-serial"
            and edge["sourceId"] in items_by_id
            and edge["targetId"] in items_by_id
        ]
        successors: dict[str, list[tuple[str, float, str]]] = {item_id: [] for item_id in items_by_id}
        predecessors: dict[str, list[tuple[str, float, str]]] = {item_id: [] for item_id in items_by_id}
        indegree = {item_id: 0 for item_id in items_by_id}
        connected: set[str] = set()
        for edge in hard_edges:
            predecessor_id = edge["targetId"]
            successor_id = edge["sourceId"]
            predecessor = items_by_id[predecessor_id]
            successor = items_by_id[successor_id]
            lag = float(edge.get("leadLagDays") or 0)
            mode = edge.get("dependencyMode") or "finish-to-start"
            if mode == "start-to-start":
                offset = lag
            elif mode == "finish-to-finish":
                offset = predecessor["durationDays"] - successor["durationDays"] + lag
            elif mode == "start-to-finish":
                offset = -successor["durationDays"] + lag
            else:
                offset = predecessor["durationDays"] + lag
            successors[predecessor_id].append((successor_id, offset, edge["id"]))
            predecessors[successor_id].append((predecessor_id, offset, edge["id"]))
            indegree[successor_id] += 1
            connected.update((predecessor_id, successor_id))

        queue = sorted(item_id for item_id, degree in indegree.items() if degree == 0)
        topological: list[str] = []
        while queue:
            item_id = queue.pop(0)
            topological.append(item_id)
            for successor_id, _offset, _edge_id in successors[item_id]:
                indegree[successor_id] -= 1
                if indegree[successor_id] == 0:
                    queue.append(successor_id)
                    queue.sort()
        cycle_item_ids = sorted(item_id for item_id, degree in indegree.items() if degree > 0)
        findings: list[dict[str, Any]] = []
        critical_ids: list[str] = []
        project_duration = 0.0
        if cycle_item_ids:
            findings.append(
                self._finding(
                    "hard-dependency-cycle",
                    "error",
                    "Hard-serial dependencies contain a cycle; critical-path calculations are suspended for the affected graph.",
                    item_ids=cycle_item_ids,
                    relationship_ids=[
                        edge["id"] for edge in hard_edges
                        if edge["sourceId"] in cycle_item_ids and edge["targetId"] in cycle_item_ids
                    ],
                )
            )
        elif connected:
            earliest = {item_id: 0.0 for item_id in items_by_id}
            for item_id in topological:
                for successor_id, offset, _edge_id in successors[item_id]:
                    earliest[successor_id] = max(earliest[successor_id], earliest[item_id] + offset)
            project_duration = max(
                earliest[item_id] + items_by_id[item_id]["durationDays"]
                for item_id in connected
            )
            latest = {
                item_id: project_duration - items_by_id[item_id]["durationDays"]
                for item_id in items_by_id
            }
            for item_id in reversed(topological):
                for successor_id, offset, _edge_id in successors[item_id]:
                    latest[item_id] = min(latest[item_id], latest[successor_id] - offset)
            for item_id in connected:
                slack = round(latest[item_id] - earliest[item_id], 2)
                item = items_by_id[item_id]
                item["criticalPathSlackDays"] = slack
                item["isCritical"] = slack <= 0
                if item["isCritical"]:
                    critical_ids.append(item_id)

        milestones = {
            item["id"] for item in items if item["primaryType"] == "milestone"
        }
        primary_milestones: dict[str, list[str]] = {}
        for edge in edges:
            if (
                edge["relationshipType"] == "contributes-to"
                and edge["state"] == "active"
                and edge.get("primaryContribution")
                and edge["targetId"] in milestones
            ):
                primary_milestones.setdefault(edge["sourceId"], []).append(edge["targetId"])

        adjacency: dict[str, set[str]] = {item_id: set() for item_id in items_by_id}
        for edge in edges:
            if (
                edge["state"] == "active"
                and edge["sourceId"] in adjacency
                and edge["targetId"] in adjacency
            ):
                adjacency[edge["sourceId"]].add(edge["targetId"])
                adjacency[edge["targetId"]].add(edge["sourceId"])
        active_memberships = [
            edge for edge in edges
            if edge["relationshipType"] == "part-of-launch" and edge["state"] == "active"
        ]
        if active_memberships:
            launch_connected = {edge["sourceId"] for edge in active_memberships}
        else:
            launch_connected = set(milestones)
            launch_queue = sorted(milestones)
            while launch_queue:
                item_id = launch_queue.pop(0)
                for related_id in sorted(adjacency[item_id]):
                    if related_id not in launch_connected:
                        launch_connected.add(related_id)
                        launch_queue.append(related_id)

        today = datetime.now(timezone.utc).date()
        for item in items:
            primary_ids = sorted(set(primary_milestones.get(item["id"], [])))
            if len(primary_ids) == 1:
                item["primaryMilestoneId"] = primary_ids[0]
            item["launchScoped"] = item["launchScoped"] or item["id"] in launch_connected
            endpoint = item.get("forecastDate") or item.get("dueDate")
            if len(primary_ids) == 1 and endpoint and primary_ids[0] in items_by_id:
                milestone_target = items_by_id[primary_ids[0]].get("dueDate")
                if milestone_target:
                    item["scheduleSlackDays"] = self._days_between(endpoint, milestone_target)
            elif item.get("forecastDate") and item.get("dueDate"):
                item["scheduleSlackDays"] = self._days_between(item["forecastDate"], item["dueDate"])
            elif item.get("criticalPathSlackDays") is not None:
                item["scheduleSlackDays"] = item["criticalPathSlackDays"]
            self._derive_item_health_and_risk(item, today)

        return {
            "durationDays": round(project_duration, 2),
            "itemIds": sorted(critical_ids),
            "cycleItemIds": cycle_item_ids,
        }, findings

    def _launch_findings(
        self,
        items: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        launches = [item for item in items if item.get("primaryType") == "launch"]
        launch_keys: dict[str, list[str]] = {}
        active_memberships = [edge for edge in edges if edge.get("relationshipType") == "part-of-launch" and edge.get("state") == "active"]
        core_targets = {edge["targetId"] for edge in active_memberships if edge.get("scopeRole") == "core"}
        for launch in launches:
            key = str(launch.get("launchKey") or "").strip()
            if not key:
                findings.append(self._finding("launch-key-missing", "error", f"Launch {launch.get('issueKey') or launch['id']} has no launchKey.", item_ids=[launch["id"]]))
            else:
                launch_keys.setdefault(key.lower(), []).append(launch["id"])
            workflow = str(launch.get("workflow") or "draft").lower()
            if workflow != "draft" and (
                not launch.get("_launchOwnerPresent")
                or not launch.get("_launchAudienceCount")
                or not launch.get("_launchScopeRevisionPresent")
                or not launch.get("_launchEntryCriteriaCount")
                or not launch.get("_launchExitCriteriaCount")
                or launch["id"] not in core_targets
            ):
                findings.append(self._finding("launch-fields-incomplete", "error", f"Launch {launch.get('issueKey') or launch['id']} is past draft with incomplete lifecycle fields or no active core member.", item_ids=[launch["id"]]))
            if launch.get("actualDate") and workflow not in {"released", "cancelled"}:
                findings.append(self._finding("launch-actual-date-unreleased", "error", f"Launch {launch.get('issueKey') or launch['id']} has actualDate before release or cancellation.", item_ids=[launch["id"]]))
            if launch.get("_storedProgress"):
                findings.append(self._finding("launch-progress-hand-set", "warning", f"Launch {launch.get('issueKey') or launch['id']} stores progress; derived launch rollup remains authoritative.", item_ids=[launch["id"]]))
        for key, ids in launch_keys.items():
            if len(ids) > 1:
                findings.append(self._finding("launch-key-duplicate", "error", f"Multiple launches share launchKey {key}.", item_ids=sorted(ids)))

        adjacency: dict[str, list[str]] = {}
        for edge in active_memberships:
            adjacency.setdefault(edge["sourceId"], []).append(edge["targetId"])
        visiting: set[str] = set()
        visited: set[str] = set()
        cycle_nodes: set[str] = set()
        def visit(node: str) -> None:
            if node in visiting:
                cycle_nodes.add(node)
                return
            if node in visited:
                return
            visiting.add(node)
            for target in sorted(adjacency.get(node, [])):
                if target in visiting:
                    cycle_nodes.update((node, target))
                visit(target)
            visiting.remove(node)
            visited.add(node)
        for node in sorted(adjacency):
            visit(node)
        if cycle_nodes:
            findings.append(self._finding("membership-cycle", "error", "Active launch memberships contain a cycle.", item_ids=sorted(cycle_nodes), relationship_ids=[edge["id"] for edge in active_memberships if edge["sourceId"] in cycle_nodes and edge["targetId"] in cycle_nodes]))

        # Launch-key tags are legacy selection and migration metadata. They do
        # not create membership and therefore cannot contradict the native
        # graph or affect validation. Active typed part-of-launch edges remain
        # the sole membership and rollup authority.
        return findings

    def _apply_launch_rollups(self, items: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
        by_id = {item["id"]: item for item in items}
        memberships = [edge for edge in edges if edge.get("relationshipType") == "part-of-launch" and edge.get("state") == "active"]
        computing: set[str] = set()
        def rollup(launch_id: str) -> dict[str, Any]:
            launch = by_id[launch_id]
            existing = launch.get("launchRollup")
            if isinstance(existing, dict):
                return existing
            if launch_id in computing:
                return {"derivedProgress": 0}
            computing.add(launch_id)
            scoped = [(edge, by_id.get(edge["sourceId"])) for edge in memberships if edge["targetId"] == launch_id]
            core = [item for edge, item in scoped if item and edge.get("scopeRole") == "core"]
            supporting = [item for edge, item in scoped if item and edge.get("scopeRole") == "supporting"]
            reviews = [item for edge, item in scoped if item and edge.get("scopeRole") == "review"]
            progress_values: list[float] = []
            for item in core:
                if item.get("primaryType") == "launch":
                    progress_values.append(float(rollup(item["id"]).get("derivedProgress", 0)))
                else:
                    progress_values.append(self._effective_deliverable_progress(item))
            scoped_ids = {launch_id, *[edge["sourceId"] for edge, _ in scoped if edge.get("scopeRole") in {"core", "acceptance"}]}
            blockers = [edge for edge in edges if edge.get("relationshipType") == "depends-on" and edge.get("state") == "active" and edge.get("hardness") == "hard-serial" and edge.get("sourceId") in scoped_ids and edge.get("targetId") in by_id and not self._is_complete(by_id[edge["targetId"]])]
            result = {
                "coreMilestonesCompleted": sum(item.get("primaryType") == "milestone" and self._is_complete(item) for item in core),
                "coreMilestonesTotal": sum(item.get("primaryType") == "milestone" for item in core),
                "supportingItemsCompleted": sum(self._is_complete(item) for item in supporting),
                "supportingItemsTotal": len(supporting),
                "reviewsCleared": sum(self._is_complete(item) for item in reviews),
                "reviewsTotal": len(reviews),
                "derivedProgress": int(sum(progress_values) / len(progress_values) + 0.5) if progress_values else 0,
                "activeHardBlockers": len(blockers),
            }
            launch["launchRollup"] = result
            launch["progress"] = result["derivedProgress"]
            computing.remove(launch_id)
            return result
        for launch_id in sorted(item["id"] for item in items if item.get("primaryType") == "launch"):
            rollup(launch_id)

    def _derive_item_health_and_risk(self, item: dict[str, Any], today: Any) -> None:
        done = self._is_complete(item)
        health = item.get("scheduleHealth") if item.get("scheduleHealth") in {"on-track", "at-risk", "late"} else "on-track"
        health_reasons: list[str] = []
        if health != "on-track":
            health_reasons.append(f"Stored schedule assessment is {health}.")
        due = self._date_value(item.get("dueDate"))
        forecast = self._date_value(item.get("forecastDate"))
        if not done and due and today > due:
            health = "late"
            health_reasons.append("The target date has passed and workflow is not complete.")
        elif forecast and due and forecast > due:
            health = "late" if today > due else "at-risk"
            health_reasons.append("The forecast date is later than the target date.")
        slack = item.get("scheduleSlackDays")
        if not done and isinstance(slack, (int, float)) and slack <= 0 and health == "on-track":
            health = "at-risk"
            health_reasons.append("Derived schedule slack is zero or negative.")
        if item.get("technicalUncertainty") in {"high", "critical"}:
            health = "at-risk" if health == "on-track" else health
            health_reasons.append("Technical uncertainty is high.")
        if item.get("capacityPressure") in {"high", "critical"}:
            health = "at-risk" if health == "on-track" else health
            health_reasons.append("Capacity pressure is high.")
        if item.get("evidenceConfidence") == "low":
            health = "at-risk" if health == "on-track" else health
            health_reasons.append("Evidence confidence is low.")
        item["scheduleHealth"] = health
        item["scheduleHealthReasons"] = health_reasons

        score = item.get("riskScore")
        if isinstance(score, (int, float)):
            level = "critical" if score >= 17 else "high" if score >= 10 else "medium" if score >= 5 else "low"
            risk_reasons = [f"Likelihood × impact = {score}."]
        else:
            level = "low"
            risk_reasons = ["Likelihood or impact is not set; base risk defaults to low."]
        rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        if item.get("isCritical") and (item.get("criticalPathSlackDays") or 0) <= 0 and rank[level] < rank["high"]:
            level = "high"
            risk_reasons.append("Critical-path work with zero or negative slack has a high-risk floor.")
        if item.get("riskDurability") in {"recurring", "structural"} and item.get("recoverability") == "hard" and rank[level] < rank["high"]:
            level = "high"
            risk_reasons.append("Recurring or structural risk with hard recovery has a high-risk floor.")
        if item.get("executionConstraint") == "blocked" and (item.get("dueDate") or item.get("primaryMilestoneId")):
            level = "critical"
            risk_reasons.append("An active execution blocker exists inside the target window.")
        item["riskLevel"] = level
        item["riskReasons"] = risk_reasons

    def _validate_timeline(
        self,
        items: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        milestones = {item["id"] for item in items if item["primaryType"] == "milestone"}
        active_edges = [edge for edge in edges if edge["state"] == "active"]
        incident: dict[str, int] = {item["id"]: 0 for item in items}
        for edge in edges:
            if edge["state"] not in {"active", "cleared"}:
                continue
            referenced_ids = {
                edge["sourceId"],
                edge["targetId"],
                *edge.get("entryEvidenceIds", []),
                *edge.get("exitEvidenceIds", []),
                *edge.get("evidenceSourceIds", []),
            }
            for item_id in referenced_ids:
                if item_id in incident:
                    incident[item_id] += 1

        for edge in active_edges:
            if edge["relationshipType"] == "depends-on" and edge.get("hardness") == "hard-serial":
                missing = []
                if not edge.get("ownerLabel"):
                    missing.append("owner")
                if not edge.get("clearingCondition"):
                    missing.append("clearing condition")
                if missing:
                    findings.append(
                        self._finding(
                            "hard-serial-controls-missing",
                            "error",
                            f"Hard-serial relationship {edge.get('issueKey') or edge['id']} is missing {' and '.join(missing)}.",
                            item_ids=[edge["sourceId"], edge["targetId"]],
                            relationship_ids=[edge["id"]],
                        )
                    )
            if edge["relationshipType"] == "reviews":
                if not edge.get("entryEvidenceIds") or not edge.get("exitEvidenceIds"):
                    findings.append(
                        self._finding(
                            "review-evidence-missing",
                            "error",
                            f"Review relationship {edge.get('issueKey') or edge['id']} needs explicit entry and exit evidence.",
                            item_ids=[edge["sourceId"], edge["targetId"]],
                            relationship_ids=[edge["id"]],
                        )
                    )

        for item in items:
            primary = [
                edge for edge in active_edges
                if edge["sourceId"] == item["id"]
                and edge["relationshipType"] == "contributes-to"
                and edge.get("primaryContribution")
                and edge["targetId"] in milestones
            ]
            requires_primary = item.get("_launchScopeExplicit") or bool(primary)
            if requires_primary and self._is_active_executable(item) and len(primary) != 1:
                findings.append(
                    self._finding(
                        "primary-milestone-cardinality",
                        "error",
                        f"Launch-scoped item {item.get('issueKey') or item['id']} must have exactly one primary milestone; found {len(primary)}.",
                        item_ids=[item["id"]],
                        relationship_ids=[edge["id"] for edge in primary],
                    )
                )
            item_types = {item["primaryType"].lower(), *(tag.lower() for tag in item.get("typeTags", []))}
            is_mr = bool(item_types.intersection({"mr", "merge-request", "pull-request", "change-request"}))
            if is_mr:
                reviews = [
                    edge for edge in active_edges
                    if edge["sourceId"] == item["id"]
                    and edge["relationshipType"] == "reviews"
                    and edge["targetId"] in milestones
                ]
                if len(reviews) != 1:
                    findings.append(
                        self._finding(
                            "mr-review-cardinality",
                            "error",
                            f"MR {item.get('issueKey') or item['id']} must have exactly one reviews relationship to a milestone; found {len(reviews)}.",
                            item_ids=[item["id"]],
                            relationship_ids=[edge["id"] for edge in reviews],
                        )
                    )
                if not item.get("ownerLabel"):
                    findings.append(
                        self._finding(
                            "unassigned-mr",
                            "error",
                            f"MR {item.get('issueKey') or item['id']} has no owner.",
                            item_ids=[item["id"]],
                        )
                    )
            governed_types = {"task", "plan", "feature", "adr", "test", "evidence", "evidence-packet"}
            if item["primaryType"] != "milestone" and item_types.intersection(governed_types) and incident[item["id"]] == 0:
                if item.get("tags") and not item.get("_launchScopeExplicit"):
                    findings.append(self._finding(
                        "standalone-seed",
                        "info",
                        f"{item.get('issueKey') or item['id']} is an intentionally tagged standalone projection seed with no typed relationship.",
                        item_ids=[item["id"]],
                    ))
                else:
                    findings.append(self._finding(
                        "orphan-item",
                        "warning",
                        f"{item.get('issueKey') or item['id']} has no active or cleared typed relationship or evidence reference.",
                        item_ids=[item["id"]],
                    ))
        return findings

    @staticmethod
    def _finding(
        code: str,
        severity: str,
        message: str,
        item_ids: list[str] | None = None,
        relationship_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "code": code,
            "severity": severity,
            "message": message,
            "itemIds": item_ids or [],
            "relationshipIds": relationship_ids or [],
        }

    @staticmethod
    def _date_value(value: Any) -> Any:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value[:10]).date()
        except ValueError:
            return None

    @staticmethod
    def _days_between(start: str, end: str) -> int:
        start_date = datetime.fromisoformat(start[:10]).date()
        end_date = datetime.fromisoformat(end[:10]).date()
        return (end_date - start_date).days

    @staticmethod
    def _bounded_integer(value: Any, minimum: int, maximum: int) -> int | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return max(minimum, min(maximum, round(value)))

    @staticmethod
    def _bounded_number(value: Any, minimum: float, maximum: float) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return max(minimum, min(maximum, float(value)))

    @staticmethod
    def _is_scheduled(item: Mapping[str, Any]) -> bool:
        return bool(
            item.get("startDate")
            or item.get("dueDate")
            or item.get("primaryType") == "milestone"
            or "timeline-item" in item.get("typeTags", [])
        )

    def _is_active_executable(self, item: Mapping[str, Any]) -> bool:
        if self._is_complete(item):
            return False
        executable_types = {str(value).lower() for value in self._registry["executableTypes"]}
        item_types = {
            str(item.get("primaryType") or "").lower(),
            *(str(tag).lower() for tag in item.get("typeTags", [])),
        }
        return bool(item_types.intersection(executable_types))

    def _in_timeline_range(
        self,
        item: Mapping[str, Any],
        from_ms: int | None,
        to_ms: int | None,
    ) -> bool:
        if from_ms is None and to_ms is None:
            return True
        start = self._optional_date_ms(item.get("startDate"), "startDate")
        end = self._optional_date_ms(item.get("dueDate"), "dueDate")
        if start is None and end is None:
            return True
        effective_start = start if start is not None else end
        effective_end = end if end is not None else start
        if effective_start is None or effective_end is None:
            return True
        if from_ms is not None and effective_end < from_ms:
            return False
        if to_ms is not None and effective_start > to_ms:
            return False
        return True

    @staticmethod
    def _relationship_targets(raw: Any) -> list[dict[str, Any]]:
        values = raw if isinstance(raw, list) else [] if raw is None else [raw]
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in values:
            if isinstance(value, str):
                target = {"itemId": value}
            elif isinstance(value, dict):
                item_id = value.get("itemId") or value.get("id")
                if not isinstance(item_id, str) or not item_id:
                    continue
                target = {
                    key: value[key]
                    for key in ("itemId", "issueKey", "title", "trackerType")
                    if key in value
                }
                target["itemId"] = item_id
            else:
                continue
            if target["itemId"] in seen:
                continue
            seen.add(target["itemId"])
            result.append(target)
        return result

    def _fit_timeline_result(self, result: dict[str, Any]) -> dict[str, Any]:
        schema_discovery = result.get("source", {}).get("schemaDiscovery")
        protect_legacy_rows = (
            isinstance(schema_discovery, dict)
            and schema_discovery.get("state") == "missing-with-live-rows"
        )

        def update_projection_count() -> None:
            if not isinstance(schema_discovery, dict):
                return
            projected_count = sum(
                item.get("primaryType") == "timeline-item"
                for item in result["items"]
            )
            schema_discovery["projectedRowCount"] = projected_count
            schema_discovery["allLiveRowsProjected"] = (
                projected_count == int(schema_discovery.get("liveRowCount") or 0)
            )

        update_projection_count()
        while result["items"] and self._json_size(result) > MAX_RESULT_BYTES:
            removable_index = len(result["items"]) - 1
            if protect_legacy_rows:
                removable_index = next(
                    (
                        index
                        for index in range(len(result["items"]) - 1, -1, -1)
                        if result["items"][index].get("primaryType") != "timeline-item"
                    ),
                    removable_index,
                )
            result["items"].pop(removable_index)
            retained = {item["id"] for item in result["items"]}
            result["milestones"] = [item for item in result["milestones"] if item["id"] in retained]
            result["relationships"] = [
                edge for edge in result["relationships"]
                if edge["sourceId"] in retained and edge["targetId"] in retained
            ]
            retained_relationships = {edge["id"] for edge in result["relationships"]}
            result["validation"] = [
                finding for finding in result["validation"]
                if all(item_id in retained for item_id in finding.get("itemIds", []))
                and all(edge_id in retained_relationships for edge_id in finding.get("relationshipIds", []))
            ]
            result["criticalPath"]["itemIds"] = [
                item_id for item_id in result["criticalPath"]["itemIds"] if item_id in retained
            ]
            result["criticalPath"]["cycleItemIds"] = [
                item_id for item_id in result["criticalPath"]["cycleItemIds"] if item_id in retained
            ]
            result["page"]["returned"] = len(result["items"])
            result["page"]["responseTruncated"] = True
            update_projection_count()
        if self._json_size(result) > MAX_RESULT_BYTES:
            raise ReaderError("RESPONSE_TOO_LARGE", "The timeline cannot fit within the safe response limit.")
        return result

    def _render_milestone_markdown(
        self,
        sections: list[Mapping[str, Any]],
        as_of: datetime,
        lookahead_days: int,
    ) -> str:
        lines = [
            "# Milestone report",
            "",
            f"Generated for {as_of.date().isoformat()} with a {lookahead_days}-day lookahead.",
            "",
        ]
        if not sections:
            lines.extend(["No milestone tracker items were found.", ""])
            return "\n".join(lines)
        for section in sections:
            milestone = section["milestone"]
            reference = milestone.get("issueKey") or milestone["id"]
            lines.extend(
                [
                    f"## {milestone['title']} ({reference})",
                    "",
                    f"- Schedule health: **{section['scheduleHealth']}**",
                    f"- Workflow: {milestone.get('workflow') or 'not set'}",
                    f"- Execution constraint: {milestone.get('executionConstraint') or 'clear'}",
                    f"- Risk: **{section['riskLevel']}**",
                    f"- Target: {milestone.get('dueDate') or 'not scheduled'}",
                    f"- Progress: {section['progress']}%",
                    f"- Deliverables: {section['completeCount']} of {section['deliverableCount']} complete",
                    f"- Active dependencies: {len(section['activeDependencies'])}",
                    f"- Execution blocked: {len(section['blockedItems'])}",
                    f"- Execution waiting: {len(section['waitingItems'])}",
                    f"- Overdue: {len(section['overdue'])}",
                    f"- Upcoming: {len(section['upcoming'])}",
                    f"- Validation findings: {len(section['validation'])}",
                    "",
                ]
            )
            for heading, entries in (("Overdue work", section["overdue"]), ("Upcoming work", section["upcoming"])):
                if not entries:
                    continue
                lines.extend([f"### {heading}", ""])
                for item in entries:
                    item_ref = item.get("issueKey") or item["id"]
                    lines.append(
                        f"- {item['title']} ({item_ref}) — "
                        f"{item.get('forecastDate') or item.get('dueDate') or 'unscheduled'}"
                    )
                lines.append("")
            if section["validation"]:
                lines.extend(["### Validation", ""])
                for finding in section["validation"]:
                    lines.append(
                        f"- **{finding['severity'].upper()}** `{finding['code']}` — {finding['message']}"
                    )
                lines.append("")
        return "\n".join(lines)

    def _comment_page(
        self,
        tracker_id: str,
        raw_comments: Any,
        params: Mapping[str, Any],
    ) -> dict[str, Any]:
        comments = raw_comments if isinstance(raw_comments, list) else []
        normalized = []
        for ordinal, raw in enumerate(comments):
            if not isinstance(raw, dict) or raw.get("deleted") is True:
                continue
            created_ms = self._timestamp_ms(raw.get("createdAt"))
            stable_id = raw.get("id")
            if not isinstance(stable_id, str) or not stable_id:
                seed = f"{tracker_id}|{created_ms}|{ordinal}"
                stable_id = f"generated-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:24]}"
            body = raw.get("body") if isinstance(raw.get("body"), str) else ""
            bounded_body, truncated = self._truncate(body, MAX_COMMENT_CHARS)
            updated = raw.get("updatedAt")
            normalized.append(
                {
                    "id": stable_id,
                    "body": bounded_body,
                    "createdAt": self._iso_from_ms(created_ms),
                    "updatedAt": None if updated is None else self._iso_from_ms(self._timestamp_ms(updated)),
                    "authorLabel": self._author_label(raw.get("authorIdentity")),
                    "truncated": truncated,
                    "_createdMs": created_ms,
                }
            )

        since_ms = params.get("sinceMs")
        if isinstance(since_ms, int):
            normalized = [item for item in normalized if item["_createdMs"] >= since_ms]

        reverse = params["order"] == "newest"
        normalized.sort(key=lambda item: (item["_createdMs"], item["id"]), reverse=reverse)

        cursor = params.get("cursor")
        if cursor:
            cursor_data = self._decode_cursor(cursor, params["order"])
            cursor_key = (cursor_data["createdAt"], cursor_data["id"])
            if reverse:
                normalized = [
                    item for item in normalized if (item["_createdMs"], item["id"]) < cursor_key
                ]
            else:
                normalized = [
                    item for item in normalized if (item["_createdMs"], item["id"]) > cursor_key
                ]

        limit = params["limit"]
        page_items = normalized[:limit]
        has_more = len(normalized) > len(page_items)
        return {
            "comments": [self._public_comment(item) for item in page_items],
            "page": {
                "limit": limit,
                "returned": len(page_items),
                "hasMore": has_more,
                "nextCursor": self._encode_cursor(page_items[-1], params["order"])
                if has_more and page_items
                else None,
            },
            "_cursorOrder": params["order"],
        }

    def _fit_result(self, result: dict[str, Any]) -> dict[str, Any]:
        cursor_order = result.pop("_cursorOrder", "newest")
        comments = result["comments"]
        while comments and self._json_size(result) > MAX_RESULT_BYTES:
            comments.pop()
            result["page"]["returned"] = len(comments)
            result["page"]["hasMore"] = True
            result["page"]["nextCursor"] = None
        if result["page"]["hasMore"] and comments:
            last_created = self._parse_iso_ms(comments[-1]["createdAt"], "createdAt")
            cursor_item = {
                "id": comments[-1]["id"],
                "_createdMs": last_created,
            }
            result["page"]["nextCursor"] = self._encode_cursor(cursor_item, cursor_order)
        if self._json_size(result) > MAX_RESULT_BYTES:
            raise ReaderError(
                "RESPONSE_TOO_LARGE",
                "The tracker item cannot fit within the safe response limit.",
            )
        return result

    @staticmethod
    def _tracker_summary(row: sqlite3.Row, data: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "issueKey": row["issue_key"],
            "title": data.get("title") if isinstance(data.get("title"), str) else "Untitled tracker item",
            "archived": bool(row["archived"]),
        }

    def _source(
        self,
        schema_fingerprint: str,
        schema_discovery: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        source = {
            "backend": "sqlite",
            "mode": "read-only",
            "schemaAdapter": SCHEMA_ADAPTER,
            "schemaFingerprint": schema_fingerprint,
            "registryVersion": self._registry["version"],
            "registryOverrideActive": self._registry_override_active,
            "registryHash": self._registry_hash,
            "readerBundle": copy.deepcopy(self._bundle_diagnostics),
        }
        if schema_discovery is not None:
            source["schemaDiscovery"] = dict(schema_discovery)
        return source

    def _registry_findings(self) -> list[dict[str, Any]]:
        if not self._registry_override_error:
            return []
        return [self._finding("registry-override-invalid", "warning", self._registry_override_error)]

    def _schema_discovery(
        self,
        workspace_path: str,
        connection: sqlite3.Connection,
    ) -> dict[str, Any]:
        tracker_type = "timeline-item"
        live_row = connection.execute(
            """
            SELECT COUNT(*)
            FROM tracker_items
            WHERE workspace = ?
              AND deleted_at IS NULL
              AND archived = 0
              AND type = ?
            """,
            (workspace_path, tracker_type),
        ).fetchone()
        live_row_count = int(live_row[0] or 0) if live_row is not None else 0
        registered_schema_path = self._registered_tracker_schema(
            workspace_path, tracker_type
        )
        registered = registered_schema_path is not None
        state = (
            "registered"
            if registered
            else "missing-with-live-rows"
            if live_row_count
            else "not-registered-no-live-rows"
        )
        discovery: dict[str, Any] = {
            "trackerType": tracker_type,
            "state": state,
            "registered": registered,
            "liveRowCount": live_row_count,
            "registryDirectory": ".nimbalyst/trackers",
            "registeredSchemaPath": registered_schema_path,
        }
        if state == "missing-with-live-rows":
            discovery["repair"] = {
                "mode": "manual-preview-required",
                "automaticMutation": False,
                "templateId": "tracker-plus/timeline-item-v2",
                "targetRelativePath": ".nimbalyst/trackers/timeline-item.yaml",
                "instruction": (
                    "Review the bundled Tracker+ timeline-item schema template, "
                    "then register it explicitly through Nimbalyst or a reviewed "
                    "workspace change."
                ),
            }
        return discovery

    @staticmethod
    def _registered_tracker_schema(
        workspace_path: str,
        tracker_type: str,
    ) -> str | None:
        schema_directory = Path(workspace_path) / ".nimbalyst" / "trackers"
        try:
            candidates = sorted(
                (
                    path
                    for path in schema_directory.iterdir()
                    if path.is_file()
                    and path.name.lower().endswith((".yaml", ".yml"))
                ),
                key=lambda path: path.name.lower(),
            )[:256]
        except OSError:
            return None

        type_pattern = re.compile(
            r"^\s*type\s*:\s*['\"]?([^'\"#\s]+)['\"]?\s*(?:#.*)?$",
            re.IGNORECASE | re.MULTILINE,
        )
        for candidate in candidates:
            try:
                with candidate.open("r", encoding="utf-8") as handle:
                    preview = handle.read(16_384)
            except (OSError, UnicodeError):
                continue
            match = type_pattern.search(preview)
            if match and match.group(1).lower() == tracker_type.lower():
                return f".nimbalyst/trackers/{candidate.name}"
        return None

    def _schema_discovery_findings(
        self,
        schema_discovery: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if schema_discovery.get("state") != "missing-with-live-rows":
            return []
        live_row_count = int(schema_discovery.get("liveRowCount") or 0)
        noun = "row" if live_row_count == 1 else "rows"
        return [
            self._finding(
                "timeline-item-schema-missing-with-live-rows",
                "warning",
                (
                    f"The workspace has {live_row_count} live legacy timeline-item "
                    f"{noun}, but no timeline-item schema is registered in "
                    ".nimbalyst/trackers. Tracker+ preserved the rows. Review the "
                    "bundled schema template before registering it manually; "
                    "Tracker+ will not mutate tracker data or schema files."
                ),
            )
        ]

    @staticmethod
    def _validation_block(findings: list[dict[str, Any]]) -> dict[str, Any]:
        state = "fail" if any(item.get("severity") == "error" for item in findings) else "warn" if findings else "pass"
        return {
            "state": state,
            "findings": findings,
            "orphanCount": sum(item.get("code") == "orphan-endpoint" for item in findings),
            "duplicateCount": sum(
                item.get("code") in {"duplicate-active-membership", "duplicate-relationship"}
                for item in findings
            ),
            "cycleCount": sum(item.get("code") == "membership-cycle" for item in findings),
        }

    def _watermark(
        self,
        fingerprint: str,
        item_count: int,
        relationship_count: int,
        started: float,
        schema_discovery: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        watermark = {
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "schemaAdapter": SCHEMA_ADAPTER,
            "schemaFingerprint": fingerprint,
            "registryVersion": self._registry["version"],
            "registryOverrideActive": self._registry_override_active,
            "registryHash": self._registry_hash,
            "readerBundle": copy.deepcopy(self._bundle_diagnostics),
            "sourceItemCount": item_count,
            "sourceRelationshipCount": relationship_count,
            "durationMs": round((time.perf_counter() - started) * 1000, 2),
        }
        if schema_discovery is not None:
            watermark["schemaDiscovery"] = dict(schema_discovery)
        return watermark

    def _semantic_duplicate_findings(
        self,
        edges: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Validate exact semantic identity only inside the selected edge set."""
        by_identity: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
        for edge in edges:
            if edge.get("state") in {"retired", "unknown"}:
                continue
            identity = (
                str(edge.get("sourceId") or ""),
                str(edge.get("relationshipType") or ""),
                str(edge.get("targetId") or ""),
                str(edge.get("scopeRole") or ""),
                str(edge.get("contributionRole") or ""),
            )
            by_identity.setdefault(identity, []).append(edge)
        findings: list[dict[str, Any]] = []
        for identity, duplicates in sorted(by_identity.items()):
            if len(duplicates) < 2:
                continue
            ordered = sorted(duplicates, key=lambda edge: edge["id"])
            relationship_type = identity[1]
            code = (
                "duplicate-active-membership"
                if relationship_type == "part-of-launch"
                else "duplicate-relationship"
            )
            findings.append(
                self._finding(
                    code,
                    "error",
                    (
                        "Selected traversal contains exact duplicate semantic "
                        f"relationship {identity[0]} → {relationship_type} → {identity[2]}."
                    ),
                    item_ids=[identity[0], identity[2]],
                    relationship_ids=[edge["id"] for edge in ordered],
                )
            )
        return findings

    @staticmethod
    def _stable_fingerprint(value: Any) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _fit_graph_result(self, result: dict[str, Any], sort: list[dict[str, str]] | None = None) -> dict[str, Any]:
        removed = False
        findings_trimmed = False
        while self._json_size(result) > MAX_RESULT_BYTES and (result["boundaryNodes"] or result["nodes"] or result["edges"] or result["validation"]["findings"]):
            removed = True
            if result["boundaryNodes"]:
                result["boundaryNodes"].pop()
            elif result["nodes"]:
                result["nodes"].pop()
            elif result["edges"]:
                result["edges"].pop()
            else:
                result["validation"]["findings"].pop()
                findings_trimmed = True
        if removed:
            retained = {item["id"] for item in [*result["nodes"], *result["boundaryNodes"]]}
            result["edges"] = [edge for edge in result["edges"] if edge.get("sourceId") in retained and edge.get("targetId") in retained]
            result["page"]["truncated"] = True
            result["page"]["responseTruncated"] = True
            if sort:
                last_id = result["nodes"][-1]["id"] if result["nodes"] else result["edges"][-1]["id"] if result["edges"] else None
                if last_id:
                    result["page"]["nextCursor"] = encode_cursor(sort, last_id)
        result["page"]["returnedCount"] = len(result["nodes"]) + len(result["boundaryNodes"])
        next_cursor = result["page"].get("nextCursor")
        result["page"]["resultsComplete"] = next_cursor is None and not result["page"].get("truncated", False)
        result["page"]["continuationRequired"] = next_cursor is not None
        if findings_trimmed:
            result["validation"]["findings"].append(self._finding(
                "validation-findings-truncated", "warning",
                "Validation findings were truncated to keep the bounded response safe.",
            ))
            result["validation"]["state"] = "fail" if any(item.get("severity") == "error" for item in result["validation"]["findings"]) else "warn"
        if self._json_size(result) > MAX_RESULT_BYTES:
            raise ReaderError("RESPONSE_TOO_LARGE", "The graph response exceeded the safe response limit.")
        for item in result["nodes"]:
            for key in [entry for entry in item if entry.startswith("_")]:
                item.pop(key, None)
        return result

    @staticmethod
    def _encode_traversal_cursor(
        result_fingerprint: str,
        node_offset: int,
        edge_offset: int,
    ) -> str:
        payload = {
            "v": 1,
            "k": "g1",
            "r": result_fingerprint,
            "n": node_offset,
            "e": edge_offset,
        }
        return base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_traversal_cursor(
        raw: Any,
        result_fingerprint: str,
        node_total: int,
        edge_total: int,
    ) -> tuple[int, int]:
        if raw is None:
            return 0, 0
        if not isinstance(raw, str):
            raise ReaderError("CURSOR_INVALID", "Traversal cursor must be opaque text.")
        try:
            payload = json.loads(
                base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            )
        except (ValueError, UnicodeError, json.JSONDecodeError, binascii.Error):
            raise ReaderError("CURSOR_INVALID", "The traversal cursor is invalid.") from None
        if (
            not isinstance(payload, Mapping)
            or payload.get("v") != 1
            or payload.get("k") != "g1"
            or payload.get("r") != result_fingerprint
            or isinstance(payload.get("n"), bool)
            or not isinstance(payload.get("n"), int)
            or isinstance(payload.get("e"), bool)
            or not isinstance(payload.get("e"), int)
            or not 0 <= payload["n"] <= node_total
            or not 0 <= payload["e"] <= edge_total
            or (payload["n"] == node_total and payload["e"] == edge_total)
        ):
            raise ReaderError(
                "CURSOR_INVALID",
                "The traversal cursor does not match the current complete graph.",
            )
        return payload["n"], payload["e"]

    def _fit_paginated_traversal_result(
        self,
        result: dict[str, Any],
        node_offset: int,
        edge_offset: int,
        node_total: int,
        edge_total: int,
        result_fingerprint: str,
    ) -> dict[str, Any]:
        findings_trimmed = False
        response_truncated = False

        def update_page() -> None:
            next_node_offset = node_offset + len(result["nodes"]) + len(result["boundaryNodes"])
            next_edge_offset = edge_offset + len(result["edges"])
            continuation_required = (
                next_node_offset < node_total or next_edge_offset < edge_total
            )
            result["page"].update({
                "returnedCount": len(result["nodes"]) + len(result["boundaryNodes"]),
                "returnedEdgeCount": len(result["edges"]),
                "nextCursor": (
                    self._encode_traversal_cursor(
                        result_fingerprint,
                        next_node_offset,
                        next_edge_offset,
                    )
                    if continuation_required
                    else None
                ),
                "truncated": continuation_required,
                "resultsComplete": not continuation_required,
                "continuationRequired": continuation_required,
                "responseTruncated": response_truncated,
            })

        update_page()
        while self._json_size(result) > MAX_RESULT_BYTES and (
            result["edges"]
            or result["boundaryNodes"]
            or result["nodes"]
            or result["validation"]["findings"]
        ):
            response_truncated = True
            if result["edges"]:
                result["edges"].pop()
            elif result["boundaryNodes"]:
                result["boundaryNodes"].pop()
            elif result["nodes"]:
                result["nodes"].pop()
            else:
                result["validation"]["findings"].pop()
                findings_trimmed = True
            update_page()

        if findings_trimmed:
            result["validation"]["findings"].append(self._finding(
                "validation-findings-truncated",
                "warning",
                "Validation findings were truncated to keep the bounded response safe.",
            ))
            result["validation"]["state"] = (
                "fail"
                if any(
                    item.get("severity") == "error"
                    for item in result["validation"]["findings"]
                )
                else "warn"
            )
        result["validation"]["findingsComplete"] = not findings_trimmed
        update_page()
        if self._json_size(result) > MAX_RESULT_BYTES:
            raise ReaderError(
                "RESPONSE_TOO_LARGE",
                "A single paged traversal entity exceeded the safe response limit.",
            )
        if (
            result["page"]["continuationRequired"]
            and not result["nodes"]
            and not result["boundaryNodes"]
            and not result["edges"]
        ):
            raise ReaderError(
                "RESPONSE_TOO_LARGE",
                "The paged traversal could not make progress within the safe response limit.",
            )
        for item in result["nodes"]:
            for key in [entry for entry in item if entry.startswith("_")]:
                item.pop(key, None)
        return result

    @staticmethod
    def _parse_type_tags(raw: Any) -> list[str]:
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return []
        else:
            parsed = raw
        return [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []

    @staticmethod
    def _bounded_string(value: Any, limit: int) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped[:limit] if stripped else None

    @staticmethod
    def _identity_label(identity: Any) -> str | None:
        if isinstance(identity, str) and identity.strip():
            value = identity.strip()
            return (value.split("@", 1)[0] if "@" in value else value)[:200]
        if not isinstance(identity, dict):
            return None
        for key in ("displayName", "gitName", "name", "username"):
            value = identity.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]
        return None

    @staticmethod
    def _date_string(value: Any) -> str | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(value / 1000, timezone.utc).date().isoformat()
            if not isinstance(value, str) or not value.strip():
                return None
            normalized = value.strip()
            normalized = normalized[:-1] + "+00:00" if normalized.endswith("Z") else normalized
            return datetime.fromisoformat(normalized).date().isoformat()
        except (ValueError, OverflowError, OSError):
            return None

    @staticmethod
    def _date_time_string(value: Any) -> str | None:
        return value[:64] if isinstance(value, str) and value else None

    @staticmethod
    def _optional_date_ms(value: Any, field_name: str) -> int | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ReaderError("INVALID_PARAMS", f"{field_name} must be an ISO-8601 date or timestamp.")
        return NativeTrackerReader._parse_iso_ms(value.strip(), field_name)

    @staticmethod
    def _author_label(identity: Any) -> str:
        if not isinstance(identity, dict):
            return "Unknown author"
        for key in ("displayName", "gitName", "name", "username"):
            value = identity.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]
        return "Unknown author"

    @staticmethod
    def _truncate(value: str, limit: int) -> tuple[str, bool]:
        if len(value) <= limit:
            return value, False
        return value[:limit], True

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _timestamp_ms(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            return NativeTrackerReader._parse_iso_ms(value, "comment timestamp")
        return 0

    @staticmethod
    def _parse_iso_ms(value: str, field_name: str) -> int:
        try:
            normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        except (ValueError, OverflowError):
            raise ReaderError(
                "INVALID_PARAMS",
                f"{field_name} must be a valid ISO-8601 timestamp.",
            ) from None

    @staticmethod
    def _iso_from_ms(value: int) -> str:
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            )
        except (ValueError, OverflowError, OSError):
            return "1970-01-01T00:00:00.000Z"

    @staticmethod
    def _encode_cursor(item: Mapping[str, Any], order: str) -> str:
        payload = {
            "v": 1,
            "createdAt": item["_createdMs"],
            "id": item["id"],
            "order": order,
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).decode("ascii")
        return encoded.rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str, order: str) -> dict[str, Any]:
        try:
            padding = "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(cursor + padding).decode("utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("v") != 1
                or payload.get("order") != order
                or not isinstance(payload.get("createdAt"), int)
                or not isinstance(payload.get("id"), str)
            ):
                raise ValueError
            return payload
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
            raise ReaderError("CURSOR_INVALID", "The pagination cursor is invalid or expired.") from None

    @staticmethod
    def _public_comment(item: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in item.items() if not key.startswith("_")}

    @staticmethod
    def _json_size(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
