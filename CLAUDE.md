# Tracker+ -- Nimbalyst Extension

This extension exposes bounded tracker-comment context and read-only timeline /
milestone projections through MCP. It also contributes the `.ntimeline` custom
editor.

- Extension ID: `com.prediclear.nimbalyst-native-tracker-comments`
- Source: `src/` and `reader/`
- Generated output: `dist/` (never edit by hand)
- Runtime permissions: read/query backend `mcp-server-register`; projection backend additionally `workspace-files`

Agents operating the installed tools must follow
[`docs/agent-guide.md`](./docs/agent-guide.md). It defines exact arguments,
durable update/resync sequencing, normalized relationship rules, filter
semantics, PR references, and error recovery.

## Development workflow

1. Run `npm install` once.
2. Run `npm test` before and after changes.
3. Build with the Nimbalyst Extension Dev `extension_build` tool.
4. Install with `extension_install`; iterate with `extension_reload`.
5. Verify a representative `.ntimeline` file and check `extension_get_status`.

Do not restart Nimbalyst unless the user explicitly asks. Prefer bundled SDK
documentation and local examples over invented host APIs.

## Safety invariants

- Do not add a tracker or database write path.
- Do not accept a workspace path, database path, or SQL statement from callers.
- Keep comment, timeline, and report response limits plus schema checks.
- Keep identity output allowlisted and exclude deleted/archived records.
- Workspace writes must be explicit, relative, confined, extension-checked
  `.ntimeline` or `.md` files; never expose absolute paths in responses/logs.
- Custom editor changes must preserve `useEditorLifecycle` and Nimbalyst CSS
  variables.
