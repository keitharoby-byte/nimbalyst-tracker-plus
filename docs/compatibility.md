# Compatibility

## Validated fixture

- Validation date: 2026-07-14
- Nimbalyst: 0.68.1 packaged build
- Electron / embedded Node: 41.8.0 / 24.16.0
- Extension: Native Tracker Comments 0.1.0
- Extension API: 1.0.0
- SDK: 0.2.2
- Platform: Windows
- Database backend: SQLite
- Schema adapter: `tracker-items-data-comments-v1`
- Python: standard-library `sqlite3`
- Schema fingerprint: `4065c61d27932cf88fd78eeecf3ed849639c7e4e763cd648eab389db264838fa`

The extension built, installed, and hot-reloaded successfully. Nimbalyst's
privileged host granted the declared `mcp-server-register` permission, spawned
the utility process, registered both tools for the active validation workspace,
and reported two ready backend methods. A bounded production read returned the
requested comments with the exact redacted contract; the active tracker row
count was unchanged before and after the read.

The adapter requires the `tracker_items` columns listed in
`reader/contracts.py`. It records a SHA-256 fingerprint of ordered column names
and SQLite types in each successful response. The fingerprint contains no data
values.

## Retest triggers

Retest after any Nimbalyst update, SQLite backend migration, comment-object
shape change, identity-object change, or timestamp-format change. A missing
required column is an automatic `SCHEMA_INCOMPATIBLE` failure. PGLite is not
supported by version 0.1.0.
