# Planned changes

A **plan** records an agreed scope as a small versioned YAML file, `<plans-dir>/<name>.yaml`. It is an overlay:
loading or checking it never writes to the graph DB. The checks compare it with the real graph deterministically, so the same plan and code always give the same report.
Example: `examples/plans/preorders.yaml` (deliberately incomplete, so `plan check` has something to find).

Schema (`plan_version: 1`; `codegraph plan validate` reports unknown keys and wrong shapes with their path):
```yaml
plan_version: 1
name: preorders                      # slug, = file name
title: "..."
status: draft|agreed|in_progress|implemented|abandoned
issues: ["#7"]                       # linked findings (also per item)
context: {findings: findings.yaml, clients: clients.yaml}   # read-only snapshots next to the plan
rationale: |  free text
assumptions: ["only authenticated customers place orders"]
add_nodes:   [{id: column:books.preorder_until, attrs: {nullable: true}}]   # kind:key; column, table, method, route, setting, ...
modify:      [{target: StockService::reserve, intent: "...", role: guard, issues: ["#7"]}]
add_edges:   [{from: StockService::reserve, kind: READS_COLUMN, to: column:books.preorder_until}]
forbid:      [{id: no-warehouse-for-preorders, type: path, from: StockService::reserve, to: table:warehouse_stock,
               when: "...", guard: {at: StockService::reserve, reads: column:books.preorder_until}}]  # type: edge needs edge_kind
require:     [{route: "route:POST /v1/stock/reserve", middleware: ["auth:api"]}]
covers:      [{spec: ..., note: ...}]          # in scope, no change expected (silences a gap)
out_of_scope: [{spec: ..., reason: ...}]
precedents:  [{name: atomic counter, pattern: "increment\\(", within: [StockService::recordSale]}]
```

Specs are the same as everywhere else (exact node id, `Class::method`, `table:<t>`, `route:<METHOD> <uri>`, ...). In a plan, a
class spec means the class node, and `Class::method` must resolve to exactly one method; otherwise it is reported as ambiguous
with candidates. Request keys, settings, config and env keys named by a planned edge count as planned implicitly.

`codegraph plan check <name> --plans-dir examples/plans --db out/graph.db` (MCP `plan_check`) reports:
1. **Resolve**: every referenced node resolves. Planned nodes must not exist yet. Route middleware requirements are
   checked against the route's `middleware` attribute. Entry routes that reach the *same* modified method are compared, and
   any middleware a peer route has but this one lacks is listed.
2. **Missing from plan**: things the change touches that no plan entry covers, each with file:line:
   - writers of a changed table (payload writers are "missing"; others are "review");
   - readers of a changed table, tagged `bypasses <guard>` when they reach the table without passing the `role: guard` method;
   - Filament resources whose `$model` is the table's model (form saves are not WRITES edges);
   - `$fillable` lacking a new column;
   - JsonResources built by the table's code or named `<Model>Resource`;
   - FormRequests that validate most of the table's columns but not the new one;
   - direct callers and entry points of every modified method, with the call chain;
   - frontend pages calling affected routes (HTTP_CALLS/MATCHES_ROUTE), and external client call sites from the snapshot,
     matched with the same route matcher as the link step;
   - parallel tables/models (column-set overlap ≥ 50 %) and mirrored or same-class sibling
     methods reached from the modified code;
   - identity columns on other tables (e.g. `orders.book_id`) and their readers;
   - `text_mention`: exact-regex hits for those names in PHP/Blade files that have no column edge on that line (Blade views,
     JsonResource `$this->x`). These are labelled and never become edges;
   - **precedents**: plan-declared regexes searched in the modified methods' source.
3. **Conflicts**: forbidden paths or edges that still exist, with the chain. When the plan declares a guard, the report
   says whether it is present yet. It also lists open findings from the snapshot whose evidence lines fall inside nodes the
   plan touches (`linked in plan` / `NOT LINKED`).
4. With `--verify`: the **verify** section (see the workflow below).

The text report is compact: one line per item, `@ file:line`, and chains as `A -KIND@file:line-> B`. `--json` gives
everything; `-o` writes the report to a file.

**Workflow for agents**
1. Write `<plans-dir>/<name>.yaml`: the scope as agreed, issue links, forbidden paths, requirements.
2. Run `plan_validate`, then `plan_check`. Work through **MISSING FROM PLAN** and decide each item: add it to `modify`/`add_edges`,
   or to `covers` (handled, no change) or `out_of_scope` (with a reason). Link the **unlinked findings** or explain them.
3. Run `plan_baseline` (writes `<plans-dir>/<name>.baseline.json`: sha1 of each modified target's source span).
4. Implement.
5. Re-index (MCP `index`, or `codegraph index` + `link`).
6. Run `plan_check(verify=true)`. It shows:
   - planned nodes and edges that now exist in the real graph, with file:line. A planned READS_COLUMN also accepts
     MENTIONS_COLUMN or a same-class helper the method calls (`via reserveLocal`);
   - each modified target as `changed`/`UNCHANGED` against the baseline;
   - forbidden paths that are gone or now guarded (the guard method reads the declared column);
   - requirements that are met.

   `verify_ok` is true only when nodes, edges, forbids and requirements all pass. Any completeness gaps still open are
   listed next to it.

Overlay view: `serve` → mode **plan overlay** (preset "PLAN overlay: pre-orders", or `#mode=plan&spec=<name>`
[`&verify=1`]), or `codegraph viz-plan <name> --db … -o file.html` for a self-contained file.
- Planned new nodes: green, dashed. Planned edges: green, dashed.
- Modified targets: amber ring (hexagon = guard).
- Uncovered items: magenta halo plus a dotted magenta check relation to what they touch. Review items: dotted lilac.
- Covered items: green ring.
- Forbidden paths that still exist: red edges with a ⊣ mark; the forbidden target has a double red border.
- Filed issues are nodes with dashed lilac `touches` edges; a red border means the issue is not linked in the plan.
- Grey edges are real indexed evidence.

The panel starts with the full check report; clicking a node shows its role, every check that flagged it, and its evidence
and source. `&focus=<id>` zooms to a node's neighbourhood.

On the sample, `plan check preorders` resolves all references and reports 10 items missing from the plan (the Filament
form and the API resource for `Book`, `Book::$fillable`, `UpdateBookRequest`, the admin update writer, the
`POST /v1/orders` caller and entry route, the mobile client from the snapshot, ...), the forbidden path
`StockService::reserve → warehouse_stock` that still exists, the missing `auth:api` on `POST /v1/stock/reserve`
(its peer route `POST /v1/orders` has it) and an open finding that is not linked. `tests/bookstore_impl` is an
implementation of the plan as an overlay on the sample; `tests/test_plans.py` checks that verify mode reaches OK on it.

