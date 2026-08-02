# Compatibility

## Validated release

- Validation date: 2026-08-02
- Nimbalyst: 0.71.3 packaged build
- Extension: Tracker+ 0.6.0
- Extension API: 1.0.0
- Extension SDK: 0.3.0
- Platform: Windows
- Database backend: SQLite
- Schema adapter: `tracker-items-normalized-timeline-v3`
- Registry: version 3
- Saved-query catalog: version 1
- Python: standard-library `sqlite3`

The extension builds, installs, reloads, and exposes one `.ntimeline` custom
editor plus six backend methods. Four read/query tools use a least-privilege
module requesting only `mcp-server-register`; two projection tools use a
separate module that additionally requests `workspace-files`.

The adapter requires the `tracker_items` columns listed in
`reader/contracts.py`. Every successful response includes a SHA-256 schema
fingerprint derived only from ordered column names and SQLite types. No data
values contribute to the fingerprint.

The effective registry combines:

- locked structural values from `reader/registry.json`;
- bundled query templates from `reader/saved-queries.json`;
- optional workspace policy from `.nimbalyst/tracker-plus.registry.json`;
- optional workspace queries from `.nimbalyst/tracker-plus.queries.json`.

Registry version, effective hash, query version, and override state appear in
result receipts. Workspace query changes do not require rebuilding the
extension.

## Retest triggers

Retest after any Nimbalyst update, SQLite backend migration, comment-object
shape change, relationship lifecycle/value change, identity-object change,
timestamp-format change, or schema adapter update. A missing required column
is an automatic `SCHEMA_INCOMPATIBLE` failure.

A disposable read cache remains a possible scale optimization, not an
authoritative store.
