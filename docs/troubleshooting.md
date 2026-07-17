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

## `TRACKER_NOT_FOUND`

The id or issue key does not resolve inside the current workspace. The same
issue key in another workspace is intentionally invisible.

## `DATABASE_BUSY`

Retry after a short delay. The reader uses a bounded one-second SQLite busy
timeout and never holds a connection between tool calls.

## Tools are absent

Confirm that the extension is enabled and that backend consent has been
granted. Use Nimbalyst Extension Dev Tools to inspect extension status and
main-process extension logs; restarting Nimbalyst is not normally required.
If install/reload reports success while the status helper lags, confirm that the
installed manifest and bundle contain the current version and look for an
`addon.activate` entry under the extension ID in the main-process log.

## `OUTPUT_DIRECTORY_NOT_FOUND`

The generation tool will not create an unknown directory or follow a symlink.
Create the intended folder inside the workspace first, then retry with the same
relative `.ntimeline` or `.md` path.

## Timeline is stale

The timeline is a durable projection file rather than a second database. Run
`native_tracker_sync_timeline` again after native tracker updates. The tool
preserves the document title and saved view settings while replacing the
bounded snapshot.
