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
