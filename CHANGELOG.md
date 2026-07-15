# Changelog

All notable changes to this project are documented in this file.

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
- Validate 14 automated tests and a live five-item, ten-edge normalized sample.

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
