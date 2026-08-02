# NAD-001 synthetic scale benchmarks

These measurements use generated tracker rows only. They contain no customer,
workspace, product, launch, role, or issue identities.

Measurements were recorded on a Windows development host with CPython's
standard `sqlite3` module. `tests/python/make_scale_fixture.py` generated each
tier at a 1:1.5 item-to-relationship ratio. Each tier ran one warm-up followed
by five timed calls; p95 is the nearest-rank value.

The query used the externally packaged
`role-active-work-and-attention` template with the generic `coordinator` role.
The traversal used one synthetic launch root, depth-one incoming membership,
and two active relationship context levels under the standard 500-node and
1,000-edge limits.

| Items | Relationships | Query p95 | Traversal p95 | Result |
|---:|---:|---:|---:|---|
| 1,500 | 2,250 | 59.85 ms | 102.48 ms | Within target |
| 5,000 | 7,500 | 192.23 ms | 293.15 ms | Within target |
| 20,000 | 30,000 | 735.45 ms | 1,237.88 ms | Re-evaluate architecture |

The 5,000-item gate is below 250 ms for queries and 500 ms for traversal. The
reader builds a per-call relationship adjacency index while SQLite remains the
read-only authority.

At 20,000 items traversal exceeds one second. Installations approaching that
scale should evaluate a disposable, rebuildable local cache that retains the
same schema, registry, query-catalog, and source watermarks. Such a cache must
never become tracker authority.

Sorted samples in milliseconds:

- 1,500: query `57.12, 57.22, 59.43, 59.68, 59.85`; traversal
  `80.34, 82.97, 84.21, 88.76, 102.48`.
- 5,000: query `179.49, 181.37, 184.49, 189.61, 192.23`; traversal
  `275.44, 282.84, 286.42, 289.10, 293.15`.
- 20,000: query `705.57, 712.40, 715.14, 719.20, 735.45`; traversal
  `1,164.10, 1,220.97, 1,234.96, 1,236.30, 1,237.88`.
