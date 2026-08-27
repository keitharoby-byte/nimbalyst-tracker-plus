# NAD-001: Public relationship and saved-query contract

## Status

Accepted; amended for Tracker+ 0.16.0. This document describes only the
extension's public, workspace-neutral architecture. Installation-specific
workflow, role, launch, route, dispatch policy, and saved queries belong in
external JSON configuration.

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
- a dedicated saved-query catalog that completely defines each workspace's
  runtime query inventory without rebuilding the extension.

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

Inline relationships are synthesized only from active item rows. An archived
item's retained inline fields are historical data and never become active graph
edges. Explicit stored relationship rows remain independently authoritative;
an active stored edge with an unavailable endpoint still fails closed.

Legacy inline fields may be wrapped in nested `customFields` mappings. The
reader unwraps a bounded, cycle-safe chain for compatibility: top-level and
nearer values take precedence, deeper values only fill missing keys, and the
existing stable relationship tuple removes exact duplicates. Exceeding the
nesting bound fails closed instead of silently dropping or partially trusting
the relationship.

The default compatibility bound is 128 envelopes. A caller may select
`maxCustomFieldsDepth` from 1 through the locked maximum of 512 for timeline,
report, query, and traversal operations. This storage-envelope bound is
independent of membership/expansion `maxDepth`: increasing it can reveal
already-stored fields but cannot expand the relationship topology. The
effective value is echoed in the operation receipt.

Rooted timeline projections derive membership from the registered root type.
Launch roots use active incoming `part-of-launch`; milestone and release roots
use active incoming `in-collection`. After the bounded node set is selected,
every stage-eligible relationship whose endpoints are both emitted is included
even when that edge was not needed to discover either endpoint.

Projection receipts partition normalized relationships into emitted,
contract-excluded, endpoint-outside-projection, and result-limit counts. The
partition must reconcile exactly. Unknown relationship types are excluded
during normalization with a named error tied to their known endpoints.

The milestone summary contains emitted, non-boundary milestone members. A
direct milestone member of a release is an interior member rather than
boundary context, so the summary and emitted membership agree.

### Queries are externally managed

Tracker+ ships no active saved queries. The integrity-checked
`reader/saved-queries.json` package asset contains an empty catalog. A
workspace manages its complete effective catalog with:

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
    }
  }
}
```

Every entry must be a complete query object; `null` is invalid. IDs absent from
the file do not exist at runtime. The reader validates the whole catalog before
activation; an absent or invalid file yields an empty saved-query inventory
without partially changing the effective registry. The effective catalog
contributes to `registryHash` and every result echoes the selected query ID,
version, parameters, expanded definition, and query fingerprint.

### Validation follows the declared projection scope

Predicate validation is query-local. It evaluates the selected page and, when
a launch is selected, the bounded membership and dependency context required
to validate that launch. Containers outside that declared scope cannot create
findings. Each query receipt declares scope counts, completeness, and a
deterministic fingerprint. An incomplete scope or a genuine selected-graph
defect remains fail-closed.

### Policy is installation-specific

`.nimbalyst/tracker-plus.registry.json` may override terminal statuses, role
aliases/attention tags, and the complete dispatch policy object. Saved queries
are rejected there to preserve one authoritative catalog. Relationship
vocabulary, scope-role vocabulary, executable types, caps, and registry
version remain locked structural values.

No bundled policy contains organization names, private issue keys, product
names, owner identities, launch identities, repository paths, or
installation-specific tags. The package contains no active saved query.

### Results are bounded and auditable

Predicate queries use allowlisted fields/operators, parameterized SQL,
deterministic sorting, opaque cursors, and bounded pages. Standard traversal
uses bounded breadth-first stages with explicit roots, membership, expansion,
boundary, node-filter, and fail-on rules. When one standard traversal result
exceeds a node, edge, or response page, an opt-in cursor binds continuation to
the selected node/edge identity so agents can aggregate the complete graph
without raising the per-response cap. Dispatch and composed traversals remain
atomic and fail closed instead of paging.

Receipts include pagination/truncation, validation, resolved roots, boundary
rules, schema adapter/fingerprint, registry version/hash, watermark, expanded
parameters, and query fingerprint.

Every query and traversal page includes boolean `hasMore`. It is true exactly
when `nextCursor` is non-null and is identical to `continuationRequired`.
`resultsComplete` is true only when `hasMore` and `truncated` are both false.

Parameter failures for all public tools include a compact usage receipt with
the tool name, required or mutually exclusive selectors, relevant bounds, and
a valid generic example. This guidance augments the stable error code and does
not include workspace paths or tracker data.

### Dispatch fails closed

The `dispatch-eligible-work-v1` execution mode is a generic engine whose
workflow values come from `dispatchPolicy`. It emits candidates only after
revision-specific QA, scope, dependency, hold, route, custody, survivor, and
collision evidence is complete and admissible.

Revision currentness is a logical signal, not a writable field contract. The
receipt derives its accepted sources from the effective registry and exposes
their type and constraint. Similar-looking fields outside that mapping remain
unaccepted; no implicit alias can weaken admission.

Any warning, error, unresolved selected edge, topology cycle, or response
truncation returns a terminal receipt with no candidates or root totals.
Incomplete evidence is terminal by default. A saved query may explicitly set
`failOn.unresolvedEvidence` to `false`; in that mode only incomplete rows are
excluded, their detailed receipts remain visible, and independently complete
candidates retain trustworthy totals.

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
