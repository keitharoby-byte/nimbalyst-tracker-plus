# Changelog

All notable changes to this project are documented in this file.

## Unreleased

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
- Watermark projections with snapshot time, schema fingerprint, ProjectState
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
