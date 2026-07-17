# NAD-001: Launch relationships, predicate query, and graph traversal contract

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-07-16 |
| Deciders | Founder (review responses), PM (PRD), code team |
| Source PRD | `C:/Development/PrediClear/nimbalyst-launch-item-relationship-query-requirements.md` (v0.1) |
| Supersedes | Nothing; extends the v0.3.0 tool surface |
| Related | `docs/agent-guide.md`, `docs/security.md`, `docs/timeline-plan.md` |

This document is the implementation contract for the PM's launch-item PRD.
It records every decision, every issue found in the code review that preceded
it, the exact fix criteria, and phase-by-phase instructions precise enough
for any agent to execute without further product input. Where this NAD and
the PRD conflict, this NAD wins; the deltas are founder-approved and listed
in [Decisions](#decisions).

---

## 1. Context

### 1.1 Current state (Tracker+ v0.3.0)

Four read-only MCP tools backed by a Python reader over the native SQLite
database:

| Tool | Purpose |
|---|---|
| `native_tracker_list_comments` | Bounded comment pages, cursor pagination |
| `native_tracker_get_with_comments` | Item orientation plus one comment page |
| `native_tracker_sync_timeline` | Projects items + normalized `timeline-link` edges into a `.ntimeline` document |
| `native_tracker_generate_milestone_report` | Markdown milestone rollup |

Key mechanics already present and reused by this NAD:

- Relationship normalization from `timeline-link` rows with a hardcoded
  type allowlist (`reader/database.py`, `_normalized_relationships`).
- Critical-path analysis with dependency modes, lead/lag, cycle detection.
- Validation-finding machinery (`_finding`) with ~10 finding codes.
- Provenance watermark (`schemaAdapter`, `schemaFingerprint`,
  `projectStateRevision`) and truncation flags.
- Comment cursor pagination (versioned, opaque, base64).
- Safety invariants: read-only SQLite (`PRAGMA query_only`), no
  caller-supplied paths or SQL, bounded responses, confined workspace
  writes, allowlisted identity output.

### 1.2 What the PRD adds

A first-class `launch` tracker type, an explicit `part-of-launch`
membership relationship, a typed predicate query contract, rooted graph
traversal with boundary nodes, saved role queries, and a launch filter for
the timeline dashboard. Full requirements: FR-1 through FR-26 in the PRD.

### 1.3 Founder decisions from the 2026-07-16 review

1. **Lifecycle enforcement**: flagging violations via validation findings
   is sufficient. No write-path hook. If notification proves insufficient
   in practice, enforcement can be scripted separately later.
2. **Saved queries**: versioned parameterized templates inside the
   extension are acceptable **provided they are editable through an
   AI-friendly mechanism** (a plain data file an agent can edit, not code).
3. **`scopeRole` vs `contributionRole`**: separate fields. Design must not
   preclude a future multi-launch cascade view where launches roll up the
   way milestones roll up today; launch→launch nesting is the mechanism.
4. **Tool architecture**: item read/traverse tools form one consolidated
   family (like the two comment tools); timeline/report tools are a
   distinct document-projection family.
5. **Caps**: initial caps accepted, but the workspace already holds ~1,500
   tracker items. The NAD must define measured thresholds that tell us
   when this approach needs rethinking, and the escalation path (e.g.
   alternative embedded engine) if scale becomes a problem.

---

## 2. Decisions

### D1 — `launch` is a workspace tracker type, not extension code

The `launch` type is registered in the PrediClear workspace as
`.nimbalyst/trackers/launch.json.yaml` (same mechanism as `milestone`).
Tracker+ never defines tracker types; it reads them. The exact YAML is in
[Appendix A](#appendix-a-launchjsonyaml). The extension's job is to
recognize `launch` in projections, queries, traversal, validation, and
rollups.

**Consequence:** Phase 1 has a deliverable in the PrediClear repo, not this
one.

### D2 — `part-of-launch` and the extended relationship registry

`timeline-link` gains two additive fields, `scopeRole` and
`clearingCondition`
([Appendix B](#appendix-b-timeline-link-schema-delta)), and the reader's
relationship registry grows from six types to eleven. `clearingCondition`
is the schema home for the hard-serial clearing signal the adapter already
normalizes and emits — its absence was the schema/adapter contradiction
raised in issue #2, and adding it (optional at the schema level) resolves
that without changing any milestone or dependency semantics.

The registry table below:

| Type | Direction | Blocks? | Notes |
|---|---|---|---|
| `part-of-launch` | member → launch | never | Requires `scopeRole`; `hardness` must be empty; only `status=active` creates membership |
| `governs` | plan/strategy → launch | never | Context only |
| `contributes-to` | work → milestone | via separate dependency | Unchanged; keeps `contributionRole` semantics |
| `reviews` | review → launch/milestone | when required by exit criteria | Unchanged rules |
| `evidences` | evidence → launch/milestone | never | Unchanged |
| `depends-on` | dependent → prerequisite | only `hardness=hard-serial` | Unchanged |
| `precedes` | earlier → later | never | New; ordering display only |
| `enables` | enabler → enabled | never | New; context only |
| `coordinates-with` | either | never | New; nonblocking |
| `implements` | existing | never | Unchanged (legacy) |
| `related` | symmetric | never | Unchanged (legacy) |

`scopeRole` is a **new field**, never normalized from `contributionRole`.
`contributionRole` continues to mean "primary deliverable of a milestone"
and continues to drive milestone math. A `part-of-launch` edge that carries
a `contributionRole` value is a validation error (`scope-role-conflict`).

**Nesting and cascade rollups (founder decision 3):** a launch may be the
*source* of a `part-of-launch` edge whose target is another launch. That is
the explicit nested-launch case. Launch rollups
([D6](#d6--response-envelope-and-derived-launch-rollups)) treat a nested
launch with `scopeRole=core` as one core member whose `derivedProgress`
feeds the parent, which is exactly how a future multi-launch cascade
timeline can roll up without any new schema. No `isParent` flag is needed;
nesting is an explicit edge and stays queryable.

### D3 — Lifecycle rules are validated, never gated

Tracker+ keeps its no-write invariant. The launch lifecycle table in the
PRD (§3) is implemented as validation findings computed during query,
traversal, and timeline sync — not as write-path enforcement. New finding
codes (all `severity=error` unless noted):

| Code | Fires when |
|---|---|
| `launch-key-missing` | `launch` item with empty/blank `launchKey` |
| `launch-key-duplicate` | Two non-archived launches share a `launchKey` |
| `launch-fields-incomplete` | Launch past `draft` missing owner, audience, scopeRevision, entry/exitCriteria, or ≥1 active core member |
| `launch-actual-date-unreleased` | `actualDate` set but status not `released`/`cancelled` |
| `launch-progress-hand-set` (warning) | A stored manual progress value exists on a launch (derived rollups are authority) |

The PRD's acceptance criterion 1 ("can be moved through the lifecycle only
when...") is **amended** to: "an illegal lifecycle state is always visible
as a validation error in every query, traversal, and dashboard response."
If flagging proves insufficient, enforcement becomes a separately scoped
script/automation outside this extension — not a Tracker+ change.

### D4 — Tool families: consolidated graph reads vs document projection

Two new tools join the read family. Final surface (6 tools, two families):

**Graph-read family** (returns data to the caller, writes nothing):

| Tool | Role |
|---|---|
| `native_tracker_list_comments` | existing |
| `native_tracker_get_with_comments` | existing |
| `native_tracker_query` | NEW — predicate queries over items |
| `native_tracker_traverse` | NEW — rooted graph traversal; hybrid via embedded `nodeWhere` predicate |

**Document-projection family** (writes a confined workspace artifact):

| Tool | Role |
|---|---|
| `native_tracker_sync_timeline` | existing, gains `launch` filter param (D8) |
| `native_tracker_generate_milestone_report` | existing |

`query` and `traverse` share one predicate compiler, one response envelope,
one registry, and one cursor format — that is the "consolidated" family
contract. They stay two tools because their inputs and failure modes
differ (a traversal has roots and stages; a query has a clause tree), and
one tool with two disjoint modes is harder to validate and document.

### D5 — Registry file: AI-editable, versioned, no new write surface

All previously hardcoded vocabulary moves into one JSON registry:

- **Bundled defaults**: `reader/registry.json`, shipped in the extension,
  copied to `dist/` by `scripts/copy-reader-assets.mjs`, version-stamped.
- **Workspace override (optional)**: `.nimbalyst/tracker-plus.registry.json`
  in the workspace root, read (never written) by the reader at call time.
  Agents edit it with ordinary file tools — this is the "AI-friendly
  script" requirement satisfied without rebuild/reload and without a new
  extension write path.

Contents ([Appendix C](#appendix-c-registry-schema)): terminal statuses,
relationship-type rules, scope roles, executable types, role→attention-tag
map, and the five required saved query templates.

Merge and safety rules (implement exactly):

1. Both files are schema-validated. An invalid override is **ignored
   entirely** and reported as a `registry-override-invalid` warning finding
   in every response until fixed; the bundled defaults remain in force.
2. Overrides may add or replace saved queries, roles, attention tags, and
   terminal statuses. Overrides may **not** raise caps, add relationship
   types, or change relationship-type rules — those keys are rejected
   (whole override ignored, same finding).
3. Every response watermark carries `registryVersion` (bundled),
   `registryOverrideActive` (bool), and `registryHash` (sha256 of the
   effective merged registry, first 12 hex chars) so results are auditable.

Required saved queries (bundled, parameterized):

| ID | Parameters |
|---|---|
| `role-active-work-and-attention` | `roleId` |
| `launch-scope` | `launchKey` |
| `launch-hard-blockers` | `launchKey` |
| `launch-open-reviews` | `launchKey` |
| `launch-unscheduled-executable-work` | `launchKey` |

Saved queries are invoked as
`{"savedQuery": {"id": "role-active-work-and-attention", "params": {"roleId": "project-manager"}}}`
on either new tool; the expanded definition is echoed in `query` in the
response so callers can see exactly what ran.

### D6 — Response envelope and derived launch rollups

Both new tools return this envelope (no exceptions):

```json
{
  "nodes": [],
  "edges": [],
  "boundaryNodes": [],
  "page": {
    "totalCount": 0,
    "returnedCount": 0,
    "nextCursor": null,
    "truncated": false
  },
  "validation": {
    "state": "pass",
    "findings": [],
    "orphanCount": 0,
    "duplicateCount": 0,
    "cycleCount": 0
  },
  "watermark": {
    "generatedAt": "2026-07-16T00:00:00Z",
    "schemaAdapter": "tracker-items-normalized-timeline-v2",
    "schemaFingerprint": "…",
    "registryVersion": 1,
    "registryOverrideActive": false,
    "registryHash": "…",
    "sourceItemCount": 0,
    "sourceRelationshipCount": 0,
    "durationMs": 0
  },
  "query": {}
}
```

Rules:

- `validation.state` is `pass`, `warn` (warnings only), or `fail` (any
  error finding). Traversal orphans, duplicate active memberships,
  conflicting scope roles, and membership cycles are always `fail`.
- `timeline-link` rows never appear in `nodes`; relationships are always
  in `edges`. `native_tracker_query` returns relationship records as
  `edges` only when `includeRelationshipRecords=true`.
- Launch nodes in any traversal response carry a derived `launchRollup`
  object (never stored, always computed):

```json
{
  "coreMilestonesCompleted": 0, "coreMilestonesTotal": 0,
  "supportingItemsCompleted": 0, "supportingItemsTotal": 0,
  "reviewsCleared": 0, "reviewsTotal": 0,
  "derivedProgress": 0,
  "activeHardBlockers": 0
}
```

  Rollup math: members are grouped by `scopeRole`; `derivedProgress` is the
  mean of core members' effective progress (reuse
  `_effective_deliverable_progress`); a nested launch member contributes
  its own `derivedProgress`. `activeHardBlockers` counts active
  `depends-on` edges with `hardness=hard-serial` whose source is the
  launch or any core/acceptance member and whose prerequisite is not
  terminal.

### D7 — Caps, observability, and the scale rethink threshold

Initial caps (registry-fixed, overrides cannot raise them):

| Cap | Value |
|---|---|
| Query page limit | default 50, max 200 |
| Query clause tree | max depth 5, max 32 clauses, max 64 values per list, max 200-char text term |
| Traversal | max 500 nodes, max 1,000 edges, max depth 4 per stage, max 8 roots |
| Response size | existing `MAX_RESULT_BYTES` (500 KB) |

The workspace holds ~1,500 items today. Note that the current global
timeline snapshot (`maxItems` cap 500) **already cannot cover the whole
repo** — rooted traversal and cursor-paged queries are the fix, not larger
snapshots.

Observability (required, cheap): every response watermark includes
`durationMs`, `sourceItemCount`, and `sourceRelationshipCount`. The backend
already logs `tool.<method> durationMs=…`; keep that.

Benchmarks and the rethink rule (Phase 6 deliverable):

1. Add a fixture generator (`tests/python/make_scale_fixture.py`) that
   builds synthetic workspaces at 1,500 / 5,000 / 20,000 items with a
   realistic 1:1.5 item:link ratio.
2. Record p95 latency for the role-wake saved query and the FFP-1-shaped
   launch traversal at each size in `docs/nad-001-benchmarks.md`.
3. **Green** while p95 query < 250 ms and p95 traversal < 500 ms at the
   5,000-item tier. **Rethink trigger**: either p95 exceeds 1 s at the
   current workspace size, or the workspace passes 20,000 items.
4. Escalation path when triggered, in order — all read-side, because the
   native DB must stay authoritative and untouched (we cannot add indexes
   to it): (a) per-call in-memory index of parsed rows; (b) a rebuildable
   extension-local cache database (PGlite/DuckDB/SQLite-attached) that is
   never authority and can be deleted at any time; (c) only then, a host
   feature request. Any cache must carry the same watermark/fingerprint
   discipline so staleness is visible.

### D8 — Timeline dashboard launch filter and disclosure

- `TimelineDocument.filters` gains optional `launch` (a `launchKey` string).
- The renderer computes the visible subset as: the launch item, all items
  reachable via active `part-of-launch` edges (depth 1), plus context items
  connected to those members by active non-membership edges already in the
  snapshot. Boundary items render with a distinct "boundary" style and are
  excluded from rollup counts.
- The header strip must show: active launch filter (or "all"), member
  count, boundary count, truncation state, validation state, and the
  existing watermark. Most of this exists; add the launch and boundary
  fields.
- Selecting or clearing the filter edits only the `.ntimeline` document —
  never tracker data. (Already structurally true; keep it that way.)
- `native_tracker_sync_timeline` gains an optional `launch` parameter
  (launchKey). When set, the snapshot is built root-first from that launch
  (same membership stage as `native_tracker_traverse`) instead of the
  global most-recent window, which also mitigates TP-05 for launch
  dashboards. Separate launches keep separate `.ntimeline` files.

---

## 3. Issues register

Every issue found in the 2026-07-16 code review. Each fix's acceptance
criteria are tested in the phase listed.

| ID | Location | Problem | Required fix | Acceptance criteria | Phase |
|---|---|---|---|---|---|
| TP-01 | `reader/database.py` `_apply_timeline_analysis` (launch-connectivity walk) and `launchScope` field read in `_timeline_item` | `launchScoped` is inferred from graph connectivity to any milestone — implicit membership the PRD bans | When ≥1 active `part-of-launch` edge exists in the workspace, `launchScoped` must be computed **only** from explicit membership; the connectivity heuristic applies only in fully unmigrated workspaces. Emit `tag-membership-mismatch` (warning) for items tagged `<launchKey>` (case-insensitive) with no active membership link | Fixture with an FFP-1 launch + members: `launchScoped=true` for exactly the linked members; tagged-but-unlinked item yields the warning finding | 3 |
| TP-02 | `reader/database.py` `COMPLETE_WORKFLOWS` (module constant) and the duplicate set inside `_is_active_executable` | Terminal statuses hardcoded in two places; PRD requires a registry | Both read from the effective registry (`terminalStatuses`); delete the duplicated literal set | Adding a status in a workspace override changes completion behavior in a test without any code edit | 0 |
| TP-03 | `reader/database.py` `_normalized_relationships` type allowlist | The six-type literal set rejects all five new PRD types, so any `part-of-launch` row created before the extension update produces `invalid-relationship-type` **errors** in every sync | Allowlist comes from the registry (`relationshipTypes` keys). **Sequencing rule: Phase 0 must be installed before any `part-of-launch` rows are created in the workspace** | Fixture rows of all 11 types normalize with zero `invalid-relationship-type` findings; a genuinely unknown type still errors | 0 |
| TP-04 | `reader/database.py` `_fit_timeline_result` / snapshot `page` | Snapshot has truncation flags but no `totalCount`/`nextCursor`; byte-fit drops items with no continuation | Accepted for the timeline **document** (bounded projection by design). The two new tools must implement `totalCount` + cursor pagination; the sync tool result must keep reporting `truncated` | `native_tracker_query` pages through >200 matches deterministically; consuming all cursors yields exactly `totalCount` distinct IDs | 2 |
| TP-05 | `timeline_snapshot` selection strategy | Global snapshot = most-recently-updated 500 links + endpoints ∪ milestones; at ~1,500 items coverage is partial and invisible to the PM | `launch` param on sync (D8) provides rooted coverage; global mode keeps the cap but the result must state the window (`sourceItemCount` vs `page.returned`) | Launch-rooted sync of a fixture larger than `maxItems` contains 100% of that launch's members | 5 |
| TP-06 | `_normalized_relationships` (`contributionRole == "primary"`) | `contributionRole` drives milestone math; overloading it for launch scope would corrupt rollups | `scopeRole` is a separate field (D2); reader must ignore `contributionRole` on `part-of-launch` edges and emit `scope-role-conflict` (error) if both are set on one | Fixture edge with both fields → error finding; milestone math unchanged in the same fixture | 3 |
| TP-07 | (absent) | No predicate query or traversal capability at all | Build `native_tracker_query` + `native_tracker_traverse` per §4/§5 | PRD acceptance criteria 5–10 pass as automated tests | 2, 4 |
| TP-08 | Owner handling (`_identity_label`) | No canonical role IDs; role queries would depend on display names | Registry `roles` map: `roleId → { ownerAliases[], attentionTags[] }`; query `owner` field matches aliases case-insensitively; saved role query resolves `roleId` through it | Renaming a display alias in the override file redirects the saved query with no code change | 2 |
| TP-09 | `src/timeline/TrackerTimeline.tsx` | No launch/plan filter; no member/boundary distinction | D8 | A `.ntimeline` with `filters.launch` set renders only that launch's subgraph; clearing the filter restores all items; document diff shows only `.ntimeline` changes | 5 |
| TP-10 | `_normalized_relationships` legacy fallback | Legacy edge synthesis is all-or-nothing on the presence of any link row | **Accepted as-is.** The workspace is already link-migrated; document the behavior in the agent guide. No code change | n/a (documented) | 6 |
| TP-11 | (design) | Lifecycle gating impossible without a write path | D3: validation-only, founder-approved | The five D3 finding codes fire on fixtures; no write-path code exists anywhere in the diff | 3 |

---

## 4. Contract: `native_tracker_query`

### 4.1 Input schema

```json
{
  "where": { "all|any|not|field clause": "…" },
  "savedQuery": { "id": "…", "params": { } },
  "sort": [ { "field": "updated", "direction": "desc" } ],
  "limit": 50,
  "cursor": "opaque",
  "includeArchived": false,
  "includeRelationshipRecords": false,
  "includeTotalCount": true
}
```

- Exactly one of `where` or `savedQuery` is required.
- Clause forms: `{"all": [ … ]}`, `{"any": [ … ]}`, `{"not": { … }}`,
  `{"field": "…", "op": "…", "value": …}`.
- The literal string `"$terminalStatuses"` as a value expands to the
  effective registry list. No other `$` tokens exist in v1; an unknown
  `$` token is `QUERY_INVALID`.

### 4.2 Queryable field registry

Implement exactly this table; any other field name is
`FIELD_NOT_QUERYABLE`. "data:" means `json_extract(data, '$.…')` with
`customFields` fallback, matching `_flatten_custom_fields`.

| Field | Source | Operators |
|---|---|---|
| `id` | column | `eq`, `in` |
| `issueKey` | column `issue_key` | `eq`, `in`, `exists` |
| `type` | column | `eq`, `neq`, `in`, `notIn` |
| `typeTags` | column `type_tags` (JSON array) | `contains`, `containsAny`, `containsAll` |
| `title` | data:title | `eq`, `contains` (case-insensitive, bounded) |
| `status` | data:status | `eq`, `neq`, `in`, `notIn` |
| `priority` | data:priority | `eq`, `neq`, `in`, `notIn` |
| `owner` | data:owner (identity object or string) | `eq`, `in` — matches username/name/displayName/gitName case-insensitively; values may be registry role IDs, which expand to that role's `ownerAliases` |
| `tags` | data:tags | `contains`, `containsAny`, `containsAll` |
| `archived` | column | `eq` |
| `launchKey` | data:launchKey | `eq`, `in` |
| `scheduleHealth` | data:scheduleHealth | `eq`, `in` |
| `executionConstraint` | data:executionConstraint | `eq`, `in` |
| `created`, `updated` | columns | `before`, `after`, `exists` |
| `startDate`, `dueDate`, `targetDate`, `forecastDate`, `actualDate` | data:same | `before`, `after`, `exists` |

Implicit clauses added to **every** query (not expressible by callers,
listed in the `query` echo):

1. `workspace = <current>` and `deleted_at IS NULL` — always.
2. `archived = 0` — unless `includeArchived=true`.
3. `type NOT IN (<relationship types from registry>)` — unless
   `includeRelationshipRecords=true`.

### 4.3 Compilation rules (security-critical)

- Validate the entire clause tree against §4.2 **before** touching the
  database. Reject with `QUERY_INVALID` / `FIELD_NOT_QUERYABLE` /
  `OPERATOR_INVALID` / `QUERY_TOO_COMPLEX` naming the offending path
  (e.g. `where.all[2].any[0].field`).
- Compile to a single parameterized SQL statement over `tracker_items`.
  Array operators use `EXISTS (SELECT 1 FROM json_each(…) WHERE …)`.
  Every caller value binds as a `?` parameter. String-concatenating a
  caller value into SQL is prohibited — the existing test
  `test_sql_shaped_tracker_id_is_only_a_value` pattern must be extended
  to the query tool (`test_sql_shaped_query_values_are_only_values`).
- `contains` on `title` compiles to `LIKE` with `ESCAPE '\'` after
  escaping `%`, `_`, and `\` in the caller's term.
- Sort: allowlisted fields `priority` (registry rank order via `CASE`),
  `updated`, `created`, `dueDate`, `title`, `id`. Always append `id ASC`
  as the final key. Cursor = versioned base64 of the last row's sort-key
  values + id, same discipline as `_encode_cursor` (reject foreign or
  reordered cursors with `CURSOR_INVALID`).
- `totalCount` = `COUNT(*)` with the same WHERE, run in the same
  connection.

### 4.4 Result mapping

`nodes` entries reuse the `_timeline_item` projection (stable IDs,
bounded strings, allowlisted identity labels — no raw identity objects,
no comment bodies). Add `type` (= primary type) and, for launches,
`launchKey` and `status`.

---

## 5. Contract: `native_tracker_traverse`

### 5.1 Input schema

```json
{
  "roots": ["LAUNCH-FFP-1"],
  "membership": {
    "relationshipTypes": ["part-of-launch"],
    "direction": "incoming",
    "status": ["active"],
    "maxDepth": 1
  },
  "expand": {
    "relationshipTypes": ["governs", "contributes-to", "reviews", "evidences", "depends-on"],
    "direction": "both",
    "maxDepth": 2,
    "edgeWhere": { "status": ["active"], "hardness": null, "scopeRole": null },
    "externalEndpointBehavior": "boundary"
  },
  "nodeWhere": { "…predicate contract from §4…" },
  "limits": { "maxNodes": 250, "maxEdges": 500 },
  "failOn": { "truncation": false, "validation": false }
}
```

- `roots`: 1–8 issue keys or internal IDs. Unknown root →
  `ROOT_NOT_FOUND`; ambiguous → `ROOT_AMBIGUOUS` (mirror
  `_find_tracker` semantics). A root resolving to an archived item is
  `ROOT_NOT_FOUND` unless `nodeWhere` explicitly permits archived.
- `membership` is optional (defaults exactly as shown when the root is a
  `launch`; empty stage otherwise). `expand` is optional.
- Relationship types must be registry keys; directions are `incoming`,
  `outgoing`, `both`; depths 1–4.

### 5.2 Algorithm (implement exactly)

1. Resolve roots to rows. Load all non-deleted `timeline-link` rows for
   the workspace once; normalize with the same code path as the snapshot
   (one edge builder, no duplication).
2. **Membership stage**: BFS from roots over edges matching
   `membership` (type ∩ allowlist, direction relative to the frontier
   node, edge status ∈ `status`), up to `maxDepth`. Visited nodes =
   **members**. Track visited edge IDs.
3. **Context stage**: BFS from roots ∪ members over `expand` edges with
   `edgeWhere` applied, up to `expand.maxDepth`. Newly reached endpoints
   are **context candidates**.
4. Classify: context candidates that are not members become
   `boundaryNodes` when `externalEndpointBehavior=boundary`; with
   `exclude`, drop them and their dangling edges.
5. Apply `nodeWhere` (same compiler as §4, evaluated in Python over the
   already-loaded rows or as SQL prefilter — either is fine, results must
   be identical) to members and context nodes. Boundary nodes bypass
   `nodeWhere` except the archived rule. A node removed by `nodeWhere`
   drops its incident edges unless both endpoints remain.
6. Deduplicate nodes and edges by stable native ID (a node reached by
   multiple paths appears once). Count duplicates encountered per
   deduplication into `validation.duplicateCount` only when they are
   *duplicate active memberships* (two active `part-of-launch` edges for
   the same member+launch pair) — path-multiplicity is not a finding.
7. Validation pass over the collected subgraph:
   - Edge endpoint missing, deleted, or archived (without the explicit
     retained-evidence case: an archived endpoint is tolerated only on an
     active `evidences` edge with `effectiveRevision` set) →
     `orphan-endpoint` (error), increments `orphanCount`.
   - Duplicate active membership → `duplicate-active-membership` (error).
   - One member with two different active `scopeRole`s for one launch →
     `conflicting-scope-roles` (error).
   - Cycle through `part-of-launch` (including self-loop / launch
     depends-on itself per PRD edge case) → `membership-cycle` (error),
     increments `cycleCount`; reuse the topological-sort approach from
     `_apply_timeline_analysis`.
   - D3 lifecycle findings for every launch node in the result.
   - TP-01 `tag-membership-mismatch` warnings.
8. Enforce `limits`. If exceeded: set `page.truncated=true` and return a
   breadth-first-complete prefix (never a partial depth level without
   flagging), or fail with `RESULT_TRUNCATED` when
   `failOn.truncation=true`.
9. If `failOn.validation=true` and `validation.state=fail`, return
   `VALIDATION_FAILED` (error payload includes the findings) instead of
   data. The `launch-scope` saved query sets both `failOn` flags true —
   that is the PRD's fail-closed launch view.
10. Compute `launchRollup` for every launch node (D6). Zero-result with
    valid roots returns an empty passing envelope — distinguish this from
    unknown roots (PRD edge case).

Determinism requirement: same database state + same input → byte-identical
`nodes`/`edges` ordering (sort nodes by `id`, edges by `id`; BFS frontiers
iterate in sorted order like the existing critical-path code).

---

## 6. Error codes

Existing codes stay. New codes (all returned via `safeErrorResult` shape):

| Code | Meaning |
|---|---|
| `QUERY_INVALID` | Malformed clause tree, unknown token, bad enum |
| `FIELD_NOT_QUERYABLE` | Field not in §4.2 |
| `OPERATOR_INVALID` | Operator not allowed for that field |
| `QUERY_TOO_COMPLEX` | Depth/clause/list caps exceeded |
| `SAVED_QUERY_NOT_FOUND` | Unknown saved query ID |
| `SAVED_QUERY_PARAMS_INVALID` | Missing/invalid template parameters |
| `ROOT_NOT_FOUND` / `ROOT_AMBIGUOUS` | Traversal root resolution |
| `RESULT_TRUNCATED` | Caps hit with `failOn.truncation=true` |
| `VALIDATION_FAILED` | `failOn.validation=true` and state=fail |
| `REGISTRY_INVALID` | Bundled registry unparseable (hard fail; override problems are findings, not errors) |

Error messages never include SQL text, absolute paths, or raw identity
objects (existing `docs/security.md` rules apply unchanged).

---

## 7. Implementation plan

General rules for every phase:

- Follow `CLAUDE.md`: `npm install` once; `npm test` before and after;
  build with `extension_build`; install/iterate with `extension_install` /
  `extension_reload`; verify a representative `.ntimeline` and
  `extension_get_status`. Do not restart Nimbalyst unless asked.
- Never add a tracker/database write path. Never accept workspace paths,
  database paths, or SQL from callers. Keep every response bounded by
  `MAX_RESULT_BYTES`.
- Each phase lands as its own commit(s) on a feature branch with tests
  green. Do not start a phase before the prior phase's exit criteria all
  pass, except Phase 2 which may run in parallel with Phase 1/3 (matches
  the PRD dependency register).

### Phase 0 — Registry extraction (TP-02, TP-03) — SHIP FIRST

*Must be installed before anyone creates a `part-of-launch` row.*

1. Create `reader/registry.json` with the full content of
   [Appendix C](#appendix-c-registry-schema).
2. Add `reader/registry.py`: load bundled file; attempt workspace override
   at `<workspacePath>/.nimbalyst/tracker-plus.registry.json`; validate
   both against the schema rules in D5 (hand-rolled checks, no new
   dependency); expose `effective_registry(workspace_path)` returning the
   merged dict plus `override_active`, `override_error`, and
   `registry_hash`. Cache per (workspace, override mtime).
3. Replace `COMPLETE_WORKFLOWS` and the literal set in
   `_is_active_executable` with registry lookups. Replace the
   relationship-type allowlist in `_normalized_relationships` with
   registry keys.
4. Update `scripts/copy-reader-assets.mjs` to copy `registry.json`; extend
   `scripts/verify-package.mjs` to assert it is present in the package.
5. Tests (`tests/python/test_registry.py`, plus extensions to
   `test_database.py` using the existing `_insert_tracker` /
   `_insert_link` helpers):
   - all 11 relationship types normalize; unknown type still errors;
   - override adds a terminal status and changes `_is_complete` behavior;
   - override attempting to raise a cap or add a relationship type is
     ignored with `registry-override-invalid` surfaced;
   - malformed override JSON → defaults used, finding surfaced.

**Exit criteria:** `npm test` green; a synced fixture timeline containing
`part-of-launch` rows shows zero `invalid-relationship-type` findings;
`verify:package` passes.

### Phase 1 — Workspace schema artifacts (in the PrediClear repo)

1. Add `C:/Development/PrediClear/.nimbalyst/trackers/launch.json.yaml`
   exactly as [Appendix A](#appendix-a-launchjsonyaml).
2. Edit `timeline-link.json.yaml` per
   [Appendix B](#appendix-b-timeline-link-schema-delta) (adds `scopeRole`
   and the optional `clearingCondition` string; nothing removed).
3. Do **not** create the FFP-1 launch or membership links yet — that is
   the Phase 6 migration rehearsal, executed with native
   `tracker_create`/`tracker_update` tools by the operating agent, not by
   Tracker+.

**Exit criteria:** `tracker_list_types` shows `launch`; creating and
deleting a throwaway launch item round-trips its fields; `timeline-link`
items accept `scopeRole` and store a `clearingCondition` on a hard-serial
`depends-on` row.

### Phase 2 — `native_tracker_query`

1. `reader/query.py`: field registry table (§4.2), clause validator with
   path-addressed errors, SQL compiler (§4.3), cursor codec (new
   versioned kind, `"k":"q1"` discriminator so comment cursors are
   rejected cleanly), saved-query template expansion from the registry.
2. `reader/database.py`: `query_items(params)` building the D6 envelope;
   reuse `_timeline_item`, `_fit_*`-style byte bounding (drop tail rows,
   set `truncated`, preserve `nextCursor` correctness by re-encoding from
   the last kept row).
3. `reader/server.py`: route new method `query_items`.
4. TypeScript: add `TOOL_QUERY = 'native_tracker_query'` to
   `src/contracts.ts` (`TOOL_NAMES`, `ReaderMethod`), descriptor +
   param validation in `src/backend.ts` mirroring the existing allowed-key
   pattern (allowed keys: `where`, `savedQuery`, `sort`, `limit`,
   `cursor`, `includeArchived`, `includeRelationshipRecords`,
   `includeTotalCount`).
5. Tests: PRD acceptance 5–7 and 10 as fixtures — role-wake query returns
   owned + attention-tagged nonterminal items across types including
   `launch`, zero `timeline-link` rows; multi-type `in`/`notIn`; cursor
   determinism (mirror `test_cursor_pagination_is_deterministic`);
   `$terminalStatuses` expansion; SQL-shaped values are only values;
   totalCount vs paged-ID reconciliation; complexity-cap rejections.

**Exit criteria:** all §4 tests green; one-call PM wake fixture passes
(PRD Phase-2 exit evidence); `npm test` + build + reload verified against
the live workspace with a real role query.

### Phase 3 — Membership validation (TP-01, TP-06, TP-11)

1. Extend edge normalization: emit `scopeRole` on edges (bounded string,
   must be a registry `scopeRoles` value on `part-of-launch`, else
   `scope-role-invalid` error finding); enforce `hardness` empty and
   `contributionRole` absent on membership edges (`scope-role-conflict`).
2. Implement the D3 lifecycle findings and TP-01 explicit-membership
   switch + `tag-membership-mismatch` in the snapshot pipeline so the
   dashboard shows them too.
3. Add duplicate-active-membership and conflicting-scope-role detection
   (workspace-wide during snapshot; subgraph-wide during traversal).
4. Tests: every D3 finding code, both TP-01 acceptance rows, TP-06 row,
   duplicate/conflict fixtures, nested launch→launch membership fixture
   (valid), membership self-cycle fixture (error).

**Exit criteria:** zero orphans/duplicates/cycles on the clean fixture;
each dirty fixture produces exactly its expected finding code and
`validation.state`.

### Phase 4 — `native_tracker_traverse`

1. `reader/traverse.py` implementing §5 exactly, reusing the Phase-2
   predicate compiler for `nodeWhere` and the Phase-3 validation pass.
2. Wire method `traverse_graph` through `server.py`, `contracts.ts`
   (`TOOL_TRAVERSE = 'native_tracker_traverse'`), and `backend.ts`
   (allowed keys: `roots`, `membership`, `expand`, `nodeWhere`, `limits`,
   `failOn`, `savedQuery`).
3. Saved queries `launch-scope`, `launch-hard-blockers`,
   `launch-open-reviews`, `launch-unscheduled-executable-work` become
   expandable to traversal inputs (registry `kind: "traversal"`).
4. Tests: PRD acceptance 4, 8, 9 — boundary prior-launch not counted as
   member; separate nodes/edges/boundaryNodes with stable-ID dedup and
   complete watermark; hybrid open-reviews and critical-blockers
   fixtures; `failOn` both modes; determinism (two runs, identical
   serialization); unknown vs empty-result roots distinguished; depth and
   cap enforcement with BFS-complete prefix.

**Exit criteria:** all §5 tests green; live `launch-scope` traversal of a
hand-built test launch in the real workspace returns expected members and
a passing validation block.

### Phase 5 — Timeline launch filter (TP-05, TP-09)

1. `src/timeline/types.ts`: `filters.launch?: string`; snapshot `source`
   gains `rootLaunch?` and `membership` counts.
2. `native_tracker_sync_timeline`: optional `launch` param (validated as
   a bounded string key); when present, reader builds the snapshot from
   the membership+context subgraph (§5 stages) instead of the global
   window.
3. `TrackerTimeline.tsx`: launch selector (populated from launch nodes
   present in the snapshot), member/boundary styling, header disclosure
   additions (D8). Preserve `useEditorLifecycle` and Nimbalyst CSS
   variables (`CLAUDE.md` invariant).
4. Tests: `tests/timeline-model.test.mjs` filter math (launch subset,
   boundary exclusion from rollups); Python test for launch-rooted sync
   coverage (TP-05 acceptance); theme-contrast test still green.

**Exit criteria:** TP-05 and TP-09 acceptance rows pass; PRD acceptance
11–12 demonstrated on a real `.ntimeline` (filter changes display only;
separate launch files still work); visual check in light + midnight
themes.

### Phase 6 — Acceptance, benchmarks, migration rehearsal, docs

1. Scale benchmarks per D7; record results in
   `docs/nad-001-benchmarks.md`; state green/rethink verdict.
2. FFP-1 migration rehearsal (read-only from Tracker+'s side): operating
   agent creates the `FFP-1` launch and the membership/governs edges per
   PRD §10 using native tracker tools, then runs `launch-scope` — expected
   members NIM-1550–NIM-1554, `governs` from NIM-1549, M-Alpha as a
   boundary dependency, zero errors. Capture the response as a fixture.
3. Update `docs/agent-guide.md`: two new tools with exact argument docs
   (match the existing per-tool section format), saved-query usage, the
   registry override file and its rules, the fail-closed launch view, and
   the TP-10 legacy-fallback note. Update `docs/security.md` (query
   compilation rules, new error codes) and `docs/compatibility.md`
   (registry file, schema adapter unchanged).
4. Bump extension to 0.4.0; `verify:package`; full `npm test`; build,
   install, `extension_get_status` clean.

**Exit criteria:** all 13 PRD acceptance criteria pass under the D3
amendment (criterion 1 = validation visibility); benchmarks recorded with
an explicit verdict; docs updated; version bumped.

---

## 8. Safety invariants (unchanged, restated as review gates)

Any PR in this program fails review if it:

1. Adds any tracker or database write path (the only writes remain
   explicit, relative, confined, extension-checked `.ntimeline`/`.md`
   outputs).
2. Accepts a workspace path, database path, or SQL text from a caller.
3. Interpolates a caller-supplied string into SQL.
4. Emits absolute paths, raw identity objects, or comment bodies in query
   or traversal responses or logs.
5. Removes a response-size, complexity, or traversal cap, or lets a
   workspace override raise one.
6. Includes deleted records anywhere, or archived records outside the
   explicit retained-evidence rule (§5.2.7) and `includeArchived=true`.
7. Breaks `useEditorLifecycle` or replaces Nimbalyst CSS variables in the
   custom editor.

---

## Appendix A: `launch.json.yaml`

Place at `C:/Development/PrediClear/.nimbalyst/trackers/launch.json.yaml`.

```yaml
type: launch
displayName: Launch
displayNamePlural: Launches
icon: rocket_launch
color: '#0f766e'
modes:
  inline: true
  fullDocument: true
idPrefix: launch
idFormat: ulid
fields:
  - name: title
    type: string
    required: true
    displayInline: true
  - name: launchKey
    type: string
    required: true
    displayInline: true
  - name: status
    type: select
    required: true
    default: draft
    displayInline: true
    options:
      - { value: draft, label: Draft }
      - { value: planned, label: Planned }
      - { value: active, label: Active }
      - { value: in-review, label: In Review }
      - { value: released, label: Released }
      - { value: on-hold, label: On Hold }
      - { value: cancelled, label: Cancelled }
  - name: launchClass
    type: select
    required: true
    displayInline: true
    options:
      - { value: internal, label: Internal }
      - { value: alpha, label: Alpha }
      - { value: feature-preview, label: Feature Preview }
      - { value: beta, label: Beta }
      - { value: ga, label: GA }
      - { value: maintenance, label: Maintenance }
      - { value: other, label: Other }
  - name: owner
    type: user
    required: true
    displayInline: true
  - name: priority
    type: select
    required: false
    displayInline: true
    options:
      - { value: low, label: Low }
      - { value: medium, label: Medium }
      - { value: high, label: High }
      - { value: critical, label: Critical }
  - name: audience
    type: array
    required: true
    displayInline: false
    itemType: string
  - name: environment
    type: array
    required: false
    displayInline: false
    itemType: string
  - name: startDate
    type: string
    required: false
    displayInline: false
  - name: targetDate
    type: string
    required: false
    displayInline: true
  - name: forecastDate
    type: string
    required: false
    displayInline: true
  - name: actualDate
    type: string
    required: false
    displayInline: false
  - name: scheduleHealth
    type: string
    required: false
    displayInline: true
  - name: executionConstraint
    type: string
    required: false
    displayInline: true
  - name: scopeRevision
    type: string
    required: true
    displayInline: false
  - name: entryCriteria
    type: array
    required: false
    displayInline: false
    itemType: object
  - name: exitCriteria
    type: array
    required: false
    displayInline: false
    itemType: object
  - name: successMeasures
    type: array
    required: false
    displayInline: false
    itemType: object
  - name: summary
    type: text
    required: true
    displayInline: true
  - name: tags
    type: array
    required: false
    displayInline: false
    itemType: string
  - name: created
    type: datetime
    required: false
    displayInline: false
    readOnly: true
  - name: updated
    type: datetime
    required: false
    displayInline: false
    readOnly: true
  - name: agentSessions
    type: array
    required: false
    displayInline: false
    itemType: object
statusBarLayout:
  - row:
      - { field: status, width: 160 }
      - { field: launchClass, width: 160 }
      - { field: targetDate, width: 140 }
      - { field: forecastDate, width: 140 }
inlineTemplate: '{icon} {title} {status} {launchClass}'
roles:
  title: title
  workflowStatus: status
  priority: priority
  assignee: owner
  tags: tags
sync:
  mode: shared
  scope: project
```

Notes: `entryCriteria`/`exitCriteria` become required-after-draft via the
D3 `launch-fields-incomplete` finding, because the type system has no
conditional requiredness. `launchKey` immutability after `planned` is
likewise a finding concern (`launch-key-duplicate` covers collisions; an
immutability audit needs history, which is out of read-only scope —
documented limitation).

## Appendix B: `timeline-link` schema delta

Add two additive fields to
`C:/Development/PrediClear/.nimbalyst/trackers/timeline-link.json.yaml`
(nothing is removed):

1. `scopeRole` (after `contributionRole`):

```yaml
  - name: scopeRole
    type: select
    required: false
    displayInline: true
    options:
      - { value: core, label: Core }
      - { value: supporting, label: Supporting }
      - { value: acceptance, label: Acceptance }
      - { value: review, label: Review }
      - { value: evidence, label: Evidence }
      - { value: dependency-only, label: Dependency Only }
```

2. `clearingCondition` (after `hardness`):

```yaml
  - name: clearingCondition
    type: string
    required: false
    displayInline: false
```

`required: false` at the schema level for both. Requiredness on
`part-of-launch` rows is enforced by the reader (`scope-role-invalid`
finding); requiredness of `clearingCondition` on hard-serial
`depends-on` rows is likewise reader-enforced (`hard-serial-controls-missing`
error), because the type system cannot express per-relationship-type
requiredness.

`clearingCondition` closes the schema/adapter contradiction reported in
issue #2: the adapter already normalizes and emits the field on the
relationship edge, but the live schema previously had nowhere to store it,
so a blocked hard-serial dependency could never satisfy the rule. The field
is a plain optional string; the reader reads it (`_normalized_relationships`),
emits it on the normalized edge, and requires it (with an owner) on active
hard-serial `depends-on` edges. An automated contract test
(`tests/python/test_query_traverse.py::
test_registry_relationship_vocabulary_matches_rendering_layer`) asserts the
schema still exposes `clearingCondition` alongside the shared relationship /
scope-role vocabulary, so schema/adapter drift fails the suite rather than
silently dropping the field.

## Appendix C: Registry schema

`reader/registry.json` initial content (bundled defaults; workspace
override at `.nimbalyst/tracker-plus.registry.json` may modify only the
keys marked *overridable*):

```json
{
  "version": 1,
  "terminalStatuses": ["done", "completed", "achieved", "closed", "shipped", "implemented", "released", "cancelled", "retired"],
  "scopeRoles": ["core", "supporting", "acceptance", "review", "evidence", "dependency-only"],
  "executableTypes": ["task", "timeline-item", "devops-item", "prediclear-item", "automation", "mr", "merge-request", "pull-request", "change-request"],
  "relationshipTypes": {
    "part-of-launch": { "membership": true, "canBlock": false, "requiresScopeRole": true, "forbidsHardness": true },
    "governs": { "membership": false, "canBlock": false },
    "contributes-to": { "membership": false, "canBlock": false },
    "reviews": { "membership": false, "canBlock": "exit-criteria" },
    "evidences": { "membership": false, "canBlock": false },
    "depends-on": { "membership": false, "canBlock": "hard-serial" },
    "precedes": { "membership": false, "canBlock": false },
    "enables": { "membership": false, "canBlock": false },
    "coordinates-with": { "membership": false, "canBlock": false },
    "implements": { "membership": false, "canBlock": false },
    "related": { "membership": false, "canBlock": false, "symmetric": true }
  },
  "roles": {
    "project-manager": {
      "ownerAliases": ["project-manager", "pm"],
      "attentionTags": ["needs-pm-attention", "needs-project-manager-attention"]
    }
  },
  "caps": {
    "queryLimitDefault": 50, "queryLimitMax": 200,
    "clauseDepthMax": 5, "clauseCountMax": 32, "listValuesMax": 64, "textTermMax": 200,
    "traverseNodesMax": 500, "traverseEdgesMax": 1000, "traverseDepthMax": 4, "traverseRootsMax": 8
  },
  "savedQueries": {
    "role-active-work-and-attention": {
      "version": 1, "kind": "predicate", "params": ["roleId"],
      "label": "Active owned work and attention requests for one role",
      "definition": {
        "where": { "all": [
          { "field": "archived", "op": "eq", "value": false },
          { "field": "status", "op": "notIn", "value": "$terminalStatuses" },
          { "any": [
            { "field": "owner", "op": "eq", "value": "{roleId}" },
            { "field": "tags", "op": "containsAny", "value": "{roleId.attentionTags}" }
          ] }
        ] },
        "sort": [
          { "field": "priority", "direction": "desc" },
          { "field": "updated", "direction": "desc" }
        ],
        "limit": 100, "includeTotalCount": true
      }
    },
    "launch-scope": {
      "version": 1, "kind": "traversal", "params": ["launchKey"],
      "label": "Everything in one launch, fail-closed",
      "definition": {
        "roots": ["{launchKey}"],
        "membership": { "relationshipTypes": ["part-of-launch"], "direction": "incoming", "status": ["active"], "maxDepth": 1 },
        "expand": { "relationshipTypes": ["governs", "contributes-to", "reviews", "evidences", "depends-on"], "direction": "both", "maxDepth": 2, "edgeWhere": { "status": ["active"] }, "externalEndpointBehavior": "boundary" },
        "limits": { "maxNodes": 500, "maxEdges": 1000 },
        "failOn": { "truncation": true, "validation": true }
      }
    },
    "launch-hard-blockers": {
      "version": 1, "kind": "traversal", "params": ["launchKey"],
      "label": "Active hard-serial blockers of one launch",
      "definition": {
        "roots": ["{launchKey}"],
        "membership": { "relationshipTypes": ["part-of-launch"], "direction": "incoming", "status": ["active"], "maxDepth": 1 },
        "expand": { "relationshipTypes": ["depends-on"], "direction": "both", "maxDepth": 2, "edgeWhere": { "status": ["active"], "hardness": ["hard-serial"] }, "externalEndpointBehavior": "boundary" },
        "limits": { "maxNodes": 500, "maxEdges": 1000 },
        "failOn": { "truncation": true, "validation": false }
      }
    },
    "launch-open-reviews": {
      "version": 1, "kind": "traversal", "params": ["launchKey"],
      "label": "Open reviews inside one launch",
      "definition": {
        "roots": ["{launchKey}"],
        "membership": { "relationshipTypes": ["part-of-launch"], "direction": "incoming", "status": ["active"], "maxDepth": 1 },
        "expand": { "relationshipTypes": ["reviews"], "direction": "both", "maxDepth": 2, "edgeWhere": { "status": ["active"] }, "externalEndpointBehavior": "boundary" },
        "nodeWhere": { "all": [ { "field": "status", "op": "notIn", "value": "$terminalStatuses" } ] },
        "limits": { "maxNodes": 500, "maxEdges": 1000 },
        "failOn": { "truncation": true, "validation": false }
      }
    },
    "launch-unscheduled-executable-work": {
      "version": 1, "kind": "traversal", "params": ["launchKey"],
      "label": "Launch members that are executable but unscheduled",
      "definition": {
        "roots": ["{launchKey}"],
        "membership": { "relationshipTypes": ["part-of-launch"], "direction": "incoming", "status": ["active"], "maxDepth": 1 },
        "nodeWhere": { "all": [
          { "field": "status", "op": "notIn", "value": "$terminalStatuses" },
          { "not": { "field": "dueDate", "op": "exists", "value": true } },
          { "not": { "field": "forecastDate", "op": "exists", "value": true } }
        ] },
        "limits": { "maxNodes": 500, "maxEdges": 1000 },
        "failOn": { "truncation": false, "validation": false }
      }
    }
  }
}
```

*Overridable keys*: `terminalStatuses`, `roles`, `savedQueries` (add or
replace whole entries; replaced entries must bump their `version`).
*Locked keys*: `relationshipTypes`, `scopeRoles`, `executableTypes`,
`caps` — an override touching these is rejected whole with the
`registry-override-invalid` finding.

Template substitution: `{param}` substitutes the validated parameter
value; `{roleId.attentionTags}` substitutes the registry role's attention
tag list; parameters are validated as bounded strings
(`^[a-z0-9][a-z0-9-]{0,63}$` for `roleId`, length ≤ 100 for `launchKey`)
before substitution, and substitution happens on the parsed JSON tree,
never by string concatenation.

## Appendix D: PRD deltas accepted in this NAD

1. Acceptance criterion 1 amended to validation visibility (D3, founder
   decision 1).
2. Saved queries live in the extension registry with a read-only
   workspace override, not tracker records (D5, founder decision 2 and
   PRD open decision 1).
3. PRD open decision 2 resolved: `scopeRole` is a new field;
   `contributionRole` untouched (D2).
4. PRD open decision 3 resolved: lifecycle labels verbatim in the type
   YAML; terminal states registered centrally (D2/D5).
5. PRD open decision 4 resolved: separate traversal tool inside a
   consolidated read family sharing contracts (D4, founder decision 4).
6. PRD open decision 5 resolved: provisional caps adopted; measured
   rethink thresholds and escalation path defined (D7, founder
   decision 5).
7. FFP-1 migration execution is performed with native tracker tools; 
   Tracker+ provides only the read-only rehearsal and validation (PRD
   LQ-DEP-05 read-only mapping).
