"""Validated predicate queries shared by Tracker+ query and traversal tools."""

from __future__ import annotations

import base64
import copy
import json
import re
from typing import Any, Mapping

try:
    from .contracts import ReaderError
except ImportError:  # pragma: no cover
    from contracts import ReaderError  # type: ignore[no-redef]

FIELD_OPERATORS: dict[str, set[str]] = {
    "id": {"eq", "in"}, "issueKey": {"eq", "in", "exists"},
    "type": {"eq", "neq", "in", "notIn"},
    "typeTags": {"contains", "containsAny", "containsAll"},
    "title": {"eq", "contains"}, "status": {"eq", "neq", "in", "notIn"},
    "priority": {"eq", "neq", "in", "notIn"}, "owner": {"eq", "in"},
    "tags": {"contains", "containsAny", "containsAll"}, "archived": {"eq"},
    "launchKey": {"eq", "in"}, "scheduleHealth": {"eq", "in"},
    "executionConstraint": {"eq", "in"},
    "walkStage": {"eq", "neq", "in", "notIn", "exists"},
    "buildState": {"eq", "neq", "in", "notIn", "exists"},
    "readiness": {"eq", "neq", "in", "notIn", "exists"},
    "created": {"before", "after", "exists"}, "updated": {"before", "after", "exists"},
    "startDate": {"before", "after", "exists"}, "dueDate": {"before", "after", "exists"},
    "targetDate": {"before", "after", "exists"}, "forecastDate": {"before", "after", "exists"},
    "actualDate": {"before", "after", "exists"},
}
SORT_FIELDS = {"priority", "updated", "created", "dueDate", "title", "id"}
DATA_FIELDS = {
    "title", "status", "priority", "owner", "tags", "launchKey", "scheduleHealth",
    "executionConstraint", "startDate", "dueDate", "targetDate", "forecastDate", "actualDate",
}
ROLE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SCOPE_MECHANISM_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _bounded_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and len(value) <= 16
        and all(isinstance(entry, str) and 0 < len(entry.strip()) <= 100 for entry in value)
        and len({entry.strip().casefold() for entry in value}) == len(value)
    )


def resolve_dispatch_scope_policy(
    definition: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and normalize externally managed dispatch scope semantics."""
    configured = definition.get("scopePolicy")
    dispatch_policy = registry["dispatchPolicy"]
    if configured is None:
        return {
            "version": 1,
            "source": "built-in-default",
            "rootTypes": ["launch", "milestone"],
            "implicitRootSelection": "all-eligible",
            "ancestryDepth": registry["caps"]["traverseDepthMax"],
            "mechanisms": [
                {
                    "id": "launch-membership",
                    "relationshipType": "part-of-launch",
                    "direction": "outgoing",
                    "authority": "authoritative",
                    "scopeRoles": list(dispatch_policy["membershipRoles"]),
                },
                {
                    "id": "milestone-contribution",
                    "relationshipType": "contributes-to",
                    "direction": "outgoing",
                    "authority": "authoritative",
                    "contributionRoles": list(dispatch_policy["contributionRoles"]),
                },
            ],
        }

    expected_keys = {
        "version", "rootTypes", "implicitRootSelection", "ancestryDepth", "mechanisms",
    }
    if not isinstance(configured, Mapping) or set(configured) != expected_keys:
        raise ReaderError(
            "DISPATCH_SCOPE_CONFIG_INVALID",
            "scopePolicy must contain version, rootTypes, implicitRootSelection, ancestryDepth, and mechanisms.",
            {"path": "definition.scopePolicy"},
        )
    root_types = configured.get("rootTypes")
    if (
        configured.get("version") != 1
        or not isinstance(root_types, list)
        or not 1 <= len(root_types) <= registry["caps"]["traverseRootsMax"]
        or not all(isinstance(value, str) and 0 < len(value.strip()) <= 64 for value in root_types)
        or len({value.strip().casefold() for value in root_types}) != len(root_types)
        or any(value.strip().casefold() == "timeline-link" for value in root_types)
    ):
        raise ReaderError(
            "DISPATCH_SCOPE_CONFIG_INVALID",
            "scopePolicy rootTypes must be a bounded unique list of non-relationship item types.",
            {"path": "definition.scopePolicy.rootTypes"},
        )
    implicit = configured.get("implicitRootSelection")
    if implicit not in {"all-eligible", "require-explicit"}:
        raise ReaderError(
            "DISPATCH_SCOPE_CONFIG_INVALID",
            "scopePolicy implicitRootSelection must be all-eligible or require-explicit.",
            {"path": "definition.scopePolicy.implicitRootSelection"},
        )
    depth = configured.get("ancestryDepth")
    if (
        isinstance(depth, bool)
        or not isinstance(depth, int)
        or not 1 <= depth <= registry["caps"]["traverseDepthMax"]
    ):
        raise ReaderError(
            "DISPATCH_SCOPE_CONFIG_INVALID",
            "scopePolicy ancestryDepth exceeds the bounded traversal depth.",
            {"path": "definition.scopePolicy.ancestryDepth"},
        )
    mechanisms = configured.get("mechanisms")
    if not isinstance(mechanisms, list) or not 1 <= len(mechanisms) <= 8:
        raise ReaderError(
            "DISPATCH_SCOPE_CONFIG_INVALID",
            "scopePolicy mechanisms must be a bounded non-empty array.",
            {"path": "definition.scopePolicy.mechanisms"},
        )
    normalized_mechanisms: list[dict[str, Any]] = []
    mechanism_ids: set[str] = set()
    mechanism_routes: set[tuple[str, str]] = set()
    registered_relationships = set(registry["relationshipTypes"])
    registered_scope_roles = set(registry["scopeRoles"])
    registered_contribution_roles = set(dispatch_policy["contributionRoles"])
    for index, mechanism in enumerate(mechanisms):
        path = f"definition.scopePolicy.mechanisms[{index}]"
        if not isinstance(mechanism, Mapping):
            raise ReaderError("DISPATCH_SCOPE_CONFIG_INVALID", "A scope mechanism is not an object.", {"path": path})
        required = {"id", "relationshipType", "direction", "authority"}
        optional = {"scopeRoles", "contributionRoles"}
        if not required.issubset(mechanism) or set(mechanism) - required - optional:
            raise ReaderError("DISPATCH_SCOPE_CONFIG_INVALID", "A scope mechanism has missing or unknown fields.", {"path": path})
        mechanism_id = mechanism.get("id")
        relationship_type = mechanism.get("relationshipType")
        direction = mechanism.get("direction")
        authority = mechanism.get("authority")
        scope_roles = mechanism.get("scopeRoles")
        contribution_roles = mechanism.get("contributionRoles")
        route = (str(relationship_type), str(direction))
        if (
            not isinstance(mechanism_id, str)
            or not SCOPE_MECHANISM_ID.fullmatch(mechanism_id)
            or mechanism_id in mechanism_ids
            or relationship_type not in registered_relationships
            or direction not in {"outgoing", "incoming"}
            or authority not in {"authoritative", "fallback"}
            or route in mechanism_routes
            or (scope_roles is not None and contribution_roles is not None)
            or (
                scope_roles is not None
                and (
                    not _bounded_string_list(scope_roles)
                    or not set(scope_roles).issubset(registered_scope_roles)
                )
            )
            or (
                contribution_roles is not None
                and (
                    not _bounded_string_list(contribution_roles)
                    or not set(contribution_roles).issubset(registered_contribution_roles)
                )
            )
        ):
            raise ReaderError(
                "DISPATCH_SCOPE_CONFIG_INVALID",
                "A scope mechanism is invalid or overlaps another mechanism route.",
                {"path": path},
            )
        mechanism_ids.add(mechanism_id)
        mechanism_routes.add(route)
        normalized = {
            "id": mechanism_id,
            "relationshipType": relationship_type,
            "direction": direction,
            "authority": authority,
        }
        if scope_roles is not None:
            normalized["scopeRoles"] = list(scope_roles)
        if contribution_roles is not None:
            normalized["contributionRoles"] = list(contribution_roles)
        normalized_mechanisms.append(normalized)
    if not any(value["authority"] == "authoritative" for value in normalized_mechanisms):
        raise ReaderError(
            "DISPATCH_SCOPE_CONFIG_INVALID",
            "scopePolicy requires at least one authoritative mechanism.",
            {"path": "definition.scopePolicy.mechanisms"},
        )
    return {
        "version": 1,
        "source": "workspace-query",
        "rootTypes": [value.strip() for value in root_types],
        "implicitRootSelection": implicit,
        "ancestryDepth": depth,
        "mechanisms": normalized_mechanisms,
    }


def expand_saved_query(saved: Any, registry: Mapping[str, Any], expected_kind: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(saved, Mapping) or not isinstance(saved.get("id"), str):
        raise ReaderError("SAVED_QUERY_PARAMS_INVALID", "savedQuery requires an id and params object.")
    query = registry["savedQueries"].get(saved["id"])
    accepted_kinds = {expected_kind}
    if expected_kind == "traversal":
        accepted_kinds.add("composed")
    if not isinstance(query, Mapping) or query.get("kind") not in accepted_kinds:
        raise ReaderError("SAVED_QUERY_NOT_FOUND", "The requested saved query does not exist for this tool.")
    params = saved.get("params", {})
    required_params = set(query.get("params", []))
    optional_params = set(query.get("optionalParams", []))
    if (
        not isinstance(params, Mapping)
        or not required_params.issubset(params)
        or set(params) - required_params - optional_params
    ):
        raise ReaderError("SAVED_QUERY_PARAMS_INVALID", "Saved query parameters are missing or unexpected.")
    clean: dict[str, Any] = {}
    for name in [*query.get("params", []), *query.get("optionalParams", [])]:
        if name not in params:
            continue
        value = params.get(name)
        if name in {"launchKeys", "rootKeys"}:
            if (
                not isinstance(value, list)
                or not 1 <= len(value) <= registry["caps"]["traverseRootsMax"]
                or not all(isinstance(entry, str) and entry.strip() and len(entry.strip()) <= 100 for entry in value)
            ):
                raise ReaderError("SAVED_QUERY_PARAMS_INVALID", f"{name} must be a bounded non-empty string array.")
            normalized = [entry.strip() for entry in value]
            if len({entry.casefold() for entry in normalized}) != len(normalized):
                raise ReaderError("SAVED_QUERY_PARAMS_INVALID", f"{name} must not contain duplicates.")
            clean[name] = normalized
            continue
        if name == "includeUnscoped":
            if not isinstance(value, bool):
                raise ReaderError("SAVED_QUERY_PARAMS_INVALID", "includeUnscoped must be a boolean.")
            clean[name] = value
            continue
        if not isinstance(value, str) or not value.strip():
            raise ReaderError("SAVED_QUERY_PARAMS_INVALID", f"Saved query parameter {name} must be a non-empty string.")
        value = value.strip()
        if name == "roleId":
            if not ROLE_ID.fullmatch(value) or value not in registry["roles"]:
                raise ReaderError("SAVED_QUERY_PARAMS_INVALID", "roleId is not registered.")
        elif name == "launchKey" and len(value) > 100:
            raise ReaderError("SAVED_QUERY_PARAMS_INVALID", "launchKey must be at most 100 characters.")
        clean[name] = value

    def substitute(value: Any) -> Any:
        if isinstance(value, str):
            match = re.fullmatch(r"\{([A-Za-z][A-Za-z0-9]*)(?:\.attentionTags)?\}", value)
            if not match:
                return value
            name = match.group(1)
            if value.endswith(".attentionTags}"):
                return list(registry["roles"][clean[name]]["attentionTags"])
            return clean[name]
        if isinstance(value, list):
            return [substitute(entry) for entry in value]
        if isinstance(value, dict):
            return {key: substitute(entry) for key, entry in value.items()}
        return value

    definition = substitute(copy.deepcopy(query["definition"]))
    echo = {"savedQuery": {"id": saved["id"], "version": query["version"], "params": clean}, "expanded": definition}
    return definition, echo


class PredicateCompiler:
    def __init__(self, registry: Mapping[str, Any]) -> None:
        self.registry = registry
        self.caps = registry["caps"]
        self.clauses = 0

    def validate(self, clause: Any, path: str = "where", depth: int = 1) -> None:
        if depth > self.caps["clauseDepthMax"]:
            raise ReaderError("QUERY_TOO_COMPLEX", f"{path} exceeds the clause depth cap.", {"path": path})
        if not isinstance(clause, Mapping):
            raise ReaderError("QUERY_INVALID", f"{path} must be a clause object.", {"path": path})
        self.clauses += 1
        if self.clauses > self.caps["clauseCountMax"]:
            raise ReaderError("QUERY_TOO_COMPLEX", f"{path} exceeds the clause count cap.", {"path": path})
        keys = set(clause)
        branch = keys.intersection({"all", "any", "not"})
        if branch:
            if len(branch) != 1 or keys != branch:
                raise ReaderError("QUERY_INVALID", f"{path} has mixed clause forms.", {"path": path})
            kind = next(iter(branch))
            child = clause[kind]
            if kind == "not":
                self.validate(child, f"{path}.not", depth + 1)
                return
            if not isinstance(child, list) or not child:
                raise ReaderError("QUERY_INVALID", f"{path}.{kind} must be a non-empty array.", {"path": f"{path}.{kind}"})
            for index, entry in enumerate(child):
                self.validate(entry, f"{path}.{kind}[{index}]", depth + 1)
            return
        if keys != {"field", "op", "value"}:
            raise ReaderError("QUERY_INVALID", f"{path} must contain field, op, and value.", {"path": path})
        field = clause["field"]
        op = clause["op"]
        if field not in FIELD_OPERATORS:
            raise ReaderError("FIELD_NOT_QUERYABLE", f"{path}.field is not queryable.", {"path": f"{path}.field"})
        if op not in FIELD_OPERATORS[field]:
            raise ReaderError("OPERATOR_INVALID", f"{path}.op is not allowed for {field}.", {"path": f"{path}.op"})
        value = clause["value"]
        if value == "$terminalStatuses":
            if op not in {"in", "notIn"}:
                raise ReaderError("QUERY_INVALID", f"{path}.value uses a list token with a scalar operator.", {"path": f"{path}.value"})
            return
        if isinstance(value, str) and value.startswith("$"):
            raise ReaderError("QUERY_INVALID", f"{path}.value contains an unknown token.", {"path": f"{path}.value"})
        if op in {"in", "notIn", "containsAny", "containsAll"}:
            if not isinstance(value, list) or not value or len(value) > self.caps["listValuesMax"]:
                raise ReaderError("QUERY_TOO_COMPLEX", f"{path}.value must be a bounded non-empty list.", {"path": f"{path}.value"})
        if field == "title" and op == "contains" and (not isinstance(value, str) or len(value) > self.caps["textTermMax"]):
            raise ReaderError("QUERY_TOO_COMPLEX", f"{path}.value is not a bounded text term.", {"path": f"{path}.value"})

    def compile(self, clause: Mapping[str, Any]) -> tuple[str, list[Any]]:
        if "all" in clause or "any" in clause:
            kind = "all" if "all" in clause else "any"
            compiled = [self.compile(entry) for entry in clause[kind]]
            joiner = " AND " if kind == "all" else " OR "
            return "(" + joiner.join(sql for sql, _ in compiled) + ")", [value for _, values in compiled for value in values]
        if "not" in clause:
            sql, values = self.compile(clause["not"])
            return f"(NOT {sql})", values
        field, op = clause["field"], clause["op"]
        value = clause["value"]
        if value == "$terminalStatuses":
            value = list(self.registry["terminalStatuses"])
        if field == "owner":
            return self._compile_owner(op, value)
        expression = self._field_expression(field)
        if op == "exists":
            return (f"({expression} IS {'NOT ' if value is True else ''}NULL)", [])
        if field in {"typeTags", "tags"}:
            values = value if isinstance(value, list) else [value]
            checks = [f"EXISTS (SELECT 1 FROM json_each(COALESCE({expression}, '[]')) WHERE LOWER(CAST(value AS TEXT)) = LOWER(?))" for _ in values]
            joiner = " AND " if op == "containsAll" else " OR "
            return "(" + joiner.join(checks) + ")", values
        if op == "contains":
            escaped = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            return f"(LOWER(CAST({expression} AS TEXT)) LIKE LOWER(?) ESCAPE '\\')", [f"%{escaped}%"]
        if op in {"in", "notIn"}:
            values = list(value)
            placeholders = ",".join("?" for _ in values)
            operator = "NOT IN" if op == "notIn" else "IN"
            return f"(LOWER(CAST({expression} AS TEXT)) {operator} ({placeholders}))", [str(entry).lower() for entry in values]
        operator = {"eq": "=", "neq": "<>", "before": "<", "after": ">"}[op]
        if isinstance(value, str):
            return f"(LOWER(CAST({expression} AS TEXT)) {operator} ?)", [value.lower()]
        return f"({expression} {operator} ?)", [value]

    def _compile_owner(self, op: str, raw: Any) -> tuple[str, list[Any]]:
        values = raw if isinstance(raw, list) else [raw]
        aliases: list[str] = []
        for value in values:
            if isinstance(value, str) and value in self.registry["roles"]:
                aliases.extend(self.registry["roles"][value]["ownerAliases"])
            else:
                aliases.append(str(value))
        expressions = [
            "json_extract(data,'$.owner.username')", "json_extract(data,'$.owner.name')",
            "json_extract(data,'$.owner.displayName')", "json_extract(data,'$.owner.gitName')",
            "json_extract(data,'$.customFields.owner.username')", "json_extract(data,'$.customFields.owner.name')",
            "json_extract(data,'$.customFields.owner.displayName')", "json_extract(data,'$.customFields.owner.gitName')",
            "json_extract(data,'$.owner')", "json_extract(data,'$.customFields.owner')",
        ]
        checks: list[str] = []
        params: list[Any] = []
        for alias in aliases:
            checks.append("(" + " OR ".join(f"LOWER(CAST({expr} AS TEXT)) = LOWER(?)" for expr in expressions) + ")")
            params.extend([alias] * len(expressions))
        return "(" + " OR ".join(checks) + ")", params

    @staticmethod
    def _field_expression(field: str) -> str:
        if field == "id": return "id"
        if field == "issueKey": return "issue_key"
        if field == "type": return "type"
        if field == "typeTags": return "type_tags"
        if field == "archived": return "archived"
        if field in {"created", "updated"}: return field
        return f"COALESCE(json_extract(data,'$.{field}'),json_extract(data,'$.customFields.{field}'))"


def validate_sort(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return [{"field": "updated", "direction": "desc"}, {"field": "id", "direction": "asc"}]
    if not isinstance(raw, list) or not 1 <= len(raw) <= 6:
        raise ReaderError("QUERY_INVALID", "sort must be a bounded non-empty array.", {"path": "sort"})
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping) or set(entry) != {"field", "direction"}:
            raise ReaderError("QUERY_INVALID", f"sort[{index}] is invalid.", {"path": f"sort[{index}]"})
        field, direction = entry["field"], entry["direction"]
        if field not in SORT_FIELDS or direction not in {"asc", "desc"} or field in seen:
            raise ReaderError("QUERY_INVALID", f"sort[{index}] is invalid.", {"path": f"sort[{index}]"})
        seen.add(field)
        result.append({"field": field, "direction": direction})
    if "id" not in seen:
        result.append({"field": "id", "direction": "asc"})
    return result


def sort_sql(sort: list[dict[str, str]]) -> str:
    expressions = {
        "priority": "CASE LOWER(CAST(COALESCE(json_extract(data,'$.priority'),json_extract(data,'$.customFields.priority')) AS TEXT)) WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END",
        "dueDate": "COALESCE(json_extract(data,'$.dueDate'),json_extract(data,'$.customFields.dueDate'))",
        "title": "LOWER(CAST(COALESCE(json_extract(data,'$.title'),json_extract(data,'$.customFields.title')) AS TEXT))",
        "updated": "updated", "created": "created", "id": "id",
    }
    return ", ".join(f"{expressions[entry['field']]} {entry['direction'].upper()}" for entry in sort)


def encode_cursor(sort: list[dict[str, str]], row_id: str) -> str:
    payload = {"v": 1, "k": "q1", "s": sort, "id": row_id}
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(raw: Any, sort: list[dict[str, str]]) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ReaderError("CURSOR_INVALID", "cursor must be an opaque query cursor.")
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception:
        raise ReaderError("CURSOR_INVALID", "The query cursor is invalid.") from None
    if not isinstance(payload, dict) or payload.get("v") != 1 or payload.get("k") != "q1" or payload.get("s") != sort or not isinstance(payload.get("id"), str):
        raise ReaderError("CURSOR_INVALID", "The query cursor does not match this query sort.")
    return payload["id"]


def predicate_matches(row: Mapping[str, Any], fields: Mapping[str, Any], clause: Mapping[str, Any], registry: Mapping[str, Any]) -> bool:
    if "all" in clause: return all(predicate_matches(row, fields, entry, registry) for entry in clause["all"])
    if "any" in clause: return any(predicate_matches(row, fields, entry, registry) for entry in clause["any"])
    if "not" in clause: return not predicate_matches(row, fields, clause["not"], registry)
    field, op, expected = clause["field"], clause["op"], clause["value"]
    if expected == "$terminalStatuses": expected = registry["terminalStatuses"]
    if field == "id": actual = row.get("id")
    elif field == "issueKey": actual = row.get("issue_key") or row.get("issueKey")
    elif field == "type": actual = row.get("type") or row.get("primaryType")
    elif field == "typeTags": actual = row.get("typeTags", [])
    elif field == "archived": actual = bool(row.get("archived"))
    elif field in {"created", "updated"}: actual = row.get(field)
    elif field == "owner": actual = fields.get("owner")
    else: actual = fields.get(field)
    if field == "owner":
        values = expected if isinstance(expected, list) else [expected]
        aliases = []
        for value in values:
            aliases.extend(registry["roles"].get(str(value), {}).get("ownerAliases", [value]))
        owner_values = list(actual.values()) if isinstance(actual, Mapping) else [actual]
        return any(str(candidate).lower() == str(alias).lower() for candidate in owner_values for alias in aliases)
    if op == "exists": return (actual is not None) == bool(expected)
    if op in {"contains", "containsAny", "containsAll"}:
        values = expected if isinstance(expected, list) else [expected]
        actual_values = actual if isinstance(actual, list) else [actual]
        hits = [any(str(item).lower() == str(value).lower() for item in actual_values) for value in values]
        return all(hits) if op == "containsAll" else any(hits)
    if op in {"in", "notIn"}:
        hit = any(str(actual).lower() == str(value).lower() for value in expected)
        return not hit if op == "notIn" else hit
    if op == "contains": return str(expected).lower() in str(actual or "").lower()
    if op == "eq": return actual == expected if not isinstance(expected, str) else str(actual).lower() == expected.lower()
    if op == "neq": return not predicate_matches(row, fields, {"field": field, "op": "eq", "value": expected}, registry)
    if op == "before": return actual is not None and str(actual) < str(expected)
    if op == "after": return actual is not None and str(actual) > str(expected)
    return False
