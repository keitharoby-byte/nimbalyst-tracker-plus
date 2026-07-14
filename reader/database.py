"""Strict, workspace-scoped, read-only access to native tracker comments."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

try:
    from .contracts import (
        DEFAULT_LIMIT,
        MAX_COMMENT_CHARS,
        MAX_LIMIT,
        MAX_RESULT_BYTES,
        MAX_TRACKER_BODY_CHARS,
        REQUIRED_COLUMNS,
        SCHEMA_ADAPTER,
        ReaderError,
    )
except ImportError:  # pragma: no cover - used when server.py runs as a script
    from contracts import (  # type: ignore[no-redef]
        DEFAULT_LIMIT,
        MAX_COMMENT_CHARS,
        MAX_LIMIT,
        MAX_RESULT_BYTES,
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
                "Version 0.1.0 supports Windows installations with APPDATA available.",
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
                "Native Tracker Comments requires Nimbalyst's SQLite backend.",
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
