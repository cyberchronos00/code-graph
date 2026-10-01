# MCP server

code-graph ships a stdio [Model Context Protocol](https://modelcontextprotocol.io) server, so an AI agent can query the graph instead of grepping. It needs the `mcp` Python SDK (2.x).

## Run

```bash
.venv/bin/python -m codegraph.mcp_server --db out/graph.db --gates examples/bookstore.gates.json --plans examples/plans
```

`--db` defaults to `out/graph.db`; `--root` sets the project root for a single-repo DB (used by the `index` tool).

## Tools

- `reaches`, `impact`, `siblings`, `writers`, `node`, `search`, `stats`;
- `downstream` (forward dependencies), `path` (one shortest evidence chain between two specs), `api_calls` (frontend
  endpoints with call sites, request keys and the matched route + controller; `unmatched` filter);
- `resolutions(concept, within?, client?, detail?)` (see [value-facts.md](value-facts.md));
- `plan_list`, `plan_load`, `plan_validate`, `plan_check(name, verify?, max_items?, review?)`, `plan_baseline`;
- `index`: re-indexes into a temp file, then swaps the DB atomically. On a combined DB pass `repo` (a name used at link
  time): that repo's own DB is re-indexed from its recorded root and the link is rebuilt.

`tests/mcp_e2e.py` runs an SDK client end to end on the sample apps and writes `docs/mcp/sample_outputs.md`.

## Client config

Most MCP hosts (Cursor, Claude Desktop and others) accept an `mcpServers` entry with a stdio command. Replace `/path/to/code-graph` with your checkout:

```json
{"mcpServers": {"code-graph": {
  "command": "/path/to/code-graph/.venv/bin/python",
  "args": ["-m", "codegraph.mcp_server", "--db", "out/graph.db",
           "--gates", "examples/bookstore.gates.json", "--plans", "examples/plans"],
  "cwd": "/path/to/code-graph"}}}
```

Relative paths in `args` are resolved against `cwd`. Point `--db` at your own graph once you index a real project.

## Suggested agent instructions

Paste something like this into your agent's rules file:

```text
Before changing code that touches a table, column, DB connection, config key or route, call the code-graph MCP tools:
- reaches(<target>) to see every function and entry point that depends on it (runtime vs command vs gated);
- impact(<Class::method>) before editing a method, and siblings(<Class::method>) to find parallel code paths;
- for a planned change, write plans/<name>.yaml, then plan_check(<name>) and resolve every MISSING FROM PLAN item.
Quote the file:line evidence from the tool output in your summary. Re-run the index tool after editing.
```
