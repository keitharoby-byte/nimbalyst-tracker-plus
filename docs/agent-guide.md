# Tracker+ agent guide

This guide is the operating contract for agents using Tracker+ in a Nimbalyst
workspace. It covers tool selection, exact arguments, durable update flow,
normalized relationships, timeline filters, pull-request references, and safe
failure handling.

## Mental model

Tracker+ has three distinct surfaces:

1. The four `native_tracker_*` tools read the current workspace's native
   tracker database. The comment tools return bounded context; the generation
   tools write a workspace-relative `.ntimeline` or `.md` artifact.
2. Nimbalyst's built-in `tracker_create`, `tracker_update`,
   `tracker_add_comment`, and related tools own durable tracker mutations.
3. A `.ntimeline` file is a replaceable projection. Its view preferences are
   durable, but its `snapshot` is regenerated from native tracker truth.

Never treat a hand-edited snapshot as a tracker update. Change the native item
with a built-in tracker tool, then run `native_tracker_sync_timeline` again.

## Enablement and discovery

- The installed extension is named **Tracker+**.
- Its stable extension ID is
  `com.prediclear.nimbalyst-native-tracker-comments`.
- The backend is consent-gated. The first `native_tracker_*` call may require
  the user to approve native-code and workspace-file access.
- MCP clients may add a server prefix to tool names. Select the tool whose name
  ends with the documented `native_tracker_*` suffix.
- Every call is automatically scoped to the workspace associated with the
  current AI session. Do not send a workspace path or database path.

If the tools are absent, confirm that Tracker+ is enabled and backend consent
has been granted. Use the extension status and main-process logs before
considering a restart; a restart is not part of normal recovery.

## Recommended operating sequence

1. Orient with `native_tracker_get_with_comments` for the item being changed.
2. Follow `page.nextCursor` with `native_tracker_list_comments` only when more
   discussion is needed.
3. Make the durable change with Nimbalyst's built-in tracker tools.
4. Create or update one normalized `timeline-link` record when topology or
   evidence changes. Never create a second record merely to store a backlink.
5. Run `native_tracker_sync_timeline` to refresh the projection.
6. Inspect Timeline, Graph, Reports, validation findings, and the provenance
   watermark.
7. Generate a milestone report when a durable status artifact is required.

## `native_tracker_get_with_comments`

Use this as the default orientation call before changing one item. It returns
the tracker summary and body, one bounded comment page, pagination metadata,
and source provenance. It never mutates tracker data.

```json
{
  "trackerId": "NIM-123",
  "limit": 20,
  "order": "newest"
}
```

Arguments:

- `trackerId` is required and accepts an issue key or internal item ID.
- `limit` defaults to 20 and must be from 1 through 100.
- `cursor` accepts the opaque `page.nextCursor` from a previous call.
- `since` is an optional ISO-8601 lower bound for comment creation time.
- `order` is `newest` or `oldest` and defaults to `newest`.

Use the returned body and comments for context. Use `tracker_update` or
`tracker_add_comment` for any durable response.

## `native_tracker_list_comments`

Use this when only comment history is needed or to continue pagination.

```json
{
  "trackerId": "NIM-123",
  "limit": 50,
  "cursor": "opaque-next-cursor",
  "order": "oldest"
}
```

The argument rules match `native_tracker_get_with_comments`. Stop when
`page.hasMore` is false. Never construct or modify a cursor.

## `native_tracker_sync_timeline`

Use this after native tracker, relationship, schedule, risk, or PR-reference
changes. It writes a bounded projection and preserves the existing document
title, view settings, and state filters.

```json
{
  "outputPath": "planning/Tracker Timeline.ntimeline",
  "includeUnscheduled": true,
  "maxItems": 500,
  "from": "2026-07-01",
  "to": "2026-09-30"
}
```

Arguments:

- `outputPath` defaults to `Tracker Timeline.ntimeline`. It must be a
  workspace-relative `.ntimeline` path without `..`. Its parent directory must
  already exist.
- `includeUnscheduled` defaults to `true`.
- `maxItems` defaults to 300 and must be from 1 through 500.
- `from` and `to` are optional ISO-8601 schedule bounds. Scheduled items are
  included when their start/target interval overlaps the range; undated items
  remain eligible when `includeUnscheduled` is true.

The result reports item, milestone, and relationship counts plus truncation and
source metadata. If it is truncated, narrow the date range or remove irrelevant
records before treating the projection as complete.

The generated snapshot includes:

- independent workflow, schedule health, execution constraint, and risk;
- normalized relationships and inverse display labels;
- primary milestone and launch-scope hydration;
- schedule slack, deterministic risk reasons, and calculated critical path;
- validation findings and projection provenance.

## `native_tracker_generate_milestone_report`

Use this for a durable Markdown status report for one milestone or all major
milestones.

```json
{
  "outputPath": "reports/Alpha Milestone Report.md",
  "milestoneId": "NIM-456",
  "asOf": "2026-07-16",
  "lookaheadDays": 45,
  "maxItems": 500
}
```

Arguments:

- `outputPath` defaults to `Milestone Report.md`. It must be a
  workspace-relative `.md` path whose parent already exists.
- `milestoneId` accepts an issue key or internal ID. Omit it to report every
  milestone.
- `asOf` is an optional ISO-8601 report date and defaults to today.
- `lookaheadDays` defaults to 30 and must be from 1 through 365.
- `maxItems` defaults to 500 and must be from 1 through 500.

Reports derive normalized relationships; they do not emit the obsolete
links/backlinks sections.

## Durable tracker updates

Tracker+ intentionally has no tracker-write API. Use Nimbalyst's built-in
tools after reading current context.

Example metadata and PR update:

```json
{
  "id": "NIM-123",
  "status": "in-review",
  "progress": 85,
  "fields": {
    "scheduleHealth": "at-risk",
    "executionConstraint": "waiting",
    "pullRequestNumber": 42,
    "pullRequestUrl": "https://github.com/example/repository/pull/42"
  }
}
```

Only schema-defined fields can be updated. The supplied `timeline-item` and
`milestone` schemas define `pullRequestNumber` and `pullRequestUrl`. A number is
still displayed when no URL exists. Imported items tagged as pull requests can
also derive both values from a native `github://owner/repository#number`
origin URN.

The inspector uses Nimbalyst's host-owned tracker-reference chip. Select it to
open the live tracker item; do not construct a `nimbalyst://` URL manually.

## Normalized relationship records

Store each edge once:

`source item → relationship type → target item`

The target-side backlink and inverse wording are queries over that one record.
To create an edge, use `tracker_create` with type `timeline-link`. Relationship
fields use the internal item ID returned by native tracker tools.

```json
{
  "type": "timeline-link",
  "title": "Implementation task contributes to Alpha milestone",
  "status": "active",
  "fields": {
    "sourceItem": { "itemId": "task_internal_id" },
    "relationshipType": "contributes-to",
    "targetItem": { "itemId": "milestone_internal_id" },
    "directedness": "directed",
    "contributionRole": "primary",
    "effectiveRevision": "project-state-r12"
  }
}
```

Supported relationship types are `depends-on`, `contributes-to`, `reviews`,
`evidences`, `implements`, and `related`.

Additional controls:

- Dependency modes: `finish-to-start`, `start-to-start`,
  `finish-to-finish`, or `start-to-finish`.
- Hardness: `hard-serial`, `shared-resource`, or `soft-coordination`.
- Lifecycle status: `active`, `cleared`, or `superseded`.
- `leadLagDays` accepts -365 through 365.
- `entryEvidence`, `exitEvidence`, and `evidenceSources` are relationship
  fields containing item-reference objects.

Rules agents must preserve:

- A hard-serial dependency needs an owner and clearing condition.
- A review edge needs explicit entry and exit evidence.
- A launch-scoped task has exactly one primary milestone contribution.
- Secondary contributions are allowed and do not create another parent.
- `related` may be symmetric; dependency edges remain directed.
- Clear or supersede an existing edge instead of creating inverse truth.

## Timeline view filters

The **Filters** menu applies to Timeline and Graph. Completion options are
`Active` and `Complete`; schedule options are `On track`, `At risk`, and
`Late`. Multiple selections are ORed inside a group and the two groups are
ANDed together.

For example, selecting `Active`, `On track`, and `At risk` shows active work
whose schedule is either on track or at risk. Deselecting `Complete` hides
finished work without changing its native status. Filters are document view
preferences, survive re-sync, and never mutate tracker items.

## Reading validation and critical path

- Workflow describes what the team is doing.
- Schedule health describes forecast credibility.
- Execution constraint describes whether the next action can proceed.
- Risk combines likelihood, impact, evidence, durability, and escalation
  floors.
- Critical path is calculated from durations and active hard-serial edges; it
  is not a manually assigned status.

Treat validation errors as data-governance findings, not automatic permission
to rewrite source records. Read the affected items and relationships first.

## Safety and error handling

Do not:

- send database paths, workspace paths, SQL, or absolute output paths;
- edit `dist/` by hand;
- write directly to the SQLite database;
- fabricate dates for undated evidence or completed history;
- duplicate backlinks or infer hierarchy from dependency topology;
- ignore `page.hasMore`, truncation flags, schema fingerprints, or snapshot
  timestamps.

Common recoveries:

- `TRACKER_NOT_FOUND`: confirm the issue key belongs to the current workspace.
- `DATABASE_BUSY`: retry after a short delay.
- `SCHEMA_INCOMPATIBLE`: stop and update the adapter/tests for the new schema.
- `OUTPUT_DIRECTORY_NOT_FOUND`: create the intended workspace directory, then
  retry the same relative output path.
- Missing tools: confirm extension enablement and backend consent, then inspect
  extension logs.

See [troubleshooting.md](troubleshooting.md) for the complete recovery list and
[security.md](security.md) for the trust boundary.

## Development handoff

Code agents must read the repository `CLAUDE.md` and follow this validation
sequence:

```powershell
npm install
npm test
npm run verify:package
```

Build with the Nimbalyst Extension Dev `extension_build` tool, install with
`extension_install`, iterate with `extension_reload`, then inspect extension
status and main/renderer logs. Verify a representative `.ntimeline` file when
an editor is mounted. Do not restart Nimbalyst unless the user explicitly asks.

