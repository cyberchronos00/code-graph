# Completeness: how far an answer reaches

Every positive answer has evidence (`file:line`, a confidence label). Completeness is the
other half: which part of the repo that answer was drawn from, so "no callers" is reliable when
the index is complete, and an agent knows where to fall back to text search when it is not.

Recorded at index time as `stats.coverage`: file buckets per language, unsupported source
types, and blind spots (patterns cg does not model, with `file:line` samples). `routes`,
`impact`, `callers`, `reaches`, `tests_covering` and `plan_check` add one `coverage note:`
when the answer could be affected. A repo with no gaps keeps the short output. Every MCP reply
also carries a `completeness` object.

## File completeness

| bucket | meaning | makes an answer partial? |
|---|---|---|
| `discovered` | source files under the root (dependency, build and VCS dirs skipped) | |
| `indexed` | files in the graph | |
| `parse_failed` | the parser rejected the file | yes |
| `skipped_oversize` | over the plugin limit (Python 1.5 MB, C/C++ `CODEGRAPH_MAX_FILE_BYTES`) | yes |
| `unmapped` | parsed, not placed in the module table | yes |
| `excluded` | the plugin's skip list (Python migrations, PHP `storage/`, generated Dart) | no |

`unmapped` is a Python file that is not an importable name or sits outside
[source roots](python.md), a `pkg.py` next to a `pkg/` package, a `.rs` file outside every
crate, or a `.ts` / `.js` file outside the TS source dirs, `bin` scripts and test trees (hint:
`.cg.yaml` `include`). Tool configs, `.d.ts` and fixtures inside those dirs are `excluded`.

Generated, copied and vendored files are not `discovered`. They are
`generated: N files excluded`. See [generated.md](generated.md). Parser mode (`exact`,
`heuristic`, `scip`, …) is independent: a language can be `exact` and still incomplete.

```console
$ cg coverage --db out/proj.db
coverage proj: not fully covered: python 4 discovered, 2 indexed (exact parser): 1 parse failed, 1 unmapped
  python: parse failed: app/bad.py
    unmapped: my-scripts/run.py
    fix: list their roots under python.source_roots in.cg.yaml
```

The first five paths per bucket are shown. `cg coverage --all-files` (MCP
`coverage(all_files=true)`) lists every path. Per-file reports come from the Python, PHP, Dart,
Rust, C/C++ and TypeScript plugins.

## Syntax errors

tree-sitter (Swift, Kotlin, Rust, C / C++) recovers around an ERROR node. TypeScript and Dart
report diagnostics and still build the file. A Python or PHP file that does not parse is
`parse_failed`.

cg counts as **lost** each declaration head in an error span that has no node of that name in
that file (a Swift `extension` is not counted; its members are). `cg coverage` lists the files,
most declarations lost first (5 per language; all with `--all-files`):

```console
$ cg coverage --db out/app.db
coverage app: not fully covered: swift 8 heuristic, 2 parsed with syntax errors
  swift: syntax errors in 2 files, 1 declaration lost:
    Sources/App/Invalid.swift:6 (1 declaration lost: broken:6)
    Tests/AppTests/OrphanTests.swift:4, 9
```

`--json` per language: `syntax_errors` (
`[{file, spans, errors, decls_lost, lost, parse_failed}]`, up to 500 files),
`syntax_error_files`, `parsed_with_errors` (parsed, not failed) and `decls_lost`. The coverage
note mentions the count. An answer whose nodes sit in such a file is not `complete`: its
`completeness` has `syntax_errors` (`[{language, file, spans, decls_lost}]`) and the note
names the file and its first error line.

## Unsupported source types

A programming extension with no plugin (`.go`, `.java`, `.qml`, `.sh`, `.lua`, `.svelte`,
…), or a `#!` line naming such an interpreter. Data and markup (`.json`, `.yaml`, `.md`,
`.svg`) never count. Extensionless launchers of indexed languages (`artisan`, `bin/console`)
are not listed.

## Blind spots

Detectors run at index time. A failure is a stderr warning, not a failed index. Each one has a
positive and a negative fixture in `tests/test_completeness.py`. A decorated function that a
plugin already made an entry point is not reported.

| kind | category | detected |
|---|---|---|
| `nest_wrapped_route_decorator` | route | `applyDecorators(Get(...))` or a factory that returns `Get(...)` |
| `django_dynamic_urlpatterns` | route | `urlpatterns` from a call, a comprehension or a loop |
| `django_unresolved_include` | route | `include(<expression>)` cg could not follow (a third-party package is not reported) |
| `express_loop_routes` | route | Express / Fastify / Koa / Hono routes in a loop, or `router[m.method](...)` |
| `laravel_loop_routes` | route | `Route::` inside `foreach` / `->each` / `array_map` |
| `python_decorator_routes` | route | `@app.route` / `@router.get` that no plugin turned into a route |
| `python_decorator_registration` | handler | `@registry.register` with no entry point and no caller (click, typer and MCP are entry points) |
| `python_registry_assignment` | handler | `registry[key] = fn` and `fn` has no caller |
| `nuxt_unevaluable_import_dirs` | handler | `imports.dirs` that is not a literal path, when `.nuxt/` is absent |

Route blind spots affect every route list and every caller answer in that language. Handler
blind spots affect the registered function and chains that run through it.

```text
$ cg routes --db out/shop.db
all routes: 1 indexed (possibly more: 1 unmodelled route registration)
coverage note: 1 route registration cg does not model (Django urlpatterns built by a function call: shop/urls.py:9).
```

Complete answers keep the usual wording (`1 of 1 routes`, `has no recorded callers`) and get
no extra line.

## MCP `completeness`

```json
{"complete": false,
 "languages": {"python": {"mode": "exact", "discovered": 4, "indexed": 2, "parse_failed": 1, "unmapped": 1, "complete": false}},
 "unsupported": {"qml": 1},
 "blind_spots": [{"kind": "django_dynamic_urlpatterns", "category": "route", "count": 1, "sample": "shop/urls.py:9"}]}
```

The object is scoped like the text note. On a combined graph, language keys are prefixed with
the repo name (`bookstore-api/php`). `coverage(json_output=true)` returns the whole-index
object as text.
