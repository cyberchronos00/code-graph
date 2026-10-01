# CLI reference

All commands: `python -m codegraph.cli <command> …` (the README defines a `cg` shell function for this). Every query command needs `--db`.

## Commands at a glance

- `index ROOT --db DB [--name N] [--gates FILE] [--scip FILE]`: detect languages/frameworks and build the graph.
- `link --backend DB --frontend DB --db OUT`: merge a backend and a frontend graph and match client HTTP calls to routes.
- `reaches SPEC... [--gate auto/none/NAME]`: everything that depends on the targets, grouped by entry classification.
- `impact METHOD`: reverse walk from a method up to its entry points.
- `downstream SPEC`: forward dependencies (page → composables → HTTP → routes → services → tables).
- `path SRC DST`: one shortest evidence chain. A `table:` target with no direct table edge on the way falls back to
  its columns (the path then ends at the column that is read or written). Exits with status 1 and prints `no path`
  when there is none.
- `writers TABLE`, `siblings SYMBOL`, `node SPEC`, `stats`: writers of a table, similar code, node details, counts.
- `api-calls SPEC`: client endpoints with call sites, request keys and the matched route.
- `resolutions CONCEPT [--within S]`: where a value is resolved, its fallback chains, and whether the client sends it.
- `plan {list,load,validate,check,baseline} NAME [--verify]`: the planned-change layer.
- `serve`, `viz-export`, `viz-plan`: the visual view (local server or self-contained HTML).

Most query commands take `--json`, `--min-confidence resolved` (or `exact`) and `--max-depth`.

## Query targets (specs)

- `table.column` or `column:table.column`: a DB column. `table:orders`: a DB table.
- `connection:warehouse`, `connection:tenant_*`: DB connection(s) from `config/database.php`, plus dynamic ones
  registered via `Config::set('database.connections.…')`.
- `env:WAREHOUSE_DB_HOST`, `config:database.connections.warehouse`: env / config keys.
- `Class::method`, `Class`, short or FQN: code symbols (suffix match).
- `page:/reports/:id`: a Nuxt page by its route path. `app/pages/x.vue`, `app/composables/useX.ts`: a TS module or Vue
  SFC by file (repo-relative, suffix match). `useX`, `useX.fn`, `fn`: a TS composable, store or function.
- `http:GET /v1/{store}/…` and `route:GET /v1/{store}/…`: a client endpoint / a backend route (`*` glob).
- `request_key:timezone`, `setting:reports.timezone`: value facts (see [value-facts.md](value-facts.md)).

Several specs in one `reaches` call are unioned.


## Full usage

Generated from `--help`.

### `index`

```
usage: python -m codegraph.cli index [-h] --db DB [--name NAME] [--scip SCIP]
                       [--gates GATES]
                       root

positional arguments:
  root

options:
  -h, --help     show this help message and exit
  --db DB
  --name NAME
  --scip SCIP
  --gates GATES  gate scenarios JSON (e.g. examples/bookstore.gates.json)
```

### `link`

```
usage: python -m codegraph.cli link [-h] --backend BACKEND --frontend FRONTEND --db DB
                      [--backend-name BACKEND_NAME]
                      [--frontend-name FRONTEND_NAME] [--report REPORT]

options:
  -h, --help            show this help message and exit
  --backend BACKEND
  --frontend FRONTEND
  --db DB
  --backend-name BACKEND_NAME
  --frontend-name FRONTEND_NAME
  --report REPORT       write <prefix>.json/.md match report
```

### `reaches`

```
usage: python -m codegraph.cli reaches [-h] --db DB [--json]
                         [--min-confidence {heuristic,resolved,exact}]
                         [--no-paths] [--max-depth MAX_DEPTH] [--gate GATE]
                         specs [specs ...]

positional arguments:
  specs

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                        indexed; 'none' to disable)
```

### `impact`

```
usage: python -m codegraph.cli impact [-h] --db DB [--json]
                        [--min-confidence {heuristic,resolved,exact}]
                        [--no-paths] [--max-depth MAX_DEPTH] [--gate GATE]
                        spec

positional arguments:
  spec

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                        indexed; 'none' to disable)
```

### `downstream`

```
usage: python -m codegraph.cli downstream [-h] --db DB [--json]
                            [--min-confidence {heuristic,resolved,exact}]
                            [--no-paths] [--max-depth MAX_DEPTH] [--gate GATE]
                            spec

positional arguments:
  spec

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                        indexed; 'none' to disable)
```

### `path`

```
usage: python -m codegraph.cli path [-h] --db DB
                      [--min-confidence {heuristic,resolved,exact}]
                      src dst

positional arguments:
  src
  dst

options:
  -h, --help            show this help message and exit
  --db DB
  --min-confidence {heuristic,resolved,exact}
```

### `writers`

```
usage: python -m codegraph.cli writers [-h] --db DB [--json]
                         [--min-confidence {heuristic,resolved,exact}]
                         [--no-paths] [--max-depth MAX_DEPTH] [--gate GATE]
                         spec

positional arguments:
  spec

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                        indexed; 'none' to disable)
```

### `siblings`

```
usage: python -m codegraph.cli siblings [-h] --db DB [--json]
                          [--min-confidence {heuristic,resolved,exact}]
                          [--no-paths] [--max-depth MAX_DEPTH] [--gate GATE]
                          spec

positional arguments:
  spec

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                        indexed; 'none' to disable)
```

### `node`

```
usage: python -m codegraph.cli node [-h] --db DB [--json]
                      [--min-confidence {heuristic,resolved,exact}]
                      [--no-paths] [--max-depth MAX_DEPTH] [--gate GATE]
                      spec

positional arguments:
  spec

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                        indexed; 'none' to disable)
```

### `stats`

```
usage: python -m codegraph.cli stats [-h] --db DB [--json]
                       [--min-confidence {heuristic,resolved,exact}]
                       [--no-paths] [--max-depth MAX_DEPTH] [--gate GATE]

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                        indexed; 'none' to disable)
```

### `api-calls`

```
usage: python -m codegraph.cli api-calls [-h] --db DB [--json]
                           [--min-confidence {heuristic,resolved,exact}]
                           [--no-paths] [--max-depth MAX_DEPTH] [--gate GATE]
                           spec

positional arguments:
  spec

options:
  -h, --help            show this help message and exit
  --db DB
  --json
  --min-confidence {heuristic,resolved,exact}
  --no-paths
  --max-depth MAX_DEPTH
  --gate GATE           gate scenario for live/gated split (default: the one
                        indexed; 'none' to disable)
```

### `resolutions`

```
usage: python -m codegraph.cli resolutions [-h] --db DB [--within WITHIN] [--no-client]
                             [--json]
                             concept

positional arguments:
  concept

options:
  -h, --help       show this help message and exit
  --db DB
  --within WITHIN  substring filter on the owning function fqn (e.g. Report)
  --no-client
  --json
```

### `plan`

```
usage: python -m codegraph.cli plan [-h] [--db DB] [--plans-dir PLANS_DIR] [--verify]
                      [--json] [-o OUT] [--max-items MAX_ITEMS]
                      {list,load,validate,check,baseline} [name]

positional arguments:
  {list,load,validate,check,baseline}
  name

options:
  -h, --help            show this help message and exit
  --db DB
  --plans-dir PLANS_DIR
  --verify              after implement + re-index: planned nodes/edges must
                        now exist
  --json
  -o, --out OUT         also write the report to this file
  --max-items MAX_ITEMS
```

### `viz-export`

```
usage: python -m codegraph.cli viz-export [-h] --db DB -o OUT [--sinks SINKS]
                            [--min-confidence {heuristic,resolved,exact}]
                            {reaches,impact,downstream,path} specs [specs ...]

positional arguments:
  {reaches,impact,downstream,path}
  specs

options:
  -h, --help            show this help message and exit
  --db DB
  -o, --out OUT
  --sinks SINKS         downstream sink kinds, comma separated (e.g.
                        table,column)
  --min-confidence {heuristic,resolved,exact}
```

### `viz-plan`

```
usage: python -m codegraph.cli viz-plan [-h] --db DB -o OUT [--plans-dir PLANS_DIR] name

positional arguments:
  name

options:
  -h, --help            show this help message and exit
  --db DB
  -o, --out OUT
  --plans-dir PLANS_DIR
```

### `serve`

```
usage: python -m codegraph.cli serve [-h] --db DB [--port PORT] [--host HOST]
                       [--plans-dir PLANS_DIR] [--presets PRESETS]

options:
  -h, --help            show this help message and exit
  --db DB
  --port PORT
  --host HOST
  --plans-dir PLANS_DIR
  --presets PRESETS     JSON list of canned queries for the UI (default: the
                        bundled sample presets)
```

### `detect`

```
usage: python -m codegraph.cli detect [-h] root

positional arguments:
  root

options:
  -h, --help  show this help message and exit
```
