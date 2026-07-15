"""Strict, workspace-scoped, read-only access to native tracker comments."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import sqlite3
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
        MAX_TRACKER_BODY_CHARS,
        REQUIRED_COLUMNS,
        SCHEMA_ADAPTER,
        ReaderError,
    )
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
        MAX_TRACKER_BODY_CHARS,
        REQUIRED_COLUMNS,
        SCHEMA_ADAPTER,
        ReaderError,
    )


class NativeTrackerReader:
    """Open a fresh read-only SQLite connection for each bounded operation."""

    def __init__(self, database_path: Path | None = None) -> None:
        self._database_path = database_path

    def list_comments(self, params: Mapping[str, Any]) -> dict[str, Any]:
        parsed = self._validated_params(params)
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
        link_rows: list[tuple[sqlite3.Row, dict[str, Any]]] = []
        try:
            with self._connect() as connection:
                fingerprint = self._validate_schema(connection)
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
                              AND type = 'milestone'
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
        project_state_revision: str | None = None

        for _row, fields in link_rows:
            project_state_revision = project_state_revision or self._bounded_string(
                fields.get("projectStateRevision"), 200
            )

        for row in rows:
            data = self._parse_data(row)
            fields = self._flatten_custom_fields(data)
            project_state_revision = project_state_revision or self._bounded_string(
                fields.get("projectStateRevision"), 200
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
        critical_path, analysis_findings = self._apply_timeline_analysis(items, edges)
        validation = [
            *relationship_findings,
            *analysis_findings,
            *self._validate_timeline(items, edges),
        ]
        item_ids = {item["id"] for item in items}
        for edge in edges:
            edge["targetInSnapshot"] = edge["targetId"] in item_ids

        source = self._source(fingerprint)
        source["projectStateRevision"] = project_state_revision or "unavailable"
        source["relationshipSource"] = (
            "native timeline-link rows" if link_rows else "legacy tracker fields"
        )
        source["relationshipRows"] = len(link_rows)
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

        for item in items:
            item.pop("_launchScopeExplicit", None)

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
        done_statuses = {"done", "completed", "achieved", "closed", "shipped", "implemented"}
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
            complete = [item for item in deliverables if str(item.get("workflow", "")).lower() in done_statuses]
            overdue = [
                item
                for item in deliverables
                if (item.get("forecastDate") or item.get("dueDate"))
                and self._parse_iso_ms(item.get("forecastDate") or item["dueDate"], "dueDate") < parsed["asOfMs"]
                and str(item.get("workflow", "")).lower() not in done_statuses
            ]
            upcoming = [
                item
                for item in deliverables
                if (item.get("forecastDate") or item.get("dueDate"))
                and parsed["asOfMs"] <= self._parse_iso_ms(item.get("forecastDate") or item["dueDate"], "dueDate") <= int(lookahead.timestamp() * 1000)
                and str(item.get("workflow", "")).lower() not in done_statuses
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
            progress = milestone.get("progress")
            if not isinstance(progress, (int, float)):
                progress = round((len(complete) / len(deliverables)) * 100) if deliverables else 0
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
        allowed = {"workspacePath", "includeUnscheduled", "maxItems", "from", "to"}
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
        return {
            "workspacePath": workspace_path,
            "includeUnscheduled": include_unscheduled,
            "maxItems": max_items,
            "fromMs": from_ms,
            "toMs": to_ms,
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
                "Version 0.3.0 supports Windows installations with APPDATA available.",
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
        return {
            "id": str(row["id"]),
            "issueKey": row["issue_key"] if isinstance(row["issue_key"], str) else None,
            "primaryType": primary_type,
            "typeTags": type_tags,
            "title": title,
            "workflow": workflow,
            "priority": self._bounded_string(fields.get("priority"), 100),
            "ownerLabel": owner_label,
            "startDate": start_date,
            "dueDate": due_date,
            "forecastDate": self._date_string(fields.get("forecastDate")),
            "progress": progress,
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
            "launchScoped": launch_scope == "launch",
            "_launchScopeExplicit": launch_scope == "launch",
            "primaryMilestoneId": None,
            "scheduleSlackDays": None,
            "criticalPathSlackDays": None,
            "durationDays": duration_days,
            "isCritical": False,
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
        explicit_keys: set[tuple[str, str, str]] = set()

        for row, fields in link_rows:
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
            if relationship_type not in {
                "depends-on", "contributes-to", "reviews", "evidences", "implements", "related"
            }:
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
            key = (source_id, relationship_type, target_id)
            if key in explicit_keys:
                findings.append(
                    self._finding(
                        "duplicate-relationship",
                        "warning",
                        f"Duplicate normalized edge {source_id} → {relationship_type} → {target_id} was ignored.",
                        relationship_ids=[link_id],
                    )
                )
                continue
            explicit_keys.add(key)
            status = self._bounded_string(fields.get("status"), 40)
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
                "targetType": self._bounded_string(target.get("trackerType"), 100),
                "relationshipType": relationship_type,
                "directed": directedness != "symmetric" and relationship_type != "related",
                "state": status if status in {"active", "cleared", "superseded"} else "active",
                "dependencyMode": self._bounded_string(fields.get("dependencyMode"), 40)
                if relationship_type == "depends-on" else None,
                "hardness": self._bounded_string(fields.get("hardness"), 40)
                if relationship_type == "depends-on" else None,
                "leadLagDays": self._bounded_number(fields.get("leadLagDays"), -365, 365) or 0,
                "clearingCondition": self._bounded_string(fields.get("clearingCondition"), 2_000),
                "ownerLabel": self._identity_label(fields.get("owner")),
                "primaryContribution": fields.get("contributionRole") == "primary",
                "entryEvidenceIds": [entry["itemId"] for entry in entry_evidence],
                "exitEvidenceIds": [entry["itemId"] for entry in exit_evidence],
                "evidenceSourceIds": [entry["itemId"] for entry in evidence_sources],
                "effectiveRevision": self._bounded_string(fields.get("effectiveRevision"), 200),
                "created": self._date_time_string(row["created"]),
                "updated": self._date_time_string(row["updated"]),
                "targetInSnapshot": target_id in items_by_id,
                "legacy": False,
            }
            if not edge["sourceTitle"] and source_id in items_by_id:
                edge["sourceTitle"] = items_by_id[source_id]["title"]
                edge["sourceIssueKey"] = items_by_id[source_id].get("issueKey")
            if not edge["targetTitle"] and target_id in items_by_id:
                edge["targetTitle"] = items_by_id[target_id]["title"]
                edge["targetIssueKey"] = items_by_id[target_id].get("issueKey")
                edge["targetType"] = items_by_id[target_id]["primaryType"]
            edges.append(edge)

        if link_rows:
            return edges, findings

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
                    key = (source_id, relationship_type, target_id)
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

    def _derive_item_health_and_risk(self, item: dict[str, Any], today: Any) -> None:
        done = str(item.get("workflow") or "").lower() in {
            "done", "completed", "achieved", "closed", "shipped", "implemented"
        }
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
                findings.append(
                    self._finding(
                        "orphan-item",
                        "warning",
                        f"{item.get('issueKey') or item['id']} has no active or cleared typed relationship or evidence reference.",
                        item_ids=[item["id"]],
                    )
                )
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

    @staticmethod
    def _is_active_executable(item: Mapping[str, Any]) -> bool:
        complete_workflows = {
            "done", "completed", "achieved", "closed", "shipped", "implemented"
        }
        if str(item.get("workflow") or "").lower() in complete_workflows:
            return False
        executable_types = {
            "task",
            "timeline-item",
            "devops-item",
            "prediclear-item",
            "automation",
            "mr",
            "merge-request",
            "pull-request",
            "change-request",
        }
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
        while result["items"] and self._json_size(result) > MAX_RESULT_BYTES:
            result["items"].pop()
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

    @staticmethod
    def _source(schema_fingerprint: str) -> dict[str, Any]:
        return {
            "backend": "sqlite",
            "mode": "read-only",
            "schemaAdapter": SCHEMA_ADAPTER,
            "schemaFingerprint": schema_fingerprint,
        }

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
