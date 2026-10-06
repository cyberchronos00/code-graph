# MCP server

code-graph ships a stdio [Model Context Protocol](https://modelcontextprotocol.io) server. An agent gets callers, entry points and `file:line` evidence from the graph. It needs the `mcp` Python SDK (2.x). Use the installed `cg-mcp` command from [Install](install.md).

## Run

```bash
cg-mcp --db out/graph.db --gates examples/bookstore.gates.json --plans examples/plans
```

`--db` defaults to `out/graph.db`. `--root` is the project root for a single-repo DB (the `index` tool). `--plans` defaults to `plans.dir` in `.cg.yaml`, else `<repo>/plans`. Flags: `cg-mcp -h`.

## Tools

| tool | what it returns |
|---|---|
| `reaches` | what depends on the targets, grouped by entry classification |
| `impact` | callers of a method up to entry points; overrides listed apart from callers |
| `callers` | direct callers; `ref@file:line` when code takes the function as a value |
| `siblings` | hierarchy, the same method on sibling classes, shared resources |
| `downstream` | forward dependencies (calls, HTTP, routes, tables) |
| `path` | one shortest evidence chain between two specs |
| `node` | one node and its edges; an ambiguous name lists candidates |
| `snippet` | that symbol's source: `path:start-end`, then numbered lines |
| `search` | name / FQN substring, plus route guard and auth names |
| `routes` | routes with guards; filter by writes, reaches, missing or unguarded ([routes and guards](cli.md#routes-and-guards)) |
| `writers` / `readers` | writers of a table, column or `Type.prop`; readers of a stored property |
| `roundtrip` | heuristic: a lossy write of `Type.prop` read back into UI state |
| `lint_async_state` | heuristic async-state lints |
| `api_calls` | client HTTP calls, request keys and the matched route |
| `channels` | broadcast channels ([Channels and tests](channels-and-tests.md)) |
| `bridges` | web / native bridge endpoints ([Web / native bridges](bridges.md)) |
| `protocol_links` | protocol endpoints and their checks ([Protocol links](protocols.md)) |
| `llm_tools` | LLM and MCP tools, resources and prompts ([AI tools](ai-tools.md)) |
| `external_systems` | databases, caches, brokers and other hosts ([External systems](external.md)) |
| `tests_covering` | tests that reach a symbol, route or table |
| `resolutions` | where a concept's value is decided ([Value facts](value-facts.md)) |
| `plan_list` `plan_load` `plan_validate` `plan_baseline` | plan files under the plans directory |
| `plan_check` | compact plan report; `details=true` adds file:line and chains |
| `index` | re-index into temp files, then swap the DBs; `.cg.yaml` is re-read |
| `doctor` | the `cg doctor` report; no graph needed |
| `coverage` | languages, files, blind spots ([Completeness](completeness.md)) |
| `platforms` / `platform_divergence` | build targets, and variants that leave one uncovered |
| `starters` | starter queries that resolve to nodes in this graph |
| `stats` | node and edge counts |

Argument lists match the CLI (`cg <cmd> -h`). `reaches`, `impact`, `downstream`, `path`, `routes` and `search` take `platform` (`windows`, `linux`, `macos`, `ios`, `android`, `web`). Rust and C / C++ specs: [Rust, C and C++ query specs](native.md#query-specs). On a combined graph, `repo:Class.method` selects one repo ([workspace linking](cli.md#workspace)). `index` with no arguments re-indexes every linked repo (two or more) and rebuilds the one combined link; `repo` or `root` narrows it. A root that matches nothing, an unknown repo, or a result with 0 nodes is refused and the current graph stays.

Paths in replies are repo-relative. An empty result says why and suggests the next call.

## Coverage

`coverage` (and `cg coverage`) reports parser mode, file counts and blind spots. Three consequences for other tools:

- `routes`, `impact`, `callers`, `reaches`, `tests_covering` and `plan_check` add one `coverage note:` when a blind spot could affect that answer.
- An empty reply or an unknown symbol ends with a coverage line for the whole index.
- Every reply includes a `completeness` object (`complete`, per-language counts, `blind_spots`) so the agent can fall back to search without parsing the prose.

Where the answer is not complete, use normal search and file reading for that part. Sample replies: [MCP sample outputs](mcp/sample_outputs.md).

## Staleness

When the working tree has changed since this graph was built, tool replies add one `index note:` line and the structured content includes `stale: true`. The check compares `<db>.refresh.state` (the fingerprint of paths, mtimes and sizes that `cg refresh` stored, plus the database mtime) with the current tree. File contents are not hashed. A database built by `cg index` or the `index` tool has no state file, so any listed file newer than the database counts. Paths come from one `git ls-files` call. The result is cached for 5 seconds. `index` and `doctor` skip the check. A combined graph (`cg link`) is not checked. `CODEGRAPH_NO_STALE_CHECK=1` turns the check off ([Configuration](configuration.md#environment-variables)).

## Client config

```json
{"mcpServers": {"code-graph": {
  "command": "cg-mcp",
  "args": ["--db", "out/graph.db"]}}}
```

Add `--gates` and `--plans` when you use them. Relative paths in `args` are resolved against the host's working directory. Point `--db` at the graph for the project you indexed.

`cg agents install` writes a short cg reading-rules block into `AGENTS.md`, `CLAUDE.md` or `.cursor/rules/cg.mdc`. It previews the change and asks before writing. `--mcp` also adds this server entry (default file `.cursor/mcp.json`). `cg agents remove` takes it back out. See `cg agents -h`.

## Suggested agent instructions

```text
Before changing code that touches a table, column, DB connection, config key or route, call the code-graph MCP tools:
- reaches(<target>) for every function and entry point that depends on it;
- impact(<Class::method>) before editing a method, and siblings(<Class::method>) for parallel paths;
- routes(writes="*", unguarded=true) or routes(reaches=[<target>]) for guards;
- for a planned feature, write plans/<name>.yaml, then plan_check(<name>) and resolve every MISSING FROM PLAN item.
Quote the file:line evidence. Re-run index after editing.
If coverage() or a coverage note says a language or file is not covered, use normal search for that part.
```
