# Tracker+ agent guide

This guide is the operating contract for agents using Tracker+ in a Nimbalyst
workspace. It covers tool selection, exact arguments, durable update flow,
normalized relationships, timeline filters, pull-request references, and safe
failure handling.

## Mental model

Tracker+ has three distinct surfaces:

1. Six `native_tracker_*` tools read the current workspace's native tracker
   database. Comment, predicate-query, and traversal tools return bounded
   context; generation tools write a workspace-relative `.ntimeline` or `.md`
   artifact.
2. Nimbalyst's built-in `tracker_create`, `tracker_update`,
   `tracker_add_comment`, and related tools own durable tracker mutations.
3. A `.ntimeline` file is a replaceable projection. Its view preferences are
   durable, but its `snapshot` is regenerated from native tracker truth.

Never treat a hand-edited snapshot as a tracker update. Change the native item
with a built-in tracker tool, then run `native_tracker_sync_timeline` again.

## Enablement and discovery

- The installed extension is named **Tracker+**.
- Its stable extension ID is declared in `manifest.json`.
- Two backend families are consent-gated. Comment/query/traversal tools may
  require first-use native-code approval; timeline/report generation may also
  require workspace-file approval.
- MCP clients may add a server prefix to tool names. Select the tool whose name
  ends with the documented `native_tracker_*` suffix.
- Every call is automatically scoped to the workspace associated with the
  current AI session. Do not send a workspace path or database path.

If tools are absent, confirm that Tracker+ is enabled and consent has been
granted for the relevant backend family. Main-process logs should show a
four-tool read/query registration and a two-tool projection registration. Use
the extension status and logs before considering a restart; a restart is not
part of normal recovery.

## Recommended operating sequence

1. Orient with `native_tracker_get_with_comments` for the item being changed.
2. Follow `page.nextCursor` with `native_tracker_list_comments` only when more
   discussion is needed.
3. Make the durable change with Nimbalyst's built-in tracker tools.
4. Create or update one normalized `timeline-link` record when topology or
   evidence changes. Never create a second record merely to store a backlink.
5. Run `native_tracker_sync_timeline` to refresh the projection.
6. Inspect Timeline, Graph, Reports, validation findings, and the provenance
   watermark.
7. Generate a milestone report when a durable status artifact is required.

For launch work, after installing or defining a workspace query catalog, begin
with the fail-closed `launch-scope` saved traversal,
then use a role query for the operator's nonterminal work and attention tags.

### Self-guiding parameter errors

Incomplete, conflicting, cursor-invalid, over-complex, and legacy-depth calls
return their stable error code plus `error.details.usage`. The usage block
names the tool, lists required or mutually exclusive arguments and relevant
bounds, and includes one valid generic example. Correct the named parameter;
do not infer a workspace or database path from the example.

## `native_tracker_get_with_comments`

Use this as the default orientation call before changing one item. It returns
the tracker summary and body, one bounded comment page, pagination metadata,
and source provenance. It never mutates tracker data.

```json
{
  "trackerId": "NIM-123",
  "limit": 20,
  "order": "newest"
}
```

Arguments:

- `trackerId` is required and accepts an issue key or internal item ID.
- `limit` defaults to 20 and must be from 1 through 100.
- `cursor` accepts the opaque `page.nextCursor` from a previous call.
- `since` is an optional ISO-8601 lower bound for comment creation time.
- `order` is `newest` or `oldest` and defaults to `newest`.

Use the returned body and comments for context. Use `tracker_update` or
`tracker_add_comment` for any durable response.

The tracker body resolves from the collaborative tracker content when it is
non-empty and otherwise falls back to the item's durable local body snapshot.
`tracker.bodySource` reports which source served the body:
`collaborative-content`, `local-snapshot`, or `empty`. A host-side write
status is not authoritative for body presence — if a body write reported a
collaborative-store failure, read the item back: a non-`empty` `bodySource`
proves the body is durably readable, so do not duplicate it into comments.

## `native_tracker_list_comments`

Use this when only comment history is needed or to continue pagination.

```json
{
  "trackerId": "NIM-123",
  "limit": 50,
  "cursor": "opaque-next-cursor",
  "order": "oldest"
}
```

The argument rules match `native_tracker_get_with_comments`. Stop when
`page.hasMore` is false. Never construct or modify a cursor.

## `native_tracker_query`

Use this for cursor-paged item predicates or a versioned saved query. Exactly
one of `where` and `savedQuery` is required.

```json
{
  "savedQuery": {
    "id": "role-active-work-and-attention",
    "params": { "roleId": "coordinator" }
  }
}
```

Direct predicates use `all`, `any`, `not`, or a field clause:

```json
{
  "where": { "all": [
    { "field": "type", "op": "in", "value": ["task", "launch"] },
    { "field": "status", "op": "notIn", "value": "$terminalStatuses" }
  ] },
  "sort": [{ "field": "updated", "direction": "desc" }],
  "limit": 50,
  "includeTotalCount": true
}
```

`packetId` is available as an exact identifier predicate and on returned item
nodes. Use `eq` for one identifier, `in` for a bounded identifier list, or
`exists` with a boolean value to select items based on presence. The same field
and operators are valid in traversal `nodeWhere` member filters:

```json
{
  "where": { "field": "packetId", "op": "eq", "value": "packet-123" }
}
```

`limit` defaults to 50 and is capped at 200. `cursor` accepts only the opaque
`page.nextCursor` from the same query and sort. When
`page.continuationRequired` is true, repeat the identical request with that
cursor and aggregate the pages until it is false. Do this automatically unless
the user explicitly requested only one page. `page.hasMore` is the same boolean
as `page.continuationRequired`; both are derived from whether `page.nextCursor`
is present. `page.resultsComplete` is true
only when the result set is complete; a response-size-truncated page remains
continuable without weakening the response cap. `includeArchived` and
`includeRelationshipRecords` default to false. Results place items in `nodes`;
requested `timeline-link` records appear only in `edges`. Always inspect the
validation and watermark blocks.

`maxCustomFieldsDepth` defaults to 128 and may be set from 1 through 512 when
legacy fields are stored in deeper envelopes. It controls storage
compatibility only and is echoed under `query`; it does not broaden the query.

Predicate validation is query-local. `query.validationScope` declares the
selected-node, context-node, relationship, and boundary-endpoint counts plus a
deterministic fingerprint. A non-launch result is not affected by launch
containers outside that scope. When a launch is selected, the scope carries
the active membership context needed to validate its lifecycle; unresolved
endpoints, invalid or duplicate relationships, membership cycles, and
incomplete launch fields remain fail-closed.

Endpoint availability is judged against the complete live workspace, not the
bounded scope: a relationship whose other endpoint resolves to a live,
non-archived item outside the current selection is boundary context, counted
in `validationScope.boundaryEndpointCount`, and never raises
`orphan-endpoint`. Only endpoints that resolve to no live item — deleted,
unresolved, or archived — fail closed as `orphan-endpoint` errors.

The watermark includes `schemaDiscovery` for `timeline-item`. State
`missing-with-live-rows` means the rows remain readable but the current
workspace has no matching schema registration. Treat the
`timeline-item-schema-missing-with-live-rows` warning as a migration task:
review the bundled template and register it manually. The receipt explicitly
sets `repair.automaticMutation` to `false`; agents must not infer permission to
write tracker schemas.

## `native_tracker_traverse`

Use traversal for a rooted launch graph with explicit members and boundary
context. Saved launch queries are the preferred contract:

```json
{
  "savedQuery": {
    "id": "launch-scope",
    "params": { "launchKey": "RELEASE-A" }
  }
}
```

Tracker+ ships no active saved queries. The copy-ready workspace catalog
defines `dispatch-eligible-work-v1`, `launch-scope`, `launch-hard-blockers`,
`launch-open-reviews`, and
`launch-unscheduled-executable-work`. A direct call
accepts `roots` (1–8 issue keys, launch keys, or IDs), optional `membership`
and `expand` stages, an optional predicate `nodeWhere`, bounded `limits`, and
`failOn.truncation` / `failOn.validation`. When `membership` is omitted,
container roots receive a default depth-one active incoming membership stage:
`part-of-launch` for `launch` roots and `in-collection` for `milestone` and
`release` roots (combined when roots mix container types). `release` is a
first-class collection container on par with `milestone`; item→container
placement made through the native inline `collection` field traverses as
`in-collection` edges. Membership nodes are returned in
`nodes`; external one-hop context is returned in `boundaryNodes` and excluded
from rollups. `launch-scope` fails closed on truncation or validation errors.
The traversal watermark carries the same `schemaDiscovery` receipt as queries.
The query receipt also includes resolved roots, boundary rules, limits,
declared `failOn` behavior, and a deterministic `queryFingerprint`. Timeline
projections persist schema discovery under `snapshot.source` for UI review.

`maxCustomFieldsDepth` defaults to 128 and may be set from 1 through 512. It is
not the same as `membership.maxDepth` or `expand.maxDepth`: the former unwraps
legacy storage envelopes, while the stage values bound graph topology.

If a standard traversal returns `RESULT_TRUNCATED`, repeat the identical call
with `paginate: true`. In that mode `limits.maxNodes` and `limits.maxEdges` are
safe page sizes. Aggregate `nodes`, `boundaryNodes`, and `edges`, following each
opaque `page.nextCursor` until `page.continuationRequired` is false and
`page.hasMore` is false, then require `page.resultsComplete` to be true. Do not
interpret an individual fragment as a
complete graph. Cursors are bound to the selected graph and fail with
`CURSOR_INVALID` if its node or edge identity changes between pages. Dispatch
and composed traversal modes remain atomic and fail closed; they do not support
pagination.

Every successful traversal page is measured after its cursor, completion,
truncation, and validation-completeness metadata is finalized. If a
`RESPONSE_TOO_LARGE` error reports `details.cause` as `entity`, narrow or repair
the leading entity because it cannot fit on a page. A `fixed-envelope` cause
means the non-entity response metadata itself exceeded the limit. Do not skip a
cursor position to work around either failure.

## Saved query and role search catalog

Saved queries are versioned, parameterized templates from the workspace's
effective Tracker+ query catalog. The integrity-checked bundled catalog is
empty; `.nimbalyst/tracker-plus.queries.json` is the complete authoritative
inventory for that workspace. The
response echoes the template ID, version, validated parameters, and fully
expanded definition under `query`, so callers can audit exactly what ran.
Always inspect `page`, `validation`, and `watermark` before acting on the
result.

### Reader generation receipts

Every source and query/traversal watermark includes `readerBundle`. A packaged
release reports `verificationState: "verified"` together with
`extensionVersion`, `adapterVersion`, `registryVersion`, `generationId`, and
the loaded registry asset paths and SHA-256 hashes. The helper runs from an
immutable snapshot of that generation.

If a live install is temporarily incomplete, Tracker+ retries for a bounded
window and then returns `READER_RESTART_REQUIRED` with the mismatched asset,
expected and actual hashes, and validation cause. The response is terminal and
contains no tracker rows, candidates, counts, or rollups. Reload the extension
and retry; do not edit tracker rows or workspace registry files to recover.

### Role resolution

For a conceptual explanation, common examples, and the difference between
query roles and relationship roles, see [Roles in Tracker+](roles.md). A
ready-to-copy multi-role registry is available at
[`examples/tracker-plus.registry.roles.json`](../examples/tracker-plus.registry.roles.json).

The bundled role catalog currently contains:

| Role ID | Owner aliases | Attention tags |
|---|---|---|
| `coordinator` | `coordinator`, `coordination` | `needs-coordination`, `coordination-requested` |

Role IDs and aliases are matched case-insensitively against string owners and
the native identity fields `username`, `name`, `displayName`, and `gitName`.
Attention tags are also matched case-insensitively. The copy-ready role query uses
this exact logic:

```text
not archived
AND status is not terminal
AND (owner matches any role alias OR tags contain any role attention tag)
```

Deleted rows are always excluded. Archived rows and `timeline-link` records are
also excluded by default. The saved role query independently requires
`archived=false`, so setting `includeArchived=true` does not broaden it. Results
sort by priority descending, updated time descending, then stable ID ascending;
the page limit is 100 and `totalCount` is included.

Copy-paste role inbox query:

```json
{
  "savedQuery": {
    "id": "role-active-work-and-attention",
    "params": { "roleId": "coordinator" }
  }
}
```

This is the normal heartbeat query. Read native comments only for returned
items. To continue, repeat the identical saved query and parameters with the
opaque `page.nextCursor`; never edit or reuse a cursor with another query.

### Copy-ready workspace queries

| Template ID | Tool | Parameter | Selection and safety behavior |
|---|---|---|---|
| `role-active-work-and-attention` | `native_tracker_query` | `roleId` | Nonterminal, non-archived work whose owner matches a role alias **or** whose tags contain a role attention tag. Excludes relationship records by default; returns deterministic cursor pages and a total count. |
| `dispatch-eligible-work-v1` | `native_tracker_traverse` | Optional `roleId`, `launchKeys[]`, `includeUnscoped` | Current, QA-passed, conflict-free task/bug packets across eligible launches. Returns auditable inclusion/exclusion receipts and fails closed on any warning, validation error, unresolved evidence, incomplete required evidence, cycle, or truncation. |
| `launch-scope` | `native_tracker_traverse` | `launchKey` | Active depth-one launch members plus two bounded context levels across `governs`, `contributes-to`, `reviews`, `evidences`, and `depends-on`. External context is returned as boundary nodes. Fails closed on truncation or error-severity validation. |
| `launch-hard-blockers` | `native_tracker_traverse` | `launchKey` | Active `hard-serial` `depends-on` edges around launch members, with external endpoints as boundaries. Fails closed on truncation; validation findings remain visible without failing warning-only runs. |
| `launch-open-reviews` | `native_tracker_traverse` | `launchKey` | Active `reviews` edges for nonterminal launch members, with external endpoints as boundaries. Fails closed on truncation. |
| `launch-unscheduled-executable-work` | `native_tracker_traverse` | `launchKey` | Nonterminal launch members with neither `dueDate` nor `forecastDate`. It does not expand context edges and reports rather than fails on truncation or validation. |

All copy-ready launch traversals use active incoming `part-of-launch` membership at
depth one. Traversal limits are at most 500 nodes and 1,000 edges per response;
in paged standard traversal mode those become page sizes. Boundary nodes are
context, not launch members, and are excluded from launch rollups.

Copy-paste dispatch and launch queries:

```json
{
  "savedQuery": {
    "id": "dispatch-eligible-work-v1",
    "params": {
      "roleId": "coordinator",
      "launchKeys": ["RELEASE-A", "RELEASE-B"],
      "includeUnscoped": false
    }
  }
}
```

```json
{
  "savedQuery": {
    "id": "launch-scope",
    "params": { "launchKey": "RELEASE-A" }
  }
}
```

```json
{
  "savedQuery": {
    "id": "launch-hard-blockers",
    "params": { "launchKey": "RELEASE-A" }
  }
}
```

```json
{
  "savedQuery": {
    "id": "launch-open-reviews",
    "params": { "launchKey": "RELEASE-A" }
  }
}
```

```json
{
  "savedQuery": {
    "id": "launch-unscheduled-executable-work",
    "params": { "launchKey": "RELEASE-A" }
  }
}
```

### Dispatch eligibility contract

`dispatch-eligible-work-v1` inspects native `task` and `bug` rows by default.
Optional `rootKeys` accepts 1–8 unique stable IDs, issue keys, or root keys.
`launchKeys` remains a mutually exclusive compatibility alias. `roleId` uses
the role catalog's owner aliases or configured attention tags, and
`includeUnscoped` is effective only for tracker types listed in
`dispatchPolicy.admittedUnscopedTypes`. Attention tags route a row to a role
but do not infer scope.

Potentially eligible rows resolve `packetRevision`, currentness,
`qaEvidenceRevision`, `qaStatus`, `holdState`, `databaseRouteState`,
`databaseBearing`, `custodyState`, `survivorState`, and `collisionState` through the effective
`dispatchEvidence` mapping. Bundled defaults read the like-named native fields.
QA evidence must match the packet revision. Optional `pullRequestCustody`,
`sessionCustody`, and `worktreeCustody` values are returned without being
interpreted as identity. `failureState` and `supersededBy`, when present, can
only exclude a packet.

Scope comes from the saved query's optional versioned `scopePolicy`. When it is
absent, the compatibility default selects launch and milestone roots through
active outgoing `part-of-launch` core membership or `contributes-to` primary
contribution relationships. A policy can instead declare neutral root types,
implicit root behavior, ancestry depth, relationship direction, role filters,
and authoritative or fallback mechanisms. Fallback paths are considered only
when no authoritative path reaches a selected root.

```json
{
  "scopePolicy": {
    "version": 1,
    "rootTypes": ["release"],
    "implicitRootSelection": "require-explicit",
    "ancestryDepth": 4,
    "mechanisms": [
      {
        "id": "current-collection",
        "relationshipType": "in-collection",
        "direction": "outgoing",
        "authority": "authoritative"
      },
      {
        "id": "historical-launch",
        "relationshipType": "part-of-launch",
        "direction": "outgoing",
        "authority": "fallback",
        "scopeRoles": ["core"]
      }
    ]
  }
}
```

Admission follows qualifying ancestry up to the configured depth, so a direct
item-to-root relationship is not required when a valid transitive path exists.
Retired, archived, filtered, and out-of-boundary relationships are excluded
before duplicate validation. Receipts identify the resolved native roots,
policy fingerprint, mechanism IDs, authority, and stable relationship IDs for
every ancestry hop. Root display-key changes do not change the fingerprint
when native IDs and policy remain unchanged.

Selected launch roots are lifecycle-validated against their actual active
`part-of-launch` graph before candidate admission. That validation graph is
not returned as candidate output: non-dispatch members can prove a launch has
active core membership without becoming candidates or detailed row receipts.

Candidate order is graph-first: cleared hard-serial dependency topology,
launch and item `criticalPathPriority`, native priority, active `precedes`
evidence, then stable issue key/ID. Train and branch fields are returned only
as frontier metadata and never impose an execution-capacity limit.

Every successful result includes:

- ordered candidate `nodes`, detailed receipts for admitted rows, and a compact
  `excluded` subset;
- revision, QA, ancestry, dependency, hold, route, custody,
  survivor/collision, scope-fingerprint, evidence-completeness, and reason
  evidence per detailed row;
- `admission` totals, reason counts, and a stable fingerprint for rows excluded
  before detailed evidence inspection;
- `candidateCount`, source `inspectedCount`, `detailedReceiptCount`,
  `preAdmissionExcludedCount`, truncation, resolved roots, boundary rules,
  schema/registry provenance, watermark, and `queryFingerprint`;
- per-root totals, only after all fail-closed checks pass.

Any warning, error, unresolved selected edge, ordering cycle, or truncation
returns a terminal structured error. Evidence gaps are also terminal when
`failOn.unresolvedEvidence` is omitted or `true`. An explicit `false` excludes
only incomplete rows, preserves `evidenceCompleteness` in their detailed
receipts, and returns independently complete candidates with trustworthy
totals. `query.unresolvedEvidenceDisposition` reports `terminal` or
`exclude-row`. Every `failOn` value must be a JSON boolean.

The full `dispatchPolicy` object is workspace-overridable. Overrides replace
the object, so include every key:

```json
{
  "dispatchPolicy": {
    "dispatchableTypes": ["task", "bug"],
    "readyStatuses": ["ready", "dispatch-ready"],
    "qaPassStatuses": ["passed"],
    "eligibleLaunchStatuses": ["active", "ready", "in-progress"],
    "membershipRoles": ["core"],
    "contributionRoles": ["primary"],
    "admittedUnscopedTypes": ["bug"],
    "admissibleDatabaseRoutes": ["none", "not-required", "ready", "approved"],
    "clearHoldStates": ["clear", "none"],
    "clearCustodyStates": ["clear", "none", "vacant"],
    "survivorStates": ["survivor", "unique"],
    "clearCollisionStates": ["clear", "none"]
  }
}
```

`dispatchEvidence` overrides merge by logical signal with the bundled mapping.
Each source is declarative and allowlisted: a named native `field`, an exact
`tag` mapped to a fixed string/boolean value, a string-valued `tag-prefix`, or
one normalized `relationship` type/direction/state mapped to a fixed value.
No source evaluates workspace code, performs a fuzzy tag match, or infers an
unstated relationship. This minimal override accepts `to-do` workflow rows and
a `qa-signed-off` tag while keeping default sources for every other signal:

```json
{
  "dispatchPolicy": {
    "dispatchableTypes": ["task", "bug"],
    "readyStatuses": ["to-do"],
    "qaPassStatuses": ["passed"],
    "eligibleLaunchStatuses": ["active", "ready", "in-progress", "waiting"],
    "membershipRoles": ["core"],
    "contributionRoles": ["primary"],
    "admittedUnscopedTypes": [],
    "admissibleDatabaseRoutes": ["none", "not-required", "ready", "approved"],
    "clearHoldStates": ["clear", "none"],
    "clearCustodyStates": ["clear", "none", "vacant"],
    "survivorStates": ["survivor", "unique"],
    "clearCollisionStates": ["clear", "none"]
  },
  "dispatchEvidence": {
    "qaStatus": {
      "sources": [{ "kind": "tag", "tag": "qa-signed-off", "value": "passed" }]
    },
    "packetRevision": {
      "sources": [{ "kind": "tag-prefix", "prefix": "packet-revision:" }]
    }
  }
}
```

Mappings are validated and activated atomically. By default, a missing required
logical signal produces `DISPATCH_EVIDENCE_INCOMPLETE` with
`incompleteEvidence[].missingLogicalSignals`, no candidates, and no root
totals. With `failOn.unresolvedEvidence: false`, that row is excluded and its
detailed receipt reports `evidenceCompleteness.state: incomplete`,
`missingFields`, and `missingLogicalSignals`; complete rows remain eligible.
Revision currentness is reported as the logical signal
`revision-currentness`, not as a writable field. Its incomplete receipt lists
the effective configured sources under `acceptedSources`; by default these are
`currentRevision` equal to `packetRevision` or `isCurrentRevision=true`. A
present `revisionCurrentness` near-name field is reported under
`unacceptedFieldsPresent` and is never consumed. Detailed receipts expose every
resolved evidence value and source;
`query.evidenceMapping.fingerprint` and the effective registry hash change with
the mapping. `includeUnscoped=true` with an empty
`admittedUnscopedTypes` list is rejected as `UNSCOPED_WORK_NOT_CONFIGURED`.

`dispatchPosture` is a complete, versioned workspace override. It uses a
closed signal/classification matrix: revision and QA signals remain
`required`; supported operational signals may use only their allowlisted
`required`, `conditional-required`, `positive-blocker`, or `advisory` posture.
Conditional database routing must use the explicit `databaseBearing=true`
predicate. Unknown signals, missing signals, forbidden downgrades, ambiguous
conditions, and conflicting shapes reject the entire override.

```json
{
  "dispatchPosture": {
    "version": 1,
    "signals": {
      "packetRevision": { "classification": "required" },
      "revisionCurrentness": { "classification": "required" },
      "qaEvidenceRevision": { "classification": "required" },
      "qaStatus": { "classification": "required" },
      "holdState": { "classification": "positive-blocker" },
      "databaseRouteState": {
        "classification": "conditional-required",
        "condition": { "signal": "databaseBearing", "op": "eq", "value": true }
      },
      "custodyState": { "classification": "advisory" },
      "survivorState": { "classification": "advisory" },
      "collisionState": { "classification": "advisory" },
      "executionConstraint": { "classification": "advisory" },
      "failureState": { "classification": "positive-blocker" },
      "supersededBy": { "classification": "positive-blocker" }
    }
  }
}
```

Detailed row receipts expose `signalPosture` with the exact classification,
admission/advisory disposition, active state, value, and trusted source. A
conditional signal also records its predicate, match result, and evidence.
`query.dispatchPosture` exposes the effective policy and fingerprint, and the
posture participates in `queryFingerprint`.

### Extending the role catalog

Add workspace-specific roles without rebuilding Tracker+ by creating
`.nimbalyst/tracker-plus.registry.json`. For example:

```json
{
  "roles": {
    "quality-lead": {
      "ownerAliases": ["quality-lead", "qa-lead", "ql"],
      "attentionTags": ["needs-quality-attention", "needs-qa-attention"]
    }
  }
}
```

The existing `role-active-work-and-attention` template immediately accepts
`{"roleId":"quality-lead"}`. Role IDs must match
`^[a-z0-9][a-z0-9-]{0,63}$`. Override roles require at least one owner alias;
the attention-tag array may be empty. Role, saved-query, and dispatch-evidence
entries merge by ID, while `terminalStatuses`, `dispatchPolicy`, and
`dispatchPosture` replace their entire bundled values when present.

Roles are selectors, not permissions or assignments. An owner alias matches
work already owned by that identity; an attention tag includes work requesting
the role's attention regardless of owner. Keep aliases specific to avoid
accidental cross-role matches. See the [role guide](roles.md) for delivery,
quality, security, and documentation examples.

### Managing saved queries without code changes

Create `.nimbalyst/tracker-plus.queries.json` in the workspace. Its `queries`
object replaces the complete runtime saved-query inventory. Query IDs absent
from the file do not exist at runtime, and `null` entries are invalid.

For a copy-ready catalog containing neutral predicate and traversal examples,
start with
[`examples/tracker-plus.queries.json`](../examples/tracker-plus.queries.json).

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
        "where": { "field": "status", "op": "eq", "value": "ready" },
        "sort": [{ "field": "priority", "direction": "desc" }],
        "limit": 100,
        "includeTotalCount": true
      }
    }
  }
}
```

The complete catalog is validated before activation. An invalid catalog is
ignored atomically, no saved queries become active, and the response reports a
registry override warning. The effective query catalog contributes to
`registryHash`, so agents can detect configuration changes. The older
`savedQueries` key in `tracker-plus.registry.json` is rejected; move those
definitions into the dedicated catalog before upgrading.

Catalog entries may use `kind: "predicate"`, `kind: "traversal"`, or
`kind: "composed"`. A composed definition runs a bounded `select` predicate,
uses the selected item IDs as traversal roots, and then applies a typed
`traverse` stage. The root selector must fit within the configured traversal
root cap; otherwise a fail-closed template returns `RESULT_TRUNCATED` without
a partial graph.

Tracker+ 0.9 removes the project-specific `walk-readiness-v1` projection.
Workspace catalogs can still define generic composed queries that select roots
and expand typed dependency or evidence relationships, but the extension does
not derive readiness from `walkStage`, `buildState`, comments, tags, or any
other installation-specific field.

An invalid override is ignored as a whole. No saved queries remain active and
every response reports `registry-override-invalid`. Overrides cannot change
relationship types, scope roles, executable types, caps, or registry version.

### Saved-query failures

- `SAVED_QUERY_NOT_FOUND`: the template ID does not exist for that tool.
- `SAVED_QUERY_PARAMS_INVALID`: parameters are missing, unexpected, malformed,
  or reference an unregistered role.
- `ROOT_NOT_FOUND` / `ROOT_AMBIGUOUS`: a traversal root cannot be resolved
  uniquely in the current workspace.
- `CUSTOM_FIELDS_NESTING_EXCEEDED`: a row contains more legacy storage
  envelopes than this call permits. Retry with a larger
  `maxCustomFieldsDepth` from the returned usage block, up to 512; do not
  change relationship-stage `maxDepth` for this error.
- `RESULT_TRUNCATED`: a fail-closed traversal exceeded its node or edge cap.
- `VALIDATION_FAILED`: a declared traversal validation condition was met, or a
  dispatch run contained any warning/error finding. Dispatch details contain a
  terminal receipt with empty `candidates` and no launch totals.
- `DISPATCH_EVIDENCE_INCOMPLETE`: a potentially eligible packet lacks required
  revision, QA, hold, route, custody, survivor, or collision evidence. The
  terminal receipt exposes missing fields but no candidates or totals.
- `DISPATCH_SCOPE_CONFIG_INVALID`: the configured scope policy is malformed,
  references unsupported values, or declares overlapping mechanisms. Details
  include the exact configuration path.
- `DISPATCH_SCOPE_AMBIGUOUS`: one relationship matches more than one configured
  scope mechanism, so authority cannot be resolved uniquely.
- `DISPATCH_ROOTS_REQUIRED`: the effective policy requires explicit `rootKeys`
  or compatible `launchKeys`, but neither was supplied.
- `UNRESOLVED_EDGE`: a selected relationship endpoint is unavailable.
- `registry-override-invalid`: the workspace override was ignored; this is a
  validation finding rather than a replacement for the bundled registry.

## `native_tracker_sync_timeline`

Use this after native tracker, relationship, schedule, risk, or PR-reference
changes. It writes a bounded projection and preserves the existing document
title, view settings, and state filters.

```json
{
  "outputPath": "planning/Tracker Timeline.ntimeline",
  "includeUnscheduled": true,
  "maxItems": 500,
  "maxCustomFieldsDepth": 128,
  "launch": "RELEASE-A",
  "from": "2026-07-01",
  "to": "2026-09-30"
}
```

Arguments:

- `outputPath` defaults to `Tracker Timeline.ntimeline`. It must be a
  workspace-relative `.ntimeline` path without `..`. Its parent directory must
  already exist.
- `includeUnscheduled` defaults to `true`.
- `maxItems` defaults to 300 and must be from 1 through 500.
- `maxCustomFieldsDepth` defaults to 128 and must be from 1 through 512. Raise
  it only to read deeper historical field envelopes; it does not expand the
  timeline graph.
- `from` and `to` are optional ISO-8601 schedule bounds. Scheduled items are
  included when their start/target interval overlaps the range; undated items
  remain eligible when `includeUnscheduled` is true.
- `launch` is an optional launch key. When present, sync builds a rooted
  membership-plus-context snapshot instead of the global recent-item window.
  Keep separate `.ntimeline` files for separate launches.
- `selector` is an optional generator-only selector and cannot be combined
  with `launch`. Its current contract is `launchTags`, containing 1–8 unique,
  non-empty tags. Values are trimmed, case-normalized, and sorted before use.

For an independent tag-seeded artifact:

```json
{
  "outputPath": "planning/Release Alpha.ntimeline",
  "selector": {
    "launchTags": ["release-a-tag"]
  },
  "includeUnscheduled": true,
  "maxItems": 500
}
```

Tag selection is fail closed. Tracker+ selects all matching active seeds,
discovers every active normalized relationship incident to those seeds, and
includes the opposite endpoints as one-hop boundary context. It does not
expand through boundary nodes. Caps are applied only after the complete
closure is discovered, so the generator never emits a dangling relationship
or a partial capped graph.

The result reports item, milestone, and relationship counts plus truncation and
source metadata. It also includes:

- `validation.state`, total findings, severity counts, and a compact `byCode`
  count map, so callers do not need to reopen the generated file to distinguish
  warnings-only output from hard errors;
- `delta.priorGenerationId` and `delta.currentGenerationId`;
- sorted added/removed node and relationship IDs; and
- prior/current/change milestone counts.

The existing document title, view settings, and filters are retained when the
file is regenerated. Global and launch-rooted sync retain their compatible
receipt behavior. Tag-selected sync refuses to replace the destination if the
selector has no matches, source/closure caps are exceeded, an endpoint cannot
be resolved, error-severity validation is present, or the response would be
truncated.

The tag-selected receipt adds normalized selector values and type, stable seed
IDs, source/closure/emitted item and relationship counts, boundary count,
schema adapter and fingerprint, registry version and hash, project-state
revision, validation counts, generated time, deterministic generation ID and
output hash, and explicit truncation state. The normal sync receipt still
provides the prior/current deltas for the destination file.

The generated snapshot includes:

- independent workflow, schedule health, execution constraint, and risk;
- normalized relationships and inverse display labels;
- primary milestone and launch-scope hydration;
- schedule slack, deterministic risk reasons, and calculated critical path;
- validation findings and projection provenance.

Validation uses `standalone-seed` at info severity when a tagged, non-explicit
launch item is intentionally present without a typed relationship. A genuinely
unrelated item with no tag seed remains `orphan-item` at warning severity;
missing relationship endpoints remain `orphan-endpoint` errors. Launch
traversal includes a selected external prerequisite as a boundary node and the
connecting edge, but does not expand through that boundary into its graph.

## `native_tracker_generate_milestone_report`

Use this for a durable Markdown status report for one milestone or all major
milestones.

```json
{
  "outputPath": "reports/Alpha Milestone Report.md",
  "milestoneId": "NIM-456",
  "asOf": "2026-07-16",
  "lookaheadDays": 45,
  "maxItems": 500,
  "maxCustomFieldsDepth": 128
}
```

Arguments:

- `outputPath` defaults to `Milestone Report.md`. It must be a
  workspace-relative `.md` path whose parent already exists.
- `milestoneId` accepts an issue key or internal ID. Omit it to report every
  milestone.
- `asOf` is an optional ISO-8601 report date and defaults to today.
- `lookaheadDays` defaults to 30 and must be from 1 through 365.
- `maxItems` defaults to 500 and must be from 1 through 500.
- `maxCustomFieldsDepth` defaults to 128 and must be from 1 through 512. The
  effective value is included in the report's source provenance.

Reports derive normalized relationships; they do not emit the obsolete
links/backlinks sections.

## Durable tracker updates

Tracker+ intentionally has no tracker-write API. Use Nimbalyst's built-in
tools after reading current context.

Example metadata and PR update:

```json
{
  "id": "NIM-123",
  "status": "in-review",
  "progress": 85,
  "fields": {
    "scheduleHealth": "at-risk",
    "executionConstraint": "waiting",
    "pullRequestNumber": 42,
    "pullRequestUrl": "https://github.com/example/repository/pull/42"
  }
}
```

Only schema-defined fields can be updated. The supplied `timeline-item` and
`milestone` schemas define `pullRequestNumber` and `pullRequestUrl`. A number is
still displayed when no URL exists. Imported items tagged as pull requests can
also derive both values from a native `github://owner/repository#number`
origin URN.

When all native pull-request fields are absent, a tracker item may declare
cross-repository delivery on its first non-empty body line:

```text
Cross-repo delivery: example/library PR #6 and PR #8
```

Each first reference must name `owner/repository`; later `PR #number`
references inherit the immediately preceding repository. Fully qualified
`https://github.com/owner/repository/pull/number` references are also accepted.
The declaration is one line. Duplicate references, unsupported prose,
multiple declarations, and declarations after other body content fail closed.
Titles, tags, issue-key matches, comments, and commit text remain diagnostic
only. They are never delivery authority.

Timeline items expose the derived result as `deliveryAttribution` with
`authority`, `state`, ordered `references[]`, bounded `validation[]`,
`evidenceSource`, and a deterministic `receiptId`. Native fields suppress body
fallback even when malformed. The receipt is projection evidence only and
does not participate in workflow, readiness, progress, or milestone rollups.

The inspector uses a safely encoded Nimbalyst tracker-reference link. Select it
to open the live tracker item. Agents should use the projected reference rather
than constructing or guessing a `nimbalyst://` URL themselves.

## Normalized relationship records

Store each edge once:

`source item → relationship type → target item`

The target-side backlink and inverse wording are queries over that one record.
To create an edge, use `tracker_create` with type `timeline-link`. Relationship
fields use the internal item ID returned by native tracker tools.

```json
{
  "type": "timeline-link",
  "title": "Implementation task contributes to Alpha milestone",
  "status": "active",
  "fields": {
    "sourceItem": { "itemId": "task_internal_id" },
    "relationshipType": "contributes-to",
    "targetItem": { "itemId": "milestone_internal_id" },
    "directedness": "directed",
    "contributionRole": "primary",
    "effectiveRevision": "project-state-r12"
  }
}
```

Supported relationship types are `part-of-launch`, `in-collection`,
`has-item`, `governs`, `contributes-to`, `reviews`, `evidences`, `depends-on`,
`precedes`, `enables`, `coordinates-with`, `implements`, and `related`.

`in-collection` and its inverse `has-item` mirror the native built-in
`collection` field (item → `milestone` or `release` container). Collection
membership is normally stored inline on each item through that field — the
reader synthesizes a native, non-legacy `in-collection` edge from it
automatically — so prefer the inline field and reserve explicit
`timeline-link` rows of these types for cases the inline field cannot
express. Neither type carries `scopeRole`, `contributionRole`, or
`hardness`, and neither blocks. Archived items do not synthesize inline
relationships; a retained collection value on an archived row is historical
rather than active graph evidence.

For legacy rows, `collection` may appear inside multiple nested
`customFields` mappings. The reader follows a cycle-safe chain of at most 32
envelopes. The nearest value wins when levels conflict, while deeper levels
only fill missing fields. Agents should continue writing the current flat
inline field; nested handling is read compatibility, not a storage pattern.

Additional controls:

- Dependency modes: `finish-to-start`, `start-to-start`,
  `finish-to-finish`, or `start-to-finish`.
- Hardness: `hard-serial`, `shared-resource`, or `soft-coordination`.
- Lifecycle status: `active`, `cleared`, or `superseded`.
- `leadLagDays` accepts -365 through 365.
- `entryEvidence`, `exitEvidence`, and `evidenceSources` are relationship
  fields containing item-reference objects.
- `clearingCondition` is a `timeline-link` string field the adapter reads and
  emits on the normalized edge. A hard-serial dependency with no clearing
  condition fails closed with `hard-serial-controls-missing`.

Rules agents must preserve:

- A hard-serial dependency needs an owner and clearing condition.
- A review edge needs explicit entry and exit evidence.
- A launch-scoped task has exactly one primary milestone contribution.
- Secondary contributions are allowed and do not create another parent.
- `related` may be symmetric; dependency edges remain directed.
- Clear or supersede an existing edge instead of creating inverse truth.
- `part-of-launch` is member → launch, must be active to create membership,
  requires `scopeRole`, and cannot carry `hardness` or `contributionRole`.
- `in-collection` is item → container (`milestone` or `release`); use the
  native inline `collection` field for it whenever possible instead of a
  `timeline-link` row.
- `scopeRole` and milestone `contributionRole` are separate semantics.

## Timeline view filters

The **Filters** menu applies to Timeline and Graph. Completion options are
`Active` and `Complete`; schedule options are `On track`, `At risk`, and
`Late`. Multiple selections are ORed inside a group and the two groups are
ANDed together.

For example, selecting `Active`, `On track`, and `At risk` shows active work
whose schedule is either on track or at risk. Deselecting `Complete` hides
finished work without changing its native status. Filters are document view
preferences, survive re-sync, and never mutate tracker items.

The Launch selector adds a document-only `filters.launch` value. It shows the
launch, active depth-one members, and non-membership context as dashed boundary
nodes. Clearing it restores the whole snapshot; it never edits tracker data.

## Registry overrides

Tracker+ ships structural policy in `reader/registry.json` and an empty,
integrity-checked query catalog in `reader/saved-queries.json`. A workspace may
override `terminalStatuses`, `roles`, and the complete `dispatchPolicy` through
`.nimbalyst/tracker-plus.registry.json`. Roles merge by ID; terminal statuses
and dispatch policy replace their complete defaults. Saved queries exist only
in `.nimbalyst/tracker-plus.queries.json`.

`relationshipTypes`, `scopeRoles`, `executableTypes`, `caps`, and `version` are
locked. Any malformed or locked override is ignored atomically and produces
`registry-override-invalid` until fixed. Every response includes registry
version, effective hash, and override state.

`reader/registry.json` is the canonical source for relationship and scope-role
vocabulary used by tracker validation, adapter
normalization, projection, and rendering. An automated contract test keeps it in
lockstep with the renderer's relationship set and the `timeline-link` schema's
`scopeRole` options, so registry/schema drift fails the suite rather than
silently dropping valid rows.

When any native `timeline-link` exists, legacy relationship synthesis remains
disabled for the whole workspace. This accepted fallback is intentionally
all-or-nothing; migrate relationships before relying on mixed legacy fields.

## Reading validation and critical path

- Workflow describes what the team is doing.
- Schedule health describes forecast credibility.
- Execution constraint describes whether the next action can proceed.
- Risk combines likelihood, impact, evidence, durability, and escalation
  floors.
- Critical path is calculated from durations and active hard-serial edges; it
  is not a manually assigned status.

Treat validation errors as source-data findings, not automatic permission
to rewrite source records. Read the affected items and relationships first.

## Safety and error handling

Do not:

- send database paths, workspace paths, SQL, or absolute output paths;
- edit `dist/` by hand;
- write directly to the SQLite database;
- fabricate dates for undated evidence or completed history;
- duplicate backlinks or infer hierarchy from dependency topology;
- ignore `page.hasMore`, truncation flags, schema fingerprints, or snapshot
  timestamps.

Common recoveries:

- `TRACKER_NOT_FOUND`: confirm the issue key belongs to the current workspace.
- `DATABASE_BUSY`: retry after a short delay.
- `SCHEMA_INCOMPATIBLE`: stop and update the adapter/tests for the new schema.
- `OUTPUT_DIRECTORY_NOT_FOUND`: create the intended workspace directory, then
  retry the same relative output path.
- `QUERY_INVALID`, `FIELD_NOT_QUERYABLE`, or `OPERATOR_INVALID`: correct the
  path-addressed predicate error; never retry by sending SQL.
- `RESULT_TRUNCATED`: for a standard traversal, retry identically with
  `paginate: true` and aggregate through the final cursor; for dispatch or
  composed modes, narrow the graph because they remain atomic. Never treat a
  partial page as complete.
- `VALIDATION_FAILED`: resolve the returned findings; fail-closed launch views
  must not be treated as complete.
- `SELECTOR_NO_MATCH`: correct the requested tag or add it to the intended
  active seed; the existing timeline file was not replaced.
- `SOURCE_LIMIT_EXCEEDED` or `RESULT_LIMIT_EXCEEDED`: narrow the selected tag
  union or raise `maxItems` within the documented cap; the existing file was
  not replaced.
- Missing tools: confirm extension enablement and consent for the relevant
  backend family, then verify the logs contain registrations of four read/query
  tools and two projection tools.

See [troubleshooting.md](troubleshooting.md) for the complete recovery list and
[security.md](security.md) for the trust boundary.

## Development handoff

Code agents must read the repository `CLAUDE.md` and follow this validation
sequence:

```powershell
npm install
npm test
npm run verify:package
```

Build with the Nimbalyst Extension Dev `extension_build` tool, install with
`extension_install`, iterate with `extension_reload`, then inspect extension
status and main/renderer logs. Verify a representative `.ntimeline` file when
an editor is mounted. Do not restart Nimbalyst unless the user explicitly asks.
