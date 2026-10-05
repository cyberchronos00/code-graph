# Planned changes

## What a plan is

A **plan** is an agreed scope in `<plans-dir>/<name>.yaml`. Loading or checking it never writes the graph DB. The same plan and the same code always produce the same report. Example: `examples/plans/preorders.yaml` (incomplete on purpose, so `plan check` has something to find).

## File

`cg plan validate` reports unknown keys and wrong shapes. `plan_version` is `1`. Specs match the rest of cg (exact id, `Class::method`, `table:<t>`, `route:<METHOD> <uri>`, …). In a plan, a class spec is the class node, and `Class::method` must resolve to one method.

```yaml
plan_version: 1
name: preorders
title: "..."
status: draft    # draft | agreed | in_progress | implemented | abandoned
issues: ["#7"]
context: {findings: findings.yaml, clients: clients.yaml}
add_nodes: [{id: column:books.preorder_until, attrs: {nullable: true}}]
modify: [{target: StockService::reserve, intent: "...", role: guard, issues: ["#7"]}]
add_edges: [{from: StockService::reserve, kind: READS_COLUMN, to: column:books.preorder_until}]
forbid: [{id: no-warehouse, type: path, from: StockService::reserve, to: table:warehouse_stock}]
require: [{route: "route:POST /v1/stock/reserve", middleware: ["auth:api"]}]
covers: [{spec: ..., note: ...}]
out_of_scope: [{spec: ..., reason: ...}]
```

Also used: `rationale`, `assumptions`, `precedents`. The full sample is the example file above.

## Check

```bash
cg plan check <name> --plans-dir examples/plans --db out/graph.db
```

MCP: `plan_check`. The report has four parts: **Resolve** (every reference resolves; planned nodes must not exist yet), **Missing from plan** (writers, readers, callers, pages and other gaps, each with file:line), **Conflicts** (forbidden paths or edges that still exist), and with `--verify` a **verify** section. One line per item. `--json` is the full report; `-o` writes it to a file. Flags: `cg plan check -h`.

## Verify

1. Write the plan.
2. `cg plan validate`, then `cg plan check`. Put each gap in `modify` / `add_edges`, `covers` or `out_of_scope`.
3. `cg plan baseline` writes `<plans-dir>/<name>.baseline.json` (sha1 of each modified target's source).
4. Implement, then re-index.
5. `cg plan check <name> --verify`. `verify_ok` is true only when planned nodes, edges, forbids and requirements all pass.

## Overlay

In `cg serve`, mode **plan overlay** (or `#mode=plan&spec=<name>`, optional `&verify=1`). Or `cg viz-plan <name> --db … -o file.html` for one HTML file. The panel opens on the check report.
