# Compatibility

## Validated release

- Validation date: 2026-08-11
- Nimbalyst: 0.72.8 packaged build
- Extension: Tracker+ 0.9.0
- Extension API: 1.0.0
- Extension SDK: 0.3.0
- Platform: Windows
- Database backend: SQLite
- Schema adapter: `tracker-items-normalized-timeline-v3`
- Registry: version 4
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
- optional workspace policy from `.nimbalyst/tracker-plus.registry.json`;
- the complete workspace query inventory from
  `.nimbalyst/tracker-plus.queries.json`, or an empty inventory when that file
  is absent or invalid.

`reader/saved-queries.json` remains an integrity-checked package asset but
contains no active templates.

Registry version, effective hash, query version, and override state appear in
result receipts. Workspace query changes do not require rebuilding the
extension.

Native reader assets are generation-locked. `bundle-manifest.json` records the
extension, adapter, and registry versions and the SHA-256 hash of every reader
asset. A helper starts only from a verified immutable snapshot. During a live
update it either continues on its already-loaded generation or starts on the
complete new generation; it never intentionally combines the two.

If the host-visible install directory does not settle on one complete
generation within the bounded startup window, tools fail closed with
`READER_RESTART_REQUIRED`. The diagnostic includes the manifest path,
generation/version identity, validation cause, and expected/actual asset hash
when available. Reloading the extension is sufficient; tracker rows and
workspace overrides do not need to be changed.

## Retest triggers

Retest after any Nimbalyst update, SQLite backend migration, comment-object
shape change, relationship lifecycle/value change, identity-object change,
timestamp-format change, or schema adapter update. A missing required column
is an automatic `SCHEMA_INCOMPATIBLE` failure.

A disposable read cache remains a possible scale optimization, not an
authoritative store.
