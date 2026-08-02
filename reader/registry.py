"""Versioned Tracker+ vocabulary and saved-query registry."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

try:
    from .contracts import ReaderError
except ImportError:  # pragma: no cover
    from contracts import ReaderError  # type: ignore[no-redef]

LOCKED_OVERRIDE_KEYS = {"relationshipTypes", "scopeRoles", "executableTypes", "caps", "version"}
OVERRIDABLE_KEYS = {"terminalStatuses", "roles", "savedQueries", "dispatchPolicy"}
REQUIRED_CAPS = {
    "queryLimitDefault", "queryLimitMax", "clauseDepthMax", "clauseCountMax",
    "listValuesMax", "textTermMax", "traverseNodesMax", "traverseEdgesMax",
    "traverseDepthMax", "traverseRootsMax",
}


def _string_list(value: Any, *, nonempty: bool = True) -> bool:
    return isinstance(value, list) and (not nonempty or bool(value)) and all(
        isinstance(entry, str) and bool(entry.strip()) for entry in value
    )


def _validate_saved_queries(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for query_id, query in value.items():
        if not isinstance(query_id, str) or not isinstance(query, dict):
            return False
        if query.get("kind") not in {"predicate", "traversal"}:
            return False
        if not isinstance(query.get("version"), int) or query["version"] < 1:
            return False
        if not _string_list(query.get("params", []), nonempty=False):
            return False
        if not _string_list(query.get("optionalParams", []), nonempty=False):
            return False
        if set(query.get("params", [])).intersection(query.get("optionalParams", [])):
            return False
        if not isinstance(query.get("definition"), dict):
            return False
    return True


def _validate_dispatch_policy(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    list_keys = {
        "dispatchableTypes", "readyStatuses", "qaPassStatuses",
        "eligibleLaunchStatuses", "membershipRoles", "contributionRoles",
        "admittedUnscopedTypes", "admissibleDatabaseRoutes",
        "clearHoldStates", "clearCustodyStates", "survivorStates",
        "clearCollisionStates",
    }
    if set(value) != list_keys:
        return False
    return all(
        _string_list(value.get(key), nonempty=key != "admittedUnscopedTypes")
        for key in list_keys
    )


def _validate_registry(value: Any) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("version"), int):
        raise ValueError("version must be an integer")
    for key in ("terminalStatuses", "scopeRoles", "executableTypes"):
        if not _string_list(value.get(key)):
            raise ValueError(f"{key} must be a non-empty string list")
    relationships = value.get("relationshipTypes")
    if not isinstance(relationships, dict) or not relationships or not all(
        isinstance(key, str) and isinstance(rule, dict) for key, rule in relationships.items()
    ):
        raise ValueError("relationshipTypes must be an object of rule objects")
    roles = value.get("roles")
    if not isinstance(roles, dict):
        raise ValueError("roles must be an object")
    for role in roles.values():
        if not isinstance(role, dict) or not _string_list(role.get("ownerAliases")) or not _string_list(role.get("attentionTags"), nonempty=False):
            raise ValueError("role entries require ownerAliases and attentionTags")
    caps = value.get("caps")
    if not isinstance(caps, dict) or not REQUIRED_CAPS.issubset(caps) or not all(
        isinstance(caps[key], int) and caps[key] > 0 for key in REQUIRED_CAPS
    ):
        raise ValueError("caps are incomplete or invalid")
    if not _validate_saved_queries(value.get("savedQueries")):
        raise ValueError("savedQueries are invalid")
    if not _validate_dispatch_policy(value.get("dispatchPolicy")):
        raise ValueError("dispatchPolicy is invalid")


def _load_bundled() -> dict[str, Any]:
    try:
        value = json.loads((Path(__file__).with_name("registry.json")).read_text(encoding="utf-8"))
        _validate_registry(value)
        return value
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise ReaderError("REGISTRY_INVALID", "The bundled Tracker+ registry is invalid.") from error


def _validate_override(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("override must be an object")
    unknown = set(value) - OVERRIDABLE_KEYS - LOCKED_OVERRIDE_KEYS
    locked = set(value).intersection(LOCKED_OVERRIDE_KEYS)
    if unknown or locked:
        raise ValueError("override contains locked or unknown keys")
    if "terminalStatuses" in value and not _string_list(value["terminalStatuses"]):
        raise ValueError("terminalStatuses must be a non-empty string list")
    if "roles" in value:
        roles = value["roles"]
        if not isinstance(roles, dict):
            raise ValueError("roles must be an object")
        for role in roles.values():
            if not isinstance(role, dict) or not _string_list(role.get("ownerAliases")) or not _string_list(role.get("attentionTags", []), nonempty=False):
                raise ValueError("override role is invalid")
    if "savedQueries" in value and not _validate_saved_queries(value["savedQueries"]):
        raise ValueError("override savedQueries are invalid")
    if "dispatchPolicy" in value and not _validate_dispatch_policy(value["dispatchPolicy"]):
        raise ValueError("override dispatchPolicy is invalid")


def effective_registry(workspace_path: str | Path) -> tuple[dict[str, Any], bool, str | None, str]:
    """Return effective registry, override state/error, and a short audit hash."""
    bundled = _load_bundled()
    effective = copy.deepcopy(bundled)
    override_active = False
    override_error: str | None = None
    override_path = Path(workspace_path) / ".nimbalyst" / "tracker-plus.registry.json"
    if override_path.is_file():
        try:
            override = json.loads(override_path.read_text(encoding="utf-8"))
            _validate_override(override)
            if "terminalStatuses" in override:
                effective["terminalStatuses"] = list(override["terminalStatuses"])
            for key in ("roles", "savedQueries"):
                if key in override:
                    effective[key].update(copy.deepcopy(override[key]))
            if "dispatchPolicy" in override:
                effective["dispatchPolicy"] = copy.deepcopy(override["dispatchPolicy"])
            _validate_registry(effective)
            override_active = True
        except (OSError, json.JSONDecodeError, ValueError):
            override_error = "The workspace Tracker+ registry override is invalid and was ignored."
    encoded = json.dumps(effective, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    registry_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]
    return effective, override_active, override_error, registry_hash
