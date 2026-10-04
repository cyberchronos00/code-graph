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
  modules (2 entry)`, `+15 callers in 11 modules`) by module, then by top-level folder, and the whole view keeps to
  30 top-level items: when the layers together would show more, the widest ones fold further (at least 3 items each)
  and the smaller layers fold too; click a cluster to show its
  first 20 members in a lane next to it (again for the next 20), Esc or Backspace folds it; nothing outside the
  cluster moves. In fcose views nodes are grouped into boxes by module (`repo · module`, a common prefix stripped;
  columns by table; HTTP calls, settings and request keys by kind), one-node modules get no box, big modules start
  folded; click a folded box to open it, double-click an open box to fold it. Edges between folded items are merged
  and labelled with the evidence-edge count.
- Labels render at 11 px or more on screen. The first fit always shows every node; with no cluster open the layer
  gaps narrow (down to 170 px) to fit the canvas width. When leaf labels would still be under 11 px a "readable zoom"
  button zooms to the target and its neighbourhood (`f` / *fit* fits all again); zoomed out, leaf labels hide (cluster,
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
- The panel opens on the query target with a view summary (callers per depth, entry points by kind, gated count).
  Click a node (or right-click for a menu): a sticky header with name, kind, file:line (middle-truncated, full path on
  hover), copy buttons for id / FQN / the `cg impact` command, *open in editor* (`vscode://file/…`, served view only),
  and actions *impact of this*, *downstream of this*, *path from here to the target* (each a new query and history
  entry); docblock, source snippet (scrolls sideways, *wrap* toggle), entry kinds (and those still live under the
  gate), gate evidence, the evidence edges in the view grouped by callers / callees and confidence (collapsible,
  sorted by depth and name), and the first 25 in/out DB edges. Drag the panel's left edge (or ← → on it) to resize
  it, ⟩ collapses it to a rail; both are remembered.
- Keyboard: `/` search, `Enter` run, `Esc` fold / clear / close, `f` fit, `+` `-` zoom, `0` reset, ← → move to a
  caller / callee (into an open cluster's members and back), ↑ ↓ within the column, `Enter` / `Space` opens a cluster
  or shows a node, `Backspace` folds, `l` list view, `?` the shortcut list. The canvas is focusable with a visible
  ring.
- Export: **PNG** downloads the whole drawing at 2x on the current theme's background, **JSON** the visible
  subgraph (nodes, edges of the shown confidences, query and URL); file names come from mode and spec.
- Theme: **theme** cycles auto (follows `prefers-color-scheme`) / light / dark, kept in localStorage. Node colour
  is one of eight Okabe–Ito-derived families (code, UI, routes, data, config, jobs, external, findings), and the
  shape tells kinds apart inside a family (method circle, function small circle with a border, class rounded
  rectangle, table barrel …). The colour checks in `tests/test_viz.py` cover both themes (edges / borders >= 3:1,
  node fills >= 3:1, label text >= 4.5:1).
- Accessibility: the canvas has `role="img"` with a generated summary, the status line is `aria-live`, and **list**
  (or `l`) shows the same subgraph as a tree by depth that opens the same panel. axe-core reports no serious or
  critical issue on the landing and graph views in either theme.
- Performance: `pixelRatio` is capped at 2; fcose runs at `quality: 'default'` and, after an expand or collapse,
  refines the kept positions instead of starting over. A truncated result shows a banner. `shoot.mjs` records the
  time to first graph, layout time, the longest main-thread task and the rendered element count per view, and fails
  above 1.5 s / 1.5 s / 400 elements (`THEME=dark` shoots the dark theme).
- `codegraph.cli viz-export MODE SPEC… --db … -o file.html [--sinks table,column]`: the same view as one
  self-contained HTML file (data + JS inlined) that opens from disk.
- Screenshots and layout checks: `codegraph/viz/tools/shoot.mjs` (puppeteer-core with a locally installed Chrome;
  `SHOTS=file.json` for your own list, `PRESETS=1` for every starter, `WIDTHS=1280,1920`); it fails on console
  noise, toolbar overflow, labels under 11 px, more than 5% overlapping labels or one-node module boxes.
  `scripts/reproduce.sh` writes sample screenshots to `out/screenshots/` when Chrome is available.

