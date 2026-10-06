#!/usr/bin/env bash
# Prepare the throwaway workspace for the live agent demo (scripts/demo/agent.tape).
#   DEMO_HOME (default /tmp/demo)      generic HOME the recording runs under
#   $DEMO_HOME/cg                      cg runtime: a copy of this checkout's cg_code_graph/ + a link to its .venv
#   $DEMO_HOME/bookstore-copy          copy of the bundled sample apps + plans; the agent works (and patches) HERE only,
#                                      the planted bugs in examples/ are never touched
#   $DEMO_HOME/bookstore-copy/.cg      graph DBs (api.db, web.db, combined graph.db) built from the copy
#   $DEMO_HOME/bookstore-copy/.cursor/mcp.json   cg MCP server entry (docs/mcp.md), read by the Cursor CLI
# Safe to re-run: it rebuilds bookstore-copy from examples/ each time (so a live patch can be repeated).
# BASELINE=1 builds the comparison workspace instead (default DEMO_HOME /tmp/demo-base): the same copy of the apps and
# plans, the same permissions and short-answer note, but no graph, no MCP server and nothing that mentions code-graph.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
BASELINE="${BASELINE:-}"
if [ -n "$BASELINE" ]; then DEMO_HOME="${DEMO_HOME:-/tmp/demo-base}"; else DEMO_HOME="${DEMO_HOME:-/tmp/demo}"; fi
W="$DEMO_HOME/bookstore-copy"
CG="$DEMO_HOME/cg"
[ -n "$BASELINE" ] && CG="$DEMO_HOME/tools"   # the baseline only gets the transcript renderer, in tools/bin
[ -x "$REPO/.venv/bin/python" ] || { echo "missing $REPO/.venv (see README quickstart)" >&2; exit 1; }

mkdir -p "$DEMO_HOME"
rm -rf "$CG" "$W"
mkdir -p "$CG" "$W/.cursor"
[ -n "$BASELINE" ] || mkdir -p "$W/.cg"
cp -r "$REPO/cg_code_graph" "$CG/cg_code_graph"
find "$CG" -name __pycache__ -prune -exec rm -rf {} +
ln -s "$REPO/.venv" "$CG/.venv"

cp -r "$REPO/examples/bookstore-api" "$REPO/examples/bookstore-web" "$W/"
cp -r "$REPO/examples/plans" "$W/plans"
[ -n "$BASELINE" ] || cp "$REPO/examples/bookstore.gates.json" "$W/gates.json"
# The example plan's header comment names what the plan leaves out (it documents the sample for humans); drop it in the
# copy so the agent has to find the gaps itself. The plan content is unchanged.
sed -i '/^# Example plan for the bundled/d; /^# Deliberately incomplete/d; /^# the API\/Filament BookResource/d' "$W/plans/preorders.yaml"

SHORT='Keep the final answer short (about 15 lines or fewer): findings with file:line evidence, no code blocks.'
if [ -n "$BASELINE" ]; then
  # Comparison workspace: same permissions minus the MCP tools, same short-answer note, no graph and no MCP config.
  cat > "$W/.cursor/cli.json" <<'JSON'
{"permissions": {"allow": ["Read(**)", "Write(bookstore-api/**)", "Write(bookstore-web/**)"],
                 "deny": ["Write(.cursor/**)", "Write(plans/**)"]}}
JSON
  mkdir -p "$W/.cursor/rules"
  printf -- '---\ndescription: answer style\nalwaysApply: true\n---\n%s\n' "$SHORT" > "$W/.cursor/rules/answers.mdc"
  printf '# Agent notes\n%s\n' "$SHORT" > "$W/AGENTS.md"
else
  cg() { (cd "$CG" && .venv/bin/python -m cg_code_graph.cli "$@"); }
  cg index "$W/bookstore-api" --name bookstore-api --gates "$W/gates.json" --db "$W/.cg/api.db" > /dev/null
  cg index "$W/bookstore-web" --name bookstore-web --db "$W/.cg/web.db" > /dev/null
  cg link --backend "$W/.cg/api.db" --frontend "$W/.cg/web.db" --db "$W/.cg/graph.db" \
    --backend-name bookstore-api --frontend-name bookstore-web > /dev/null

  cat > "$W/.cursor/mcp.json" <<JSON
{"mcpServers": {"code-graph": {
  "command": "$CG/.venv/bin/python",
  "args": ["-m", "cg_code_graph.mcp_server", "--db", "$W/.cg/graph.db",
           "--gates", "$W/gates.json", "--plans", "$W/plans"],
  "cwd": "$CG"}}}
JSON

  # Project permissions for the Cursor CLI: cg tools, reads anywhere in the copy, writes only to the sample app sources.
  # Anything else (shell commands, other writes) is not auto-approved and is rejected in print mode.
  cat > "$W/.cursor/cli.json" <<'JSON'
{"permissions": {"allow": ["Mcp(code-graph:*)", "Read(**)", "Write(bookstore-api/**)", "Write(bookstore-web/**)"],
                 "deny": ["Write(.cursor/**)", "Write(.cg/**)", "Write(plans/**)"]}}
JSON

  # The agent instructions suggested in docs/mcp.md (plus a short-answer line for the video), as an always-applied
  # project rule (and AGENTS.md for other agents).
  mkdir -p "$W/.cursor/rules"
RULE='Before changing code that touches a table, column, DB connection, config key or route, call the code-graph MCP tools:
- reaches(<target>) to see every function and entry point that depends on it (runtime vs command vs gated);
- impact(<Class::method>) before editing a method, and siblings(<Class::method>) to find parallel code paths;
- routes(writes="*", unguarded=true) or routes(reaches=[<target>]) to review which routes reach it and with which guards;
- for a planned feature, write plans/<name>.yaml, then plan_check(<name>) and resolve every MISSING FROM PLAN item
  (a small fix such as adding a guard needs no plan: edit, re-index, re-check);
Quote the file:line evidence from the tool output in your summary. Re-run the index tool after editing.
If coverage() or a reply'"'"'s coverage note says a language or file is not covered (or heuristic only), use normal search
and file reading for that part.
'"$SHORT"
  printf -- '---\ndescription: use the code-graph MCP tools\nalwaysApply: true\n---\n%s\n' "$RULE" > "$W/.cursor/rules/code-graph.mdc"
  printf '# Agent notes\n%s\n' "$RULE" > "$W/AGENTS.md"
fi

# Live transcript renderer for the CLI's stream-json events (see agent-stream.py), on PATH as `agent-stream`
# ($CG/bin: cg/bin, or tools/bin for the baseline).
mkdir -p "$CG/bin"
cp "$REPO/scripts/demo/agent-stream.py" "$CG/bin/agent-stream"
chmod +x "$CG/bin/agent-stream"
[ -n "$BASELINE" ] && rm -rf "$CG/cg_code_graph" "$CG/.venv"

# A local git repo in the copy so the live patch can be shown with `git diff` (generic identity, never pushed).
(cd "$W" && { [ -n "$BASELINE" ] && printf '.cursor/\n' || printf '.cg/\n.cursor/\n'; } > .gitignore && git init -q -b main && git add -A \
  && git -c user.name=demo -c user.email=demo@example.com commit -qm "bookstore sample (copy)")

# Approve the project's MCP server for the Cursor CLI (stored under $DEMO_HOME/.cursor, not in the repo).
AGENT="${AGENT:-$(command -v agent || command -v cursor-agent || true)}"
[ -n "$AGENT" ] || { echo "Cursor CLI not found (curl https://cursor.com/install -fsS | bash)" >&2; exit 1; }
[ -n "$BASELINE" ] || (cd "$W" && HOME="$DEMO_HOME" "$AGENT" mcp enable code-graph > /dev/null)
# `agent` on the demo PATH, so the recording never shows the recorder's own HOME.
mkdir -p "$DEMO_HOME/.local/bin"
ln -sfn "$(readlink -f "$AGENT")" "$DEMO_HOME/.local/bin/agent"
echo "workspace ready: $W"
