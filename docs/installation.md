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
6. Invoke a `native_tracker_*` tool and approve the first-use native-code and
   workspace-file prompt.
7. During iteration use `extension_reload`, then check `extension_get_status`
   and the main-process extension logs.

For agent operation after installation, use [agent-guide.md](agent-guide.md).
It distinguishes read-only orientation, native tracker mutations, and generated
projection/report files.

The installed extension and `.ntimeline` editor are available across Nimbalyst
projects. Each call is still scoped to the workspace associated with that AI
session. Install the supplied `timeline-item` and `milestone` schemas in each
workspace that needs native schedule and relationship fields, including the
`timeline-link` schema.

## First timeline

1. Define or copy the `timeline-item`, `milestone`, and `timeline-link` schemas into
   `.nimbalyst/trackers`.
2. Create native milestone and timeline records, then create one
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
