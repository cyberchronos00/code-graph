# Visual view

## Open

```bash
cg serve --db out/graph.db [--port 8177] [--host 127.0.0.1] [--plans-dir examples/plans] [--presets FILE]
```

Read-only stdlib HTTP server. Cytoscape.js and fcose are vendored (MIT, versions in `VERSIONS.txt`) and served locally, so the UI works offline. Open `http://127.0.0.1:8177/`. There is no auth; keep the host on localhost. Flags: `cg serve -h`.

Watch the [visual view demo](media/cg-view-demo.mp4).

## What you can do

- **Landing.** Project, index time, counts, a search box (kind chips, fuzzy short names, ↑/↓/Enter) and starter cards. Starters come from `--presets FILE` or `viz.presets`, then sample presets whose targets are in the graph, then queries derived from the graph ([configuration.md](configuration.md#viz-presets-and-starter-queries)).
- **Query.** Modes `reaches`, `impact`, `downstream` (with sinks), `path` (`source, [waypoints…,] target`) and `plan overlay`. Filters: min confidence and sinks. The subgraph is the union of the evidence paths.
- **Layout.** Impact, downstream and path draw in layers; reaches and plan overviews use fcose with module boxes. Wide layers fold into clusters; click a cluster or a folded box to open it.
- **Detail.** Click a node for file:line, a source snippet, evidence edges and actions that run a new query (`impact of this`, `downstream of this`, `path from here`). Copy id, FQN or the `cg impact` command. Drag the panel edge to resize it; the width is remembered.
- **Search default.** Enter on a hit runs the default query for its kind: reaches for tables, columns, config and env; downstream for pages and routes; impact otherwise.
- **Plan overlay.** Loads a plan on top of the graph. Colours mark planned nodes, modified targets, gaps and forbidden paths ([plans.md](plans.md)).
- **Share.** The URL hash stores the query, selection and layout. **Copy link** copies it.

Layered views read left to right: impact (entry points, then the target), downstream (source, then sinks), path (the specs in order). A layer with more than 12 nodes folds into counted clusters. Click a cluster to show its next 20 members; Esc folds it. In the force-directed view, nodes sit in module boxes (columns by table). Click a folded box to open it; double-click an open box to fold it.

## Reading the graph

Colour is the node kind. A diamond marks an entry point (its kind is in the label), a star marks the query target, and a dashed red border means the node is gated for the indexed scenario. Edges encode confidence as well as colour: solid for exact, dashed for resolved, dotted for heuristic. The legend lists only what this view contains; click a kind to dim the rest.

Hover a node for its full name, kind, file:line and confidence. The first fit shows every node. When leaf labels would fall under 11 px, zoom to the target; cluster, module and selected labels stay visible zoomed out. **List** (`l`) shows the same subgraph as a tree. **Theme** cycles auto (follows the system theme), light and dark.

## Keyboard

`/` search, `Enter` run, `Esc` fold or close, `f` fit, `+` `-` zoom, `0` reset, arrows move along callers and callees, `l` list view, `?` shortcut list.

## Export

`cg viz-export MODE SPEC… --db … -o file.html` writes the same view as one HTML file (data and JS inlined) that opens from disk. `MODE` is `reaches`, `impact`, `downstream` or `path`. Optional `--sinks table,column`.

In the browser, **PNG** downloads the whole drawing at 2x on the current theme. **JSON** downloads the visible subgraph (nodes, edges of the shown confidences, the query and the URL). File names come from the mode and the spec.
