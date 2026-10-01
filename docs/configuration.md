# Configuration

code-graph needs no config file to index a project. The optional inputs are listed here.

## Gate scenarios (`--gates`)
A gates file names scenarios and the settings that are true in each, e.g. `examples/bookstore.gates.json`:
`new_inventory` = `features.new_inventory.enabled`.

How it works:
- The PHP extractor emits a control skeleton per function.
- `codegraph/plugins/php/gating.py` abstract-interprets it, seeded by setting reads through the scenario's
  `setting_accessors` (e.g. `getSetting(<key>)`) and by memoised method summaries, so wrappers such as
  `FeatureGate::usesNewInventory()` are understood too (see `tests/gating_fixture`).
- A branch is dead when its path condition is unsatisfiable *because of* a scenario fact.
- Edges made from dead code get `edges.gate=<scenario>` plus `attrs.guard`/`guard_expr`.

Schema additions: `edges.gate`, `node_entry_live(scenario, …)` (entry tagging that skips gated edges), `gate_predicates`.

Usage: index with `--gates FILE`, then query with `reaches … --gate auto` (or `none`, or a scenario name). Each item
reports `gate_status`: `live`, `gated_target` or `gated_entry`. In the sample, `Admin\InventoryController::index` only
reaches the `warehouse` connection inside the old-inventory branch, so it is listed as gated for `new_inventory`.


Gates file format (see `examples/bookstore.gates.json`):

```json
{
  "scenarios": [
    {
      "name": "new_inventory",
      "description": "free text",
      "setting_accessors": ["getSetting"],
      "true_settings": ["features.new_inventory.enabled"],
      "false_settings": []
    }
  ]
}
```

Beta: one scenario per index (the first one).

## Viz presets (`serve --presets FILE`)

The preset menu in the visual view. The built-in presets target the sample apps. For your own project, pass a JSON list:

```json
[
  {"id": "warehouse_reaches", "label": "what reaches the warehouse connection", "mode": "reaches",
   "specs": ["connection:warehouse", "table:warehouse_stock"]},
  {"id": "report_page_tables", "label": "report page -> tables", "mode": "downstream",
   "specs": ["page:/reports/:id"], "sinks": ["table", "column"]},
  {"id": "plan_preorders", "label": "plan overlay: pre-orders", "mode": "plan", "specs": ["preorders"]}
]
```

`mode` is one of `reaches`, `impact`, `downstream`, `path`, `plan`.

## Screenshots (`codegraph/viz/tools/shoot.mjs`)

`node shoot.mjs <base-url> <out-dir>` against a running `serve`. Environment: `CHROME` (browser binary, default
`/usr/bin/google-chrome`), `SHOTS` (JSON list of `{file, hash}`, where `hash` is the view's URL hash), `ONLY`
(substring filter on file names). Install with `PUPPETEER_SKIP_DOWNLOAD=1 npm ci` in `codegraph/viz/tools`.

## Plans directory

`plan …`, `viz-plan` and `serve` take `--plans-dir DIR` (MCP server: `--plans DIR`). Default: `plans/` in the
repository root.

## Environment variables

| variable | effect |
|---|---|
| `CODEGRAPH_NO_CACHE=1` | disable the TS extractor facts cache |
| `CODEGRAPH_CACHE=DIR` | cache location (default `~/.cache/codegraph`) |
