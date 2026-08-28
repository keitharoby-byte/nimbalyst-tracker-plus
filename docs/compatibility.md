# Compatibility

## Validated release

- Validation date: 2026-08-28
- Nimbalyst: 0.72.8 packaged build
- Extension: Tracker+ 0.18.1
- Extension API: 1.0.0
- Extension SDK: 0.3.0
- Platform: Windows
- Database backend: SQLite
- Schema adapter: `tracker-items-normalized-timeline-v14`
- Registry: version 6
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

The normalized item contract exposes a bounded `packetId` string when present.
Predicate queries and traversal `nodeWhere` filters accept `eq`, `in`, and
`exists` for that field, using parameterized, case-insensitive comparisons
through the effective `customFields` envelope depth.

For read compatibility, the adapter follows up to 128 nested `customFields`
mapping envelopes by default. Timeline, report, query, and traversal calls may
set `maxCustomFieldsDepth` from 1 through the locked maximum of 512. Top-level
fields take precedence, followed by the nearest envelope; deeper envelopes
only fill missing keys. Repeated object identities terminate the walk, and a
distinct envelope beyond the effective call limit fails closed with
`CUSTOM_FIELDS_NESTING_EXCEEDED`. This does not migrate or write tracker data.

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

Dispatch posture is a complete, versioned workspace override. Its closed
signal/classification matrix preserves mandatory revision and QA gates while
allowing supported operational evidence to be required, conditional,
positive-blocking, or advisory. Invalid, incomplete, unknown, or ambiguous
postures are rejected atomically. The effective posture and fingerprint appear
in dispatch query and row receipts.

Paginated traversal fitting accounts for the finalized page and validation
metadata before accepting a response. Successful pages remain below the
500-KiB result limit and the 512-KiB process-line ceiling while advancing the
opaque cursor whenever at least one leading entity fits.

Dispatch traversal supports opt-in pagination. Admission, validation, evidence
completeness, ordering, totals, and the result fingerprint are computed over
the complete logical result before paging. The cursor binds receipt/boundary
and edge offsets to that complete result. Each page stays within the response
cap, emits each detailed receipt only once at the top level, and exposes
consistent completion and response-fitting flags.

Native reader assets are generation-locked. `bundle-manifest.json` records the
extension, adapter, and registry versions and the SHA-256 hash of every reader
asset. A helper starts only from a verified immutable snapshot. During a live
update it either continues on its already-loaded generation or starts on the
complete new generation; it never intentionally combines the two.

Reader deadlines are selected by operation and bounded request size. A timed
out request fails without partial data as `READER_TIMEOUT`, reports only safe
execution metadata, terminates its helper generation, and permits the next
request to start cleanly from the same verified bundle.

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
