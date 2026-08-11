# Roles in Tracker+

Tracker+ uses the word **role** in two separate ways. Keeping them distinct
prevents surprising query or dispatch results:

1. **Query roles** group owner aliases and attention tags under a stable
   `roleId`. They answer questions such as “what work needs a quality
   reviewer?”
2. **Relationship roles** describe how one item participates in a graph, such
   as whether an item is core launch scope or supporting evidence.

Neither kind of role grants permissions, changes an assignee, or creates a
user account. They are matching and graph-classification metadata only.

## Query roles

A query role has three parts:

- The **role ID** is the stable value passed to a saved query, for example
  `quality-reviewer`.
- **Owner aliases** are owner values that represent the role, for example
  `qa-reviewer` or `quality-reviewer`.
- **Attention tags** include work that needs the role even when somebody else
  owns the item, for example `needs-quality-review`.

Matching is case-insensitive. Owners can be strings or native identities; for
native identities Tracker+ checks `username`, `name`, `displayName`, and
`gitName`.

The bundled catalog includes the deliberately generic `coordinator` role. An
installer can add roles such as:

| Example role ID | Useful for | Example owner aliases | Example attention tags |
|---|---|---|---|
| `delivery-coordinator` | Cross-team sequencing and readiness | `delivery-coordinator`, `release-coordinator` | `needs-coordination`, `release-attention` |
| `quality-reviewer` | Test evidence and acceptance review | `quality-reviewer`, `qa-reviewer` | `needs-quality-review`, `qa-attention` |
| `security-reviewer` | Security review requests | `security-reviewer`, `security-team` | `needs-security-review`, `security-attention` |
| `documentation-owner` | Documentation work and review | `documentation-owner`, `docs-team` | `needs-docs`, `documentation-attention` |

These names are examples, not required workflow. Prefer aliases and tags that
already exist in the installation. Avoid broad aliases such as `team`, `lead`,
or `reviewer`, which can unintentionally match unrelated work.

### Install example roles

Copy [`examples/tracker-plus.registry.roles.json`](../examples/tracker-plus.registry.roles.json)
to `.nimbalyst/tracker-plus.registry.json` in the workspace, then edit or
remove the examples to fit the installation. Role entries merge by ID with the
bundled catalog.

A role inbox query then looks like:

```json
{
  "savedQuery": {
    "id": "role-active-work-and-attention",
    "params": { "roleId": "quality-reviewer" }
  }
}
```

The `role-active-work-and-attention` query is supplied by the copy-ready
[`examples/tracker-plus.queries.json`](../examples/tracker-plus.queries.json)
catalog. Copy that catalog into `.nimbalyst/tracker-plus.queries.json`, or
define an equivalent workspace query, before using this invocation.

This selects current, non-archived work when either the owner matches an alias
or the item has an attention tag. It does not change ownership. The same
`roleId` can optionally narrow `dispatch-eligible-work-v1`.

## Relationship roles

Relationship roles belong to individual graph edges, not people:

| Field/value | Meaning |
|---|---|
| `part-of-launch.scopeRole = core` | The item is a launch member and may qualify for dispatch. |
| `scopeRole = supporting` | The item supports the launch but is not core dispatch scope. |
| `scopeRole = acceptance` | The item represents acceptance or exit-criteria context. |
| `scopeRole = review` | The item participates as review context. |
| `scopeRole = evidence` | The item is evidence supporting a decision or gate. |
| `scopeRole = dependency-only` | The item is present only to explain ordering or blocking. |
| `contributes-to.contributionRole = primary` | The contribution is the primary milestone path admitted by the default dispatch policy. |

For example, the same task may have a `core` membership edge to one launch and
a `supporting` edge to another. Tracker+ keeps both because `scopeRole` and
`contributionRole` are part of relationship identity.

Query-role configuration does not alter these graph roles. If an installation
wants different dispatch admission rules, override the complete
`dispatchPolicy` in `.nimbalyst/tracker-plus.registry.json` as documented in
the [agent guide](agent-guide.md#dispatch-eligibility-contract).

## Practical checks

- Use a stable role ID even if display names or team membership change.
- Keep aliases specific and verify they match actual owner values.
- Use attention tags for requests, not as a substitute for ownership.
- Run `role-active-work-and-attention` after adding a role and inspect the
  echoed expanded query before acting on results.
- Treat an unknown `roleId` as a configuration error; Tracker+ fails closed
  instead of silently broadening the query.
