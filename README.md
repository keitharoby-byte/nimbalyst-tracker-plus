# Native Tracker Comments

Native Tracker Comments is a consent-gated Nimbalyst extension that makes the
native SQLite tracker comment history readable to authorized AI agents. It is a
read-only visibility bridge: durable updates still go through Nimbalyst's
built-in `tracker_update` and `tracker_add_comment` tools.

This is an independent community extension, not an official Nimbalyst feature.
Version 0.1.0 is Windows-first and was validated with Nimbalyst 0.68.1. Because
it adapts a private SQLite schema, retest it after upgrading Nimbalyst.

## Agent tools

- `native_tracker_list_comments` returns a bounded, paginated comment history.
- `native_tracker_get_with_comments` returns the tracker orientation fields,
  markdown body, and a bounded comment page in one call.

Both tools take their workspace from the Nimbalyst backend context. Callers
cannot choose a workspace, database path, or SQL statement.

## Safety model

- The helper opens SQLite with `mode=ro` and immediately enables
  `PRAGMA query_only = ON`.
- Every lookup includes the current workspace and excludes soft-deleted items.
- Comment identity objects are reduced to an allowlisted display label.
- Deleted comments are excluded; individual bodies and total responses are
  capped and explicitly marked when truncated.
- The extension requests only `mcp-server-register`; it never requests the
  Nimbalyst database write broker.
- Python uses only the standard library, avoiding Electron native-module ABI
  packaging.

## Development

```powershell
npm install
npm test
npm run build
npm run verify:package
```

Build, install, reload, and inspect the extension through Nimbalyst Extension
Dev Tools as described in [installation.md](docs/installation.md). Do not run
the build script directly when developing inside Nimbalyst.

The implementation follows the project's NAD-001 architecture decision. See
[implementation-review.md](docs/implementation-review.md) for the review
findings, [compatibility.md](docs/compatibility.md) for the validated host
matrix, and [security.md](docs/security.md) for the trust boundary.

## License

MIT. See [LICENSE](LICENSE).
