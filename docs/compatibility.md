# Compatibility

## Validated fixture

- Validation date: 2026-07-17
- Nimbalyst: 0.68.1 packaged build
- Electron / embedded Node: 41.8.0 / 24.16.0
- Extension: Tracker+ 0.4.1
- Extension API: 1.0.0
- SDK: 0.2.2
- Platform: Windows
- Database backend: SQLite
- Schema adapter: `tracker-items-normalized-timeline-v2`
- Python: standard-library `sqlite3`
- Schema fingerprint: `4065c61d27932cf88fd78eeecf3ed849639c7e4e763cd648eab389db264838fa`

The extension built and installed successfully, and installed manifest/backend/
reader/registry hashes matched the final build. The package declares six
backend methods across a four-tool read/query module and a two-tool projection
module, plus one `.ntimeline` custom editor. The read/query module requests only
`mcp-server-register`; the projection module additionally requests
`workspace-files`. Thirty-nine Python and eleven backend/renderer/model/
contrast checks passed. Nimbalyst 0.68.1 was observed truncating a single
six-tool registration to four, so Tracker+ 0.4.1 packages the two families as
separate utility modules. Package verification guards both module layout and
against reintroducing the unsupported renderer import.

The adapter requires the `tracker_items` columns listed in
`reader/contracts.py`. It records a SHA-256 fingerprint of ordered column names
and SQLite types in each successful response. The fingerprint contains no data
values.

The adapter name is unchanged. NAD-001 adds the bundled versioned
`reader/registry.json` and an optional read-only workspace override at
`.nimbalyst/tracker-plus.registry.json`. Registry version, effective hash, and
override state appear in every response watermark.

## Retest triggers

Retest after any Nimbalyst update, SQLite backend migration, comment-object
shape change, relationship-value change, identity-object change, or
timestamp-format change. A missing
required column is an automatic `SCHEMA_INCOMPATIBLE` failure. PGLite remains
an unimplemented read-cache escalation option rather than an authoritative
store.
