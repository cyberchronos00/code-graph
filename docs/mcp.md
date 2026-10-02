# MCP server

code-graph ships a stdio [Model Context Protocol](https://modelcontextprotocol.io) server, so an AI agent gets exact callers, entry points and `file:line` evidence straight from the graph. It needs the `mcp` Python SDK (2.x).

## Run

```bash
.venv/bin/python -m codegraph.mcp_server --db out/graph.db --gates examples/bookstore.gates.json --plans examples/plans
```

`--db` defaults to `out/graph.db`; `--root` sets the project root for a single-repo DB (used by the `index` tool).

## Tools

- `reaches`, `impact`, `siblings`, `writers`, `node`, `stats`;
- `callers(symbol, min_confidence?, limit?)`: direct callers of a function, method or class (one level, with the call
  site and confidence; `ref@file:line` for code that takes the function as a value, such as a dispatch table or a
  callback); `impact` follows them up to the entry points and lists those references in a `by reference` line;
- `search(name, kind?, limit?)`: nodes by name / FQN substring, plus route middleware, guard, auth and access names
  with the routes that carry them (`search("auth")` finds `auth:api`, `ApiKeyGuard`, `IsAuthenticated`, …);
- `routes(writes?, reaches?, missing?, unguarded?, auth_pattern?, max_items?, paths?, min_confidence?)`: routes with
  their guards, scoped to the routes that write (`writes="*"` or a table) or reach any spec, filtered to routes
  without a named guard (`missing="auth:api"`) or without an auth guard from the framework presets or the auth name
  pattern (`unguarded=true`); each route shows one
  evidence chain and its frontend callers on a combined graph (see [cli.md](cli.md#routes-and-guards));
- `downstream` (forward dependencies), `path` (one shortest evidence chain between two specs), `api_calls` (frontend
  endpoints with call sites, request keys and the matched route + controller; `unmatched`, a substring or a `*` glob
  such as `GET /v1/*/orders*` as the filter);
- `channels(pattern?, source?)`: broadcast channels with who can join (auth route, callback source, the checks it
  calls), the events that publish on them and the client code that subscribes (see
  [channels-and-tests.md](channels-and-tests.md#broadcast-channels));
- `tests_covering(target, min_confidence?, paths?)`: the tests that exercise a symbol, route or table, direct and
  transitive, closest first, with the test cases in the graph per framework (PHPUnit, Pest, Vitest, Jest,
  Playwright, Cypress, pytest, unittest; see [channels-and-tests.md](channels-and-tests.md#tests));
- `resolutions(concept, within?, client?, detail?)` (see [value-facts.md](value-facts.md));
- `plan_list`, `plan_load`, `plan_validate`, `plan_check(name, verify?, details?, max_items?, review?)`, `plan_baseline`.
  `plan_check` replies with a compact summary by default: counts per section and per check, the top `max_items` (5)
  missing items, failed requirements, middleware gaps, forbidden paths, open findings and verify counts.
  `details=true` returns the full report with file:line evidence and call chains;
- `index(root?, gates?, repo?)`: re-indexes into temp files, then swaps the DBs atomically. On a combined DB, no
  arguments re-index every linked repo from its recorded root; `repo` (a name used at link time) picks one; a `root`
  picks the repo whose recorded root contains it, or every repo below it (a parent directory such as the workspace
  root). The link is rebuilt afterwards. A `root` that matches no recorded repo, an unknown `repo`, or a result with
  0 nodes is refused with an error and the current graph is kept. `.cg.yaml` is re-read on every re-index (an invalid
  file is refused the same way), and Python source roots given with `cg index --python-root` and
  `--include-generated` are kept;
- `coverage(path?, all_files?, json_output?)`: which languages and files the index covers, unsupported source types and
  blind spots (see [Coverage and completeness](#coverage-and-completeness)), and the Python source roots with their
  origin ([python.md](python.md)); on a combined DB, per linked repo. `json_output=true` adds the roots as
  `python_source_roots`.
- `platforms()`: the project's targets and where they come from (`.cg.yaml`, Flutter platform folders, Expo
  `app.json`, React Native, Electron / Tauri, the conditions), conditions found and evaluated, files and symbols per
  target ([platforms.md](platforms.md));
- `platform_divergence(target?, kind?, max_items?)`: variants that leave a declared target uncovered, API
  differences between variants, and calls / imports live on a target where the callee is not built, each with
  file:line;
- `starters()`: starter queries derived from the graph, each with the tool call to run: the write route without an
  auth guard that writes the most tables, the most-written and most-read tables, the busiest DB connection and env
  key, the page with the largest backend reach and the most-called functions. Every starter resolves to existing
  nodes, so it is a good first call on an unfamiliar repository; the visual view's preset menu offers the same list.

Rust, C and C++ graphs use the same tools. Specs take native forms (`kv_core::store::Store::get`, `ns::Class::method`,
`mod:crate::module`, a file path, `feature:`/`cfg:`/`define:`/`unsafe:`/`env:` nodes; see [native.md](native.md#query-specs)).
`reaches` groups results by module (Rust module path or C/C++ directory) and splits them into RUNTIME (`main`,
`ffi_export`), LIBRARY API (`public_api`) and DEV/BUILD-ONLY (tests, benches, examples, `build.rs`).

`reaches`, `impact`, `downstream`, `path`, `routes` and `search` take `platform` (windows, linux, macos, ios, android,
web) for one target's build. The reply's first line names the filter, how many symbols and references it left out
and how many conditions could not be evaluated for that target (their code stays in); the structured content carries
the same as `platform` (`{platform, excluded_nodes, excluded_edges, unevaluated_conditions}`). Platform-specific
symbols are labelled with their targets (`[ios, android]`) in every reply. See [platforms.md](platforms.md).

`impact` also lists the external clients recorded in snapshot files in the plans directory (`--plans`) that call an
affected route, marked `snapshot, not indexed`.

Replies are written for an agent's context window: paths are repo-relative (the working directory, the checkout and
the indexed repo roots are stripped; the home directory shows as `~`), and an empty result says why and suggests the
next call. `path` and `api_calls` add a line for keys a call site passes that the request never sends
(`sent but not forwarded`), and `resolutions` lists them in a section of their own.

## Coverage and completeness

`cg index` records, per language, the parser mode (`exact`, `heuristic` when the exact-mode indexer such as
rust-analyzer or scip-clang is missing, `skipped` when the toolchain such as `php` or `node` is missing, `unsupported`
for source types without a plugin) and the file completeness (discovered, indexed, parse failed, over the size limit,
unmapped, excluded), plus the blind spots it detected: route and handler registrations cg does not model, with
`file:line` samples. The `coverage` tool prints that report (`all_files=true` for every path); with `path` it says
whether a file or directory is in the graph, and for a generated or copied file names the reason (edit its source
instead). Generated, copied and vendored files are listed by reason ([generated.md](generated.md)). Details:
[completeness.md](completeness.md).

Answers use it in three ways:

- `routes`, `impact`, `callers`, `reaches`, `tests_covering` and `plan_check` end with one `coverage note:` line when
  a blind spot or a file that is not indexed could affect the answer, scoped to the languages, repos and registered
  handlers involved (`all routes: 1 indexed (possibly more: 1 unmodelled route registration)`, `no callers found in
  indexed code (blind spots: …)`). Complete answers keep the usual wording and get no extra line.
- Replies that come back empty or name an unknown symbol end with a coverage line for the whole index.
- **Every reply carries a `completeness` object** in its structured content (`{"result": <text>, "completeness":
  {...}}`, declared in the tool's output schema): `complete`, per-language `mode` / `discovered` / `indexed` /
  bucket counts, `unsupported` counts on whole-index answers and the `blind_spots` that apply, so an agent can decide
  when to fall back to text search without parsing prose. `coverage(json_output=true)` returns the same object as text.

The server instructions carry the rule: where an answer is not complete, use normal search and file reading for that
part (an empty cg answer there is not proof of absence). On the CLI: `cg coverage --db out/graph.db [--all-files]`.

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
If coverage() or a reply's coverage note says a language or file is not covered (or heuristic only), or names a blind
spot (a route or handler registration cg does not model), use normal search and file reading for that part.
```
