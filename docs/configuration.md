# Configuration

code-graph indexes a project with zero configuration. The optional inputs are listed here.

## Project config file (`.cg.yaml`)

A `.cg.yaml` (or `.cg.yml`) at the indexed root is read on every index, by the CLI, the MCP `index` tool and the
visual view, so project-specific knowledge is recorded once and travels with the repository. Command-line flags take
precedence over it.

```yaml
version: 1
python:
  source_roots: [lib, tools/scripts]   # directories Python imports from; replaces detection ("." = the indexed root)
exclude: ["legacy/**", "*.generated.ts"]  # paths no plugin indexes and the coverage scan leaves out
skip_dirs:
  add: [fixtures_big]                  # more directory names to skip everywhere
  keep: [static]                       # directory names skipped by default that hold project code here
generated:
  paths: ["src/api/generated/**"]      # generated files detection misses (kept out of the graph, listed by coverage)
  vendored: ["third_party/**"]         # vendored copies of other projects
  keep: ["src/schema.gen.ts"]          # hand-maintained files a detection rule would classify
  include: false                       # true: index them, labelled attrs.generated
frameworks:
  add: [nest]                          # turn on a framework layer or preset detection did not pick
  remove: [flutter]                    # turn off one that was detected
auth:
  extra_patterns: ["requireTenantMember", "withOrgScope"]   # guard names (regex) that count as auth
secret:
  extra_patterns: ["verifyStripeSignature"]                 # guard names that check a shared secret / signature
gates: config/gates.json               # gate scenarios file (cg index --gates)
plans:
  dir: docs/plans                      # plans directory (--plans-dir)
  text_mention_dirs: [src, templates]  # where plan checks look for text mentions of a planned name
viz:
  presets:                             # canned queries for the visual view's preset menu
    - {id: orders_writes, label: what writes the orders table, mode: reaches, specs: ["table:orders"]}
platforms:
  targets: [ios, android, web]         # the project's build targets (default: detected)
  paths: {"src/win32/**": [windows]}   # files built only for some targets
  file_suffixes: true                  # React Native .ios.ts / .android.ts / .native.ts / .web.ts files
  path_conventions: true               # C / C++ win/ unix/ posix/ directories and *_win.c file names
```

| key | meaning | flag |
|---|---|---|
| `version` | file format version, `1` (optional) | |
| `python.source_roots` | Python source roots, relative to the indexed root ([python.md](python.md)) | `cg index --python-root DIR` (repeatable) |
| `exclude` | gitignore-style globs (`legacy/**`, `*.generated.ts`, `/tools`), applied by every language plugin and by the coverage scan | |
| `skip_dirs.add` / `skip_dirs.keep` | directory names to add to, or take out of, the shared skip lists | |
| `generated.paths` / `generated.vendored` / `generated.keep` | globs of generated / vendored files detection misses, and of hand-maintained files it should leave alone ([generated.md](generated.md)) | |
| `generated.include` | index generated, copied and vendored files, labelled `attrs.generated` (default: kept out of the graph and listed by `cg coverage`) | `cg index --include-generated` |
| `frameworks.add` / `frameworks.remove` | framework layers and presets to turn on or off (`laravel`, `nuxt`, `django`, `djangorestframework`, `django-ninja`, `flutter`, `nest`, `nextjs`, `express`; aliases such as `nestjs`, `next`, `fastify`, `drf` work too) | |
| `auth.extra_patterns` | regexes on guard names that count as auth in `routes` | `--auth-pattern` |
| `secret.extra_patterns` | regexes on guard names that count as a secret or signature check | |
| `gates` | gate scenarios file, relative to the indexed root | `cg index --gates FILE` |
| `plans.dir` | plans directory used by `plan`, `viz-plan`, `serve`, `impact` and the MCP server | `--plans-dir DIR` / `--plans DIR` |
| `plans.text_mention_dirs` | directories scanned for text mentions of planned names | |
| `viz.presets` | preset menu entries for `serve` (`{id, label, mode, specs[, sinks]}`) | `serve --presets FILE` |
| `platforms.targets` | the targets `--platform` and `cg platforms divergence` work with (windows, linux, macos, ios, android, web); default: Flutter platform folders, Expo `app.json`, React Native, Electron / Tauri, else the desktop targets plus those the conditions name ([platforms.md](platforms.md#targets)) | |
| `platforms.paths` | glob → targets: files built only for those targets (`unix` and `native` work too) | |
| `platforms.file_suffixes` / `platforms.path_conventions` | React Native platform files and C / C++ platform directories / file names (default `true`) | |

The settings are stored with the graph, so `cg serve`, `cg routes`, `cg plan` and the MCP server read the plans
directory, auth patterns and presets from the graph without repeating the flags.

### Checking a config file

`cg config show [ROOT]` prints the effective configuration, one row per value with the place it comes from (a
flag, `.cg.yaml`, a framework preset or detection); `--json` gives the same as data. `cg config validate [ROOT]`
checks the file and exits with status 2 when it is invalid:

```text
$ cg config show .
effective configuration of bookstore-api
  config file                .cg.yaml   [found at the indexed root]
  languages                  php   [detected]
  frameworks                 laravel   [detected]
  presets                    common, php, laravel   [built-in (codegraph/presets), picked by detection]
  exclude                    legacy/**   [.cg.yaml exclude]
  skip_dirs                  fixtures_big   [.cg.yaml skip_dirs.add]
  auth.guards                auth, auth.basic, auth.session, can, password.confirm, verified, ...   [preset laravel]
  auth.extra_patterns        requireTenantMember   [.cg.yaml auth.extra_patterns]
  plans.dir                  plans   [.cg.yaml plans.dir]
  ...
$ cg config validate .
invalid: .cg.yaml: auth.extra_patterns[0]: '(unclosed' is not a valid regular expression (missing ), unterminated subpattern at position 0)
```

Invalid files stop the index with a message that names the file and the key. Top-level keys this version does not
read are kept and listed under `stats.config.ignored_keys`, so one file can serve several cg versions; `cg config
validate` names them with a suggestion for a likely typo (`skip_dir` → `skip_dirs`).

## Framework presets

Each detected language and framework brings a curated preset from `codegraph/presets/*.yaml`, so a typical project
gets accurate results with no configuration:

| preset | contents |
|---|---|
| `common` | auth and secret name patterns, directory names every plugin and the coverage scan skip |
| `php`, `python`, `typescript`, `dart`, `rust`, `c_cpp` | per-language skip lists (vendor, build output, caches, generated-file suffixes) |
| `laravel` | auth middleware (`auth`, `auth:*`, `can:*`, `verified`, Sanctum abilities, Spatie roles / permissions), signature middleware, plan prefixes and text-mention dirs |
| `django`, `djangorestframework`, `django-ninja` | view access decorators and mixins, DRF permission classes, ninja auth classes |
| `nest`, `nextjs`, `express`, `nuxt` | Nest passport and RBAC guards, Next.js auth helpers (next-auth, Auth.js, Clerk, Auth0, iron-session), Express / Fastify / Koa / Hono auth middleware, Nuxt session helpers |

Every guard entry names its source (the framework's documentation or package). An entry matches the guard's own
name, with its namespace, parameters and call arguments aside (`auth:sanctum`, `AuthGuard('jwt')`,
`Illuminate\Auth\Middleware\EnsureEmailIsVerified`); a project's own guards are recognised by the shared name pattern
or by `auth.extra_patterns`. Presets are merged in order:
`common`, then the languages, then the frameworks, then `.cg.yaml`, then flags. `cg index` records the detected
frameworks and the applied presets in `stats.presets` and on the `cg coverage` summary line, and `cg routes`
reports which guards each preset or project pattern recognised (`auth guards by source: preset laravel 169, name
pattern 6`). Entries a preset marks as not auth (`csrf_protect`, `ThrottlerGuard`, `AllowAny`) never count.
`cg config show` lists every preset value with its source.

## Gate scenarios (`--gates`)
A gates file names scenarios and the settings that are true in each, e.g. `examples/bookstore.gates.json`:
`new_inventory` = `features.new_inventory.enabled`.

How it works:
- The PHP extractor emits a control skeleton per function.
- `codegraph/plugins/php/gating.py` abstract-interprets it, seeded by setting reads through the scenario's
  `setting_accessors` (e.g. `getSetting(<key>)`) and by memoised method summaries, so wrappers such as
  `FeatureGate::usesNewInventory()` are understood too (see `tests/gating_fixture`).
- A branch is dead when its path condition is unsatisfiable *because of* a scenario fact.
- Edges made from dead code get `edges.gate=<scenario>` plus `attrs.guard`/`guard_expr`.

Schema additions: `edges.gate`, `node_entry_live(scenario, …)` (entry tagging that skips gated edges), `gate_predicates`.

Usage: index with `--gates FILE`, then query with `reaches … --gate auto` (or `none`, or a scenario name). Each item
reports `gate_status`: `live`, `gated_target` or `gated_entry`. In the sample, `Admin\InventoryController::index` only
reaches the `warehouse` connection inside the old-inventory branch, so it is listed as gated for `new_inventory`.


Gates file format (see `examples/bookstore.gates.json`):

```json
{
  "scenarios": [
    {
      "name": "new_inventory",
      "description": "free text",
      "setting_accessors": ["getSetting"],
      "true_settings": ["features.new_inventory.enabled"],
      "false_settings": []
    }
  ]
}
```

Beta: one scenario per index (the first one).

### Native gate keys (Rust, C, C++)

A scenario can also switch Cargo features, `cfg` atoms and preprocessor macros. Rust items, statements and modules
under a false `#[cfg]`, and C/C++ code in a false `#if` region, are reported as gated:

```json
{"scenarios": [{"name": "minimal_build",
  "features_off": ["kv-core/fs"], "features_on": [], "cargo_features": "default",
  "cfg_true": ["unix"], "cfg_false": ["windows"],
  "defines_on": ["NDEBUG", "LEVEL=2"], "defines_off": ["RB_THREADSAFE"]}]}
```

`features_*` take `pkg/feature` or a bare feature name (any package). `cargo_features` (`"default"`, `"none"` or a
list) fixes the exact enabled set, including what `default` and feature dependencies turn on. Atoms the scenario
doesn't mention stay unknown, and unknown code is treated as live. Example: `examples/native.gates.json`.

## Viz presets and starter queries

The preset menu in the visual view is built from, in order:

1. `serve --presets FILE` (a JSON list), or `viz.presets` in `.cg.yaml`;
2. the sample presets for the bundled example apps, when their targets exist in the graph;
3. **starter queries** derived from the graph at index time: a write route without an auth guard, the most-written
   and most-read tables, the busiest connection and env key, the page with the largest backend reach and the
   most-called functions. Each one resolves to existing nodes; `cg starters --db DB` and the MCP `starters` tool list
   them with the matching command.

A presets file:

```json
[
  {"id": "warehouse_reaches", "label": "what reaches the warehouse connection", "mode": "reaches",
   "specs": ["connection:warehouse", "table:warehouse_stock"]},
  {"id": "report_page_tables", "label": "report page -> tables", "mode": "downstream",
   "specs": ["page:/reports/:id"], "sinks": ["table", "column"]},
  {"id": "plan_preorders", "label": "plan overlay: pre-orders", "mode": "plan", "specs": ["preorders"]}
]
```

`mode` is one of `reaches`, `impact`, `downstream`, `path`, `plan`.

## Screenshots (`codegraph/viz/tools/shoot.mjs`)

`node shoot.mjs <base-url> <out-dir>` against a running `serve`. Environment: `CHROME` (browser binary, default
`/usr/bin/google-chrome`), `SHOTS` (JSON list of `{file, hash}`, where `hash` is the view's URL hash), `ONLY`
(substring filter on file names). Install with `PUPPETEER_SKIP_DOWNLOAD=1 npm ci` in `codegraph/viz/tools`.

## Plans directory

`plan …`, `viz-plan` and `serve` take `--plans-dir DIR` (MCP server: `--plans DIR`). Without the flag they use
`plans.dir` from the indexed project's `.cg.yaml`, then `plans/` in the repository root.

## Environment variables

| variable | effect |
|---|---|
| `CODEGRAPH_NO_CACHE=1` | disable the TS and Dart extractor facts caches and the native SCIP cache (all keyed by file content) |
| `CODEGRAPH_CACHE=DIR` | cache location (default `~/.cache/codegraph`) |
| `CODEGRAPH_RUST_SCIP=0`, `CODEGRAPH_C_SCIP=0`, `CODEGRAPH_COMPDB`, `CODEGRAPH_CFAMILY`, ... | Rust / C / C++ options: see [native.md](native.md#environment-variables) |
