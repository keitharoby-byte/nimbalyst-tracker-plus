"""Newline-delimited JSON process wrapper for the native tracker reader."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    # Isolated mode intentionally removes the script directory from sys.path.
    # Restore only this trusted, packaged directory so sibling modules import.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .contracts import MAX_INPUT_LINE_BYTES, MAX_OUTPUT_LINE_BYTES, ReaderError
    from .database import NativeTrackerReader
except ImportError:  # Script execution from the packaged reader directory.
    from contracts import MAX_INPUT_LINE_BYTES, MAX_OUTPUT_LINE_BYTES, ReaderError
    from database import NativeTrackerReader


def write_response(response: dict[str, Any]) -> None:
    encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_OUTPUT_LINE_BYTES:
        encoded = json.dumps(
            {
                "id": response.get("id", "unknown"),
                "ok": False,
                "error": {
                    "code": "RESPONSE_TOO_LARGE",
                    "message": "The tracker response exceeded the safe process output limit.",
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()


def safe_diagnostic(event: str, **fields: Any) -> None:
    allowed = {key: value for key, value in fields.items() if key in {"method", "durationMs", "code"}}
    print(json.dumps({"event": event, **allowed}, separators=(",", ":")), file=sys.stderr, flush=True)


def handle(request: Any, reader: NativeTrackerReader) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ReaderError("PROTOCOL_INVALID", "Each helper request must be a JSON object.")
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})
    if not isinstance(request_id, str) or not request_id:
        raise ReaderError("PROTOCOL_INVALID", "Each helper request requires an id.")
    if not isinstance(params, dict):
        raise ReaderError("PROTOCOL_INVALID", "Helper request params must be an object.")

    if method == "list_comments":
        result = reader.list_comments(params)
    elif method == "get_with_comments":
        result = reader.get_with_comments(params)
    elif method == "timeline_snapshot":
        result = reader.timeline_snapshot(params)
    elif method == "milestone_report":
        result = reader.milestone_report(params)
    else:
        raise ReaderError("METHOD_NOT_FOUND", "The requested native tracker operation is not supported.")
    return {"id": request_id, "ok": True, "result": result}


def main() -> int:
    reader = NativeTrackerReader()
    for raw_line in sys.stdin.buffer:
        started = time.perf_counter()
        request_id = "unknown"
        method = "unknown"
        if len(raw_line) > MAX_INPUT_LINE_BYTES:
            write_response(
                {
                    "id": request_id,
                    "ok": False,
                    "error": {
                        "code": "PROTOCOL_LINE_TOO_LARGE",
                        "message": "The helper request exceeded the safe input limit.",
                    },
                }
            )
            continue
        try:
            request = json.loads(raw_line)
            if isinstance(request, dict):
                request_id = request.get("id") if isinstance(request.get("id"), str) else "unknown"
                method = request.get("method") if isinstance(request.get("method"), str) else "unknown"
            response = handle(request, reader)
            write_response(response)
            safe_diagnostic(
                "tool.success",
                method=method,
                durationMs=round((time.perf_counter() - started) * 1000),
            )
        except ReaderError as error:
            write_response({"id": request_id, "ok": False, "error": error.to_dict()})
            safe_diagnostic(
                "tool.error",
                method=method,
                code=error.code,
                durationMs=round((time.perf_counter() - started) * 1000),
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            write_response(
                {
                    "id": request_id,
                    "ok": False,
                    "error": {
                        "code": "PROTOCOL_JSON_INVALID",
                        "message": "The helper request was not valid JSON.",
                    },
                }
            )
            safe_diagnostic("tool.error", method=method, code="PROTOCOL_JSON_INVALID")
        except Exception:
            write_response(
                {
                    "id": request_id,
                    "ok": False,
                    "error": {
                        "code": "READER_INTERNAL_ERROR",
                        "message": "The native tracker reader failed without exposing tracker content.",
                    },
                }
            )
            safe_diagnostic("tool.error", method=method, code="READER_INTERNAL_ERROR")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
