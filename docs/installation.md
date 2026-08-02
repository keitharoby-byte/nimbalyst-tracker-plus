# Installation and validation

## Prerequisites

- Windows with the supported Nimbalyst SQLite backend.
- Python 3 available as `py -3`, `python3`, or `python`.
- Nimbalyst Extension Dev Tools enabled.

Run `scripts/preflight.ps1` to check versions, backend selection, schema
columns, and a read-only connection without printing tracker content.

## Developer workflow

1. Run `npm install` once.
2. Run `npm test`.
3. Build with Nimbalyst's `extension_build` developer tool.
4. Install with `extension_install` using this repository root.
5. Enable **Tracker+** in Extensions settings.
6. Invoke a read/query tool and approve its first-use native-code prompt. The
   separate projection backend asks for workspace-file access when a timeline
   or milestone report is first generated.
7. During iteration use `extension_reload`, then check `extension_get_status`
   and the main-process extension logs.

Tracker+ deliberately packages two backend modules. The read/query module
registers comment orientation, predicate/saved queries, and bounded traversal
(four tools). The projection module registers timeline sync and milestone
report generation (two tools). This split keeps every registration within the
supported host batch size and gives read-only calls least-privilege consent.

For agent operation after installation, use [agent-guide.md](agent-guide.md).
It distinguishes read-only orientation, native tracker mutations, and generated
projection/report files.

## Workspace configuration

Tracker+ does not require source changes for installation-specific roles or
saved queries:

1. Copy
   [`examples/tracker-plus.registry.roles.json`](../examples/tracker-plus.registry.roles.json)
   to `.nimbalyst/tracker-plus.registry.json`, then edit or remove the neutral
   example roles to match owner aliases and attention tags already used in the
   workspace.
2. Copy
   [`examples/tracker-plus.queries.json`](../examples/tracker-plus.queries.json)
   to `.nimbalyst/tracker-plus.queries.json`, then edit the predicate,
   traversal, or composed templates as needed.
3. Invoke each configured saved query once and inspect its echoed definition,
   `validation`, `page`, `watermark`, registry hash, and query fingerprint
   before an agent acts on the result.

Query roles are selectors, not permissions or assignments. Relationship
`scopeRole` and `contributionRole` values classify graph edges instead of
people. See [roles.md](roles.md) for delivery, quality, security, and
documentation examples and [agent-guide.md](agent-guide.md#managing-saved-queries-without-code-changes)
for the complete catalog contract and failure behavior.

The installed extension and `.ntimeline` editor are available across Nimbalyst
projects. Each call is still scoped to the workspace associated with that AI
session. Install the supplied `launch`, `timeline-item`, `milestone`, and
`timeline-link` schemas in each workspace that needs launch, schedule, and
relationship fields.

## First timeline

1. Define or copy the `launch`, `timeline-item`, `milestone`, and
   `timeline-link` schemas into `.nimbalyst/trackers`.
2. Create native launch, milestone, and timeline records, then create one
   `timeline-link` record per source/type/target edge.
3. Call `native_tracker_sync_timeline` with an output such as
   `Tracker Timeline.ntimeline`.
4. Open the file in Nimbalyst. The extension supplies Timeline, Graph, and
   Reports tabs.
5. Call `native_tracker_generate_milestone_report` for a durable `.md` report.

## Rollback

Disable the extension, revoke its backend consent grant, and uninstall it.
Delete generated `.ntimeline` / report files and the optional custom tracker
schema YAML files if they are no longer wanted. There is no database migration
rollback because the extension creates no tables and writes no tracker rows.
