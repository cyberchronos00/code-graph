# Visual view

`codegraph.cli serve --db out/graph.db [--port 8177] [--host 127.0.0.1] [--plans-dir examples/plans] [--presets FILE]`:
stdlib HTTP server, read-only, Cytoscape.js + fcose vendored under `codegraph/viz/static/vendor` (MIT, versions in
`VERSIONS.txt`; everything is served locally, so it works offline). Open `http://127.0.0.1:8177/`.
- `/` opens a landing page: project, index time, node / edge / entry-point counts by kind and the share of edges by
  confidence (`/api/stats`), a search box with kind chips, fuzzy matching on short names and ↑/↓/Enter (Enter runs
  the default query for the hit's kind: reaches for tables, columns, config and env, downstream for pages and routes,
  impact otherwise), and the starter queries as cards with their `why` and CLI equivalent. Starters come from
  `--presets FILE` (a JSON list of `{id, label, mode, specs[, sinks]}`) or `viz.presets` in `.cg.yaml`, then the
  sample-app presets whose targets are in the graph, then starter queries derived from the graph (see
  [configuration.md](configuration.md#viz-presets-and-starter-queries)). The brand link returns to it; Back and
  Forward work.
- Toolbar: query (modes `reaches`, `impact`, `downstream` with sinks, `path` (`source, [waypoints…,] target`) and
  `plan overlay`), filters (min confidence, sinks) and view (layout, collapse / expand all, fit, copy link; behind `⋯`
  below 1440 px). It stays on one line from 1024 px up. State is in the URL hash
  (`#mode=impact&spec=…&expand=<cluster or group>[~shown]|…&select=<node id>&layout=…`), written as you open
  clusters, select a node or change the layout; **copy link** copies it, and the link reproduces the same visible
  elements, selection and panel. Each new query is a history entry, so Back returns to the previous one.
- The subgraph is the union of the query's evidence paths. `layout: auto` draws impact, downstream and path left to
  right in layers (impact: entry points left, target right, one column per depth; downstream: source left, sinks
  right; path: the specs in order); reaches and plan overviews use the force-directed fcose layout with module boxes.
  In the layered layout a layer with more than 12 nodes folds into counted clusters (`Account · 16 callers in 10
  modules (2 entry)`, `+15 callers in 11 modules`) by module, then by top-level folder; click a cluster to show its
  first 20 members in a lane next to it (again for the next 20), Esc or Backspace folds it; nothing outside the
  cluster moves. In fcose views nodes are grouped into boxes by module (`repo · module`, a common prefix stripped;
  columns by table; HTTP calls, settings and request keys by kind), one-node modules get no box, big modules start
  folded; click a folded box to open it, double-click an open box to fold it. Edges between folded items are merged
  and labelled with the evidence-edge count.
- Labels render at 11 px or more on screen: the first fit shows everything when that keeps leaf labels legible,
  otherwise the target and its neighbourhood with a "fit all (N)" button; zoomed out, leaf labels hide (cluster,
  module, target, entry-point and hovered / selected labels stay readable). Hover a node for its full name, kind,
  file:line and confidence.
- Colour = node kind; ◆ entry points with their kind (`[HTTP route]`, `[artisan]`, `[UI page]` …); ★ query targets;
  dashed red border = gated for the indexed gate scenario. Edges encode confidence by pattern and width as well as
  colour: solid 2 px = exact, dashed 1.5 px = resolved, dotted 1.5 px = heuristic (an edge folding several shows its
  strongest), red dashed = gated, amber = partly gated. Every edge and border colour is at least 3:1 against the
  canvas and a module box (WCAG 1.4.11; `tests/test_viz.py` checks the style table). The legend's `resolved` /
  `heuristic` chips hide and show those edges without a refetch, and the status line counts the evidence edges shown.
- The legend (bottom-left, collapsible) lists only what the view contains: the node kinds with counts, the shapes
  and edge styles present, and what the boxes mean. Click a kind to dim everything else (again, or a click on the
  canvas, to clear).
- Click a node: docblock, file:line, source snippet (read from the indexed roots), entry kinds (and those still live
  under the gate), gate evidence, evidence edges in the view, and its first 25 in/out DB edges with attributes.
- `codegraph.cli viz-export MODE SPEC… --db … -o file.html [--sinks table,column]`: the same view as one
  self-contained HTML file (data + JS inlined) that opens from disk.
- Screenshots and layout checks: `codegraph/viz/tools/shoot.mjs` (puppeteer-core with a locally installed Chrome;
  `SHOTS=file.json` for your own list, `PRESETS=1` for every starter, `WIDTHS=1280,1920`); it fails on console
  noise, toolbar overflow, labels under 11 px, more than 5% overlapping labels or one-node module boxes.
  `scripts/reproduce.sh` writes sample screenshots to `out/screenshots/` when Chrome is available.

