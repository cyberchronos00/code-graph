# Configuration

code-graph indexes a project with zero configuration. A `.cg.yaml` (or `.cg.yml`) at the indexed root is optional. The CLI, the MCP `index` tool and the visual view read it on every index. Command-line flags take precedence.

## Project file

```yaml
version: 1
python:
  source_roots: [lib, tools/scripts]   # directories Python imports from; replaces detection ("." = the indexed root)
exclude: ["legacy/**", "*.generated.ts"]  # paths no plugin indexes and the coverage scan leaves out
include: [src/generated, build/api]   # directories indexed although a built-in skip leaves them out
skip_dirs:
  add: [fixtures_big]                  # more directory names to skip everywhere
  keep: [static]                       # directory names skipped by default that hold project code here
generated:
  paths: ["src/api/generated/**"]      # generated files detection misses (kept out of the graph, listed by coverage)
  vendored: ["third_party/**"]         # vendored copies of other projects
  keep: ["src/schema.gen.ts"]          # hand-maintained files a detection rule would classify
  include: false                       # true: index them, labelled attrs.generated
frameworks:
  add: [nest]                          # turn on a framework layer detection did not pick
  remove: [flutter]                    # turn off one that was detected
auth:
  extra_patterns: ["requireTenantMember", "withOrgScope"]   # guard names (regex) that count as auth
secret:
  extra_patterns: ["verifyStripeSignature"]                 # guard names that check a shared secret / signature
gates: config/gates.json               # gate scenarios file (cg index --gates)
plans:
  dir: docs/plans                      # plans directory (--plans-dir / --plans)
  text_mention_dirs: [src, templates]  # where plan checks look for text mentions of a planned name
viz:
  presets:                             # canned queries for the visual view's starter cards
    - {id: orders_writes, label: what writes the orders table, mode: reaches, specs: ["table:orders"]}
apps:                                  # workspace: one `cg index <root>` builds one combined graph
  - {name: api, root: apps/api, role: backend}
  - {name: web, root: apps/web, role: frontend, links: [api]}   # backends it calls (default: every backend)
  - {name: orders, root: ../orders-api, role: backend}          # absolute or ../other-repo is allowed
platforms:
  targets: [ios, android, web]         # build targets (default: detected)
  paths: {"src/win32/**": [windows]}   # files built only for some targets
  file_suffixes: true                  # React Native .ios.ts / .android.ts / .native.ts / .web.ts
  path_conventions: true               # C / C++ win/ unix/ posix/ directories and *_win.c names
rust:
  targets: off                         # extra rust-analyzer runs: auto (default), off, or [windows, macos]
protocols:
  external: ["kafka:audit.*"]          # <protocol>:<name glob> handled outside the analysed repos
lossy: [squash, "toDisplay*"]          # extra lossy-transform names / globs for `cg roundtrip`
```

Comments in the file are the meanings. Flags and env vars that override a key:

| key | override |
|---|---|
| `python.source_roots` | `cg index --python-root DIR` (repeatable) |
| `generated.include` | `cg index --include-generated` |
| `auth.extra_patterns` | `--auth-pattern` |
| `gates` | `cg index --gates FILE` |
| `plans.dir` | `--plans-dir DIR` / `--plans DIR` |
| `viz.presets` | `cg serve --presets FILE` |
| `apps` | `cg index --no-apps` indexes the root as one project |
| `rust.targets` | `CG_RUST_TARGETS` (takes precedence; [Rust, C and C++](native.md)) |

`include` walks only the path down to those directories; `exclude` still applies inside them. `skip_dirs.keep` also lets a walk into a hidden directory such as `.storybook`. `frameworks` names include `laravel`, `nuxt`, `django`, `djangorestframework`, `django-ninja`, `flutter`, `nest`, `nextjs`, `astro`, `express`, `spring` (aliases such as `nestjs`, `next`, `fastify`, `drf` work). `platforms.targets` accepts `windows`, `linux`, `macos`, `ios`, `android`, `web`; `unix` and `native` work in `platforms.paths`. Details: [Python](python.md), [Generated files](generated.md), [Platforms](platforms.md).

The settings are stored with the graph, so `cg serve`, `cg routes`, `cg plan` and the MCP server read plans, auth patterns and presets from the DB without repeating the flags.

`cg config show [ROOT]` prints each effective value and where it comes from (flag, `.cg.yaml`, preset or detection); `--json` prints the same as data. `cg config validate [ROOT]` checks the file and exits 2 when it is invalid. Unknown top-level keys are kept and listed under `stats.config.ignored_keys`; validate suggests a likely typo (`skip_dir` → `skip_dirs`).

## Apps and workspace

`cg index <root> --db out/mono.db` indexes each app into `out/mono.<app>.db` and writes **one** combined `out/mono.db` for every app. Per-pair databases are not written. With a single app, `out/mono.db` is that app's graph.

Each app reads its own `.cg.yaml`. The root file's `apps` decides what is indexed. `root` is relative to the workspace, or absolute, or `../other-repo`, so separate checkouts can share one config. A missing directory is rejected with the same message as a missing in-repo root.

`role` is `backend` (publishes routes, and may call other backends) or `frontend` (calls servers, does not publish routes). `links:` on a frontend is the allow-list of backends it calls; omit it to call every backend. Client calls in every app are matched against every other backend, including backend-to-backend HTTP.

```bash
cg index . --db out/workspace.db
```

The same merge from graphs you already built is `cg link --repo` ([workspace](cli.md#workspace)). An id shared by two apps is stored as `repo:` plus the original id. `orders:Order` selects one app; `Order` matches every app that defines it.

## Framework presets

A detected language or framework brings a preset from `cg_code_graph/presets/*.yaml`:

| preset | what it adds |
|---|---|
| `common` | auth and secret name patterns, shared skip directories |
| `php`, `python`, `typescript`, `dart`, `rust`, `c_cpp`, `kotlin`, `java`, `swift` | per-language skip lists |
| `laravel` | auth and signature middleware, plan prefixes, text-mention dirs |
| `django`, `djangorestframework`, `django-ninja` | view access decorators, DRF permissions, ninja auth |
| `nest`, `nextjs`, `express`, `nuxt` | Nest guards, Next.js auth helpers, Express-family middleware, Nuxt session helpers |
| `spring` | Spring Security method annotations and `SecurityFilterChain` guard names (`presets/spring.yaml`) |

A guard matches on its name, ignoring namespace and arguments (`auth:sanctum`, `AuthGuard('jwt')`). Project guards match the shared name pattern or `auth.extra_patterns`. Merge order: `common`, languages, frameworks, `.cg.yaml`, then flags. `cg coverage` and `cg routes` name which presets and patterns matched. `cg config show` lists every preset value with its source.

## Gate scenarios

A gates file names scenarios and the settings that are true in each (`examples/bookstore.gates.json`):

```json
{"scenarios": [{
  "name": "new_inventory",
  "description": "free text",
  "setting_accessors": ["getSetting"],
  "true_settings": ["features.new_inventory.enabled"],
  "false_settings": []
}]}
```

Index with `--gates FILE`, then query with `reaches … --gate auto` (or `none`, or a scenario name). Each item reports `gate_status`: `live`, `gated_target` or `gated_entry`. Beta: one scenario per index (the first one).

Rust, C and C++ scenarios can also switch Cargo features, `cfg` atoms and macros. Atoms the scenario does not mention stay unknown, and unknown code stays live. Example: `examples/native.gates.json`.

```json
{"scenarios": [{"name": "minimal_build",
  "features_off": ["kv-core/fs"], "cfg_true": ["unix"], "cfg_false": ["windows"],
  "defines_on": ["NDEBUG"], "defines_off": ["RB_THREADSAFE"]}]}
```

## Viz presets and starter queries

Starter cards are built in this order: `serve --presets FILE` or `viz.presets`; sample presets for the bundled apps whose targets exist; then starter queries derived at index time (an unguarded write route, the most-written and most-read tables, the busiest connection and env key, the widest page, the most-called functions). `cg starters --db DB` and the MCP `starters` tool list them.

```json
[
  {"id": "warehouse_reaches", "label": "what reaches the warehouse connection", "mode": "reaches",
   "specs": ["connection:warehouse", "table:warehouse_stock"]},
  {"id": "report_page_tables", "label": "report page -> tables", "mode": "downstream",
   "specs": ["page:/reports/:id"], "sinks": ["table", "column"]},
  {"id": "plan_preorders", "label": "plan overlay: pre-orders", "mode": "plan", "specs": ["preorders"]}
]
```

`mode` is `reaches`, `impact`, `downstream`, `path` or `plan`.

## Environment variables

| variable | effect |
|---|---|
| `CG_MCP_TOOLS=LIST` | tools `cg-mcp` lists: `core`, names, globs, `-name`. Unset or empty means all ([Choosing tools](mcp.md#choosing-tools)) |
| `CG_NO_CACHE=1` | disable the TS, Dart and native SCIP caches (keyed by file content) |
| `CG_NO_HOOKS=1` | git hooks installed by `cg hooks` do nothing for that command ([CLI reference](cli.md#hooks)) |
| `CG_NO_STALE_CHECK=1` | MCP tool replies skip the staleness `index note:` ([MCP server](mcp.md#staleness)) |
| `CG_CACHE=DIR` | cache root (else `%LOCALAPPDATA%\cg` on Windows, `$XDG_CACHE_HOME/cg`, `~/.cache/cg`). `cg clean` empties it ([clean](cli.md#clean)) |
| `CG_NODE=PATH` | JavaScript runtime for the TypeScript extractor (Node.js or Bun; a path or a name on PATH); else `node`, then `bun` on PATH ([Installing and updating cg](install.md#javascript-runtime)) |
| `CG_RUST_SCIP=0`, `CG_C_SCIP=0`, `CG_COMPDB`, `CG_CFAMILY`, … | Rust / C / C++ options ([environment variables](native.md#environment-variables)) |
| `CG_JAVA_SCIP=1` | run scip-java on the Gradle / Maven build (opt-in; it runs the build and can clean build caches). One run per project root, shared with Kotlin ([Java exact mode](java.md#exact-mode)) |
| `CG_JAVA_SCIP_FILE` | prebuilt scip-java index. Wins over `CG_KOTLIN_SCIP_FILE` when both are set |
| `CG_KOTLIN_SCIP=1`, `CG_KOTLIN_SCIP_FILE` | same scip-java run and index as the `CG_JAVA_SCIP*` names. Either opt-in starts the one run ([Kotlin exact mode](kotlin.md#exact-mode)) |

`CODEGRAPH_*` names still work in 0.17.x and 0.18.x with a deprecation warning; they are removed in 0.19.0. `CODEGRAPH_CACHE` and `CODEGRAPH_CACHE_DIR` are read as `CG_CACHE`.
