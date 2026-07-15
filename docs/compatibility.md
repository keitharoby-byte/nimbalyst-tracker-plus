# Compatibility

## Validated fixture

- Validation date: 2026-07-14
- Nimbalyst: 0.68.1 packaged build
- Electron / embedded Node: 41.8.0 / 24.16.0
- Extension: Tracker+ 0.3.0
- Extension API: 1.0.0
- SDK: 0.2.2
- Platform: Windows
- Database backend: SQLite
- Schema adapter: `tracker-items-normalized-timeline-v2`
- Python: standard-library `sqlite3`
- Schema fingerprint: `4065c61d27932cf88fd78eeecf3ed849639c7e4e763cd648eab389db264838fa`

The extension built, installed, and hot-reloaded successfully. Nimbalyst's
privileged host accepted the declared `mcp-server-register` and
`workspace-files` permissions. The package registered four backend methods and
one `.ntimeline` custom editor. A live validation set projected five native
work items and ten normalized relationship records with no validation errors;
Timeline, Graph, and Reports rendered successfully. Fourteen automated tests
passed, and the active tracker row count
remained unchanged across read-only projections.

The adapter requires the `tracker_items` columns listed in
`reader/contracts.py`. It records a SHA-256 fingerprint of ordered column names
and SQLite types in each successful response. The fingerprint contains no data
values.

## Retest triggers

Retest after any Nimbalyst update, SQLite backend migration, comment-object
shape change, relationship-value change, identity-object change, or
timestamp-format change. A missing
required column is an automatic `SCHEMA_INCOMPATIBLE` failure. PGLite is not
supported by version 0.3.0.
