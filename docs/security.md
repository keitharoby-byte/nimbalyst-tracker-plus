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
  `packetId` supports only exact, bounded-list, and presence predicates and is
  returned only as a bounded normalized string.
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
  revision. Archived item rows never synthesize active inline relationships;
  explicitly stored active relationships keep unavailable-endpoint validation.
- Legacy `customFields` compatibility follows 128 mapping envelopes by default
  and accepts a per-call bound from 1 through a locked maximum of 512 on
  timeline, report, query, and traversal tools. It stops on repeated object
  identity and fails closed if the effective depth cap is exceeded. Nearer
  fields take precedence so deeper legacy values cannot override current data.
- Workspace policy overrides are read only from
  `.nimbalyst/tracker-plus.registry.json`; saved-query catalog overrides are
  read only from `.nimbalyst/tracker-plus.queries.json`. Both are validated
  atomically. The query catalog replaces the complete saved-query inventory;
  missing or malformed catalogs activate no saved queries. Locked
  vocabulary/cap keys or malformed definitions surface a warning in every
  response.
- Dispatch posture overrides use a closed signal/classification matrix and
  replace the complete versioned posture atomically. Packet revision,
  currentness, QA revision, and QA status cannot be downgraded. Conditional
  database routing depends only on the explicit boolean `databaseBearing`
  evidence signal; advisory values retain their trusted source and disposition
  in receipts without affecting admission.
- Comment bodies are capped at 20,000 Unicode characters. Tracker bodies are
  capped at 100,000 characters. Each serialized result page is capped below the
  512-KiB process-line ceiling. Paginated traversal fitting includes finalized
  cursor, completion, truncation, and validation metadata in that budget before
  returning a page. Predicate queries and opt-in standard
  traversals can retrieve a complete logical result through opaque,
  identity-bound continuation cursors without raising that per-response limit.
  Composed traversals remain atomic and fail closed on truncation. Non-paged
  dispatch also fails closed when the complete result cannot fit one response.
- Dispatch pagination preserves atomic admission: the complete logical result
  is validated and fingerprinted before a page is emitted. Opaque cursors bind
  receipt/boundary and edge offsets to that result, reject changed results, and
  never authorize a candidate independently of the complete evaluation.
- The helper rejects input lines over 64 KiB and uses bounded, operation-aware
  deadlines scaled by requested page or graph size. A timeout fails closed with
  no partial result, records only the method, configured deadline, execution
  phase, attempt, and verified generation, terminates that helper, and does not
  consume the transport retry reserved for recoverable pipe failures. Unknown
  methods and schema drift also fail closed.
- Logs contain operation names, duration, and error codes only. They never log
  titles, bodies, comment text, identity objects, email addresses, or paths.
- Generated paths must be relative, remain inside the current workspace, end
  in `.ntimeline` or `.md`, resolve through an existing in-workspace directory,
  and cannot be symbolic links. Writes use a temporary file followed by rename.
- The generation tools write only projection/report files. SQLite remains open
  in read-only and query-only modes for the entire operation.
- Dispatch role attention tags affect routing only. They do not infer launch
  scope or eligibility, and launch validation reads the selected roots' active
  typed membership graph independently of candidate admission. Missing
  endpoints, lifecycle evidence, or row evidence remain terminal.

## Residual risk

The extension depends on a private Nimbalyst schema and on ambient native-code
capabilities granted to utility backends. Schema fingerprints and required
column checks detect known structural drift, but a semantic change within the
same columns still requires compatibility testing after host upgrades.

Predicate failures use `QUERY_INVALID`, `FIELD_NOT_QUERYABLE`,
`OPERATOR_INVALID`, or `QUERY_TOO_COMPLEX`; saved-query and root failures have
dedicated codes. Error payloads never include SQL text, absolute paths, raw
identity objects, or tracker bodies/comments.
