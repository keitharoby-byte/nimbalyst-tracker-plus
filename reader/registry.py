"""Versioned Tracker+ vocabulary and saved-query registry."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

try:
    from .contracts import SCHEMA_ADAPTER, ReaderError
except ImportError:  # pragma: no cover
    from contracts import SCHEMA_ADAPTER, ReaderError  # type: ignore[no-redef]

LOCKED_OVERRIDE_KEYS = {"relationshipTypes", "scopeRoles", "executableTypes", "caps", "version"}
OVERRIDABLE_KEYS = {"terminalStatuses", "roles", "savedQueries", "dispatchPolicy"}
BUNDLE_MANIFEST = "bundle-manifest.json"
BUNDLE_FORMAT_VERSION = 1
BUNDLE_DIAGNOSTIC_FILES = ("registry.py", "registry.json", "saved-queries.json")
REQUIRED_CAPS = {
    "queryLimitDefault", "queryLimitMax", "clauseDepthMax", "clauseCountMax",
    "listValuesMax", "textTermMax", "traverseNodesMax", "traverseEdgesMax",
    "traverseDepthMax", "traverseRootsMax",
}
_BUNDLED_CACHE: dict[str, Any] | None = None
_BUNDLED_DIAGNOSTICS: dict[str, Any] | None = None


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
        if query.get("kind") not in {"predicate", "traversal", "composed"}:
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


def _validate_query_catalog(value: Any, *, allow_removal: bool = False) -> bool:
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("version"), int)
        or value["version"] < 1
        or not isinstance(value.get("queries"), dict)
        or set(value) != {"version", "queries"}
    ):
        return False
    queries = value["queries"]
    if allow_removal:
        active = {key: query for key, query in queries.items() if query is not None}
        if not all(isinstance(key, str) and key for key in queries):
            return False
        return _validate_saved_queries(active)
    return _validate_saved_queries(queries)


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_identity(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        return {}
    return {
        "extensionVersion": manifest.get("extensionVersion", "unavailable"),
        "adapterVersion": manifest.get("adapterVersion", "unavailable"),
        "registryVersion": manifest.get("registryVersion", "unavailable"),
        "generationId": manifest.get("generationId", "unavailable"),
    }


def _bundle_details(
    directory: Path,
    manifest: Any,
    cause: str,
    *,
    asset: str | None = None,
    expected_hash: str | None = None,
    actual_hash: str | None = None,
) -> dict[str, Any]:
    details = {
        **_manifest_identity(manifest),
        "manifestPath": str(directory / BUNDLE_MANIFEST),
        "assetPaths": {
            name: str(directory / name)
            for name in BUNDLE_DIAGNOSTIC_FILES
        },
        "validationCause": cause[:300],
    }
    if asset:
        details["assetPath"] = str(directory / asset)
    if expected_hash:
        details["expectedHash"] = expected_hash
    if actual_hash:
        details["actualHash"] = actual_hash
    return details


def _validated_bundle_manifest(directory: Path, *, require_manifest: bool) -> dict[str, Any] | None:
    manifest_path = directory / BUNDLE_MANIFEST
    if not manifest_path.is_file():
        if require_manifest:
            raise ReaderError(
                "READER_RESTART_REQUIRED",
                "Tracker+ could not load one complete reader generation. Reload the extension and retry.",
                _bundle_details(directory, {}, "bundle-manifest-missing"),
            )
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReaderError(
            "READER_RESTART_REQUIRED",
            "Tracker+ could not load one complete reader generation. Reload the extension and retry.",
            _bundle_details(directory, {}, f"bundle-manifest-invalid: {error}"),
        ) from error
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("formatVersion") != BUNDLE_FORMAT_VERSION
        or not isinstance(manifest.get("generationId"), str)
        or not isinstance(manifest.get("extensionVersion"), str)
        or not isinstance(manifest.get("adapterVersion"), int)
        or not isinstance(manifest.get("registryVersion"), int)
        or not isinstance(files, dict)
    ):
        raise ReaderError(
            "READER_RESTART_REQUIRED",
            "Tracker+ could not load one complete reader generation. Reload the extension and retry.",
            _bundle_details(directory, manifest, "bundle-manifest-fields-invalid"),
        )
    for name in BUNDLE_DIAGNOSTIC_FILES:
        expected = files.get(name)
        path = directory / name
        if not isinstance(expected, str) or len(expected) != 64:
            raise ReaderError(
                "READER_RESTART_REQUIRED",
                "Tracker+ could not load one complete reader generation. Reload the extension and retry.",
                _bundle_details(directory, manifest, "asset-hash-missing", asset=name),
            )
        try:
            actual = _sha256(path)
        except OSError as error:
            raise ReaderError(
                "READER_RESTART_REQUIRED",
                "Tracker+ could not load one complete reader generation. Reload the extension and retry.",
                _bundle_details(
                    directory,
                    manifest,
                    f"asset-unavailable: {error}",
                    asset=name,
                    expected_hash=expected,
                ),
            ) from error
        if actual != expected:
            raise ReaderError(
                "READER_RESTART_REQUIRED",
                "Tracker+ could not load one complete reader generation. Reload the extension and retry.",
                _bundle_details(
                    directory,
                    manifest,
                    "asset-hash-mismatch",
                    asset=name,
                    expected_hash=expected,
                    actual_hash=actual,
                ),
            )
    return manifest


def _load_bundle_from(directory: Path, *, require_manifest: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _validated_bundle_manifest(directory, require_manifest=require_manifest)
    try:
        value = json.loads((directory / "registry.json").read_text(encoding="utf-8"))
        catalog = json.loads((directory / "saved-queries.json").read_text(encoding="utf-8"))
        if not _validate_query_catalog(catalog):
            raise ValueError("saved query catalog is invalid")
        value["savedQueries"] = copy.deepcopy(catalog["queries"])
        _validate_registry(value)
        if manifest and manifest["registryVersion"] != value["version"]:
            raise ValueError("registry version does not match bundle manifest")
        diagnostics = {
            **_manifest_identity(manifest or {}),
            "verificationState": "verified" if manifest else "development-unmanifested",
            "manifestPath": str(directory / BUNDLE_MANIFEST),
            "assetPaths": {
                name: str(directory / name)
                for name in BUNDLE_DIAGNOSTIC_FILES
            },
            "assetHashes": {
                name: _sha256(directory / name)
                for name in BUNDLE_DIAGNOSTIC_FILES
            },
        }
        if not manifest:
            diagnostics.update({
                "extensionVersion": "development",
                "adapterVersion": SCHEMA_ADAPTER,
                "registryVersion": value["version"],
                "generationId": "development-unmanifested",
            })
        return value, diagnostics
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise ReaderError(
            "REGISTRY_INVALID",
            "The bundled Tracker+ registry is invalid.",
            _bundle_details(directory, manifest or {}, str(error)),
        ) from error


def _load_bundled() -> dict[str, Any]:
    global _BUNDLED_CACHE, _BUNDLED_DIAGNOSTICS
    if _BUNDLED_CACHE is None:
        require_manifest = os.environ.get("TRACKER_PLUS_REQUIRE_BUNDLE_MANIFEST") == "1"
        _BUNDLED_CACHE, _BUNDLED_DIAGNOSTICS = _load_bundle_from(
            Path(__file__).resolve().parent,
            require_manifest=require_manifest,
        )
    return copy.deepcopy(_BUNDLED_CACHE)


def bundled_diagnostics() -> dict[str, Any]:
    _load_bundled()
    return copy.deepcopy(_BUNDLED_DIAGNOSTICS or {})


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
    override_errors: list[str] = []
    override_path = Path(workspace_path) / ".nimbalyst" / "tracker-plus.registry.json"
    if override_path.is_file():
        try:
            override = json.loads(override_path.read_text(encoding="utf-8"))
            _validate_override(override)
            candidate = copy.deepcopy(effective)
            if "terminalStatuses" in override:
                candidate["terminalStatuses"] = list(override["terminalStatuses"])
            for key in ("roles", "savedQueries"):
                if key in override:
                    candidate[key].update(copy.deepcopy(override[key]))
            if "dispatchPolicy" in override:
                candidate["dispatchPolicy"] = copy.deepcopy(override["dispatchPolicy"])
            _validate_registry(candidate)
            effective = candidate
            override_active = True
        except (OSError, json.JSONDecodeError, ValueError):
            override_errors.append("The workspace Tracker+ registry override is invalid and was ignored.")
    query_override_path = Path(workspace_path) / ".nimbalyst" / "tracker-plus.queries.json"
    if query_override_path.is_file():
        try:
            query_catalog = json.loads(query_override_path.read_text(encoding="utf-8"))
            if not _validate_query_catalog(query_catalog, allow_removal=True):
                raise ValueError("query catalog is invalid")
            candidate = copy.deepcopy(effective)
            for query_id, query in query_catalog["queries"].items():
                if query is None:
                    candidate["savedQueries"].pop(query_id, None)
                else:
                    candidate["savedQueries"][query_id] = copy.deepcopy(query)
            _validate_registry(candidate)
            effective = candidate
            override_active = True
        except (OSError, json.JSONDecodeError, ValueError):
            override_errors.append("The workspace Tracker+ saved-query catalog is invalid and was ignored.")
    encoded = json.dumps(effective, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    registry_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]
    return effective, override_active, " ".join(override_errors) or None, registry_hash
