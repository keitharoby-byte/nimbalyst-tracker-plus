# Native Tracker Comments -- Nimbalyst Extension

This is a backend-only Nimbalyst extension that exposes bounded, read-only
tracker comment context to AI agents through MCP tools.

- Extension ID: `com.prediclear.nimbalyst-native-tracker-comments`
- Source: `src/` and `reader/`
- Generated output: `dist/` (never edit by hand)
- Runtime permission: `mcp-server-register` only

## Development workflow

1. Run `npm install` once.
2. Run `npm test` before and after changes.
3. Build with the Nimbalyst Extension Dev `extension_build` tool.
4. Install with `extension_install`; iterate with `extension_reload`.
5. Check the installed result with `extension_get_status` and backend logs.

Do not restart Nimbalyst unless the user explicitly asks. Prefer the bundled
Nimbalyst SDK documentation and local examples over invented host APIs.

## Safety invariants

- Do not add a tracker or database write path.
- Do not accept a workspace path, database path, or SQL statement from callers.
- Keep comment pagination, body limits, response limits, and schema checks.
- Keep identity output allowlisted and exclude soft-deleted items/comments.
- Never expose local database paths in tool responses or logs.
