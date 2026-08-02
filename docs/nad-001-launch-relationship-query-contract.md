# NAD-001: Public relationship and saved-query contract

## Status

Accepted. This document describes only the extension's public, workspace-
neutral architecture. Installation-specific workflow, role, launch, route,
and dispatch policy belongs in external JSON configuration.

## Context

Tracker+ needs bounded predicate queries and graph traversal without adding a
tracker write path. Native tracker rows remain authoritative. Relationship
records are stored once as source, type, target, lifecycle, and optional role
qualifiers; inverse labels and backlinks are projections.

Hard-coded workspace identities or workflow policy would make the public
extension unsafe to reuse. Therefore the implementation separates:

- a locked structural registry for schema vocabulary and safety caps;
- an overridable policy registry for roles, terminal states, and dispatch
  eligibility values;
- a dedicated saved-query catalog that an installation can add to, replace,
  or disable without rebuilding the extension.

## Decisions

### Native data remains authoritative

The Python reader opens SQLite with `mode=ro` and enables `query_only`. It does
not repair tracker rows, register tracker schemas, or accept SQL/database paths
from callers.

### Relationships are lossless and role-aware

Normalization preserves native relationship IDs and lifecycle states. Semantic
identity contains source, relationship type, target, `scopeRole`, and
`contributionRole`. Role-distinct parallel relationships survive. Exact
selected duplicates produce deterministic findings.

Traversal excludes retired, archived, filtered, and out-of-boundary evidence
before duplicate validation. Current endpoint rows override stale embedded
metadata. Unresolved selected roots or endpoints are terminal.

### Queries are externally managed

Bundled templates live in `reader/saved-queries.json`, separate from code and
the structural registry. An installation manages its effective catalog with:

`.nimbalyst/tracker-plus.queries.json`

The file has this form:

```json
{
  "version": 1,
  "queries": {
    "workspace-ready-items": {
      "version": 1,
      "kind": "predicate",
      "params": [],
      "label": "Workspace-defined ready items",
      "definition": {
        "where": { "field": "status", "op": "eq", "value": "ready" }
      }
    },
    "launch-open-reviews": null
  }
}
```

An object adds or replaces a query by ID. `null` disables a bundled query. The
reader validates the whole catalog before activation; an invalid file is
ignored without partially changing the effective registry. The effective
catalog contributes to `registryHash` and every result echoes the selected
query ID, version, parameters, expanded definition, and query fingerprint.

### Policy is installation-specific

`.nimbalyst/tracker-plus.registry.json` may override terminal statuses, role
aliases/attention tags, saved queries (legacy compatibility), and the complete
dispatch policy object. Relationship vocabulary, scope-role vocabulary,
executable types, caps, and registry version remain locked structural values.

No bundled query or policy contains organization names, private issue keys,
product names, owner identities, launch identities, repository paths, or
installation-specific tags.

### Results are bounded and auditable

Predicate queries use allowlisted fields/operators, parameterized SQL,
deterministic sorting, opaque cursors, and bounded pages. Traversal uses bounded
breadth-first stages with explicit roots, membership, expansion, boundary,
node-filter, and fail-on rules.

Receipts include pagination/truncation, validation, resolved roots, boundary
rules, schema adapter/fingerprint, registry version/hash, watermark, expanded
parameters, and query fingerprint.

### Dispatch fails closed

The `dispatch-eligible-work-v1` execution mode is a generic engine whose
workflow values come from `dispatchPolicy`. It emits candidates only after
revision-specific QA, scope, dependency, hold, route, custody, survivor, and
collision evidence is complete and admissible.

Any warning, error, unresolved selected edge, incomplete evidence, topology
cycle, or response truncation returns a terminal receipt with no candidates or
launch totals.

Candidate ordering is:

1. cleared hard-dependency topology;
2. launch and item critical-path priority;
3. native priority;
4. active durable `precedes` evidence;
5. stable issue key and ID.

Branch and train values are metadata only and never set execution capacity.

## Safety and compatibility gates

- No tracker/database write API is introduced.
- Query and registry overrides are validated atomically.
- Unknown relationship lifecycle values never become active.
- Locked schema vocabulary cannot be changed by workspace configuration.
- Response and traversal caps remain mandatory.
- Schema adapter and registry versions advance when public semantics change.
- Synthetic fixtures and examples use organization-neutral identities only.
