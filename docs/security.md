# Security model

Native Tracker Comments is privileged local code because it opens a Nimbalyst
application database and spawns Python. Nimbalyst therefore keeps its utility
backend disabled until the user approves the first-use native-code prompt.

## Enforced controls

- The manifest requests only `mcp-server-register`. It does not request
  `nimbalyst-database-write`, `secrets-read`, or network access.
- The database URI includes `mode=ro`; every connection immediately enables
  and verifies `PRAGMA query_only = ON`.
- SQL is fixed in source and values are parameterized. There is no SQL tool.
- The database path comes from Nimbalyst's local configuration. The
  `NIMBALYST_SQLITE_PATH` environment variable is only a process-start
  development/test override and is never an MCP argument.
- The workspace path comes from the backend host context. Every tracker lookup
  includes `workspace = ?` and `deleted_at IS NULL`.
- Raw `authorIdentity` data is never returned. Only `displayName`, `gitName`,
  `name`, or `username` may become the display-only author label.
- Deleted comments are filtered before sorting and pagination.
- Comment bodies are capped at 20,000 Unicode characters. Tracker bodies are
  capped at 100,000 characters. The serialized result is capped below the
  512-KiB process-line ceiling.
- The helper rejects input lines over 64 KiB, times out after five seconds,
  restarts at most once after a transport failure, and fails closed on unknown
  methods or schema drift.
- Logs contain operation names, duration, and error codes only. They never log
  titles, bodies, comment text, identity objects, email addresses, or paths.

## Residual risk

The extension depends on a private Nimbalyst schema and on ambient native-code
capabilities granted to utility backends. Schema fingerprints and required
column checks detect known structural drift, but a semantic change within the
same columns still requires compatibility testing after host upgrades.
