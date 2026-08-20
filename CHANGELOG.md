# Changelog

All notable changes to this project are documented in this file.

## Unreleased

- Register the native `in-collection` / `has-item` relationship types
  (registry version 5) so collection membership traverses with the same
  parity as `part-of-launch`, synthesize native non-legacy `in-collection`
  edges from the built-in inline `collection` field, and treat `release` as
  a first-class collection container: release rows surface in timeline
  snapshots with `targetDate` parity, and `milestone` / `release` traversal
  roots default to depth-one active incoming `in-collection` membership. (#35)
- Resolve the tracker body in `native_tracker_get_with_comments` from the
  collaborative tracker content when non-empty, falling back to the durable
  local body snapshot so a lagging or unreconciled collaborative write never
  reads as an empty body. The response now reports `tracker.bodySource`
  (`collaborative-content`, `local-snapshot`, or `empty`) so readers can
  distinguish a genuinely empty body from a snapshot-served one. (#34)
- Document and regression-test that dispatch launch admission follows
  qualifying active ancestry (for example item → milestone → launch) within
  the bounded traversal depth, without requiring a duplicate direct
  item-to-launch relationship, and that non-qualifying scope roles never
  establish scope. Receipts already report each hop's relationship ID on its
  own ancestry row; no engine behavior changed.

## 0.9.1 - 2026-08-17

- Scope predicate and role-query launch validation to the selected page plus
  the membership context required by selected launches. Query receipts now
  declare and fingerprint that validation scope, while genuine lifecycle,
  endpoint, duplicate, cycle, and relationship defects remain fail-closed.
  (#33)
- Separate the revision-currentness logical signal from writable evidence
  fields. Incomplete dispatch receipts now identify the effective configured
  sources and constraints, flag a misleading near-name field as unaccepted,
  and continue to accept only matching revision evidence or an explicit
  current-revision boolean. (#32)

## 0.9.0 - 2026-08-11

- Make `.nimbalyst/tracker-plus.queries.json` the complete authoritative saved-
  query inventory for each workspace. The integrity-checked bundled catalog is
  empty, missing or invalid workspace catalogs activate no queries, `null`
  entries are rejected, and legacy registry `savedQueries` no longer merge.
- Move the six reusable predicate and traversal templates to the copy-ready
  example catalog with stable IDs. Remove the injected
  `walk-ready-milestones` query and project-specific `walk-readiness-v1`
  projection while preserving generic composed-query support. (#30)
- Validate against Nimbalyst 0.72.8 and update fixable transitive development
  dependencies without crossing SDK or host major-version boundaries.

## 0.8.2 - 2026-08-10

- Honor configured role `attentionTags` alongside owner aliases during
  dispatch pre-admission so routed rows receive detailed inclusion or
  exclusion receipts. Validate selected launch roots against their actual
  active membership graph independently of candidate admission, while keeping
  unresolved endpoints, lifecycle omissions, evidence gaps, and truncation
  terminal and withholding all candidates and totals. (#27)

## 0.8.1 - 2026-08-10

- Make complete predicate-query retrieval explicit: responses now distinguish
  complete results from continuable pages, and the MCP contract requires agents
  to follow opaque cursors automatically across limit or response-size
  truncation unless the user requested only one page. Standard graph traversals
  can now opt into the same complete-result workflow, treating node and edge
  caps as page sizes while binding cursors to the selected graph; dispatch and
  composed traversals remain atomic and fail closed.
- Stop legacy launch-key migration tags from producing non-PASS native timeline
  validation. Active typed `part-of-launch` relationships remain the sole
  membership and rollup authority, including when an item belongs to one launch
  while retaining another launch's migration tag. (#25)
- Fix launch-rooted timeline projections dropping registered `timeline-item`
  rows that are `part-of-launch` members of a nested lane (a member that is
  itself a launch container). The launch snapshot now keeps `part-of-launch`
  in its one-hop expand stage, so nested lane members surface as boundary
  context instead of being silently suppressed. They remain boundary nodes:
  excluded from launch rollups and never promoted to direct launch membership.
  (#23)
- Add a copy-ready external query catalog covering predicate, traversal, and
  composed templates, and link it from installation, agent, and release
  documentation.
- Refresh the README graph and light/dark timeline screenshots from the current
  0.7 viewer, and advance the bundled sample projections to 0.7 provenance.

## 0.7.0 - 2026-08-02

- Add registry-supported composed saved queries that select bounded roots with
  a predicate and then expand typed relationships in one deterministic result.
- Add the generic `walk-ready-milestones` template with native walk/build
  fields, evidence-backed readiness, predecessor controls, a consistent
  readiness metric, and stored-versus-derived provenance.
- Preserve explicit `unknown` readiness when native stage, acceptance,
  implementation evidence, or runtime availability is missing; never infer a
  positive build state from titles, tags, progress, or confidence fields.
- Treat a terminal selected walk root as authoritative and prevent stale
  nonterminal child evidence from reopening its gate.
- Restore legacy-versus-native relationship suppression with role-aware
  semantic identity, preventing saved role queries from crashing while keeping
  scope-role and contribution-role parallels distinct.
- Apply dispatch archive, workflow, role, and scope admission before detailed
  receipt materialization so normal large workspaces remain within response
  bounds.
- Add compact pre-admission reason totals and a stable exclusion fingerprint
  while keeping fail-closed terminal receipts free of candidates and launch
  totals.
- Add in-process, helper-boundary, external-catalog, composed traversal,
  evidence, terminal-root, selection-overflow, and large-workspace regressions.

## 0.6.2 - 2026-08-02

- Package every native reader generation with a versioned manifest containing
  the extension, adapter, and registry versions plus SHA-256 asset hashes.
- Start each helper from a verified immutable temporary snapshot so a running
  process remains on one coherent generation during live extension updates.
- Retry boundedly while an install is in progress and return
  `READER_RESTART_REQUIRED` with version, path, hash, and validation-cause
  diagnostics instead of reporting a mixed bundle as `REGISTRY_INVALID`.
- Cache the verified bundled registry for the lifetime of the reader process
  while continuing to load safe workspace overrides per request.
- Expose verified reader-bundle provenance in source and watermark receipts,
  and add package, mismatch, and immutable-snapshot regression tests.

## 0.6.1 - 2026-08-02

- Add a dedicated role guide that distinguishes query-role matching from
  relationship scope and contribution roles.
- Document neutral delivery, quality, security, and documentation role
  examples, matching behavior, safety guidance, and copy-paste queries.
- Add a ready-to-copy workspace role registry so installers can adopt or edit
  example roles without rebuilding the extension.

## 0.6.0 - 2026-08-02

- Scope traversal validation to the eligible selected graph and declared
  boundary evidence, so retired, archived, filtered, and out-of-boundary edges
  cannot contaminate a bounded result.
- Preserve `blocked` and `retired` relationship lifecycle states without
  promoting them to active, and fail closed on unknown selected states.
- Define semantic relationship identity with source, type, target,
  `scopeRole`, and `contributionRole`; retain role-distinct native edges and
  reject only exact selected duplicates with stable relationship IDs.
- Canonicalize relationship target type, title, and issue key from the current
  native target row rather than stale embedded edge metadata.
- Make unresolved roots and selected edges terminal, and make declared
  validation/truncation conditions return no usable graph result.
- Add the versioned `dispatch-eligible-work-v1` traversal with optional
  `roleId`, `launchKeys[]`, and `includeUnscoped` parameters.
- Return deterministic included/excluded dispatch receipts covering packet and
  QA revisions, launch/milestone/train ancestry, dependency clearing, holds,
  database routes, PR/session/worktree custody, survivor/collision state,
  scope and query fingerprints, and explicit reasons.
- Order candidates by cleared hard-dependency topology, launch/critical-path
  priority, native priority, durable `precedes` evidence, and stable issue key;
  train metadata never limits capacity.
- Fail dispatch closed on warnings, validation errors, unresolved evidence,
  incomplete required evidence, cycles, or response truncation, returning no
  candidates or launch totals in the terminal receipt.
- Move all dispatch workflow, type, QA, launch, scope, route, hold, custody,
  survivor, collision, and unscoped admission policy into the safe workspace
  registry override.
- Advance the normalized schema adapter to v3 and the bundled registry to v3
  so receipts make the lifecycle/identity and dispatch contract change
  explicit.
- Move all bundled saved-query templates into the standalone
  `reader/saved-queries.json` catalog and support atomic per-workspace add,
  replace, or disable operations through
  `.nimbalyst/tracker-plus.queries.json` without rebuilding the extension.
- Remove installation-specific roles, tracker types, workspace paths, issue
  identities, and decision language from public defaults, fixtures, comments,
  and architecture/benchmark documentation; advance the neutral registry to
  version 3.

## 0.5.0 - 2026-07-30

- Add an optional `selector.launchTags` generator contract to
  `native_tracker_sync_timeline` while preserving global, launch-rooted,
  saved launch-scope, and role-query behavior.
- Select normalized tag seeds first, include complete active one-hop
  relationship boundaries, and preserve relationship direction, lifecycle,
  dependency, scope/contribution, revision, and evidence provenance.
- Fail before replacing the destination on invalid/duplicate selectors, no
  matches, source or closure overflow, missing endpoints, error-severity
  validation, or response truncation.
- Add deterministic output identities and auditable seed, source, closure,
  emission, schema, registry, validation, and truncation receipts.
- Add independent Alpha/Demo, deterministic multi-tag union, source-isolation,
  boundary, cap, invalid-selector, and endpoint failure tests.

## 0.4.2 - 2026-07-30

- Keep `page.returnedCount` commensurable with item totals by excluding
  relationship edges while still counting traversal boundary nodes.
- Detect live legacy `timeline-item` rows when the current workspace has no
  registered schema, preserve those rows, and surface a structured
  `timeline-item-schema-missing-with-live-rows` warning plus manual,
  preview-required repair metadata in query, traversal, projection, and UI
  receipts.
- Package the reviewed `timeline-item` schema template without adding an
  automatic tracker, database, or workspace-schema mutation path.
- Validate Tracker+ against Nimbalyst 0.71.3 and update the pinned Extension
  SDK from 0.2.2 to 0.3.0.

## 0.4.1 - 2026-07-17

- Split the six MCP tools into a four-tool read/query backend and a two-tool
  projection backend so Nimbalyst 0.68.1 registers every tool instead of
  truncating a single six-tool batch.
- Give the read/query backend least-privilege `mcp-server-register` consent;
  reserve `workspace-files` for timeline and milestone projection tools.
- Add package and registration-layout checks, remove obsolete single-backend
  build output, and document installation, consent, and recovery behavior.

## 0.4.0 - 2026-07-17

- Add a versioned bundled registry with safe workspace overrides for terminal
  statuses, roles, and saved query templates.
- Add `native_tracker_query` with validated predicates, allowlisted fields and
  operators, parameterized SQL, deterministic cursor paging, and total counts.
- Add `native_tracker_traverse` with rooted membership/context BFS, boundary
  nodes, launch rollups, lifecycle and relationship validation, and fail-closed
  launch views.
- Add all eleven normalized relationship types, explicit `scopeRole`, launch
  lifecycle findings, nested memberships, and explicit-membership semantics.
- Add launch-rooted timeline sync and a document-only launch filter with member,
  boundary, truncation, validation, and registry disclosures.
- Add validation severity/code summaries and deterministic prior/current
  projection deltas to timeline sync receipts.
- Preserve curated timeline title, view, and filters while assigning each
  generated snapshot a durable generation identifier.
- Classify tagged standalone projection seeds separately from broken orphans,
  and stop launch traversal after including one-hop boundary dependencies.
- Add scale fixtures/benchmarks, query/traversal tests, workspace launch schemas,
  and updated security, compatibility, and agent guidance.
- Add a complete operator catalog for saved queries and role searches, including
  copy-paste calls, role overrides, exclusions, caps, and failure semantics.

- Add a complete agent runbook with exact tool arguments, durable
  mutation/resync sequencing, normalized relationship examples, filter and PR
  behavior, safety constraints, and recovery guidance.
- Add persistent multi-select Completion and Schedule health filters to the
  Timeline and Graph views, including a one-click reset and visible-item count.
- Add a safely encoded tracker-reference link so inspector navigation resolves
  through Nimbalyst without importing a host API absent from version 0.68.1.
- Project pull-request numbers and HTTPS URLs from native tracker fields or an
  imported GitHub origin URN, and retain the PR number when no URL is available.
- Preserve state-filter preferences when a timeline is re-synced and validate
  the combined filter and safe-link behavior in the automated suite.

## 0.3.0 - 2026-07-14

- Rename the public extension and repository from the comment-reader-specific
  name to Tracker+, while retaining the extension ID for upgrade continuity.
- Separate workflow, schedule health, execution constraint, priority, and
  derived risk so dependency topology never implies execution blockage.
- Add native `timeline-link` records as single-source normalized edges with
  generated inverse labels, lifecycle state, dependency mode, hardness,
  lead/lag, clearing condition, owner, evidence, and revision provenance.
- Derive 5×5 risk levels with critical-path, durability/recoverability, and
  active-blocker escalation floors plus auditable rationale.
- Calculate hard-serial critical paths, schedule slack, and dependency cycles.
- Validate launch milestone cardinality, MR review/evidence rules, hard-serial
  controls, orphan records, and duplicate or malformed edges.
- Watermark projections with snapshot time, schema fingerprint, source state
  revision, and projection version; render nodes by schedule health and edges
  by relationship type.
- Add persistent Compact row density and responsive Fit-to-width timeline
  controls beside Day, Week, and Month.
- Preserve native projection relationships that use `kind`, `status`,
  `contributionRole`, `directedness`, and object-form evidence fields when the
  editor parses and auto-saves a timeline.
- Resolve active `timeline-link` endpoints before row limits, include explicitly
  linked archived evidence, and suppress legacy edge synthesis whenever native
  link rows are present.
- Derive primary milestone and launch connectivity from normalized edges; count
  only active executable items as undated work while retaining completed and
  reference records without fabricating dates.
- Replace the legacy details split between links and backlinks with one
  normalized relationship list whose forward or inverse label is derived from
  the item's side of the single stored edge.
- Exempt intentional secondary contributions from primary-milestone
  cardinality while still rejecting explicit launch items with zero primaries
  and items with multiple primary assignments.
- Treat active and cleared relationship endpoints and evidence references as
  durable incidence for orphan validation; cleared review evidence is no
  longer misclassified as unrelated.
- Add a compact-mode Critical path toggle that highlights calculated path rows
  with a red outline without replacing their schedule-health color.
- Add optional summary grouping from exactly one active primary
  `contributes-to` edge; secondary and dependency edges never create hierarchy
  or duplicate placement.
- Hide the item inspector by default, open it when a record is selected, and
  provide an explicit close control that restores the full timeline width.
- Replace fixed status and relationship colors with Nimbalyst semantic theme
  tokens; use accessible tints and neutral text instead of low-contrast white
  text on purple, blue, gray, green, amber, or red fills.
- Add WCAG AA contrast regression coverage and a reusable four-theme visual
  fixture for Light, Dark, Crystal Dark, and Midnight Orchid.
- Validate 21 automated tests, the five-item development sample, and the live
  Alpha projection with 91 items and 105 active normalized native edges.

## 0.2.0 - 2026-07-14

- Add native `timeline-item` and `milestone` workspace tracker schemas.
- Add blocker, waiting-on, related, source, and milestone hierarchy fields on
  top of Nimbalyst's first-class relationship model.
- Add a `.ntimeline` custom editor with Gantt, relationship graph, and milestone
  report views.
- Add `native_tracker_sync_timeline` and
  `native_tracker_generate_milestone_report` backend tools.
- Add bounded timeline projection and milestone health-report contracts.
- Validate 13 tests, package output, hot reload, four live native tracker items,
  eleven relationships, and all three custom-editor views.

## 0.1.0 - 2026-07-14

- Add `native_tracker_list_comments` with bounded cursor pagination.
- Add `native_tracker_get_with_comments` for one-call tracker orientation.
- Enforce workspace scoping, SQLite read-only mode, query-only mode, schema
  compatibility checks, identity minimization, and response caps.
- Add contract, schema, security, packaging, and smoke-test coverage.
- Validate build, install, reload, and representative reads on Nimbalyst 0.68.1.
