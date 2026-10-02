# Completeness: how far an answer reaches

Every positive cg answer comes with evidence (`file:line` hops and a confidence label). Completeness reporting makes
the other half explicit: which part of the repository the answer is drawn from, so a "no callers", "no path" or
"3 of 3 routes" can be relied on when the index is complete, and an agent knows exactly where to fall back to text
search when it is not. It has three parts, all recorded at index time (`stats.coverage` in the DB meta):

1. **File completeness per language**, next to the parser mode.
2. **Unsupported source types**, by extension or shebang.
3. **Blind spots**: patterns cg knows it does not model, detected in the indexed repository, with `file:line` samples.

Answers then carry a short `coverage note:` when they could be affected, and every MCP reply carries a machine-readable
`completeness` object. A repository with no gaps and no blind spots keeps the short output.

## File completeness

For each language the plugin reports what happened to every source file it discovered:

| bucket | meaning |
|---|---|
| `discovered` | source files of the language under the indexed root (dependency, build and VCS directories skipped) |
| `indexed` | files that are in the graph |
| `parse_failed` | the parser rejected the file (syntax error, encoding) |
| `skipped_oversize` | over the plugin's size limit (Python 1.5 MB, C/C++ `CODEGRAPH_MAX_FILE_BYTES`) |
| `unmapped` | parsed, but not placed in the module table: a Python file whose path is not an importable name (`my-scripts/`) or that lies outside the configured source roots ([python.md](python.md)), a `pkg.py` next to a `pkg/` package, a `.rs` file outside every crate's module tree |
| `excluded` | deliberately left out by the plugin's skip list (Python migrations, PHP `storage/` and `bootstrap/cache/`, generated Dart, directories a plugin does not walk) |

`excluded` files are a choice, so they never make an answer partial; the other three buckets do. The parser mode stays
what it was (`exact`, `resolved`, `heuristic`, `scip`, `skipped`), so a language can be `exact` and still incomplete:

```console
$ cg coverage --db out/proj.db
coverage proj: not fully covered: python 4 discovered, 2 indexed (exact parser): 1 parse failed, 1 unmapped; qml 1 unsupported; sh 1 unsupported
  python: 4 files (.py 4) 2 indexed, 1 parse failed, 1 unmapped
    parse failed: app/bad.py
    unmapped: my-scripts/run.py
    fix: unmapped .py files are in directories that are not importable module paths (a name with '-' or '.'), outside the detected source roots, or claim a module name another file has; list their roots under python.source_roots in .cg.yaml
  qml: 1 files (.qml 1) unsupported
  …
```

The first five paths per bucket are shown; `cg coverage --all-files` (MCP: `coverage(all_files=true)`) lists every
path, excluded files included. Per-file reports come from the Python, PHP, Dart, Rust and C/C++ plugins; TypeScript /
JavaScript report the parser mode and file counts.

## Unsupported source types

Files are counted as unsupported source by a generic rule instead of a fixed language list: the extension of a
programming or scripting language without a plugin (`.go`, `.java`, `.kt`, `.swift`, `.qml`, `.sh`, `.lua`, `.svelte`,
`.ps1`, …), or a `#!` line naming such an interpreter for extensionless scripts (`bin/release` with
`#!/usr/bin/env bash` counts as `sh`). Data, markup, config and asset files (`.json`, `.yaml`, `.md`, `.svg`, `.csv`)
never count, and extensionless launchers of indexed languages (`artisan`, `bin/console`) are not listed.

## Blind spots

Detectors run at index time and record each pattern with a count and `file:line` samples. Route blind spots affect
every route list and every caller answer in their language. Handler blind spots record the registered functions and
affect exactly the answers that involve one of them: the function itself (its "no callers" comes from the
registration) or a caller chain that runs through it (an entry point is missing there). A same-file decorator such as
`@tool` defined next to its uses is recorded too, since its uses are not calls either.

| kind | category | what is detected | documented limitation |
|---|---|---|---|
| `nest_wrapped_route_decorator` | route | NestJS methods using a decorator built with `applyDecorators(Get(...), ...)` or a factory returning `Get(...)` | [TS frameworks](limitations.md) (`applyDecorators`) |
| `django_dynamic_urlpatterns` | route | `urlpatterns` entries produced by a function call (also `*call()`), a comprehension or a loop | [Python / Django](limitations.md) (URL confs built in loops / functions) |
| `django_unresolved_include` | route | `include(<expression>)` or a `.urls` target the URL resolver could not follow (a literal include of a package outside the repo is third-party code and is not reported) | [Python / Django](limitations.md) |
| `express_loop_routes` | route | Express / Fastify / Koa / Hono routes registered in a loop or callback over a list, or with a computed method (`router[m.method](...)`) | [TS frameworks](limitations.md) (dynamic registration) |
| `laravel_loop_routes` | route | `Route::` calls inside `foreach` / `for` / `while`, `->each(...)` / `->map(...)` or `array_map(...)` | [Laravel](limitations.md) (routes built from data) |
| `python_decorator_routes` | route | a function with a route decorator (`@app.route`, `@router.get`) that no plugin turned into a route | [Python / Django](limitations.md) (frameworks without a plugin) |
| `python_decorator_registration` | handler | a function registered through a decorator (`@registry.register`, `@app.task`) with no entry point and no caller besides the decorator's own reference (click / typer / MCP registrations are entry points) | [Python / Django](limitations.md) (registries) |
| `python_registry_assignment` | handler | `registry[key] = fn` where `fn` has no caller in the graph (a call through the registry, `registry[key](...)`, counts) | [Python / Django](limitations.md) (registries) |

Detectors look at what the graph already models: a decorated Python function that a plugin made an entry point (a
django-ninja operation, a Celery task the Django plugin knows) or that has callers is not reported. Each detector has a
positive and a negative fixture in `tests/test_completeness.py`. A detector that fails never fails the index (it is
skipped with a warning on stderr).

## Notes on answers

`routes`, `impact`, `callers`, `reaches`, `tests_covering` and `plan_check` (CLI and MCP) add one line when the answer
could be affected by a blind spot or by files that are not indexed, scoped to the languages, repos and registered
handlers involved in the answer:

```text
$ cg routes --db out/shop.db
all routes: 1 indexed (possibly more: 1 unmodelled route registration)
…
coverage note: 1 route registration cg does not model (Django urlpatterns built by a function call, comprehension or loop: shop/urls.py:9). There, use your normal search and file reading (an empty cg answer is not proof of absence).

$ cg impact shop.views.list_orders --db out/shop.db
shop.views.list_orders: no callers found in indexed code (blind spots: 1 unmodelled route registration); …
coverage note: 1 route registration cg does not model (…: shop/urls.py:9). …
```

Complete answers keep the usual wording (`1 of 1 routes`, `has no recorded callers`) and get no extra line.

## MCP `completeness`

Every MCP tool reply has structured content `{"result": <the text reply>, "completeness": {...}}` (declared in each
tool's output schema), so an agent can decide when to fall back to text search without parsing prose:

```json
{"complete": false,
 "languages": {"python": {"mode": "exact", "discovered": 4, "indexed": 2, "parse_failed": 1, "unmapped": 1, "complete": false}},
 "unsupported": {"qml": 1, "sh": 1},
 "blind_spots": [{"kind": "django_dynamic_urlpatterns", "category": "route", "language": "python",
                  "what": "Django urlpatterns built by a function call, comprehension or loop", "count": 1, "sample": "shop/urls.py:9"}]}
```

The object is scoped like the text note (`unsupported` appears on whole-index answers such as `coverage` and `stats`).
On a combined graph, language keys are prefixed with the repo name (`bookstore-api/php`). `coverage(json_output=true)`
returns the whole-index object as text, for clients that only show text content.
