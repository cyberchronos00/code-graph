# Architecture

An index is a deterministic graph in SQLite. Queries, MCP, and the visual view read that graph.
What each language extracts lives on its own page. This page is the pipeline and the rules every plugin shares.

## Invariants

These hold everywhere in the code base. A change that breaks one is a design change.

- **Deterministic edges.** Every edge comes from a parser, the type checker, or a named rule. The same code gives the same graph.
- **Edges carry evidence.** The `file:line` that produced the edge, plus a confidence: `exact`, `resolved`, or `heuristic`.
- **Static analysis.** The indexer reads source files. The app, its code, and its databases stay untouched.
- **Plans are overlays.** Loading or checking a plan leaves the graph DB unchanged.
- **Read-only serving.** MCP and the visual view only read the graph. The MCP `index` tool rebuilds it from source.

## Index, store, query

```text
source files
    │  detect languages and frameworks
    ▼
language plugin.index()
    │  framework hooks, then framework.contribute()
    ▼
GraphBuilder  →  SQLite (core/store.py)
    │
    ├── query.py      reaches, impact, path, writers, readers
    ├── link.py       N repo DBs → one DB (`link` is the two-repo form)
    ├── plans.py      overlay checks (the DB stays unchanged)
    ├── mcp_server    stdio; read-only except index
    └── viz/          local read-only web view
```

| step | who | result |
|---|---|---|
| detect | `core/detect.py`, then `.cg.yaml` `frameworks.add` / `remove` | the active languages and frameworks |
| index | `LanguagePlugin.index` | syntax nodes, calls, a per-file report |
| hooks | `FrameworkPlugin.register_hooks` | type rules and fact handlers, before resolution |
| contribute | `FrameworkPlugin.contribute` | framework nodes and edges |
| finish | `indexer.py` | completeness, entry tags, the SQLite write |

Marker files include `composer.json`, `package.json`, `Cargo.toml`, `pyproject.toml`, and `Package.swift`.
Presets in `presets/` supply guard names and skip lists (common, then language, then framework) and are stored in graph meta. `routes` reads them back.

`core/paths.py` is the one skip list and `.cg.yaml` exclude set.
Every plugin and the coverage scan use it, so the same rules decide what is indexed and what `cg coverage` counts.

`GraphBuilder.add_node(kind, key, …)` gives the id `kind:key`. `add_edge` stores the edge with its evidence.

After `index()`, a plugin may set `self.file_report` (repo-relative paths):

| key | meaning |
|---|---|
| `seen` | files that became nodes |
| `parse_failed` | syntax errors |
| `skipped_oversize` | over the size limit |
| `unmapped` | parsed, not placed in the module table |
| `excluded` | left out by skips or exclude globs |
| `roots` | directories (ending in `/`) and files the plugin reads |

`coverage.py` buckets every discovered file of that language from the report ([Completeness](completeness.md)).
A file missing from `seen` counts as excluded, or as unmapped when the report has `roots` and the file sits under none of them.

## Plugins

```python
class LanguagePlugin(ABC):
    name: str
    def detect(self, project: Project) -> bool
    def index(self, project, builder: GraphBuilder, frameworks: list[FrameworkPlugin]) -> dict

class FrameworkPlugin(ABC):    # Laravel on PHP, Django on Python, Nuxt on TypeScript
    name: str; language: str
    def detect(self, project) -> bool
    def register_hooks(self, lang_ctx) -> None
    def contribute(self, project, builder, lang_ctx) -> dict
```

A framework teaches the host resolver, then adds its own nodes.

| hook | example |
|---|---|
| type rule | `Model::query()` → `builder:Model`; `app(X::class)` → `X` |
| fact handler | `->where('col')` on that builder → `READS_COLUMN`; `DB::connection('x')` → `USES_CONNECTION` |

Compiler indexes (rust-analyzer, scip-clang, scip-java, the Swift index) match SCIP occurrences onto the syntax-layer nodes by file, line, and name.
Ids stay the same in heuristic and exact mode. `cg index --scip FILE` merges an existing index.
Go has an indexer recipe only (`plugins/stubs`). Java is a heuristic plugin ([Java](java.md)).

Per-language facts: [Python](python.md), [PHP](php.md), [TypeScript / JavaScript frameworks](ts-frameworks.md), [Kotlin](kotlin.md), [Java](java.md), [Swift](swift.md), [Rust, C and C++](native.md).

## Queries

| command | walk |
|---|---|
| `reaches` | propagating edges in reverse; depth recorded; shortest evidence path per dependent (`KIND @file:line [confidence]`); grouped by entry kind and module |
| `impact` | that walk from a method, stopping at entry points |
| `writers` / `readers` | `WRITES_*` / `READS_*` edges for a table or a stored property |
| `siblings` | the same method on sibling types; other users of the same tables, columns, config keys, and connections; co-callers by Jaccard similarity of callee sets |
| `path` | one shortest chain between two specs |

Override hops are the override relation: `impact` shows `overrides:` and `(via override)`, and `tests` / `reaches` seed the same way.
With a gate scenario indexed, dependents reached only through gated code sit in a GATED group ([gate scenarios](configuration.md#gate-scenarios)).
Specs: [query targets](cli.md#query-targets-specs).

## Cross-repo link

`cg link` copies every repo graph into one SQLite DB ([workspace](cli.md#workspace)).
`file` gains a repo prefix and `attrs.repo` is set. An id that occurs in more than one repo is stored as `<repo>:<id>` and its edges are rewritten, except `external:` and `endpoint:` ids, which stay one node (one system, one protocol name). Other ids are unchanged, so two repos with no shared ids keep the same ids.
The command adds `MATCHES_ROUTE` from each repo's `http:` endpoints to routes of every other server (a backend may be the client), then runs channels, protocols, payload checks and entry tagging once over the union.
`impact`, `downstream`, and `path` then cross the whole workspace, including a chain of three or more repos.
A frontend `links:` list limits which servers that repo is matched against ([apps and workspace](configuration.md#apps-and-workspace)).

Matching (`cg_code_graph/link.py`) is deterministic: the method must agree, then each path segment.
A literal fitted into `{param}` is `resolved`. Catch-alls absorb the tail. The best candidate has the fewest heuristic fits, then the most literal agreements; a tie is ambiguous.
`exact` means the segments agree and the client base was traced. An unknown origin is a suffix match at `heuristic`. The report lists every endpoint with its match and evidence.

### Payload / field check

For a client endpoint with one route match, `cg_code_graph/payload.py` compares the keys the client sends and parses with the server schema or the returned shape, and writes `payload_checks` (each issue has `file:line` on both sides).
Ambiguous matches are skipped. Kind names: [Graph schema](schema.md).
