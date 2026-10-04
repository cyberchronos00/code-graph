# Validation on public projects

The Python/Django and Dart/Flutter plugins, Laravel broadcasting and test indexing, the Nuxt layout
handling, the framework presets with their starter queries and route-guard classification, and generated-file detection were checked against shallow clones of well-known open-source projects
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

Python source-root detection (`src/` and `lib/` layouts, monorepos, namespace packages) is validated on
fastapi/full-stack-fastapi-template, opentelemetry-python, ansible, flask, pytest and netbox: see
[python.md](python.md#validation). Test indexing (pytest, unittest, fixtures, Django / DRF test clients) is validated on
the same projects: see [python.md](python.md#tests-pytest-and-unittest).

`tests.py` modules and collection chains, checked on django (211 `tests.py` files), netbox, saleor, ansible,
opentelemetry-python, mkdocs, flake8, httpie, beets, sphinx and pylint: every graph is identical before and after (same
nodes, edges and attributes; index time within noise, for example django 50.5 s -> 52.2 s, saleor 62.8 s -> 62.7 s).
The Django `tests.py` files hold test cases and stay test code, and no collection call gained or lost an edge. On cg's
own source, the two plugin modules named `tests.py` that application code imports became application code (136
nodes, 148 test edges back to calls), and `index_project` gained the 9 calls through its framework and language plugin
lists (every framework plugin's `contribute`, the SCIP indexer plugins' `index`), all correct.

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

## Laravel broadcasting and tests

Shallow clones of Laravel apps that use `Broadcast::channel` (default branch, October 2026). "Channels" lists the
declared and undeclared channels found; publishers were compared by hand with every `ShouldBroadcast` event's
`broadcastOn()`, subscriptions and listened events with every `Echo` / pusher call in the repo's own JS.

| Project | Channels | Publishers (events → channel) | Client subscriptions matched | Listened events matched | Notes |
|---|---|---|---|---|---|
| UNIT3D | 2 (presence chatroom, private chatter) | 4 / 4 | 2 / 2 (Alpine + Echo in `resources/js`) | 3 / 3 that have a backend event (`.new.ping` and whispers have none) | presence visibility comes from the publishers |
| koel | 2 | 2 / 2 | 2 / 2 (Echo and pusher-js `private-…`) | 2 / 2 | the base event's `broadcastOn()` returns `[]` (correctly no channel) |
| invoiceninja | 4 (2 declared, 2 undeclared public) | 10 / 10 events that name a channel | n/a (the frontend is a separate repo) | n/a | one broadcast event returns `[]` |
| pixelfed | 4 | 9 / 9 | n/a (no Echo client in the repo) | n/a | `live.chat.{id}` has a callback but its 7 events publish on a public `Channel`: flagged PUBLIC PUBLISH |
| coolify | 2 | 18 / 18 | 0: listeners are Livewire `echo-private:` keys in PHP (not modelled) | n/a | route middleware given as arrays is skipped |

Tests: TEST_HTTP edges, 10 sampled at random per project and checked against the cited source line (verb, path or
route name, target route): 40 / 40 correct.

| Project | Test cases | HTTP test calls | Matched to a route | Unmatched (main reason) |
|---|---|---|---|---|
| UNIT3D | 861 (852 Pest) | 601 | 538 | 59 (incl. 9 calls to a route name shared by a whole route group, left out on purpose) |
| koel | 1,673 PHPUnit (+ Vitest) | 741 (449 through `getAs()` / `postAs()` helpers) | 502 | 237 (Subsonic routes are registered in a `foreach`) |
| invoiceninja | 5,981 PHPUnit (+ Playwright) | 2,023 | 1,970 | 46 |
| pixelfed | 1,410 (1,175 Pest) | 592 | 566 | 20 |

## Nuxt layouts and clean checkouts

Shallow clones indexed without `npm install` and without `.nuxt` (the clean-checkout path):

| Project | Layout | Pages / layouts / components found | Checks |
|---|---|---|---|
| breeze-nuxt (Nuxt 3 client for a Laravel Breeze API) | source at the repo root | 8 / 2 / 14 (all) | 8 / 8 HTTP calls found with method and path (7 through a `$fetch.create` instance auto-imported from `utils/`); the `runtimeConfig.public.backendUrl` default resolves from `nuxt.config.ts` |
| elk | `app/` | 59 / 2 / 196 (all) | RENDERS edges: 20 / 20 sampled correct; of the 25 components without a RENDERS edge, 14 are rendered through `<component :is>` and 11 from TS (`h()`, TipTap node views) |

## Presets, starter queries and route guards

`cg index <repo>` with no flags and no `.cg.yaml`, then `cg starters` and `cg routes --unguarded`. Monorepos are
indexed per app (`immich/server`, `cal.com/apps/web`, ...). "Starters" counts the starter queries that resolve to
existing nodes out of those generated; "auth by" counts auth guards by the rule that recognised them (`preset X`: the
framework preset lists the guard by its own name; `name pattern`: the shared auth token pattern; `framework`: the
plugin knows the check, e.g. Laravel broadcast channel callbacks). Index time includes the starters (last column).

| Project | Commit | Frameworks detected | Presets applied (+ common) | Starters | Routes / with auth / unguarded | Auth by | Index | Starters |
|---|---|---|---|---|---|---|---|---|
| koel/koel | 295d8c1 | laravel | php, typescript, laravel | 5 / 5 | 191 / 166 / 23 | preset laravel 161, name pattern 8, framework 2 | 12.22 s | 0.08 s |
| laravelio/laravel.io | 24be489 | laravel | php, typescript, laravel | 6 / 6 | 64 / 0 / 64 | – | 2.55 s | 0.01 s |
| netbox-community/netbox | 251458b | django, djangorestframework | python, django, djangorestframework | 6 / 6 | 956 / 92 / 864 | name pattern 105, preset django 31, preset djangorestframework 2 | 34.78 s | 1.2 s |
| saleor/saleor | 8385ca6 | django | python, django | 6 / 6 | 9 / 0 / 9 | – | 68.31 s | 0.37 s |
| immich-app/immich `server/` | c5e06dc | nest, express | typescript, nest, express | 4 / 4 | 295 / 295 / 0 | name pattern 295, preset nest 295 | 12.3 s | 0.28 s |
| immich-app/immich `mobile/` | c5e06dc | flutter | python, dart | 3 / 3 | – | – | 3.79 s | 0.07 s |
| calcom/cal.com `apps/api/v2` | 54343aa | nest | typescript, nest | 3 / 3 | 162 / 140 / 22 | name pattern 217 | 7.76 s | 0.01 s |
| calcom/cal.com `apps/web` | 54343aa | nextjs | typescript, nextjs | 4 / 4 | 99 / 0 / 99 | – | 11.28 s | 0.06 s |
| brocoders/nestjs-boilerplate | 9620f15 | nest | typescript, nest | 6 / 6 | 22 / 11 / 11 | preset nest 16 | 2.6 s | 0.01 s |
| hagopj13/node-express-boilerplate | 179ae84 | express | typescript, express | 2 / 2 | 14 / 6 / 8 | name pattern 6 | 2.18 s | 0.0 s |
| elk-zone/elk | 8a90074 | nuxt | typescript, nuxt | 3 / 3 | – | – | 4.29 s | 0.01 s |
| BurntSushi/ripgrep | 3fce3b5 | – | rust | 3 / 3 | – | – | 27.59 s | 0.05 s |
| redis/redis | b540ca4 | – | python, c_cpp | 3 / 3 | – | – | 5.53 s | 0.09 s |
| pallets/flask | d73fa1c | – | python | 3 / 3 | – | – | 0.74 s | 0.01 s |

saleor sets `testpaths = ["saleor"]`, its application package. Since
[#18](https://github.com/cyberchronos00/code-graph/issues/18) only the files pytest collects there (plus `conftest.py`
and `tests/` directories) are test code: test modules 2,866 -> 1,718, `CALLS` edges 0 -> 11,848 (`TEST_CALLS`
58,676 -> 42,370), the same 12,866 test cases, and 6 starters instead of 1. `impact
saleor.order.utils.invalidate_order_prices` now lists 1,152 application callers (before: none, only tests). On
the now connected call graph the starters took 63 s at first, almost all of it in the unguarded-write-routes report
(one reverse closure per written table, 95 tables). Since
[#25](https://github.com/cyberchronos00/code-graph/issues/25) the route report walks the graph once for all write
targets, limited to what the routes reach, with the same output (`cg routes --writes --unguarded` and the 6 starters
are identical), and the starters keep to a 20 s budget (`starters_skipped` names any left out). Index time, best of
three on the same box:

| Project | Before #25: index / starters | After #25: index / starters |
|---|---|---|
| saleor | 124.41 s / 57.47 s | 68.31 s / 0.37 s |
| netbox | 29.96 s / 0.98 s | 27.91 s / 0.27 s |

Every starter resolved on every project. Index time on netbox, best of three on the same box: 34.1 s before (v0.4.0)
and 33.5 s after, with 1.1 s of that spent on the starters; the shared skip lists and presets add no measurable cost.

**Hand-checked `routes --unguarded` samples.** For each framework, the first unguarded routes and a sample of the
guarded ones were compared with the source:

| Project | Guards found by the framework preset | Unguarded routes, checked against the source | Needs a project pattern or a later feature |
|---|---|---|---|
| koel (Laravel) | `auth`, `auth:sanctum`-style aliases on route groups (161) | `GET /api/ping`, `POST /api/me` (login), `POST /api/forgot-password`, `POST /api/reset-password`, `GET /api/invitations`, `GET /demo/new-session`: public by design | – |
| laravel.io (Laravel) | – | `POST /forum/{thread}/lock`, `PUT /forum/{thread}/mark-solution/{reply}`, `POST /articles`, `PUT /admin/articles/{article}/pinned`: protected by `$this->middleware(Authenticate::class, ...)` in the controller constructor | controller-constructor middleware (planned) |
| netbox (Django + DRF) | `LoginRequiredMixin`, `UserPassesTestMixin`, `IsAuthenticated`; netbox's own `ConditionalLoginRequiredMixin`, `ObjectPermissionRequiredMixin` and `IsSuperuser` by name pattern | 851 REST API routes (`POST /api/wireless/wireless-links/`, ...): protected by `REST_FRAMEWORK['DEFAULT_PERMISSION_CLASSES']` in settings; `GET /login/`, `GET /logout/`, `POST /oauth/begin/{backend}/`: public by design | DRF settings defaults (planned); `IsAuthenticatedOrLoginNotRequired` (anonymous access when `LOGIN_REQUIRED` is off) counts once listed in `auth.extra_patterns`: 864 → 860 |
| saleor (Django) | – | 9 plain Django views (the GraphQL endpoint, plugin webhook endpoints, thumbnails, images, JWKS, static files): public endpoints; the GraphQL API checks permissions per resolver and each plugin checks its own requests | – |
| immich `server/` (NestJS) | `AuthGuard` on all 295 routes (with immich's `MaintenanceAuthGuard` by name pattern) | none; the 2 routes the test setups in `test/medium/specs/*.spec.ts` build are test code since [#19](https://github.com/cyberchronos00/code-graph/issues/19) (297 / 2 unguarded before) | – |
| cal.com `apps/api/v2` (NestJS) | cal.com's `ApiAuthGuard`, `PermissionsGuard`, `OAuthClientGuard` by name pattern | `GET /health`, `GET /v2/atoms/event-types/{eventSlug}/public`, `POST /v2/auth/oauth2/token`, OAuth callbacks: public by design; `POST /v2/webhooks/vercel/deployment-promoted` | `VercelWebhookGuard` counts as a signature check once listed in `secret.extra_patterns` (22 → 21) |
| cal.com `apps/web` (Next.js) | – | `POST /api/auth/two-factor/totp/disable`, `POST /api/availability/calendar`: the handler calls `getServerSession()` and returns 401 itself; `POST /api/auth/signup`, `POST /api/auth/forgot-password`: public by design | session checks inside the handler body |
| nestjs-boilerplate (NestJS) | `AuthGuard('jwt')`, `RolesGuard` (16) | `POST /v1/auth/email/login`, `/register`, `/forgot/password`, `/reset/password`, social logins: public by design | – |
| node-express-boilerplate (Express) | the project's `auth()` middleware by name pattern | `POST /login`, `/register`, `/refresh-tokens`, `/forgot-password`, `/reset-password`, `/verify-email`: public by design | – |

A `.cg.yaml` for the two project patterns above:

```yaml
auth:
  extra_patterns: ["IsAuthenticatedOrLoginNotRequired"]   # netbox
secret:
  extra_patterns: ["WebhookGuard$"]                        # cal.com apps/api/v2
```

## Monorepo apps

One `cg index <root>` with `.cg.yaml` `apps` (the file was written for the run and removed afterwards), compared
with indexing each app and linking each pair one by one: every per-app and per-pair graph has the same nodes and
edges.

| repo | apps | one command | per app (nodes / edges) | links (endpoints matched) |
|---|---|---|---|---|
| immich | server, ml (`machine-learning`), web, mobile | 20.6 s | server 8,028 / 36,234; ml 727 / 1,956; web 2,351 / 3,670; mobile 7,443 / 22,990 | web → server 0/0, mobile → server 0/0 (both call through generated SDKs that are not in the checkout) |
| cal.com | api-v2 (`apps/api/v2`), web (`apps/web`) | 18.5 s | api-v2 3,813 / 12,304; web 5,689 / 12,571 | web → api-v2 0/28 (the web app calls its own Next `/api` routes) |
| bundled samples | api (`bookstore-django`), web, flutter, android, ios | 2.0 s | api 207 / 341 | flutter 6/7, android 3/3, ios 3/3, web 0/6 (it targets the Laravel sample) |

Taking the extractor skip lists from the presets changed no graph: immich server / web / mobile, cal.com `apps/web`,
elk, social-app and nestjs-boilerplate index to the same nodes and edges as before.

## Generated and copied files

`cg index <repo>` with no flags and no `.cg.yaml` (default: generated, copied and vendored files excluded), then again
with `--include-generated`; "before" is v0.4.0 plus the presets change. The Nuxt row is the `nuxt/starter` template
(v4 branch) after `npm install` and `nuxi prepare`, so `.nuxt/` exists as in a developer checkout; the
Ionic/Capacitor row is a public Angular + Capacitor app with its `android/` project and `www/` build committed.

| Project | Commit | Files classified, by reason | Discovered source files (before → after) | Nodes / edges before → default → `--include-generated` |
|---|---|---|---|---|
| ionic-team/capacitor-plugins | 87c0bb8 | – (no build output or copies committed) | TS 96 → 96 | 28 / 45 → 28 / 45 → 28 / 45 |
| abritopach/angular-ionic-master-detail | ba286ac | copy of `www/` (Capacitor webDir) 107, web build output (webDir) 104 | TS 351 → 140 | 38 / 50 → 38 / 50 → 38 / 50 |
| nuxt/starter (v4) | 9b0ac50 | Nuxt build output (`.nuxt/`) 27 | TS 29 → 2 | 1 / 0 → 1 / 0 → 1 / 0 |
| grpc/grpc `examples/` | e124571 | protoc Python 40, protoc / gRPC banner 16, protoc JS 4, `<auto-generated>` header 1 | Python 143 → 103, PHP 20 → 11, TS 13 → 9 | 1364 / 1835 → 1154 / 1670 → 1364 / 1835 |
| jellyfin/jellyfin-sdk-typescript | 1ef0625 | OpenAPI Generator banner 409 | TS 507 → 98 | 3082 / 9489 → 345 / 857 → 3082 / 9489 |
| openfga/js-sdk | d6d4035 | OpenAPI Generator banner 10, `.openapi-generator/FILES` 4 | TS 54 → 40 | 685 / 3219 → 453 / 2063 → 685 / 3219 |
| flutter/samples | a05867d | Flutter plugin registrant 60, `*.g.dart` 46, `*.freezed.dart` 18, Flutter `ephemeral/` 2 | Dart 484 → 420, C/C++ 210 → 162 | 5394 / 11368 → 4631 / 10141 → 6091 / 12666 |

In every default index no node comes from a classified file, and `cg coverage` lists each one by reason. In every
`--include-generated` index, every node from a classified file carries `attrs.generated` (0 unlabelled): 210 on grpc,
2737 on jellyfin, 232 on openfga, 1478 on flutter/samples. flutter/samples grows past "before" with the flag, because
`*.freezed.dart` output, which the Dart plugin always skipped, is indexed too. On the Ionic app and on capacitor-plugins
the node count does not move: the TS plugin only reads `src/`, so the 211 copies were never parsed, but they were
counted as unindexed TypeScript and inflated the coverage gap. Linking the JS side of a Capacitor plugin to its
Android/iOS implementation needs the Kotlin/Java and Swift plugins (see [generated.md](generated.md)).

Bundled examples are unchanged except `bookstore-flutter` (90 / 171 → 87 / 164: the `book.g.dart` json_serializable
output is no longer a node; `PARSES_JSON` is unchanged, because the file is still read for the exact JSON keys). The classification scan
takes 0.00–0.08 s on these projects; index time on netbox (no generated files),
median of five alternating runs on a shared box: 35.6 s before and 36.0 s after (run-to-run spread 33–37 s), with
the scan itself at 0.07 s.

## Platform-specific code

`cg index <repo>` with no flags and no `.cg.yaml`, then `cg platforms divergence`. "Before" is the index without
platform tags; node counts are identical before and after on every project, and the added edges are the
`platform_variant_of` copies that give each sibling variant the callers of the one the resolver picked (plus Rust
`cfg-inactive` references, which carry the condition instead of being dropped). Targets come from the project itself:
Flutter folders, React Native and its `react-native-web` dependency, or the desktop default for native code, plus every
OS a condition names.

| Project | Commit | Targets | Conditions (not evaluable) | Tagged nodes / edges | Edges before → after | Variants / API differences / missing callees | Platforms pass |
|---|---|---|---|---|---|---|---|
| BurntSushi/ripgrep | 3fce3b5 | windows, linux, macos | 106 (7) | 59 / 316 | 25765 → 25812 | 17 / 0 / 0 | 0.29 s |
| alacritty/alacritty | d692748 | windows, linux, macos | 480 (18) | 324 / 1167 | 23135 → 23303 | 18 / 0 / 0 | 0.13 s |
| libuv/libuv (C, heuristic) | 49b1c06 | windows, linux, macos, ios, android | 1153 (211) | 5300 / 30318 | 52483 → 61235 | 235 / 0 / 85 | 0.77 s |
| curl/curl (C, compile database) | d4dc7d1 | windows, linux, macos | 476 (212) | 527 / 2486 | 113311 → 115781 | 114 / 0 / 0 | 0.28 s |
| dart-lang/http | 47c57df | 6 Flutter folders | 88 (0) | 104 / 204 | 11365 → 11378 | 30 / 1 / 1 | 0.21 s |
| bluesky-social/social-app (React Native) | db23528 | ios, android, web | 240 (3) | 1038 / 4328 | 44842 → 47065 | 108 / 9 / 102 | 1.13 s |
| localsend/localsend (Flutter) | c5bbe36 | 6 Flutter folders | 132 (64) | 10 / 95 | 10860 → 10860 | 1 / 0 / 0 | 0.15 s |

What the findings are, from spot checks:

- **ripgrep, alacritty, curl:** every variant group is a real per-OS definition set (`#[cfg(unix)]` / `#[cfg(windows)]`
  pairs, `#ifdef WIN32` / `#else` function and macro pairs), and no reference lacks a callee on any target.
- **dart-lang/http:** the conditional imports (`client_stub.dart` / `io_client.dart` / `browser_client.dart`,
  `connect_stub.dart` / …) form the variant groups, including imports whose default is an SDK library (`dart:isolate`).
  The one API difference and missing callee are `connect` in `browser_web_socket.dart`, a top-level
  `const connect = BrowserWebSocket.connect` tear-off the Dart extractor does not record as a symbol.
- **social-app:** the `.ios` / `.android` / `.native` / `.web` file groups. Sampled findings come from variant files
  that re-export the symbol (`export {x}`, `export {a as b} from 'react-dom'`), which the TS extractor does not record
  as a definition in that file, and from `.web.ts` files that tests call directly.
- **libuv:** the per-OS function sets of `src/unix/*` and `src/win/*`. Missing callees come from the C heuristic
  extractor: functions generated by macros (`SOCKOPT_SETTER` in `src/win/udp.c`), functions after a region tree-sitter
  cannot parse (`src/win/tty.c`), and one macro defined in two `#if` branches of one header.
- **localsend:** almost all platform logic is runtime `Platform.isX` / `defaultTargetPlatform` checks inside shared code;
  64 of them use `TargetPlatform` values that do not exist on web, so the web count of unevaluated conditions is high
  and those branches stay in for web.

Index time on netbox (no platform-specific code), best of three alternating runs on a shared box: 43.2 s before and
42.7 s after (spread 42.7–45.4 s); nodes and edges are identical (38028 / 140181).

Divergence false positives removed in #63 (before → after on the same commits):

| Project | Commit | missing_callee | variants | Edges | What changed |
|---|---|---|---|---|---|
| libuv | 49b1c06 | 38 → 35 (+3 counted, built for no target) | 496 → 495 | 70889 → 70826 | `alloc_buffer` of the separate `docs/code/*` programs is no variant group (63 mirrored edges between programs gone); `strnlen` (sunos.c) ×2 and an aix.c helper not listed |
| SwiftUIX | 3a99044 | 51 → 49 | 103 → 103 | unchanged | `Path(...)` bound to a project `extension Path { init }` under `#if canImport(UIKit)` |
| social-app | db23528 | 155 → 155 | 108 → 108 | 60563 → 60559 | `bootstrap.test.ts` imports `./index.web` explicitly: its 4 test edges to `index.ts` are gone |
| mattermost-mobile, IceCubesApp | e5be311, 9efcb16 | unchanged | unchanged | unchanged | |

## Kotlin

Heuristic mode (tree-sitter-kotlin 1.1.0), shallow clones, `cg index` on the default branch:

| Repository | Kotlin files | Declarations | Calls resolved / unresolved | Framework facts | Index time |
|---|---|---|---|---|---|
| android/nowinandroid | 350 | 1,219 | 1,608 / 5,696 | 4 Retrofit endpoints, 3 activities, 1 service, 2 workers, 2 deep links | 0.8 s |
| ktorio/ktor-samples | 204 | 570 | 652 / 5,635 | 77 Ktor routes, 115 Ktor client endpoints, 10 KMP source-set files | 0.4 s |
| spring-petclinic/spring-petclinic-kotlin | 40 | 139 | 112 / 548 | 19 Spring routes | 0.1 s |
| touchlab/KaMPKit | 38 | 114 | 167 / 562 | 2 `expect` → `actual` links, 5 KMP source-set files, 1 activity | 0.1 s |

Unresolved calls are mostly library and standard-library calls (Compose, coroutines, collections), which have no
node in the graph.

Framework facts added with exact mode (same corpora, before → after; every other node and edge unchanged apart from
calls now owned by the new page / route-handler nodes): nowinandroid 2 → 7 pages (Navigation 3 `entry<Key>`), 0 → 3
`NAVIGATES_TO`; ktor-samples 77 → 90 routes (type-safe resources), 3 Exposed tables with 14 reads / writes, 8 → 11
Ktor client calls (builder blocks); spring-petclinic-kotlin 4 tables with 17 Spring Data reads / writes (plus 38 from
tests). KaMPKit (URL built in a helper) and android/architecture-samples (routes from `const val` strings) are unchanged.
No corpus has a `SecurityFilterChain`; the fixture covers it.

### Kotlin exact mode (scip-java)

scip-java 0.12.3 standalone launcher, JDK 17, `CODEGRAPH_KOTLIN_SCIP_FILE` pointing at the index. scip-java's
semanticdb-kotlinc plugin only loads into Kotlin ≤ 2.1, and these builds use Kotlin 2.4: each was indexed from a copy
with the Kotlin Gradle plugin pinned to 2.1.20 (Ktor samples also with `-Xskip-metadata-version-check`, since Ktor 3.6
is compiled with Kotlin 2.3 metadata); the sources are unchanged. Precision: share of heuristic call edges (`CALLS` /
`INSTANTIATES`, same source and target) that the index confirms; recall: share of compiler edges the heuristic layer
found.

| Project | Kotlin files in index | Defs matched / unmatched | Heuristic edges | Exact edges | Agree | Precision | Recall | scip-java run |
|---|---|---|---|---|---|---|---|---|
| spring-petclinic-kotlin | 38 / 38 | 140 / 4 | 98 | 96 | 96 | 0.98 | 1.00 | ~2 min cold, 24 s warm |
| ktor-samples/kweet | 16 / 16 | 74 / 7 | 77 | 95 | 74 | 0.96 | 0.78 | 28 s |
| ktor-samples/mvc-web | 12 / 12 | 30 / 1 | 25 | 32 | 25 | 1.00 | 0.78 | 21 s |
| ktor-samples/youkube | 9 / 9 | 24 / 2 | 25 | 29 | 25 | 1.00 | 0.86 | 27 s |
| ktor-samples/postgres | 7 / 7 | 14 / 1 | 11 | 11 | 11 | 1.00 | 1.00 | 24 s |
| 8 smaller ktor-samples (1–2 files each) | 11 / 11 | 100 / 12 | 17 | 28 | 15 | 0.88 | 0.54 | 17–26 s |
| **Total (13 Gradle builds)** | 93 | 382 / 27 | 253 | 291 | 246 | **0.97** | **0.85** | |

The heuristic layer's misses are mostly extension functions called on a library receiver (`call.redirect(...)`),
overloads and lambda receivers (`it.area()`); its few disagreeing edges attribute a call to the file instead of the
enclosing function, or pick a same-named function. Unmatched definitions (SCIP definitions with no syntax-layer
declaration on that line) get no edges. Not run: chat, h2, opentelemetry (the build does not
configure with the pinned plugin), httpbin (needs Kotlin 2.2 language features), and the Android projects
(nowinandroid, KaMPKit, architecture-samples: no Android SDK on the validation machine). Without a JDK, or with the
unpinned Kotlin 2.4 build, the index completes in heuristic mode and `cg coverage` names the reason (`no JDK`, or
`scip-java run failed (exit 1: ...)`).

Kotlin 2.2 and mixed Kotlin / Java (scip-java 0.13.1 launcher next to 0.12.3, `CODEGRAPH_KOTLIN_SCIP=1`, build not
edited): statsig-io/java-server-sdk 9edc7ca, Kotlin 2.2.10, Gradle 8.5 wrapper, 86 Kotlin + 12 Java (test) files. cg
read Kotlin 2.2.10 from the build, ran 0.13.1 (93 s cold): 84 Kotlin files exact, 944 / 59 definitions matched /
unmatched (compiler-generated members such as data class `copy` / `componentN` and anonymous objects not counted),
2,207 references resolved to project code; heuristic precision 0.91, recall 0.81 (1,434 of 1,573 heuristic edges
confirmed, 1,779 exact). Java: 13 documents, 30 classes and 192 methods as `java` nodes, 358 exact Java -> Kotlin / Java edges;
coverage `kotlin 86 exact; java 12 scip` (heuristic mode: `java 12 unsupported`). Graph 1,003 -> 1,097 nodes,
3,226 -> 3,852 edges. The same Gradle fixture on Kotlin 2.1.20 (0.12.3) and 2.2.10 (0.13.1) gives identical edges;
on 2.2.21, 2.3.21 and 2.4.20 neither release loads (`NoSuchMethodError` / `AnalyzerRegistrar is incompatible`) and
coverage names the version.

Android: android/architecture-samples ee66e15 (Kotlin 2.1.10, modules `app`, `shared-test`) with an Android SDK
installed in the user's home (command-line tools + `sdkmanager "platforms;android-35" "build-tools;35.0.0"`, no
sudo). The default scip-java run fails on the settings' `FAIL_ON_PROJECT_REPOS`; cg reports that and names both
Android modules. With an init script switching the mode to `PREFER_SETTINGS` and the variant tasks requested
explicitly (`:app:compileDebugKotlin`), the build compiles (1 min 27 s) but scip-java 0.12.3's Gradle plugin attaches
no compiler plugin to Android variant tasks: no SemanticDB output, so the index stays heuristic (61 Kotlin files).

## Swift

Heuristic mode (tree-sitter-swift 0.7.3) on Linux without Xcode, shallow clones, `cg index` on the default branch:

| Repository | Swift files | Declarations | Calls resolved / unresolved | Framework facts | Index time |
|---|---|---|---|---|---|
| Alamofire/Alamofire | 101 | 2,019 | 3,817 / 8,170 | 198 extensions, 33 `#if` platform blocks, 5 `AF.request` endpoints (the example apps) | 1.1 s |
| Dimillian/IceCubesApp | 428 | 2,156 | 3,537 / 8,174 | 21 SwiftUI pages, 21 navigations, 14 `@main` entries (app, extensions, widgets), 259 `#if` platform blocks | 1.2 s |
| pointfreeco/isowords | 388 | 1,729 | 2,606 / 7,794 | 17 SwiftUI pages, 12 navigations, 12 `@main` entries, 1 URLSession endpoint | 1.1 s |
| vapor/template | 9 | 16 | 20 / 81 | 3 Vapor routes with their `RouteCollection` handlers | < 0.1 s |

Unresolved calls are mostly SDK calls (SwiftUI modifiers, Foundation, Combine), which have no node in the graph.
IceCubesApp and isowords call their APIs through typed endpoint enums and a swift-parsing router, which are not HTTP
client forms the plugin reads yet. The Vapor template's `routes.swift` contains Mustache markup, so its two
closure routes do not parse. Since exact mode, the body of an overloaded method is owned by its (merged) method node
rather than the enclosing type: Alamofire 3,825 → 3,817 resolved calls (overloads calling each other became self
calls), isowords gained one URLSession endpoint (built inside an overloaded `request`).

Moya and Fluent: none of the four corpora uses Moya (the fixture `tests/swift_facts_fixture` covers it, including a
`TargetType` with a relative base linked to Vapor routes). The Vapor template rendered with Fluent (`fluent: true`,
SQLite, no Leaf) gives `table:todos` (`MAPS_TO_TABLE` from `Todo`), 2 migration writes, 2 reads (`Todo.query`,
`Todo.find`) and 1 write (`todo.delete(on:)`) from `TodoController`, and 2 test uses; the unrendered template gives
the same. `create` saves the model returned by `TodoDTO.toModel()`, whose type is not inferred. No corpus has
`@available(iOS / macOS, unavailable)` or a call to a function defined per `#if os(...)` branch (the fixture covers
both). Apart from these and the overload change above, the four corpora index as before.

### Swift exact mode (index store)

Swift 6.3.2 for Linux (Debian 12 build), `libIndexStore.so` from the same toolchain, `swift build
--enable-index-store` (debug). Precision: share of heuristic call edges (`CALLS` / `INSTANTIATES`, same source and
target) in the files the store covers that the index confirms; recall: share of compiler edges the heuristic layer
found.

| Project | Swift files in store | Defs matched / unmatched | Heuristic edges | Exact edges | Agree | Precision | Recall | Build |
|---|---|---|---|---|---|---|---|---|
| Alamofire/Alamofire (`swift build`, via `CODEGRAPH_SWIFT_INDEX=1`) | 44 / 96 | 868 / 15 | 655 | 833 | 544 | 0.83 | 0.65 | 7 s cold, cache hit 0.4 s |
| pointfreeco/isowords (`swift build --product server`, existing store) | 104 / 384 | 424 / 17 | 246 | 345 | 157 | 0.64 | 0.46 | 41 s (dependencies built) |
| vapor/template (rendered with Fluent + SQLite, existing store) | 7 / 8 | 17 / 0 | 11 | 10 | 9 | 0.82 | 0.90 | ~2 min cold with dependencies |
| **Total** | 155 | 1,309 / 32 | 912 | 1,188 | 710 | **0.78** | **0.60** | |

After the heuristic precision fixes of [#58](https://github.com/cyberchronos00/code-graph/issues/58) (same stores,
same sources; SDK initializers of extended types, initializer argument labels, standard-library collection methods,
and the exact layer's initializers declared in extensions of SDK types):

| Project | Heuristic edges | Exact edges | Agree | Precision | Recall |
|---|---|---|---|---|---|
| Alamofire/Alamofire | 655 → 585 | 833 → 848 | 544 → 552 | 0.83 → **0.94** | 0.65 → 0.65 |
| pointfreeco/isowords (server product) | 246 → 174 | 345 → 374 | 157 → 163 | 0.64 → **0.94** | 0.46 → 0.44 |
| vapor/template | 11 → 11 | 10 → 10 | 9 → 9 | 0.82 → 0.82 | 0.90 → 0.90 |
| **Total** | 912 → 770 | 1,188 → 1,232 | 710 → 724 | 0.78 → **0.94** | 0.60 → 0.59 |

The exact edges grew by the initializers projects declare in extensions of SDK types (`extension Data {
init(hex:) }`), which the layer used to drop; recall moves by that denominator, not by lost heuristic edges. The whole
graph also loses the SDK-initializer edges in files without a store (all edges: Alamofire 7,321 → 6,756, isowords
4,973 → 4,295, mostly `Result`, `Data`, `URL`, `URLRequest` and `UUID` constructions in tests).

What a Linux build covers: Alamofire's `Source/` (tests and example apps need Apple frameworks); isowords' server
modules (the full `swift build` stops at SwiftUI; the server product needs `libsqlite3-dev`), the rest of its 384 files
keep heuristic edges and `cg coverage` counts them; the Vapor app's `App` target (its test target was not built). Not
run: Dimillian/IceCubesApp (iOS-only SwiftUI app, no Linux build; heuristic, with the reason in `cg coverage`).

Where they disagreed before #58: the heuristic layer linked initializers of SDK types the project extends (`URL(...)`,
`Date(...)`, `UUID(...)` → the extension's class node, while the compiler resolves them to Foundation) and picked
same-named methods (`xs.first(where:)` → a project `first`; now fixed; a Fluent migration's `.create()` still matches
`TodoController.create`); it misses initializer calls
through `Self(...)` / `.init(...)` / nested types, calls to explicit `init` declarations (the index adds a `CALLS` to
`T.init` next to `INSTANTIATES`), and members reached through inferred types (`.live`, closure parameters). In
Alamofire, 247 heuristic edges sit in inactive `#if` branches (Apple-only code); they are kept with
`via: "not-compiled"`. Without the opt-in or a toolchain the index completes in heuristic mode and `cg coverage`
names the reason (`Swift toolchain found but the package was not built ...`, `no Package.swift ...`,
`swift build failed (exit 1: ...)`).

## Web / native bridges

`cg index <repo>` with no flags and no `.cg.yaml`, then `cg bridges` ([bridges.md](bridges.md)). The two Capacitor
repositories are monorepos without a root `tsconfig.json`; the TypeScript plugin indexes their per-package
tsconfigs as one program (capacitor: 794 / 1664 → 1633 / 4521 nodes / edges, before that the plugin did not run;
capacitor-plugins: 589 / 640 → 959 / 1367, 20 package tsconfigs instead of 3–4 files picked up by the fallback). Index time is one run each on a shared box (the spread between repeated
runs of the same build was 2–4 s); the bridge pass itself takes the time shown.

| Project | Commit | Endpoints | Linked | Receivers (stubs) | Checks | Bridge pass | Index before → after |
|---|---|---|---|---|---|---|---|
| ionic-team/capacitor (framework) | ba28569 | 24 | 1 | 39 (19) | 4 external, 1 missing_on, 19 no_sender | 0.10 s | |
| ionic-team/capacitor-plugins (library) | 87c0bb8 | 81 | 0 | 159 (86) | 2 missing_on, 75 no_sender | 0.10 s | |
| flutter/samples (platform_channels, add_to_app, pigeon, platform_view_swift) | a05867d | 14 | 14 | 21 (7) | none | 0.05 s | 599 / 999 → 605 / 1023 |
| mattermost/mattermost-mobile (React Native) | e5be311 | 42 | 32 | 71 (30) | 3 no_sender | 0.05 s | 23.1–24.2 s → 24.3–25.0 s; 21639 / 81163 → 21711 / 81297 nodes / edges |
| bluesky-social/social-app (Expo modules) | db23528 | 33 | 1 | 62 (0) | 4 missing_on, 32 no_sender | 0.03 s | 15.6–19.2 s → 16.7–18.1 s; 9900 / 47568 → 9933 / 47632 |
| social-app with `include: [modules]` | db23528 | 33 | 21 | 62 (0) | 12 no_sender | 0.03 s | |
| immich-app/immich `mobile/` (Flutter, Pigeon) | c5e06dc | 45 | 43 | 86 (0) | 3 missing_on, 2 no_sender | 0.08 s | 7443 / 22989 → 7488 / 23131 |

What the findings are, from spot checks:

- **capacitor:** the framework's own core plugins (CapacitorHttp, CapacitorCookies, WebView, Console) are received on
  both platforms; `CapacitorHttp#request` is the one method the framework's own web code sends (through
  `nativePromise`), the others are the plugins' public API, called by apps. `Console#log` exists on iOS only (Android logs through the web
  view). The external endpoints are test literals and `App#exitApp` from the `@capacitor/app` package.
- **capacitor-plugins:** a library repository, so no senders. The two `missing_on` are real: `TextZoom.get` / `set`
  are Android methods (iOS has `getPreferred` only).
- **immich mobile:** 11 Pigeon APIs (10 `@HostApi`, 1 `@FlutterApi`) from `mobile/pigeon/*.dart` (the generated
  files are git-ignored): 50 Dart sends, 86 Kotlin / Swift receivers, 3 native sends to the Dart
  `BackgroundWorkerBgService`. Kotlin implementations inherit shared methods from `NativeSyncApiImplBase`
  (`hashAssets` is received there on Android and in `NativeSyncApiImpl` on iOS). The `missing_on` are real
  (`BackgroundWorkerLockApi` and `ViewIntentHostApi` are implemented on Android only), as are the two `no_sender`
  (`NetworkApi#addCertificate`, `NativeSyncApi#clearSyncCheckpoint` have no Dart caller).
- **flutter/samples:** every channel method is linked on both platforms. Native → Dart: `reportCounter`
  (add_to_app fullscreen, Kotlin and Swift), `setCount` (multiple_flutters) and `setCellNumber` (android_view list
  cells) are sent by `invokeMethod` and received by the Dart `setMethodCallHandler`; the books sample's Pigeon
  `HostBookApi` / `FlutterBookApi` link both ways. `platform_view_swift` is an iOS-only app
  (no `android/` next to its `pubspec.yaml`), so its `switchView` is not reported missing on Android. The stubs are
  Swift methods the Swift plugin does not index (multi-line `application(...)` signatures, `viewDidLoad` overrides).
- **mattermost-mobile:** the app imports its modules from local `file:` packages (`@mattermost/rnutils`,
  `@mattermost/calls-native`, ...) without `node_modules`; they resolve to their source, through
  `isTurboModuleEnabled ? require('./NativeX').default : NativeModules.X`, `X || new Proxy(...)` and
  `Object.assign(X, { onEvent })` (whose helpers are not endpoints). `setNavigationBarColor` is Android-only and its
  only call is under `Platform.OS === 'android'`, so it is not missing on iOS. The 3 senderless `MattermostShare`
  methods are called from `share_extension/`, which the default source dirs do not index; the module is declared
  `codegenConfig.platforms: ["android"]`.
- **social-app:** the Expo modules are called from wrapper functions inside `modules/*/src`, which is outside the
  default source dirs; with `include: [modules]` 21 methods link and no platform gap remains
  (`ExpoBlueskyReferrer` is called from an `.android.ts` file, `setAudioActive` after
  `if (Platform.OS !== 'ios') return`). The senderless methods are view-ref methods (`GifView.playAsync`) and
  notification-extension preferences.
- **immich:** the mobile app talks to native code through Pigeon-generated APIs only, which are not modelled yet.

Native events, Cordova and dynamic names (#61), before → after on the same commits:

| Project | Commit | Endpoints | Linked | New endpoints | Unresolved (dynamic) | Nodes / edges |
|---|---|---|---|---|---|---|
| mattermost-mobile | e5be311 | 42 → 49 | 32 → 33 | 7 react-native-event | 4 | 23813 / 95635 → 23820 / 95643 |
| capacitor-plugins (library) | 87c0bb8 | 81 → 96 | 0 → 0 | 15 capacitor-event (29 `notifyListeners` sends) | 6 | 1192 / 1847 → 1221 / 1876 |
| EddyVerbruggen/Toast-PhoneGap-Plugin (Cordova) | 55856bc | 0 → 2 | 0 → 2 | 2 cordova | 0 | |
| social-app | db23528 | 33 → 33 | 1 → 1 | none | 0 | unchanged |

- **mattermost-mobile:** `DimensionsChanged` links the Swift `sendEvent(name:)` to `useWindowDimensions`'
  `NativeEventEmitter(RNUtils)` listener (the Android side emits through an enum value, not followed). Three native
  events have "no JS listener found": their listeners sit in the local `@mattermost/*` packages and take the name from
  a constant object or a `switch` on the event. Three `DeviceEventEmitter` listeners belong to the app's JS event bus
  (emitted with names built elsewhere) and are not flagged. No existing endpoint or check changed.
- **capacitor-plugins:** a library, so the events have no JS listener; `appRestoredResult`, `backButton` and the
  local / push notification action events are emitted on Android only. The unresolved sends are `notifyListeners`
  calls with a variable name (Browser, StatusBar).
- **Toast-PhoneGap-Plugin:** `show` / `hide` from `www/Toast.js` link to the Java `execute` actions (compared with
  `ACTION_SHOW_EVENT.equals(action)` constants) and the Objective-C `CDVPlugin` methods on both platforms.
- **social-app:** no native events or Cordova calls; its `addListener` calls are navigation, `Keyboard` and a web-only
  `EventEmitter`, none of which became endpoints.

## Desktop process boundaries (Electron, Tauri) and declared targets

`cg index <repo>` with no flags and no `.cg.yaml`, then `cg bridges`; Rust in heuristic mode
(`CODEGRAPH_RUST_SCIP=0`). Index times are one run each on a shared box.

| Project | Commit | Endpoints | Linked | Checks | Index before → after (nodes / edges) |
|---|---|---|---|---|---|
| electron/fiddle | a63225b | 87 electron-ipc, 62 electron-preload | 87, 60 | 2 no_sender | 4.1 s → 3.8 s; 2195 / 5811 → 2456 / 6319 |
| clash-verge-rev/clash-verge-rev (Tauri 2, React) | 3607e66 | 90 tauri | 89 | 1 no_sender | 5.4 s → 5.0 s; 5263 / 15540 → 5353 / 15720 |
| tauri-apps/tauri `examples/` (no root `Cargo.toml`) | 30da1fd | 7 tauri (2 of them `plugin:app-menu\|…` from a plugin `Builder`) | 0 | 7 no_sender | the frontends are `.svelte` / plain HTML, which the TypeScript extractor does not read: no senders |

- **fiddle:** channels are `IpcEvents` enum members, registered through an `ipcMainManager` wrapper; main → renderer
  messages go to a preload `addEventListener` that maps event names to channels through a lookup table (a
  union-typed channel). Every channel is linked. The two `no_sender` members are real: `getTemplateValues` and
  `removeAllListeners` of the isolated run-button API are exposed but never called. `impact` on the main process's
  `readThemeFile` went from 5 callers (main only) to 18, through the preload script into the renderer components.
- **clash-verge-rev:** 90 `#[tauri::command]` functions, all registered in `generate_handler!`; 89 are invoked from
  the TypeScript services. `perf_state` (a command in a perf script crate) has no sender.
- Declared targets: swift-Alamofire `windows, linux, macos, ios` (desktop default) → `windows, linux, macos, ios,
  android` (`Package.swift` platforms, plus the targets its `#if os(Linux) || os(Windows) || os(Android)` names);
  `canImport` / `targetEnvironment` conditions take it from 33 to 103 platform conditions and from 26 to 370 tagged
  nodes. KaMPKit `windows, linux, macos, ios, android` → `ios, android` (the `kotlin { }` block of
  `shared/build.gradle.kts`); Kotlin/kotlinx-datetime (c73ca37) `windows, linux, macos, web` → `windows, linux, macos,
  ios, android, web` (`mingwX64()`, `linuxX64()`, `macosArm64()`, `iosArm64()`, `androidNativeArm64()`, `js()`; a
  plain `jvm()` is not a platform target). Graph edges are unchanged on all three.

## Overrides in `tests` / `reaches`, inherited specs, TypeScript class hierarchy

`cg index <repo>` before and after (#53), no flags. TypeScript graphs gain only class hierarchy edges (no node and no
other edge changes); Python graphs are identical (the change is in the queries).

| Project | Commit | Edges before → after | Added |
|---|---|---|---|
| brocoders/nestjs-boilerplate | 9620f15 | 2097 → 2147 | EXTENDS 8, IMPLEMENTS 6, IMPLEMENTED_BY 36 (repositories `implements` an abstract repository class) |
| bluesky-social/social-app | db23528 | 47632 → 47650 | IMPLEMENTS 13, EXTENDS 2, OVERRIDDEN_BY 3 |
| mattermost/mattermost-mobile | e5be311 | 81297 → 81308 | IMPLEMENTS 8, EXTENDS 3 |
| calcom/cal.com `apps/api/v2` | 54343aa | 12304 → 12322 | EXTENDS 9, IMPLEMENTS 9 |
| elk-zone/elk, breeze-nuxt, node-express-boilerplate | | unchanged | (no class hierarchies) |
| sphinx-doc/sphinx (Python) | b04a210 | 33264 → 33264 | none |

Spot checks: every added edge was a real `extends` / `implements` / override. On social-app,
`impact MergeFeedSource_Following._getFeed` had no callers and now lists `MergeFeedSource._fetchNextInner` (via the
base `_getFeed`) up to `fetchNext`; on nestjs-boilerplate the repository implementations were already reached through
Nest's BOUND_TO edges and now also state `overrides: FileRepository.create`. Calls on a TypeScript **interface** type
(`api.fetch()` with `api: FeedAPI`) still had no target node then; #55 below adds interface members.

## Receiver types on inherited calls in Kotlin, Swift, Dart and PHP (#62)

`cg index` before and after on the same commits: nodes and edges unchanged, calls into an inherited method now
carry `attrs.recv` (the receiver's class), which `impact` / `reaches` / `tests` on `Sub.method` use to leave out
calls on sibling subclasses.

| Project | Commit | Nodes / edges | Calls with `recv` |
|---|---|---|---|
| android/nowinandroid (Kotlin) | a49ed25 | 1824 / 3414 unchanged | 0 → 2 |
| Dimillian/IceCubesApp (Swift) | 9efcb16 | 4028 / 8945 unchanged | 0 → 65 |
| koel/koel (PHP) | 295d8c1 | 15865 / 33158 unchanged | 0 → 708 |
| dart-lang/dart_style (Dart) | 60f31a6 | 1610 / 6365 unchanged | 0 → 150 |

## Interface members, receiver-narrowed inherited specs, container bindings as dispatch (#55)

`cg index <repo>` before and after, no flags; queries with the old and the new code on the old and the new graph.

| Project | Commit | Nodes / edges before → after | Interface members | Calls into them | IMPLEMENTED_BY added (structural) | Calls with `recv` |
|---|---|---|---|---|---|---|
| bluesky-social/social-app | db23528 | 9933 / 47648 → 9959 / 47724 | 26 | 16 | 34 (8) | 4 |
| mattermost/mattermost-mobile | e5be311 | 21711 / 81269 → 21735 / 81384 | 24 | 91 | 0 | 224 |
| immich-app/immich `server/` | c5e06dc | 8028 / 36415 → 8038 / 36450 | 10 | 20 | 5 (0) | 1062 |
| calcom/cal.com `apps/api/v2` | 54343aa | 3813 / 12322 → 3821 / 12341 | 8 | 0 | 11 (0) | 0 |
| brocoders/nestjs-boilerplate | 9620f15 | 764 / 2147 → unchanged | 0 | 0 | 0 | 0 |

No edge was lost: the only edges that changed source are 22 REFERENCES_TYPE from an interface to the types its
member signatures use, which now start at the member. Precision, from spot checks: all 50 added IMPLEMENTED_BY
edges are real (`implements` declarations; the 8 structural ones are `return new AuthorFeedAPI(...)` etc. in a
function returning the other `FeedAPI` interface of social-app's `followingV2`), and the 36 sampled calls into
interface members are calls on values typed with that interface (`api.peekLatest()`, `bulk.getAssetIds(...)`,
`Intl.formatMessage`, mattermost's REST client mixins). `impact AuthorFeedAPI.peekLatest` (social-app) 0 → 48
callers via `FeedAPI.peekLatest`; `impact MemoryRepository.getAssetIds` (immich) 0 → 15 callers, 7 entry points via
`IBulkAsset.getAssetIds`; `impact BaseConfig.getCommand` 0 → 3. mattermost's REST client implements its interfaces
through mixin class expressions, so those members have callers but no IMPLEMENTED_BY yet.

Inherited specs: `impact AppDataOperator.handleRecords` (mattermost) leaves out 4 of 42 calls into
`BaseDataOperator.handleRecords`, all `this.handleRecords(...)` in the sibling `ServerDataOperatorBase` (656 → 339
transitive callers); `ExifTestContext.newUser` (immich tests) 123 of 624 calls on `LibraryTestContext` /
`SyncTestContext`; `MergeFeedSource_Custom.take` (social-app) the call on `MergeFeedSource_Following`. Calls on
mattermost's `ServerDataOperator` (a class built with `mix(ServerDataOperatorBase).with(...)` merged with an
interface) are kept, since the graph does not know that class's ancestry. Container bindings: on
nestjs-boilerplate all 36 bound repository implementations listed the abstract method as a d=1 caller (284 callers
in total); now 0, the abstract method is under `overrides:` (248 callers, exactly those 36 fewer).

Queries on sphinx (graph unchanged): `reaches` on `ASTBaseBase._stringify` (135 overrides) 26 → 31 dependents (5 via
override), on `Builder.write_doc` 25 → 26; `tests` counts unchanged there, since sphinx's calls land on the base
declarations. `DirectoryHTMLBuilder.write_doc` (inherited) answered "no method matches" and now resolves to
`StandaloneHTMLBuilder.write_doc` (27 callers, 504 tests). On cg itself `tests FrameworkPlugin.contribute` goes from 0
to 226 tests (via the plugins' overrides) and `impact FlaskPlugin.contribute` resolves to the inherited
`_PyWebPlugin.contribute`. Index times are unchanged within noise (social-app 18.4 → 16.8 s, mattermost 25.7 → 26.4 s,
cal.com api v2 7.2 → 7.0 s, sphinx 7.9 → 7.8 s; single runs).

## Exact index per target, Swift availability, re-exports in variant files, C macro gaps (#56)

`cg index <repo>` before and after, no flags (Rust in exact mode with rust-analyzer, `CODEGRAPH_RUST_TARGETS`
default `auto`); `cg platforms divergence` counts are variants / API surface / missing callee. Index times are one
run each on a shared box.

| Project | Commit | Edges before → after | Divergence before → after | Notes |
|---|---|---|---|---|
| BurntSushi/ripgrep | 3fce3b5 | 25812 → 25845 | | extra runs windows + web: 47 exact references; `cfg-inactive` edges 16 → 2; 16.9 → 33.8 s |
| alacritty/alacritty | d692748 | 23303 → 23586 | | windows 275 + macos 126 exact references; `cfg-inactive` 123 → 5; 14.5 → 41.0 s |
| tauri-apps/tauri | 30da1fd | 47065 → 48953 | | windows 787 / macos 1869 / android 301 exact references; `cfg-inactive` 1208 → 137; target runs 149 + 108 + 59 s cold |
| libuv/libuv (C, heuristic) | 49b1c06 | 61235 → 70877 | 235 / 0 / 85 → 496 / 0 / 43 | 33 macro-generated functions, 29 recovered definitions; calls into `src/unix` + `src/win` pairs; 3.4 → 3.5 s |
| dart-lang/http | 47c57df | 11448 → 11529 | 30 / 1 / 1 → 30 / 0 / 0 | `export` IMPORTS edges; the `connect` tear-off |
| bluesky-social/social-app | db23528 | 47724 → 47887 | 108 / 9 / 102 → 108 / 5 / 88 | +163 re-export IMPORTS edges |
| clash-verge-rev/clash-verge-rev | 3607e66 | 15720 → 15747 | | +27 re-export IMPORTS edges |
| expo/expo (full monorepo) | c0cac77 | 245593 → 246979 | 738 / 188 / 886 → 738 / 177 / 879 | 88030 nodes; +1376 re-export IMPORTS edges; 105 → 110 s |

- **Rust:** every lost edge was a `cfg-inactive` name match the per-target index contradicts (ripgrep 6, alacritty
  22, tauri 343: e.g. a call matched to another type's `load` / `height`), and owner attribution improved (tauri's
  Windows event loop `new_any_thread` now owns its Windows-only calls). The extra runs roughly double a cold Rust
  index; rust-analyzer's cache makes later runs cheaper. `CODEGRAPH_RUST_TARGETS=0` turns them off.
- **libuv:** `UV_LOOP_WATCHER_DEFINE` / `SOCKOPT_SETTER` expansions are functions now; `kqueue.c`'s `uv__io_poll` had
  swallowed 6 following definitions and `win/tty.c` 14. Calls to a function defined once in `src/unix/` and once in
  `src/win/` (`uv_close`, `uv_tty_reset_mode`, ...) had no edge at all, since the file-qualified keys of the two
  definitions were not seen as one symbol; they now reach both (+9,600 CALLS, mostly from tests and docs examples).
  261 more per-platform definition groups (163 unix/win pairs; 2 are same-named functions of separate docs
  programs). 62 edges were lost: references inside a recovered definition that kept its head only, and calls the
  main parse had attributed to a swallowing function.
- **Re-exports:** the findings removed on social-app (4 API surface, 14 missing callee), dart-lang/http and expo (15
  API surface, 7 missing callee) were re-exports (`export {LockScroll} from 'react-remove-scroll'`,
  `export { SymbolView } from './SymbolView.ios'`, `const connect = IOWebSocketChannel.connect`). Expo gains 4
  API-surface findings, all web-only named exports the native variant does not have (`AudioPlayerWeb` re-exported
  by `AudioModule.web.ts`).
- **Swift (expo):** 194 declarations with `@available` versions (iOS 17: 53, iOS 18: 41, tvOS 17: 33, iOS 26: 30),
  13 deprecated, 316 `#available` branches with 254 references in them, 1 declaration unavailable everywhere
  (`BaseModule.init`). Spot checks of 8 matched the source.
- Expo's targets come from `apps/bare-expo/app.json` (`macos, ios, android, web`).

## Protocol links: shared endpoint model (#31)

`cg index` with no flags and no `.cg.yaml`, then `cg link` for the pairs and `cg protocols`
([protocols.md](protocols.md)). The existing links are read through adapters, so the graph and the outputs of
`cg link`, `cg channels` and `cg bridges` were compared before and after on every project below: nodes, edges and all
three outputs are identical. Index time is one run each on a shared box, within the run-to-run spread.

| Project | Commit | Protocols (endpoints / linked) | Checks | `cg protocols` | Index before → after |
|---|---|---|---|---|---|
| immich-app/immich `server/` | c5e06dc | http 297 / 0, nest-event-emitter 36 / 0 | 1 external, 35 no_receiver, 1 no_sender | 0.18 s | 11.7 s → 11.4 s |
| saleor/saleor | 8385ca6 | celery 75 / 69, django-signal 8 / 7, http 9 / 0 | 6 no_sender, 1 external, 9 unguarded | 0.35 s | 68.3 s → 70.2 s |
| netbox-community/netbox | 251458b | django-signal 44 / 10, http 956 / 0 | 34 external, 857 unguarded | 0.37 s | 27.4 s → 28.0 s |
| examples bookstore-nest + bookstore-next | – | http 26 / 6, bull 1 / 1, nest-event 2 / 0, nest-event-emitter 1 / 1, nest-rpc 1, nest-ws 1 | 5 no_receiver, 15 no_sender, 17 unguarded, 1 + 1 nest-event | 0.09 s | |
| a private Laravel API + Vue client pair (~29k nodes) | – | http 1,007 / 823, pusher 25 / 22, laravel-event 7 / 6, laravel-queue 7 / 7 | 4 external, 3 + 3 no_receiver, 83 test_sender_only, 91 no_sender, 40 unguarded | 0.3 s | identical graph, `cg link` 439 / 441 unchanged |

What the findings are, from spot checks:

- **unguarded** is the same classification as `cg routes --unguarded` (the private pair: 40 = 40; netbox: 857 of
  `cg routes`' 864, the rest are admin and unmounted routes, which `cg link` leaves out of its uncalled list too).
  A side is only judged when the graph holds the other side of that protocol, so a server indexed alone reports no
  `no_sender` for its HTTP routes.
- **immich:** the 35 events are emitted through immich's own `EventRepository` and received with its own
  `@OnEvent({ name })` decorator (built on `SetMetadata`). Since #101 the object's `name` is read: 44 named events,
  all 35 emitted ones meet their 86 listeners (before: one `event:?` endpoint; 20 sampled listener links correct). Its WebSocket gateway only emits (the web client listens through `socket.io-client`) and its jobs use the
  same `SetMetadata` system, so there are no gateway messages or BullMQ jobs to show yet (#32).
- **bookstore-nest:** `order.placed` is emitted by `ClientProxy.emit` and the microservice handler listens to
  `order.shipped`; both ends are reported.
- **Django signals:** the first run reported netbox's `post_save[Interface]`, `user_logged_in`, `request_finished`
  ... as `no_sender` (10) or `test_sender_only` (24): Django sends them itself, so a `django.*` signal nobody in the
  repo sends is `external` ("sent by the framework") now, as is saleor's `post_migrate`. The 10 linked netbox signals
  are its own (`core.signals.job_start`, ...) and model signals fired by a resolved model write.
- **saleor:** the 6 senderless Celery tasks are queued from data migrations (`delete_files_from_storage_task`,
  `update_discounted_prices_task`; migrations are not indexed), by the beat schedule in `settings.py`
  (`observability_reporter_task`) or through a `.delay` passed as a value (`handle_transaction_request_task.delay`).
- **Socket.IO (first new protocol):** `tests/protocol_fixtures` (a FastAPI service emitting through an
  `AsyncClient`, a Django worker with an `AsyncServer`) links `order:created` and the template `order:{status}`,
  reports the unreceived `order:cancelled`, the external `audit:order` and the unguarded `ping`, and
  `cg path "route:POST /orders" table:orders` crosses the two services through SENDS_TO / RECEIVED_BY.

## AI harnesses: LLM tools, MCP servers and agents (#66)

`cg index` with no flags, then `cg tools` ([ai-tools.md](ai-tools.md)). Graphs of projects without these SDKs are
unchanged (bookstore-django 207 / 341, netbox 38,028 / 140,181, saleor 43,390 / 210,377 nodes / edges, identical
before and after).

| Project | Commit | Server / declared tools (handled) | Client calls (matched) | Agents | Other |
|---|---|---|---|---|---|
| modelcontextprotocol/python-sdk | 2118f14 | 128 tools (124), 35 resources, 18 prompts | 264 tool / 111 resource / 28 prompt calls (58 / 32 / 6) | – | 11 s |
| openai/openai-agents-python | 81f0ccf | 90 llm tools (42; 48 declared inside tests), 6 MCP tools | 10 MCP tool calls (1) | 497 (6 handoffs) | 50 model calls, 39 s |
| code-graph itself | – | 29 MCP tools (29) | – | – | 29 decorator references replaced |
| modelcontextprotocol/servers | f46d957 | 0 | – | – | TypeScript servers; the Python ones branch on enum members (since #76: the 12 `mcp-server-git` tools) |

What the findings are, from spot checks:

- **python-sdk:** `examples/snippets/clients/stdio_client.py` `call_tool("add")` matches the `add` tool of three
  example servers (`Demo`, `Calculator`, `audited`): the client does not name its server, so every server tool of
  that name is a candidate. Most unmatched client calls went to servers built inside a factory function
  (`examples/stories/*/server.py`: `def build(): mcp = MCPServer(...)` with nested `@mcp.tool()` handlers) or inside
  tests. Since #76 these are endpoints received by the enclosing function: MCP endpoints 584 → 955, `no_receiver`
  tool calls 30 → 10.
- **openai-agents-python:** `appointment_referral_status_lookup` (`examples/sandbox/healthcare_support`) links the
  `@function_tool` handler to the module building the agent; `ask_order_agent` is offered in a realtime session
  config whose handler is `agent.as_tool()` (not modelled). The first run flagged 7 `getattr(context, f.name)`-style
  calls in the SDK itself as dynamic dispatch; only runtime tool names (`name`, `tool_call.function.name`, ...) count now.
- **langchain-ai/langgraph** (7dc9195) did not finish indexing within 400 s, before and after this change (deep type
  inference in the Python plugin); not measured.

## External systems (#40)

`cg index` with no flags, then `cg external` ([external.md](external.md)). The only graph change is additive:
`external` nodes and their CONNECTS_TO / CONFIGURED_BY / CREDENTIAL_FROM edges; projects without env reads,
connections or settings dicts are unchanged.

| Project | Commit | Systems | New nodes / edges | What was found |
|---|---|---|---|---|
| netbox-community/netbox | 251458b | 3 postgres, 5 redis | +8 / +12 (38,028 / 140,181 before) | `DATABASES[default]` and `REDIS[tasks]` / `REDIS[caching]` in `configuration_example.py` and `configuration_testing.py` (loopback hosts: `config:<module>.<setting>` targets with `address_default`, literal test password as location only); `scripts/smoketest_configuration.py` reads `POSTGRES_HOST` / `REDIS_HOST` with their password keys. Postgres comes from the dependencies (`DATABASE` has no `ENGINE`) |
| immich-app/immich `server/` | c5e06dc | 1 postgres | +1 / +2 (8,038 / 36,450 before) | `DB_URL` (protocol from the `pg` / `postgres` dependencies); the env schema is a zod DTO, so the main reader is not seen and the one user is `src/bin/sync-open-api.ts`, which assigns the variable; the compose files are under `../docker` |
| outline/outline | 478e812 | 1 postgres, 1 https | +1 / +4 (13,358 / 52,050 before) | `DATABASE_URL` read by `server/config/database.js`, resolved through `.env.sample` to the compose service `postgres:5432` (resource outline, credential in the URL); `updates.getoutline.com`; Redis / S3 / SMTP keys go through an `environment.X` wrapper and are missed; three example.com origins called only from tests are left out |
| bookstore-django (examples) | – | 1 redis | +1 / +2 (207 / 341 before) | `CELERY_BROKER_URL` from the environment with a loopback default |
| a private Laravel API + Vue client pair | – | 1 postgres, 1 redis, 1 memcached (API) | +3 / +12 (API); client graph identical | a custom connection with its host and password keys, the Redis keys read by `config/database.php` / `config/reverb.php`, the memcached store of `config/cache.php`; the default connection is sqlite (no network system). Link results identical |

Fixture (`tests/external_fixture`): a Laravel API, an Express worker and a Django project sharing `db` / `redis`
services: after `cg link` the API's `connection:pgsql` and the worker's `pool()` use the same
`external:postgres:db:5432`, and `reaches` lists `OrderController::index` and `POST /sync`. The fixture secrets
appear nowhere in the database files.

Seen on the way: column nodes from Laravel migrations differ between two runs of the same commit (a few columns
attributed to another table), with or without this change.

## Swift: selectors, static vs instance, SDK receivers (#70)

Same stores and sources as for #58 (heuristic edges in the files the store covers, against the compiler's):

| Project | Heuristic edges | Agree | Precision | Recall |
|---|---|---|---|---|
| Alamofire/Alamofire | 585 → 602 | 552 → 580 | 0.94 → **0.96** | 0.65 → **0.68** |
| pointfreeco/isowords (server product) | 174 → 180 | 163 → 167 | 0.94 → 0.93 | 0.44 → **0.45** |
| vapor/template | 11 → 9 | 9 → 9 | 0.82 → **1.00** | 0.90 → 0.90 |
| **Total** | 770 → 791 | 724 → 756 | 0.94 → **0.96** | 0.59 → **0.61** |

Recall goes up because labelled selectors on receivers cg cannot type (`client.put(endpoint:)`, `toastCenter.update(id:toast:)`)
are bound again when exactly one project method fits, also for short names. Precision on isowords moves by two edges:
calls of `Dictionary.transformKeys`, which isowords declares twice (in two modules); cg merges both into one node and
the compiler's target is the copy without a node, so these real calls count as disagreements. The Alamofire false
positives left (about 50 in the heuristic-only graph, 55 before) are mostly owner differences (`HTTPHeaders.init`
calling `HTTPHeader.init`) and Apple-only branches.

Dimillian/IceCubesApp (`9efcb16`, heuristic only, no Linux build), before → after:

- `MediaUIAttachmentVideoViewModel.resume`: 20 callers → 1 (`viewModel.resume()`); `continuation.resume(returning:)`
  inside `withCheckedContinuation` is no longer bound.
- `MastodonClient.post`: the 10 `NotificationCenter.default.post(name:object:)` calls are gone (64 → 54 edges, all
  `client.post(endpoint:)`).
- `InAppSafariManager.open`: 9 → 1 (`safariManager.open(url)`); `cg platforms divergence` missing-callee findings
  12 → 4 (the 8 `UIApplication.shared.open` findings are gone, the 4 left are the project's own `close` defined per
  `#if`).
- Removed 46 call edges, all false (also `data.write(to:)` → `DeepLUserAPIHandler.write`,
  `UserDefaults.standard.register(defaults:)` → `SoundEffectManager.register`); added 122 (labelled selectors on
  untyped receivers, `MediaContainer.pending(...)` static factories, `extension View` modifiers on SDK view chains,
  members of nested types). `calls_sdk_selector`: 2,132 calls left unbound by the SDK-selector rule.
- `cg starters` still suggests `MastodonClient.get` / `.post`, now with real callers only (76 / 42 direct callers).

## Swift Testing, XCTest and Kotlin test cases (#71)

Test cases in `cg tests`, before → after (before, Swift and Kotlin test functions were `test` entries but the header
counted only `test` nodes, so every project read "of 0 test cases"; all graphs heuristic, sources at the commits
named):

| Project | Before | After (per framework) |
|---|---|---|
| swiftlang/swift-testing (`b5c410c`) | 0 | 1,010 (swift-testing 885, xctest 125) |
| Alamofire/Alamofire (`bda9ed5`) | 0 | 834 (xctest 761, swift-testing 73) |
| Dimillian/IceCubesApp (`9efcb16`) | 0 | 65 (xctest 40, swift-testing 25) |
| pointfreeco/isowords (`c727d3a`) | 0 | 237 (xctest 237) |
| android/nowinandroid (`a49ed25`) | 0 | 232 (junit4 232) |
| touchlab/KaMPKit (`4af0200`) | 0 | 28 (kotlin-test 24, xctest 4) |
| spring-petclinic-kotlin (`c77f77b`) | 0 | 41 (junit5 41) |
| ktorio/ktor-samples (`1c9df7c`) | 0 | 162 (kotlin-test 149, junit4 9, xctest 4) |

- Swift Testing cases are new (863 on swift-testing, 73 on Alamofire, 25 on IceCubesApp: every uncommented `@Test`
  in Alamofire and IceCubesApp). On swift-testing's own suite, 137 are parameterized, 143 types are `@Suite`s and
  29 nodes carry tags. Its raw-identifier suites (`` struct `ABI.EncodedEvent Tests` ``,
  `` @Test func `Decode issueRecorded`() ``) parse now: 2,831 → 2,953 nodes and 6,952 → 7,159 edges. 59 functions
  that a parse error had turned into free functions are methods of their suite again. The `@Test` lines still
  missing are an `@Test` inside `#if` in front of its function and a few declarations the grammar cannot read.
- XCTest: 3 `test*` methods are no longer test cases, all helpers with parameters (`IssueTests.testThrowing(_:producesIssueMatching:)`,
  `BuiltInConformances.test(_:)` on swift-testing, `TimelineFilterTests.testCodableOn(filter:)` on IceCubesApp). No
  other test case was lost, on any project.
- Alamofire `cg tests Instant`: 0 direct, 7 transitive (of 0 test cases) → 3 direct (the three `InstantTests` cases),
  8 transitive (a Swift Testing `RequestInterceptorTests` case joins the XCTest ones), of 834.
- Kotlin numbers are unchanged; they now have a framework from the file's imports. Kotlin `@Test` lines without a
  test case: benchmarks in `src/main` (nowinandroid, not test code by path), `@Test` inside lint-test source strings,
  and on ktor-samples 30 expression-bodied tests (`fun anything() = testApplication { ... }`) in three httpbin files
  where the Kotlin grammar drops the class body's functions.

## Python inference on self-referencing attributes (#78)

langchain-ai/langgraph (`7dc9195`, 461 `.py` files) did not finish indexing in 400 s. `PregelLoop` re-assigns
`self.checkpoint_pending_writes` from list comprehensions over itself, several per method with one shared loop name
`w`; inferring the attribute re-entered it once per binding of `w` at every level, with a fresh per-function guard
each time, until the depth limit of 40, so the work grew exponentially. Attribute types (`self.x` assignments and
class attributes) are now memoised per class and attribute, an attribute already being inferred further up the stack
is unknown, and one top-level inference stops after 5,000 steps. A result computed while a cycle was cut is not
memoised, so a partial answer is not reused elsewhere. The plugin stats report the cuts as
`inference_limits` (`attr_cycles`, `budget_exhausted`).

- langgraph: indexes in 14 s, 9,774 nodes / 38,141 edges, 34 cycle cuts, budget never reached.
- The same graphs before and after (every node and every edge with kind and confidence identical), and times within
  noise or faster, on django (259 cuts), flask, saleor, netbox (186; 39 → 29 s), pytest (372), sphinx (20), ansible (9;
  21 → 16 s), beets, flake8, httpie, microblog, mkdocs, pylint (17), opentelemetry-python (1,075) and
  full-stack-fastapi-template. The work budget was not reached on any of them.
- `tests/test_python_infer_cycles.py` has the langgraph shape in a dozen lines: it indexed for more than 60 s before
  (stopped by the test's alarm) and takes 0.2 s now.

## Laravel graphs independent of the hash seed (#79)

A property read on a receiver that can be several Eloquent models, in a `??` fallback chain, took the table of the
first model a set iteration produced, so two indexes of one commit could differ (in the issue's repro, seed 3 picked
`incomes` where seeds 1, 2, 4, 5, 6 picked `expenses`). Each such step now has one FALLS_BACK_TO edge per candidate
model's column, all with the step's `order`, `ambiguous: true` and `candidates`; the candidates are those whose
migrations declare the column, else those whose table the migrations create. A single-model read is unchanged.

Every graph below indexed under `PYTHONHASHSEED` 1 and 2 is identical (nodes, edges, attributes), and against the
previous version only the multi-model steps changed:

| Project | Edges before (seed 1) → after | What changed |
|---|---|---|
| koel, solidtime, firefly-iii, pixelfed, vito, panel, UNIT3D, akaunting, librenms, laravel.io | unchanged | no multi-model fallback step |
| monica | 34,205 → 34,211 | 3 resolutions (`UpdateVCard`, `UpdateVCalendar`, `GetEtag`): `distant_etag` on `vcard_resources` and `vcalendar_resources`, before `vcard_resources` only |
| coolify | 83,087 → 83,088 | `Show::copyValue`: `value` on `environment_variables` and `shared_environment_variables`, before the latter only |
| invoiceninja | 172,259 → 172,269 | `line_items` and `design_id` on credits, invoices, purchase_orders, quotes and recurring_invoices (before purchase_orders only); `$entity->client` on clients and vendors (no migration declares either column; before vendors only) |

`tests/test_laravel_determinism.py` indexes the issue's fixture under two seeds that differed before and compares the
whole graph.

## Raw TCP / UDP sockets and UDP application protocols (#39)

Socket and UDP-application edges on the corpora that use them (indexed at the commits shown):

| Project | Endpoints (paired) | Edges | What pairs |
|---|---|---|---|
| mini-redis 3d93b42 | tcp 1 (1) | 1 RECEIVED_BY, 4 SENDS_TO | the `pub` / `sub` / `hello_world` examples and the CLI (clap `default_value_t = DEFAULT_PORT`) reach `server::run` through `Client::connect`'s call sites on 6379 |
| tokio examples 5d5cd8b | tcp 3 (2), udp 1 (1) | 8 RECEIVED_BY, 3 SENDS_TO | `hello_world` with `chat` / `graceful-shutdown` on 6142, `proxy` with the 8080 servers, `udp-client` with `echo-udp` |
| statsd f7157b8 | tcp 2, udp 1 (1) | 3 RECEIVED_BY, 1 SENDS_TO | the Python example client with the Node UDP server on 8125; the TCP server and admin listeners (8125, 8126) have no client in the repo; the PHP example reads its port from an ini file and the Java / Kotlin clients take it as a constructor parameter with no caller, so they are counted as unresolved |
| libuv | tcp 5 (1), udp 6 (2) | 99 RECEIVED_BY, 99 SENDS_TO | the tests on `TEST_PORT` 9123 (and `TEST_PORT_2` 9124); 10 ephemeral binds are counted, not linked |
| python-zeroconf f0c27b3 | mdns 13 (1) | 1 RECEIVED_BY, 5 SENDS_TO, 84 TEST_CALLS | `examples/async_registration.py` advertises `_http._tcp`, `async_browser.py` / `async_service_info_request.py` browse it |
| aiocoap f867dc4 | coap 29 (3) | 12 RECEIVED_BY, 2 SENDS_TO, 35 TEST_CALLS | `server.py`'s `/time` and `/other/block` with `clientGET.py` / `clientPUT.py`; 6 doctest examples in docstrings are skipped |

tauri gains one edge, the CLI's built-in dev server listening on its default port 1430 (`port.unwrap_or(1430)`).
No edge changed on redis, httpie, flask, opentelemetry-python, the MCP python-sdk and servers, electron-fiddle,
alacritty, ktor-samples, django, netbox, immich, outline, saleor, element-x-ios, eslint and ansible (whose `SSH_AUTH_SOCK` Unix-socket paths
are not TCP ports). A random 20 of the new edges were checked by hand against the source: 20 correct (an earlier
sample of 20 found a wrong `/` path for a concatenated CoAP URI, fixed before this one). `tests/test_sockets.py` covers
each language on `tests/sockets_fixture` and the BSD / libuv C calls on `tests/sockets_c_fixture`.
