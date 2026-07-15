# NAD-001 implementation review

The proposed architecture is suitable for the current Nimbalyst host and has
been implemented without changing its trust boundary.

## Findings

1. The native SQLite premise is confirmed by the local
   `database-backend.json` selection and the installed tracker schema.
2. The installed SDK marks its PGLite `host.data.query()` surface as unstable
   for the native SQLite transition. Direct read-only SQLite access behind a
   consent-gated backend module remains the appropriate version 0.3 boundary.
3. Nimbalyst's installed `nimbalyst-memory` backend confirms the supported MCP
   integration pattern: call `ctx.services.registerMcpTools()` during backend
   activation and return handlers under `methods`.
4. The observed tracker schema contains all required NAD-001 columns. Tracker
   content is plain text; metadata and comments live in the `data` JSON object;
   comment timestamps are epoch milliseconds.
5. The current starter workspace has been repurposed as the independent
   extension repository. Runtime code has no dependency on any product
   application repository.

## Deliberate boundaries

- This extension reads. It does not add a second write implementation.
- Durable changes continue through Nimbalyst's built-in `tracker_update` and
  `tracker_add_comment` tools after an agent has read the tracker and comments.
- Native comments become visible context; this implementation does not change
  which coordination record a separate factory process considers canonical.
- The external NAD-001 record remains unchanged. Implementation authorization
  does not silently rewrite its governance status.
- Timeline and Markdown generation write ordinary workspace files only. They do
  not write tracker rows or bypass Nimbalyst's relationship validation.
- `timeline-item`, `milestone`, and `timeline-link` use shipped field types and
  date/status roles. Existing built-in tracker schemas are not overridden.
- `timeline-link` records hold one source/type/target edge. Target-side
  backlinks and inverse labels are projection queries, not duplicated truth.

## Contract notes

Comment tools accept `trackerId`, `limit`, `cursor`, `since`, and `order`.
Timeline/report tools accept bounded filters plus a workspace-relative output
file. Unknown properties are rejected. The backend adds the active workspace
path after validation, so an MCP caller cannot override it. Structured failures
use stable error codes and never include database paths or tracker content.
