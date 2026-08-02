"""Validation helpers for deterministic rooted Tracker+ graph traversal."""

from __future__ import annotations

from typing import Any, Mapping

try:
    from .contracts import ReaderError
except ImportError:  # pragma: no cover
    from contracts import ReaderError  # type: ignore[no-redef]


def validate_stage(raw: Any, path: str, registry: Mapping[str, Any], *, membership: bool) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ReaderError("QUERY_INVALID", f"{path} must be an object.", {"path": path})
    allowed = {"relationshipTypes", "direction", "maxDepth"}
    if membership:
        allowed.add("status")
    else:
        allowed.update({"edgeWhere", "externalEndpointBehavior"})
    if set(raw) - allowed:
        raise ReaderError("QUERY_INVALID", f"{path} contains unknown fields.", {"path": path})
    relationship_types = raw.get("relationshipTypes")
    if not isinstance(relationship_types, list) or not relationship_types or not all(
        isinstance(value, str) and value in registry["relationshipTypes"] for value in relationship_types
    ):
        raise ReaderError("QUERY_INVALID", f"{path}.relationshipTypes contains an unknown type.", {"path": f"{path}.relationshipTypes"})
    direction = raw.get("direction", "both")
    depth = raw.get("maxDepth", 1)
    if direction not in {"incoming", "outgoing", "both"}:
        raise ReaderError("QUERY_INVALID", f"{path}.direction is invalid.", {"path": f"{path}.direction"})
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= registry["caps"]["traverseDepthMax"]:
        raise ReaderError("QUERY_TOO_COMPLEX", f"{path}.maxDepth exceeds the traversal cap.", {"path": f"{path}.maxDepth"})
    result = {"relationshipTypes": relationship_types, "direction": direction, "maxDepth": depth}
    if membership:
        statuses = raw.get("status", ["active"])
        if not isinstance(statuses, list) or not statuses or not all(
            value in {"active", "cleared", "blocked", "retired", "superseded", "unknown"}
            for value in statuses
        ):
            raise ReaderError("QUERY_INVALID", f"{path}.status is invalid.", {"path": f"{path}.status"})
        result["status"] = statuses
    else:
        edge_where = raw.get("edgeWhere", {})
        if not isinstance(edge_where, Mapping) or set(edge_where) - {"status", "hardness", "scopeRole"}:
            raise ReaderError("QUERY_INVALID", f"{path}.edgeWhere is invalid.", {"path": f"{path}.edgeWhere"})
        for key, value in edge_where.items():
            if value is not None and (not isinstance(value, list) or not all(isinstance(entry, str) for entry in value)):
                raise ReaderError("QUERY_INVALID", f"{path}.edgeWhere.{key} is invalid.", {"path": f"{path}.edgeWhere.{key}"})
        behavior = raw.get("externalEndpointBehavior", "boundary")
        if behavior not in {"boundary", "exclude"}:
            raise ReaderError("QUERY_INVALID", f"{path}.externalEndpointBehavior is invalid.", {"path": f"{path}.externalEndpointBehavior"})
        result.update({"edgeWhere": dict(edge_where), "externalEndpointBehavior": behavior})
    return result


def archived_explicitly_allowed(clause: Any) -> bool:
    if not isinstance(clause, Mapping):
        return False
    if clause.get("field") == "archived" and clause.get("op") == "eq" and clause.get("value") is True:
        return True
    return any(archived_explicitly_allowed(value) for key, value in clause.items() if key in {"all", "any", "not"} for value in (value if isinstance(value, list) else [value]))


def edge_matches(edge: Mapping[str, Any], stage: Mapping[str, Any], *, membership: bool) -> bool:
    if edge.get("relationshipType") not in stage["relationshipTypes"]:
        return False
    if membership:
        return edge.get("state") in stage["status"]
    for key, values in stage.get("edgeWhere", {}).items():
        if values is not None and edge.get("state" if key == "status" else key) not in values:
            return False
    return True


def neighbor(edge: Mapping[str, Any], node_id: str, direction: str) -> str | None:
    if direction in {"outgoing", "both"} and edge.get("sourceId") == node_id:
        return str(edge.get("targetId"))
    if direction in {"incoming", "both"} and edge.get("targetId") == node_id:
        return str(edge.get("sourceId"))
    return None
