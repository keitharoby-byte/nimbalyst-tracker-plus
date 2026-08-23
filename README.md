# Nimbalyst Tracker+

Tracker+ is a consent-gated Nimbalyst extension that makes the native
tracker surface easier for people and agents to understand. It combines
readable tracker comments with a Gantt-style timeline, a normalized relationship
graph, critical-path analysis, governance validation, and milestone reports.

This is an independent community extension, not an official Nimbalyst feature.
Version 0.10.0 is Windows-first and was validated with Nimbalyst 0.72.8. Because
the read adapter uses a private SQLite schema, retest after upgrading Nimbalyst.

The extension keeps its original technical ID so existing installations
upgrade in place despite the broader Tracker+ name.

## Screenshots

![Tracker+ current relationship graph with typed-edge legend, launch filter, validation summary, and workflow-aware nodes](docs/screenshots/tracker-plus-theme-audit.png)

<p align="center">
  <img src="docs/screenshots/tracker-plus-light.png" width="49%" alt="Tracker+ 0.7 timeline viewer in the Nimbalyst Light theme">
  <img src="docs/screenshots/tracker-plus-midnight-orchid.png" width="49%" alt="Tracker+ 0.7 timeline viewer in a Nimbalyst dark theme">
</p>

## What's new in 0.10.0

Tracker+ now derives governed cross-repository delivery attribution when an
item has no native pull-request fields. The first non-empty body line must use
the exact label and a repository-qualified declaration:

```text
Cross-repo delivery: example/library PR #6 and PR #8
```

The projection emits a deterministic `deliveryAttribution` receipt with its
authority, evidence source, validation findings, receipt ID, and every PR's
repository, number, and canonical URL. Native `pullRequestNumber` and
`pullRequestUrl` remain authoritative. Unlabeled, non-leading, malformed, or
ambiguous text never becomes attribution evidence, and attribution does not
change workflow, readiness, progress, or milestone rollups. (#38)

This release also includes the query endpoint-liveness fix, native collection
and release support, and durable body fallback completed after 0.9.1.

## Previously in 0.9.1

- Keep predicate and role-query validation local to returned rows and the
  membership context needed by selected launches, with a deterministic scope
  receipt.
- Report accepted configured evidence sources when revision currentness is
  unresolved, without accepting ambiguous near-name fields.

## Previously in 0.9.0

- Make `.nimbalyst/tracker-plus.queries.json` the exact saved-query inventory
  for each workspace; Tracker+ no longer injects default queries.
- Move six neutral, editable query templates into the copy-ready example
  catalog while preserving stable query IDs and generic composed queries.
- Remove the project-specific walk-readiness projection and its dependency on
  optional workflow fields.

## Previously in 0.8.2

- Route dispatch work by configured role attention tags as well as owner
  aliases, preserving detailed fail-closed evidence receipts for routed rows.
- Validate selected launch roots from their actual active membership graph,
  even when workflow, role, or scope admission yields zero candidate rows.

## Previously in 0.8.1

- Retrieve complete predicate results by automatically following explicit
  continuation cursors across ordinary and response-size-truncated pages.
- Retry truncated standard graph traversals with `paginate: true`, then
  aggregate identity-bound node and edge pages without raising the per-response
  safety limit.
- Keep legacy launch tags non-blocking and preserve nested-lane timeline items
  as boundary context while typed relationships remain authoritative.

## Previously in 0.8.0

- Configure the trusted source for every dispatch-evidence signal through
  `.nimbalyst/tracker-plus.registry.json`. Field, exact-tag, tag-prefix, and
  normalized-relationship sources are allowlisted, provenance is included in
  detailed receipts, and invalid mappings are ignored atomically.
- Fail closed when a configured signal is missing, include its logical name in
  the terminal receipt, and reject `includeUnscoped=true` when no unscoped type
  is explicitly admitted.

## Previously in 0.7.0

- Compose a validated root predicate and a typed relationship traversal in one
  bounded saved query. Installers can define composed templates in
  `.nimbalyst/tracker-plus.queries.json` without changing extension code.
- Inspect evidence-backed walk readiness with normalized `walkStage`,
  `buildState`, and `readiness` fields, hard-serial predecessor controls, and
  stored-versus-derived provenance. Missing evidence stays explicitly unknown.
- Keep role inbox queries stable when result rows also contain legacy
  relationship fields, while preserving role-distinct native relationships.
- Keep large dispatch scans bounded by applying archive, workflow, role, and
  scope admission before detailed evidence receipts. Auditable exclusion totals
  and fingerprints remain available without serializing the whole source set.

## Previously in 0.6.0

- Validate only eligible selected traversal edges and explicit boundary
  evidence; retired, archived, filtered, and unrelated edges no longer leak
  duplicate findings into bounded results.
- Preserve blocked/retired lifecycle truth, keep role-distinct parallel
  relationships by `scopeRole` and `contributionRole`, canonicalize target
  types from current native rows, and make declared failures terminal.
- Add the fail-closed `dispatch-eligible-work-v1` multi-launch saved traversal.
  It returns deterministic candidate and exclusion receipts with revision,
  QA, ancestry, dependency, hold, database-route, custody, collision, scope,
  schema, registry, watermark, and query-fingerprint evidence.
- Keep dispatch policy in the registry instead of embedding workspace-specific
  project names, tags, owners, launch identities, or routes.
- Keep bundled templates in `reader/saved-queries.json`; installations can add,
  replace, or disable them through
  `.nimbalyst/tracker-plus.queries.json` without recoding or rebuilding.

## Previously in 0.5.0

- Generate independent, fail-closed timeline artifacts from one or more
  normalized `selector.launchTags` values.
- Include active one-hop relationship endpoints as explicit boundary context
  without leaking through them into unrelated launch graphs.
- Refuse to replace an existing artifact when the selector has no matches, its
  complete closure exceeds a cap, an endpoint is missing, validation fails, or
  the safe response envelope would truncate the result.
- Emit deterministic output identities plus selector seeds, closure counts,
  source counts, schema/registry provenance, validation counts, and truncation
  state in the generation receipt.

## Previously in 0.4.2

- Keep cursor-page item counts independent from normalized relationship-edge
  counts.
- Detect a missing workspace `timeline-item` schema when live legacy rows
  remain, preserve and count those rows, and expose the same structured warning
  in query, traversal, projection, and custom-editor receipts.
- Provide a bundled schema template and manual, preview-required repair
  guidance without automatically mutating tracker data or workspace schemas.
- Validate the extension against Nimbalyst 0.71.3 and Extension SDK 0.3.0.

## Previously in 0.4.1

- Register all six tools through separate read/query and projection backends,
  avoiding the Nimbalyst 0.68.1 four-tool registration ceiling.
- Keep comment, saved-query, and traversal calls on a least-privilege backend;
  request workspace-file access only for timeline and report generation.

## Previously in 0.4.0

- Add `launch` items and explicit `part-of-launch` membership with separate
  scope roles, lifecycle validation, nested-launch rollups, and hard blockers.
- Add bounded predicate queries and rooted graph traversal with saved role and
  launch templates, boundary nodes, cursor paging, and auditable watermarks.
- Move terminal states, relationship vocabulary, roles, caps, and saved queries
  into a versioned registry with a safe read-only workspace override.
- Add launch-rooted timeline sync plus a persistent launch selector and
  member/boundary/truncation/validation disclosure in the custom editor.
- Preserve curated timeline metadata during sync and return compact validation
  summaries, deterministic projection deltas, and generation identifiers.
- Distinguish intentional standalone seeds from broken orphans and stop graph
  expansion after including a selected cross-launch boundary dependency.

## Previously in 0.3.0

- Model workflow, schedule health, execution constraints, and risk as separate
  dimensions so a dependency does not automatically mean an item is blocked.
- Store each typed relationship once and derive backlinks and inverse labels.
- Support dependency modes, lead/lag, clearing conditions, evidence, provenance,
  and hard/soft coordination controls.
- Calculate schedule slack, deterministic risk, hard-serial critical paths, and
  dependency cycles.
- Validate milestone ownership, review evidence, orphan tasks, unassigned merge
  requests, duplicate edges, and incomplete hard dependencies.
- Project live tracker state into Timeline, Graph, and Reports views, with durable
  milestone reports and snapshot/schema/revision watermarks.
- Add compact timeline rows and responsive fit-to-width controls alongside Day,
  Week, and Month zoom.

## Timeline and relationships

The launch relationship model uses four native custom tracker types:

- `launch` identifies a first-class launch root, lifecycle, target window, and
  release outcome.
- `timeline-item` separates workflow, schedule health, execution constraint,
  risk inputs, forecasts, launch scope, and optional pull-request number/URL.
- `milestone` uses the same independent state dimensions plus milestone target,
  gate, forecast, reporting, and optional pull-request reference fields.
- `timeline-link` stores one normalized relationship edge as a native tracker
  record with stable ID, source, type, target, state, dependency controls,
  evidence, provenance, and effective revision.

Relationship truth is stored once as:

`source item → relationship type → target item`

Supported types are `part-of-launch`, `governs`, `contributes-to`, `reviews`,
`evidences`, `depends-on`, `precedes`, `enables`, `coordinates-with`,
`implements`, and `related`. Backlinks and inverse labels are derived from the
single edge record. A dependency describes topology; it does not set the
source item's execution constraint to blocked.

The `.ntimeline` custom editor provides Timeline, Graph, and Reports views. Its
tracker references link back to the durable native item. Timeline rows can be
collapsed with **Compact**, while **Fit** automatically resizes the date scale
when the editor width changes; Day, Week, or Month restores manual scaling.
In compact mode, **Critical path** toggles a red outline on calculated path
rows. **Summaries** optionally groups a task beneath its one active primary
milestone contribution; dependency edges never imply hierarchy.

**Filters** opens persistent multi-select controls for Completion (`Active` or
`Complete`) and Schedule health (`On track`, `At risk`, or `Late`). Values are
ORed within each group and ANDed between groups, so excluding `Complete` hides
finished work without changing schedule-health meaning. The active filter and
visible-item count apply to Timeline and Graph and survive a tracker re-sync.

The item inspector is hidden by default so the timeline uses the full editor
width. Selecting an item opens it contextually, and its close button clears the
selection and collapses the inspector again. Its safely encoded tracker link
uses Nimbalyst's contextual navigation. A separate pull-request section shows
`pullRequestNumber` and opens `pullRequestUrl` when it is a valid HTTPS URL; a
number-only reference remains visible when no GitHub URL has been stored.
Imported GitHub pull-request items can also resolve this section from their
native `github://owner/repository#number` origin reference.

Tracker+ derives its palette from Nimbalyst's semantic theme variables rather
than assigning fixed purple, blue, gray, green, amber, or red fills. Timeline
labels, graph nodes, inspector badges, and report text remain on neutral or
lightly tinted host surfaces; color is carried by borders, progress fills,
workflow dots, and typed relationship edges. The contrast regression covers
Light, Dark, Crystal Dark, and Midnight Orchid; System and Auto resolve to the
corresponding Light or Dark palette.

When explicit `timeline-link` rows exist, Tracker+ resolves their source and
target records before applying response limits, includes linked archived
evidence, and does not mix in legacy relationship arrays. Opening a native
projection also hydrates primary-milestone and launch-scope dimensions from the
normalized graph. The undated lane counts only active executable work; completed
history and plans, ADRs, features, bugs, or findings remain inspectable as
evidence but do not inflate unscheduled work. Milestone windows are contextual
only—Tracker+ never invents item dates.

## Agent tools

New agents should start with the complete [Tracker+ agent guide](docs/agent-guide.md).
It includes exact arguments, safe mutation/resync patterns, normalized-link
examples, filter behavior, PR references, and recovery steps.
Its [saved-query and role-search catalog](docs/agent-guide.md#saved-query-and-role-search-catalog)
documents every copy-ready workspace template, role expansion, invocation,
external query-catalog rules, caps, and failure behavior.
The dedicated [role guide](docs/roles.md) explains query roles versus graph
roles and includes neutral examples plus a ready-to-copy installer registry.
The [installation guide](docs/installation.md#workspace-configuration) and
[`examples/tracker-plus.queries.json`](examples/tracker-plus.queries.json)
provide copy-ready external predicate and traversal query templates; the agent
guide documents how to define generic composed queries.

- `native_tracker_list_comments` returns bounded, paginated comment history.
- `native_tracker_get_with_comments` returns tracker orientation and comments.
- `native_tracker_query` runs bounded cursor-paged predicates or saved role
  queries and explicitly requires automatic continuation until the full result
  set has been retrieved, unless the caller requested only one page.
- `native_tracker_traverse` returns rooted members, edges, boundary context,
  launch rollups, validation, provenance, fail-closed dispatch candidates, and
  opt-in cursor paging for complete standard graphs that exceed one response.
- `native_tracker_sync_timeline` projects current native tracker data into a
  workspace-relative `.ntimeline` document and returns validation and
  prior/current projection deltas in its sync receipt.
- `native_tracker_generate_milestone_report` writes a bounded Markdown report
  for one or all major milestones.

All tools take their workspace from the Nimbalyst backend context. Callers
cannot choose a database path or SQL statement. Durable tracker changes still
go through Nimbalyst's built-in `tracker_create`, `tracker_update`, and
`tracker_add_comment` tools.

Query and traversal watermarks and timeline projection sources include a
`schemaDiscovery` descriptor for `timeline-item`. If its state is
`missing-with-live-rows`, Tracker+ continues to return those legacy rows and
adds the warning code `timeline-item-schema-missing-with-live-rows`. Review the
bundled schema template before manually registering
`.nimbalyst/trackers/timeline-item.yaml`; Tracker+ never performs that repair
automatically.

They also include a verified reader-bundle generation with extension, adapter,
and registry versions plus bounded asset path/hash provenance. Each native
helper runs from an immutable snapshot, so a live extension update cannot mix
Python code and JSON assets. If no complete generation becomes available
during the bounded startup window, Tracker+ returns
`READER_RESTART_REQUIRED` with actionable diagnostics and no tracker content.

## Quick start

1. Confirm **Tracker+** is enabled in Nimbalyst Extensions. New installs
   default the safe renderer contribution to enabled; backend consent remains
   first-use and opt-in. Tracker+ separates its four read/query tools from its
   two projection tools so every tool is registered on supported hosts.
2. Create `launch`, `milestone`, `timeline-item`, and `timeline-link` tracker
   records in the native tracker.
3. Create a Tracker Timeline from the New File menu, or call
   `native_tracker_sync_timeline` with an `.ntimeline` output path.
4. Open the timeline file and switch among Timeline, Graph, and Reports.
5. Call `native_tracker_generate_milestone_report` when a durable Markdown
   status report is needed.

The included [Tracker Timeline.ntimeline](Tracker%20Timeline.ntimeline) and
[Milestone Dashboard.ntimeline](Milestone%20Dashboard.ntimeline) demonstrate
the editor against native validation items.

## Safety model

- SQLite opens with `mode=ro` and immediately enables `PRAGMA query_only = ON`.
- Every database lookup is workspace-scoped and excludes deleted/archived data.
- Comment and timeline responses are bounded; identity objects are minimized.
- The read/query backend requests only `mcp-server-register`. The projection
  backend additionally requests `workspace-files`; neither requests the
  Nimbalyst database-write broker, secrets, or network access.
- Workspace writes are limited to validated relative `.ntimeline` and `.md`
  files explicitly requested through the generation tools.
- Python uses only the standard library, avoiding Electron native-module ABI
  packaging.

See [security.md](docs/security.md) for the complete trust boundary and
[timeline-plan.md](docs/timeline-plan.md) for the architecture and acceptance
criteria. Agent operators should also keep [agent-guide.md](docs/agent-guide.md)
available as the tool runbook.

## Development

```powershell
npm install
npm test
npm run verify:package
```

Build, install, reload, and inspect the extension through Nimbalyst Extension
Dev Tools—not a substitute npm build—as described in
[installation.md](docs/installation.md).

## License

MIT. See [LICENSE](LICENSE).
