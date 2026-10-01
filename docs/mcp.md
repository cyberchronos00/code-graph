# MCP server

code-graph ships a stdio [Model Context Protocol](https://modelcontextprotocol.io) server, so an AI agent gets exact callers, entry points and `file:line` evidence straight from the graph. It needs the `mcp` Python SDK (2.x).

## Run

```bash
.venv/bin/python -m codegraph.mcp_server --db out/graph.db --gates examples/bookstore.gates.json --plans examples/plans
```

`--db` defaults to `out/graph.db`; `--root` sets the project root for a single-repo DB (used by the `index` tool).

## Tools

- `reaches`, `impact`, `siblings`, `writers`, `node`, `stats`;
- `search(name, kind?, limit?)`: nodes by name / FQN substring, plus route middleware, guard, auth and access names
  with the routes that carry them (`search("auth")` finds `auth:api`, `ApiKeyGuard`, `IsAuthenticated`, …);
- `routes(writes?, reaches?, missing?, unguarded?, auth_pattern?, max_items?, paths?, min_confidence?)`: routes with
  their guards, scoped to the routes that write (`writes="*"` or a table) or reach any spec, filtered to routes
  without a named guard (`missing="auth:api"`) or without an auth-like guard (`unguarded=true`); each route shows one
  evidence chain and its frontend callers on a combined graph (see [cli.md](cli.md#routes-and-guards));
- `downstream` (forward dependencies), `path` (one shortest evidence chain between two specs), `api_calls` (frontend
  endpoints with call sites, request keys and the matched route + controller; `unmatched` filter);
- `resolutions(concept, within?, client?, detail?)` (see [value-facts.md](value-facts.md));
- `plan_list`, `plan_load`, `plan_validate`, `plan_check(name, verify?, details?, max_items?, review?)`, `plan_baseline`.
  `plan_check` replies with a compact summary by default: counts per section and per check, the top `max_items` (5)
  missing items, failed requirements, middleware gaps, forbidden paths, open findings and verify counts.
  `details=true` returns the full report with file:line evidence and call chains;
- `index(root?, gates?, repo?)`: re-indexes into temp files, then swaps the DBs atomically. On a combined DB, no
  arguments re-index every linked repo from its recorded root; `repo` (a name used at link time) picks one; a `root`
  picks the repo whose recorded root contains it, or every repo below it (a parent directory such as the workspace
  root). The link is rebuilt afterwards. A `root` that matches no recorded repo, an unknown `repo`, or a result with
  0 nodes is refused with an error and the current graph is kept;
- `coverage(path?)`: which languages and files the index covers (see [Coverage](#coverage)).

Rust, C and C++ graphs use the same tools. Specs take native forms (`kv_core::store::Store::get`, `ns::Class::method`,
`mod:crate::module`, a file path, `feature:`/`cfg:`/`define:`/`unsafe:`/`env:` nodes; see [native.md](native.md#query-specs)).
`reaches` groups results by module (Rust module path or C/C++ directory) and splits them into RUNTIME (`main`,
`ffi_export`), LIBRARY API (`public_api`) and DEV/BUILD-ONLY (tests, benches, examples, `build.rs`).

`impact` also lists the external clients recorded in snapshot files in the plans directory (`--plans`) that call an
affected route, marked `snapshot, not indexed`.

Replies are written for an agent's context window: paths are repo-relative (the working directory, the checkout and
the indexed repo roots are stripped; the home directory shows as `~`), and an empty result says why and suggests the
next call. `path` and `api_calls` add a line for keys a call site passes that the request never sends
(`sent but not forwarded`), and `resolutions` lists them in a section of their own.

## Coverage

`cg index` records, per language, how the files it found are covered: `exact`, `heuristic` (the exact-mode indexer,
such as rust-analyzer or scip-clang, is missing; the install hint is stored), `skipped` (the language's toolchain is
missing, for example `php` or `node`; the rest of the index still builds) or `unsupported` (no plugin: file counts by
extension such as `.go`, `.java`, `.kt`, `.swift`, `.rb`, `.cs`). The `coverage` tool prints that table; with `path` it
says whether a file or directory is in the graph. The server instructions carry the same rule, and replies that come
back empty or name an unknown symbol end with a coverage line. When a language or file is not covered or only
heuristic, the agent is told to use its normal search and file reading for that part: an empty cg answer there is not
proof of absence. On the CLI: `cg coverage --db out/graph.db`.

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

## Cursor CLI

The Cursor CLI (`agent`) reads the same `mcpServers` entry from `.cursor/mcp.json` in the project (or `~/.cursor/mcp.json`).

1. Install it (`curl https://cursor.com/install -fsS | bash`) and sign in (`agent login`, or set `CURSOR_API_KEY`).
2. Put the server entry in `.cursor/mcp.json` at the project root (paths absolute, or relative to `cwd`).
3. Allow the tools in `.cursor/cli.json`, so print mode can call them without a prompt:

   ```json
   {"permissions": {"allow": ["Mcp(code-graph:*)", "Read(**)"], "deny": []}}
   ```

4. Approve the server once: `agent mcp enable code-graph`. `agent mcp list` should show `code-graph: ready`, and
   `agent mcp list-tools code-graph` lists the tools.
5. Ask in the interactive UI (`agent`), or headless in print mode. `--trust` trusts the workspace without the
   interactive prompt; `--output-format stream-json --stream-partial-output` streams every tool call and text delta
   as JSON lines:

   ```bash
   agent -p --trust "Use code-graph: which API routes write data without auth?"
   agent -p --trust --output-format stream-json --stream-partial-output "Use code-graph to check plans/preorders.yaml" > run.jsonl
   ```

Put the [suggested agent instructions](#suggested-agent-instructions) in `.cursor/rules/code-graph.mdc` (with
`alwaysApply: true`) or `AGENTS.md`. `scripts/demo/agent-setup.sh` builds a complete example workspace this way.

## See it in action

The [agent demo (MP4, about 195 s)](media/cg-agent-demo.mp4) is a live Cursor CLI session (GPT-5.4 Mini at medium
reasoning) on a copy of the sample apps, recorded with `scripts/demo/record-agent.sh`. Every question is typed on camera
and the answers stream in as the model writes them:

1. a security review: the agent calls `routes(writes="*", unguarded=true)` and lists every route that writes data
   without an auth guard, with `file:line` evidence;
2. a plan check: `plan_check("preorders")` finds what the feature plan leaves out (admin form, API resource,
   `$fillable`, other entry routes, the mobile client) before any code is written;
3. fix everything: in the same chat, the agent guards every write route that has no auth and implements the
   `preorders` plan including what the plan missed, then calls `index` and re-runs `routes` and `plan_check` to verify;
   `git diff --stat` shows the change. It edits only the throwaway copy.

Before the plan question the video shows the plan (`examples/plans/preorders.yaml`, as copied without its header
comment). The three questions run in one chat (`agent create-chat`, then `--resume`), so the third can refer to the
first two. Task 3 ran 228 s; 178 s of it are shown at 6× speed with a visible note on screen (added afterwards with
ffmpeg; nothing is cut).

### Comparison without code-graph

[cg-agent-baseline.mp4](media/cg-agent-baseline.mp4) (about 285 s) is one live take of the same agent and model on the
same three tasks (in one chat) in a fresh copy with no MCP server configured (`scripts/demo/record-agent.sh baseline`, tape
`scripts/demo/agent-baseline.tape`). The prompts are the same without the words that name code-graph. It ends with
[this card](media/cg-agent-compare.png):

| task | with code-graph | without code-graph |
|---|---|---|
| 1. Security review: write routes without auth (3 in the code) | 12 s, 1 tool call, 46.4k in / 894 out tokens; found 3 of 3 | 40 s, 39 tool calls, 205.7k in / 4.6k out; found 3 of 3 |
| 2. Plan check: gaps in `preorders.yaml` (7 in the code) | 13 s, 1 tool call, 52.6k in / 1.1k out; found 7 of 7, plus the failed `auth:api` requirement | 26 s, 6 tool calls, 50.6k in / 3.6k out; found 3 of 7 (admin update path, `UpdateBookRequest`, mobile client) |
| 3. Fix everything: guard the routes, implement the plan, verify | 228 s, 56 tool calls (10 cg), 2,071.5k in / 23.5k out; routes guarded 3 of 3; gaps fixed 7 of 7 (6 in code, the mobile client recorded in the plan since the client is not in the copy); re-indexed, `plan_check` 0 missing, verify OK | 119 s, 50 tool calls, 1,127.1k in / 14.6k out; routes guarded 3 of 3; gaps fixed 4 of 7 (not the Filament form, the customer on the `POST /v1/orders` path, the mobile client); verified by re-reading the route file |
| total | 253 s, 58 tool calls, 2,170.5k in / 25.5k out | 185 s, 95 tool calls, 1,383.4k in / 22.8k out |

Time, tool calls and tokens come from the Cursor CLI's own usage report (input tokens include cached input); one live
take per side, so the numbers vary between runs. Results were checked against the code afterwards: both copies pass
`php -l` and re-index after task 3, and both also put `POST /v1/stock/reserve` behind `auth:api` as the plan requires.
The 7 plan gaps are the admin update path, `UpdateBookRequest`, `Book::$fillable`, the API and Filament
`BookResource`, the `POST /v1/orders` path (the customer passed to `reserve`) and the mobile client.

`scripts/demo/agent-stream.py` only reformats the CLI's stream-json events for the screen (one line per tool call,
text as it arrives). To see any tool's raw reply without an agent, `scripts/demo/mcp-session.py` is a small JSON-RPC
client:

```bash
.venv/bin/python scripts/demo/mcp-session.py tools
.venv/bin/python scripts/demo/mcp-session.py call impact '{"method": "StockService::reserve"}'
```

The [setup video (MP4)](media/cg-setup-demo.mp4) shows the Cursor `mcp.json` entry from a fresh clone.

## Suggested agent instructions

Paste something like this into your agent's rules file:

```text
Before changing code that touches a table, column, DB connection, config key or route, call the code-graph MCP tools:
- reaches(<target>) to see every function and entry point that depends on it (runtime vs command vs gated);
- impact(<Class::method>) before editing a method, and siblings(<Class::method>) to find parallel code paths;
- routes(writes="*", unguarded=true) or routes(reaches=[<target>]) to review which routes reach it and with which guards;
- for a planned feature, write plans/<name>.yaml, then plan_check(<name>) and resolve every MISSING FROM PLAN item
  (a small fix such as adding a guard needs no plan: edit, re-index, re-check);
Quote the file:line evidence from the tool output in your summary. Re-run the index tool after editing.
If coverage() or a reply's coverage note says a language or file is not covered (or heuristic only), use normal search
and file reading for that part.
```
