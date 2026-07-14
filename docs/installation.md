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
5. Enable **Native Tracker Comments** in Extensions settings.
6. Invoke `native_tracker_get_with_comments` or
   `native_tracker_list_comments` and approve the first-use native-code prompt.
7. During iteration use `extension_reload`, then check `extension_get_status`
   and the main-process extension logs.

The installed extension is available across Nimbalyst projects. Each call is
still scoped to the workspace associated with that AI session.

## Rollback

Disable the extension, revoke its backend consent grant, and uninstall it.
There is no database rollback because the extension creates no tables and
writes no tracker data.
