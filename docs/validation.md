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

## Kotlin

Heuristic mode (tree-sitter-kotlin 1.1.0), shallow clones, `cg index` on the default branch:

| Repository | Kotlin files | Declarations | Calls resolved / unresolved | Framework facts | Index time |
|---|---|---|---|---|---|
| android/nowinandroid | 350 | 1,219 | 1,608 / 5,696 | 4 Retrofit endpoints, 3 activities, 1 service, 2 workers, 2 deep links | 0.8 s |
| ktorio/ktor-samples | 204 | 570 | 652 / 5,635 | 77 Ktor routes, 115 Ktor client endpoints, 10 KMP source-set files | 0.4 s |
| spring-petclinic/spring-petclinic-kotlin | 40 | 139 | 112 / 548 | 19 Spring routes | 0.1 s |
| touchlab/KaMPKit | 38 | 114 | 167 / 562 | 2 `expect` → `actual` links, 5 KMP source-set files, 1 activity | 0.1 s |

Unresolved calls are mostly library and standard-library calls (Compose, coroutines, collections), which have no
node in the graph. An exact-mode comparison (scip-java) is on the roadmap in [kotlin.md](kotlin.md).

## Swift

Heuristic mode (tree-sitter-swift 0.7.3) on Linux without Xcode, shallow clones, `cg index` on the default branch:

| Repository | Swift files | Declarations | Calls resolved / unresolved | Framework facts | Index time |
|---|---|---|---|---|---|
| Alamofire/Alamofire | 101 | 2,019 | 3,825 / 8,162 | 198 extensions, 33 `#if` platform blocks, 5 `AF.request` endpoints (the example apps) | 1.1 s |
| Dimillian/IceCubesApp | 428 | 2,156 | 3,537 / 8,174 | 21 SwiftUI pages, 21 navigations, 14 `@main` entries (app, extensions, widgets), 259 `#if` platform blocks | 1.2 s |
| pointfreeco/isowords | 388 | 1,729 | 2,606 / 7,794 | 17 SwiftUI pages, 12 navigations, 12 `@main` entries, 1 URLSession endpoint | 1.1 s |
| vapor/template | 9 | 16 | 20 / 81 | 3 Vapor routes with their `RouteCollection` handlers | < 0.1 s |

Unresolved calls are mostly SDK calls (SwiftUI modifiers, Foundation, Combine), which have no node in the graph.
IceCubesApp and isowords call their APIs through typed endpoint enums and a swift-parsing router, which are not HTTP
client forms the plugin reads yet. The Vapor template's `routes.swift` contains Mustache markup, so its two
closure routes do not parse. The exact-mode comparison (compiler index store) is on the roadmap in [swift.md](swift.md).
