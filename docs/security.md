# Security model

Tracker+ is privileged local code because it opens a Nimbalyst
application database and spawns Python. Nimbalyst therefore keeps its utility
backends disabled until the user approves their first-use native-code prompts.

## Enforced controls

- The read/query backend requests only `mcp-server-register`. The projection
  backend additionally requests `workspace-files`. Neither requests
  `nimbalyst-database-write`, `secrets-read`, or network access.
- The database URI includes `mode=ro`; every connection immediately enables
  and verifies `PRAGMA query_only = ON`.
- SQL is fixed in source and values are parameterized. There is no SQL tool.
- Predicate trees are validated against a fixed field/operator registry before
  SQLite is touched. Caller values are always `?` bindings; title search also
  escapes `%`, `_`, and `\`. Query depth, clauses, list values, text length,
  page size, traversal roots/depth/nodes/edges, and serialized bytes are capped.
- The database path comes from Nimbalyst's local configuration. The
  `NIMBALYST_SQLITE_PATH` environment variable is only a process-start
  development/test override and is never an MCP argument.
- The workspace path comes from the backend host context. Every tracker lookup
  includes `workspace = ?` and `deleted_at IS NULL`.
- Raw `authorIdentity` data is never returned. Only `displayName`, `gitName`,
  `name`, or `username` may become the display-only author label.
- Deleted comments are filtered before sorting and pagination.
- Timeline snapshots exclude tracker bodies, comments, raw identities, and
  archives. They expose only bounded orientation, schedule, and relationship
  fields.
- Query and traversal envelopes apply the same identity allowlist and exclude
  deleted records. Archived records require an explicit query flag; traversal
  retains archived evidence only on active `evidences` edges with an effective
  revision.
- Workspace registry overrides are read only from
  `.nimbalyst/tracker-plus.registry.json`. Locked vocabulary or cap keys make
  the entire override invalid; bundled defaults remain active and the warning
  is surfaced in every response.
- Comment bodies are capped at 20,000 Unicode characters. Tracker bodies are
  capped at 100,000 characters. The serialized result is capped below the
  512-KiB process-line ceiling.
- The helper rejects input lines over 64 KiB, times out after five seconds,
  restarts at most once after a transport failure, and fails closed on unknown
  methods or schema drift.
- Logs contain operation names, duration, and error codes only. They never log
  titles, bodies, comment text, identity objects, email addresses, or paths.
- Generated paths must be relative, remain inside the current workspace, end
  in `.ntimeline` or `.md`, resolve through an existing in-workspace directory,
  and cannot be symbolic links. Writes use a temporary file followed by rename.
- The generation tools write only projection/report files. SQLite remains open
  in read-only and query-only modes for the entire operation.

## Residual risk

The extension depends on a private Nimbalyst schema and on ambient native-code
capabilities granted to utility backends. Schema fingerprints and required
column checks detect known structural drift, but a semantic change within the
same columns still requires compatibility testing after host upgrades.

Predicate failures use `QUERY_INVALID`, `FIELD_NOT_QUERYABLE`,
`OPERATOR_INVALID`, or `QUERY_TOO_COMPLEX`; saved-query and root failures have
dedicated codes. Error payloads never include SQL text, absolute paths, raw
identity objects, or tracker bodies/comments.
