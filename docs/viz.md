# Visual view

`codegraph.cli serve --db out/graph.db [--port 8177] [--host 127.0.0.1] [--plans-dir examples/plans] [--presets FILE]`:
stdlib HTTP server, read-only, Cytoscape.js + fcose vendored under `codegraph/viz/static/vendor` (MIT, versions in
`VERSIONS.txt`; no CDN at runtime). Open `http://127.0.0.1:8177/`.
- Search box with suggestions; modes `reaches`, `impact`, `downstream` (sinks: tables via columns / routes / all),
  `path` (`source, [waypoints…,] target`) and `plan overlay`; min confidence; presets (built-in ones target the sample
  app; `--presets FILE` loads a JSON list of `{id, label, mode, specs[, sinks]}`). State is in the URL hash
  (`#mode=reaches&spec=…&expand=<group>|…&select=<node id>`), so views can be bookmarked.
- The subgraph is the union of the query's evidence paths. Nodes are grouped into boxes by module (`repo · module`;
  columns by table; HTTP calls, settings and request keys by kind). Big modules start folded into one box with counts;
  click a folded box to open it, double-click an open box to fold it. Edges between folded boxes are merged and
  labelled with the evidence-edge count.
- Colour = node kind; ◆ entry points with their kind (`[HTTP route]`, `[artisan]`, `[UI page]` …); ★ query targets;
  dashed red border = gated for the indexed gate scenario. Edges: solid = exact, dashed = resolved, dotted = heuristic,
  red dashed = gated.
- Click a node: docblock, file:line, source snippet (read from the indexed roots), entry kinds (and those still live
  under the gate), gate evidence, evidence edges in the view, and its first 25 in/out DB edges with attributes.
- `codegraph.cli viz-export MODE SPEC… --db … -o file.html [--sinks table,column]`: the same view as one
  self-contained HTML file (data + JS inlined) that opens from disk.
- Screenshots: `codegraph/viz/tools/shoot.mjs` (puppeteer-core with a locally installed Chrome; `SHOTS=file.json`
  for your own list). `scripts/reproduce.sh` writes sample screenshots to `out/screenshots/` when Chrome is available.

