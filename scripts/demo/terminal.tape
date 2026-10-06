# Terminal demo of code-graph on the bundled sample apps (examples/bookstore-api + examples/bookstore-web).
# Rendered by charmbracelet/vhs (https://github.com/charmbracelet/vhs). Record it with
#   scripts/demo/record-terminal.sh
# which runs this tape from the repo root and encodes docs/media/cg-terminal-demo.{mp4,gif}.

Output out/demo/terminal-raw.mp4

Require jq

Set Shell "bash"
Set FontFamily "DejaVu Sans Mono"
Set FontSize 20
Set LineHeight 1.2
Set Width 1600
Set Height 900
Set Padding 30
Set Theme "Catppuccin Mocha"
Set Framerate 25
Set TypingSpeed 20ms
Set CursorBlink false

# Neutral setup: a plain '$ ' prompt, UTC, history off and a fresh output dir, so the screen shows only the demo.
Hide
Type `export PS1='$ ' PS2='  ' TZ=UTC LC_ALL=C.UTF-8 HISTFILE=/dev/null; cg() { .venv/bin/python -m cg_code_graph.cli "$@"; }`
Enter
Type "rm -f out/api.db* out/web.db* out/graph.db* && mkdir -p out && clear"
Enter
Sleep 500ms
Show

Sleep 800ms
Type "# Index the Laravel API and the Nuxt app, then link them into one graph, page to column"
Sleep 500ms
Enter
Sleep 400ms
Type "cg index examples/bookstore-api --gates examples/bookstore.gates.json --db out/api.db | jq -c '{nodes,edges}'"
Sleep 400ms
Enter
Sleep 1200ms
Type "cg index examples/bookstore-web --db out/web.db | jq -c '{nodes,edges}'"
Sleep 400ms
Enter
Sleep 1200ms
Type `cg link --backend out/api.db --frontend out/web.db --db out/graph.db \`
Enter
Type "  --backend-name bookstore-api --frontend-name bookstore-web | jq -c '{endpoints_matched}'"
Sleep 400ms
Enter
Sleep 2s

# 1. reaches: every code path into the warehouse, grouped by runtime, operator-only and flag-gated
Ctrl+L
Sleep 400ms
Type "# See every code path that reaches the warehouse, grouped by runtime, operator-only and flag-gated"
Sleep 400ms
Enter
Type "cg reaches connection:warehouse table:warehouse_stock --db out/graph.db --no-paths | head -n 20"
Sleep 400ms
Enter
Sleep 6500ms

# 2. path: frontend page to DB column, hop by hop, with file:line evidence
Ctrl+L
Sleep 400ms
Type "# Follow a Nuxt page to the DB column it reads, hop by hop, with file:line evidence"
Sleep 400ms
Enter
Type "cg path page:/reports/:id table:orders --db out/graph.db"
Sleep 400ms
Enter
Sleep 6s

# 3. impact: callers of a method and the routes that reach it
Ctrl+L
Sleep 400ms
Type "# Get every caller of StockService::reserve and the routes that reach it"
Sleep 400ms
Enter
Type "cg impact StockService::reserve --db out/graph.db"
Sleep 400ms
Enter
Sleep 5s

# 4. plan check: the full scope of a planned change
Ctrl+L
Sleep 400ms
Type "# Check the 'preorders' plan: every affected item it still needs to cover, with file:line"
Sleep 400ms
Enter
Type "cg plan check preorders --plans-dir examples/plans --db out/graph.db | head -n 18"
Sleep 400ms
Enter
Sleep 6500ms
