"""Shared limits and structured errors for the reader protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA_ADAPTER = "tracker-items-data-comments-v1"
REQUIRED_COLUMNS = (
    "id",
    "issue_key",
    "type",
    "data",
    "workspace",
    "content",
    "archived",
    "type_tags",
    "deleted_at",
    "created",
    "updated",
)

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_COMMENT_CHARS = 20_000
MAX_TRACKER_BODY_CHARS = 100_000
MAX_RESULT_BYTES = 500 * 1024
MAX_INPUT_LINE_BYTES = 64 * 1024
MAX_OUTPUT_LINE_BYTES = 512 * 1024


@dataclass(slots=True)
class ReaderError(Exception):
    code: str
    message: str
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload
