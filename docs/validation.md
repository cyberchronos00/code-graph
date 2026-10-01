# Validation on public projects

The Python/Django and Dart/Flutter plugins were checked against shallow clones of well-known open-source projects
(default branch, October 2026). Numbers are from `python -m codegraph.cli index <repo>` on a single 8-core Linux box;
wall time includes the Dart extractor (facts cache cold) but not the one-off `dart compile exe` of the extractor.

"Spot check" means: 20 routes (or every route when there are fewer) sampled at random from the graph and compared by
hand with the source line they cite: HTTP method, full path including every `include()`/`add_router()`/router prefix,
path parameters, and the handler symbol.

## Django / Python

| Project | What it exercises | .py files parsed | Index | Nodes / edges | Routes | Spot check | Parse failures |
|---|---|---|---|---|---|---|---|
| django-ninja (repo incl. docs) | NinjaAPI, Router, add_router, path params, auth, schemas | 183 / 185 | 1.7 s | 2,269 / 5,877 | 300 (33 mounted through urls; the rest are doc/test snippet routers, emitted with `mounted=0`) | 20/20 | 0 |
| Django-Styleguide-Example | APIView-based services, nested includes, Celery | 162 / 190 | 0.5 s | 806 / 1,440 | 22 | 20/20 | 0 |
| channels-examples | Channels `URLRouter`, consumers, function views | 25 / 28 | 0.1 s | 93 / 109 | 7 (2 websocket) | 6/6 paths; 2 handlers are Django's own auth views (external) | 0 |
| paperless-ngx | DRF routers, ViewSets, `@action` (incl. regex `url_path`), Channels, Celery, signals | 353 / 407 | 10 s | 7,672 / 31,332 | 223 | 20/20 paths; 3 handlers external (RedirectView, allauth) | 0 |
| netbox | large DRF API, plugin URL registries | 980 / 1,290 | 29 s | 23,917 / 112,393 | 956 | 20/20 | 0 |
| wagtail | admin viewsets, hooks, many `include()` levels | 1,058 / 1,324 | 29 s | 22,395 / 69,911 | 195 | 20/20 paths; 5 handlers unresolved (viewset registry `as_view`, external LoginView) | 0 |
| saleor | GraphQL-first API | 2,867 / 4,335 | 68 s | 30,521 / 134,683 | 9 | n/a (GraphQL is not modelled) | 0 |

The difference between files and parsed files is migrations (skipped by default; `python_include_migrations` turns
them on) and files over the size cap. No file failed `ast.parse`.

Gaps seen:
- netbox: the API prefix comes from an f-string over `settings.BASE_PATH`, rendered as `{?}`; views registered through
  `register_model_view()` / `get_model_urls()` are partly unresolved.
- wagtail: `page_viewset_registry` / hook-generated URL lists resolve paths but not always the handler.
- saleor: one GraphQL endpoint; resolvers/mutations are not routes.

## Flutter / Dart

| Project | What it exercises | .dart files | Index | Nodes / edges | HTTP call sites (endpoints) | Pages | Parse failures |
|---|---|---|---|---|---|---|---|
| flutter/samples (whole repo) | package:http, dart:io HttpClient, go_router (nested + ShellRoute), Navigator | 465 | 1.2 s | 5,391 / 11,366 | 31 (25) | 62 | 0 |
| lichess mobile | custom `http.BaseClient`, `Uri(path:)`, Riverpod, Dart 3.13 primary constructors | 735 | 21 s | 131,930 / 284,800 | 148 (123) | 92 | 0 |
| localsend | Rust/FFI networking, enum-built URL table, custom router | 383 | 5 s | 33,979 / 74,062 | 7 | 1 | 0 |
| flutter_clean_arch | Dio + BaseOptions(baseUrl), bloc, auto_route v4 | 49 | 0.04 s | 196 / 479 | 4 (3) | 0 | 0 |
| mova | Dio, URL helper class, bloc | 61 | 0.07 s | 319 / 770 | 9 (7) | 3 | 0 |

Spot checks:
- flutter/samples: 12 sampled HTTP calls all correct (method, host, path, `{id}` params); 2 calls whose whole URL is a
  runtime value are labelled `dynamic`. All 13 go_router routes of `navigation_and_routing` (nested paths joined to their
  parent, `:id` → `{id}`) and their screens are correct.
- lichess: 18/20 HTTP calls correct; the two imprecise ones are an env-hosted CDN URL rendered with a leading `/` and a
  HEAD whose URI comes from a list. 105 call sites keep an `unknown` origin because the host is added in the client's
  `send()` override. Pages come from the project's `buildScreenRoute(screen: X())` helper (89 call sites in the source).
- mova and flutter_clean_arch: every HTTP call correct, including the Dio `baseUrl` path prefix (`/auth/login`).

Gaps seen:
- localsend talks to peers through a Rust bridge and an enum whose constructor builds the paths; neither is modelled.
- auto_route v4 annotation configs (`@MaterialAutoRouter(routes: [...])`) are not read; v6 `@RoutePage` classes are pages
  without a path.
- Data-driven route lists (`demos.map((d) => GoRoute(path: d.route, ...))`) leave navigation targets unresolved.

Before the analyzer 10 upgrade, 499 lichess files failed to parse (Dart 3.13 primary constructors); they now parse with
the `primary-constructors` experiment fallback.
