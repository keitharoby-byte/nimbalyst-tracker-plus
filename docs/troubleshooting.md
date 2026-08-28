# Troubleshooting

## `PYTHON_NOT_FOUND`

Install Python 3 and confirm one of `py -3`, `python3`, or `python` works in a
new terminal. Reload the extension afterward.

## `DATABASE_NOT_SQLITE`

The active Nimbalyst backend is not SQLite. This release intentionally refuses
to guess at a PGLite or future backend schema.

## `SCHEMA_INCOMPATIBLE`

The host's tracker schema changed. Record the host version and reported missing
columns/fingerprint, then update the adapter and synthetic fixtures before
reading production comments again.

## `timeline-item-schema-missing-with-live-rows`

Nimbalyst scopes tracker-type definitions to the project that registered them.
If a workspace still has live legacy `timeline-item` database rows but
does not contain a matching schema in `.nimbalyst/trackers`, native type
discovery can omit the type even though direct reads still succeed.

Tracker+ preserves and counts the rows and returns a `schemaDiscovery`
descriptor in query/traversal watermarks and timeline projection sources. Its
repair block is intentionally `manual-preview-required` and
`automaticMutation: false`. Review the bundled
`dist/reader/timeline-item.schema.yaml` template against the workspace, then
register `.nimbalyst/trackers/timeline-item.yaml` explicitly through Nimbalyst
or a reviewed workspace change. Tracker+ never writes tracker data or schema
registry files.

## `TRACKER_NOT_FOUND`

The id or issue key does not resolve inside the current workspace. The same
issue key in another workspace is intentionally invisible.

## `DATABASE_BUSY`

Retry after a short delay. The reader uses a bounded one-second SQLite busy
timeout and never holds a connection between tool calls.

## `READER_TIMEOUT`

The native reader exceeded the bounded deadline selected for its operation and
request size. The diagnostic identifies the method, configured deadline,
execution phase, attempt, and verified reader generation without exposing
tracker data or paths. No partial result is returned. Retry the request once;
the timed-out helper is terminated and the next request starts cleanly. If the
same bounded request repeatedly times out, reduce its page or traversal limits
or capture the safe diagnostic and extension logs for investigation.

## Tools are absent

Confirm that the extension is enabled and consent was granted for the relevant
backend family. Main-process logs should show the read/query module registering
four tools and the projection module registering two. Use Nimbalyst Extension
Dev Tools to inspect extension status and logs; restarting Nimbalyst is not
normally required. An in-place upgrade from the older single-module package is
the exception: after install/reload, restart once so the host discards its
cached backend manifest, then approve first-use consent for the new projection
module. If status still lags, confirm that the installed manifest and bundles
contain the current version and look for `family=read tools=4` and
`family=projection tools=2` activation entries under the extension ID.

## `OUTPUT_DIRECTORY_NOT_FOUND`

The generation tool will not create an unknown directory or follow a symlink.
Create the intended folder inside the workspace first, then retry with the same
relative `.ntimeline` or `.md` path.

## Timeline is stale

The timeline is a durable projection file rather than a second database. Run
`native_tracker_sync_timeline` again after native tracker updates. The tool
preserves the document title and saved view settings while replacing the
bounded snapshot.
