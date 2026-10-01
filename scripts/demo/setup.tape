# Setup demo: fresh clone, install, first index, first query, visual view, MCP config for an AI agent.
# Every command shown is the README quickstart as written. Rendered by charmbracelet/vhs; record it with
#   scripts/demo/record-setup.sh
# which runs this tape and encodes docs/media/cg-setup-demo.mp4. The demo works in a throwaway HOME (/tmp/demo).

Output out/demo/setup-raw.mp4

Require git
Require python3
Require composer
Require npm
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
Set TypingSpeed 18ms
Set CursorBlink false
Set WaitTimeout 300s
Set WaitPattern /\$ *$/

# Neutral setup: a plain '$ ' prompt, a throwaway HOME, UTC and history off, so the screen shows only the demo.
Hide
Type `export HOME=/tmp/demo PS1='$ ' PS2='> ' TZ=UTC LC_ALL=C.UTF-8 HISTFILE=/dev/null PIP_DISABLE_PIP_VERSION_CHECK=1; set +m`
Enter
Type "rm -rf /tmp/demo && mkdir -p /tmp/demo && cd /tmp/demo"
Enter
# CG_CLONE_FROM (record-setup.sh): record a release before it is pushed; the clone of the public URL is served from
# that local repo instead, the commands on screen stay the README's.
Type `[ -n "$CG_CLONE_FROM" ] && git config --global url."$CG_CLONE_FROM".insteadOf https://github.com/cyberchronos00/code-graph.git; clear`
Enter
Sleep 500ms
Show

Sleep 600ms
Type "# Install: one clone, and every dependency lives inside the checkout"
Sleep 400ms
Enter
Type "git clone https://github.com/cyberchronos00/code-graph.git && cd code-graph"
Enter
Wait
Sleep 300ms
Type `python3 -m venv .venv && .venv/bin/pip install "mcp>=2.2" pyyaml pytest protobuf tree-sitter tree-sitter-rust tree-sitter-c tree-sitter-cpp`
Enter
Wait
Sleep 300ms
Type "(cd codegraph/plugins/php/extractor && composer install)"
Enter
Wait
Sleep 300ms
Type "(cd codegraph/plugins/ts/extractor && npm ci)"
Enter
Wait
Sleep 300ms
Type `cg() { .venv/bin/python -m codegraph.cli "$@"; }    # shorthand used below`
Enter
Sleep 1500ms

Ctrl+L
Sleep 300ms
Type "# Index the Laravel API and the Nuxt app, then link them into one graph"
Sleep 400ms
Enter
Type "mkdir -p out"
Enter
Type "cg index examples/bookstore-api --name bookstore-api --gates examples/bookstore.gates.json --db out/api.db > out/api.stats.json"
Enter
Wait
Type "cg index examples/bookstore-web --name bookstore-web --db out/web.db > out/web.stats.json"
Enter
Wait
Type `cg link --backend out/api.db --frontend out/web.db \`
Enter
Type "        --backend-name bookstore-api --frontend-name bookstore-web --db out/graph.db > out/link.stats.json"
Enter
Wait
Type "jq -c '{nodes, edges}' out/api.stats.json out/web.stats.json; jq -c '{endpoints_matched}' out/link.stats.json"
Enter
Sleep 2500ms

Ctrl+L
Sleep 300ms
Type "# First query: every caller of StockService::reserve and the routes that reach it, with file:line"
Sleep 400ms
Enter
Type "cg impact StockService::reserve --db out/graph.db"
Enter
Wait
Sleep 6s

Ctrl+L
Sleep 300ms
Type "# Explore the same graph in your browser: search, click a node, see its source and evidence"
Sleep 400ms
Enter
Type "(cg serve --db out/graph.db --plans-dir examples/plans &); sleep 1"
Enter
Wait
Sleep 3500ms

Ctrl+L
Sleep 300ms
Type "# Connect your AI agent: add code-graph to Cursor's MCP config (~/.cursor/mcp.json)"
Sleep 400ms
Enter
Set TypingSpeed 8ms
Type "mkdir -p ~/.cursor && cat > ~/.cursor/mcp.json <<'EOF'"
Enter
Type '{"mcpServers": {"code-graph": {'
Enter
Type '  "command": "/tmp/demo/code-graph/.venv/bin/python",'
Enter
Type '  "args": ["-m", "codegraph.mcp_server", "--db", "out/graph.db",'
Enter
Type '           "--gates", "examples/bookstore.gates.json", "--plans", "examples/plans"],'
Enter
Type '  "cwd": "/tmp/demo/code-graph"}}}'
Enter
Type "EOF"
Enter
Set TypingSpeed 18ms
Sleep 500ms
Type "# Quick check: the configured server command starts and lists its options"
Sleep 300ms
Enter
Type ".venv/bin/python -m codegraph.mcp_server --help"
Enter
Wait
Sleep 1500ms
Type "# Reload Cursor: your agent can now call reaches, impact, path, plan_check and 15 more cg tools"
Sleep 400ms
Enter
Sleep 5s

Hide
Type "pkill -f '[c]odegraph.cli serve --db out/graph.db --plans-dir examples/plans'; true"
Enter
Sleep 500ms
