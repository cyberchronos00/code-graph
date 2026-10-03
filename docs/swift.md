# Swift (iOS / macOS apps, SwiftUI, Vapor)

cg indexes Swift (`.swift`) with a tree-sitter syntax layer (`pip install tree-sitter tree-sitter-swift`). No Xcode,
Swift toolchain or build is needed, so any checkout indexes as-is, on Linux CI too. `cg coverage` reports Swift as
**heuristic** then: references are resolved by name, as for Kotlin, Rust and C / C++ without their compiler indexers.
With a Swift toolchain, calls come from the compiler's index store instead ([exact mode](#exact-mode)).

## What is in the graph

| Area | Facts |
|---|---|
| Declarations | classes, structs, enums, actors, protocols, functions, methods, initializers (`class:` / `function:` / `method:` ids by type-qualified name); extensions merge into the type they extend; a SwiftUI `body` property is a `method:<View>.body` node |
| Calls | `CALLS` resolved through the enclosing type, its extensions and supertypes, the receiver's parameter / property type (`api.book(id)` with `let api: BooksAPI`) and a project-wide unique name; initializer calls as `INSTANTIATES`; protocol / superclass members → conformances and overrides (`IMPLEMENTED_BY` / `OVERRIDDEN_BY`) |
| Entry points | `@main` types and `App` conformances (`main`); `UIApplicationDelegate` / scene delegate / `UIViewController` / `AppIntent` / `Widget` lifecycle callbacks; `BGTaskScheduler.shared.register` handlers (`queue_job`); XCTest `test*` methods (`test`) |
| SwiftUI / UIKit | views reached by `NavigationLink(destination:)`, `.navigationDestination { }`, `.sheet` / `.fullScreenCover` / `.popover { }`, `TabView` and the `WindowGroup` root become `page:swift:<View>` (ROUTES_TO its `body`) with `NAVIGATES_TO` edges; UIKit `pushViewController(V(), ...)` / `present(V(), ...)` likewise |
| HTTP clients | URLSession (`data(from:)`, `data(for:)`, `dataTask`, `upload(for:)`) with the URL built in the same function (`URL(string: "...")`, `baseURL.appendingPathComponent("...")`, `"\(baseURL)/..."`) and `request.httpMethod = "POST"`; Alamofire `AF.request(url, method: .post)`. `\(base)` keeps `{base}` as the origin, so `cg link` still matches the path |
| Moya | `enum API: TargetType` (or an `extension API: TargetType`): `baseURL` (`URL(string: "...")`, otherwise `{baseURL}`), `path` and `method` read per `case` from `switch self` (with or without `return`, `default:` included) or as a single value → one `http:<METHOD> <path>` node per case with `HTTP_CALLS` from the enum (`how: "moya target"`); `provider.request(.case)` / `requestPublisher` / `provider.rx.request` → `HTTP_CALLS` from the calling function, the target type taken from `MoyaProvider<API>` on the provider variable or a case name unique among targets. Edges carry `target: "API.case"`; a relative base links to the backend's routes |
| Fluent | `Model` classes with `static let schema = "todos"` (or `static var schema: String { "todos" }`) → `table:todos` (`MAPS_TO_TABLE`); a migration's `database.schema("todos")...create()` / `.update()` / `.delete()` → `WRITES_TABLE` from `prepare` / `revert` (`via: "migration create"`); `Todo.query(on:)` and `Todo.find(...)` → `READS_TABLE` (`WRITES_TABLE` when the chain ends in `delete` / `update` / `set`); `todo.save(on:)` / `create` / `update` / `delete(on:)` with `todo` typed by a parameter, property or `let todo = Todo(...)` / `Todo.find(...)` in the function → `WRITES_TABLE` (`resolved`) |
| Vapor | `app.get("orders", ":id") { }`, `routes.post("x", use: handler)`, `grouped("v1")` / `group("v1") { v1 in }` prefixes, middleware passed to `grouped(...)` (`User.authenticator()`, `User.guardMiddleware()`) as the route's guards, `RouteCollection.boot(routes:)` controllers → `route:GET /v1/orders/{id}`; closure handlers are nodes of their own |
| Platforms | `#if os(iOS)` / `#elseif os(macOS)` / `#else` blocks, `canImport(UIKit)` (ios) / `canImport(AppKit)` (macos), `targetEnvironment(macCatalyst)` (macos; `simulator` is unknown) and `@available(macOS, unavailable)` / `@available(iOS, unavailable)` / `@available(*, unavailable)` on a declaration feed the platform tags (`cg platforms`, `--platform`); version forms keep the code everywhere and record the minimum OS versions: `@available(iOS 17, *)` / `@available(iOS, introduced: 15)` as node `attrs.available` (`{"iOS": "17"}`, members inherit their type's, shown as `[iOS 17+]`), `deprecated` as `attrs.deprecated`, and the references inside `if #available(...)`, after `guard #available(...)` and in the `else` of `if #unavailable(...)` as edge `attrs.available`; a function defined once per branch keeps one node per platform, and a call to it also reaches the other branches' definitions (`platform_variant_of`); the project's targets come from `Package.swift` `platforms:` |
| Tests | files under `Tests/`, `*Tests/` and `*Tests.swift` are test code |

SwiftPM build output (`.build`, `.swiftpm`), Xcode `DerivedData`, CocoaPods `Pods` and `Carthage` are skipped
(`codegraph/presets/swift.yaml`).

## Exact mode

The Swift compiler records every definition and reference in an index store. cg reads it through `libIndexStore`
(shipped with every Swift toolchain, Linux included) and replaces the heuristic `CALLS` / `INSTANTIATES` edges of each
file the store covers; declarations and framework facts stay those of the syntax layer. `cg coverage` then reports
Swift as **exact** (`Swift index store (swift build)`), or names the reason it stayed heuristic.

| Setting | Effect |
|---|---|
| `CODEGRAPH_SWIFT_INDEX=1` | for a SwiftPM package (`Package.swift`), cg runs `swift build --enable-index-store` into its cache (`~/.cache/codegraph/swift-build/`, or `$CODEGRAPH_CACHE`), never into the checkout. The store is reused while the sources, manifests and toolchain are unchanged; otherwise `swift build` runs again (incrementally, in the same build directory). `CODEGRAPH_NO_CACHE=1` forces the run, `CODEGRAPH_INDEXER_TIMEOUT` caps it |
| `CODEGRAPH_SWIFT_INDEX_STORE=/path/to/store` | an existing store: Xcode's `DerivedData/<project>/Index.noindex/DataStore` (`Index/DataStore` before Xcode 14), a CI build's `.build/<triple>/debug/index/store`. Paths from another checkout are mapped by their longest common suffix |
| `CODEGRAPH_SWIFT=/path/to/swift`, `CODEGRAPH_LIBINDEXSTORE=/path/to/libIndexStore.so` | toolchain and library, when they are not on `PATH` / next to `swift` (`~/tools/swift-*`, `/usr/share/swift`, `/opt/swift`, and on macOS the selected Xcode's default toolchain are searched) |

The build is opt-in because `swift build` runs the package manifest and its plugins. Without a toolchain, without the
opt-in, or for an Xcode project without `Package.swift`, the index completes in heuristic mode and `cg coverage` says
why. A failed build keeps whatever the compiler indexed before the failure (`partial`), with the first `error:` line in
the coverage reason; files the store does not cover keep heuristic edges, and `cg coverage` counts them.

What a Linux build covers: only the targets that compile there. SwiftUI / UIKit modules, iOS-only apps and test
targets that need Apple frameworks are not in a Linux store (use the store from an Xcode build for those). Code in an
inactive `#if` branch is not compiled, so its heuristic edges are kept with `via: "not-compiled"`.

`index` stats: `index_files`, `index_defs_matched` / `index_defs_unmatched`, `index_references`, `index_refs_external`
(SDK and dependency symbols), `heuristic_kept_not_compiled` and `exact_vs_heuristic` (precision and recall of the
heuristic call edges against the store, for the covered files).

## Example

`examples/bookstore-ios` is a SwiftUI + URLSession client of `examples/bookstore-django`:

```bash
cg index examples/bookstore-django --db /tmp/dj.db
cg index examples/bookstore-ios    --db /tmp/ios.db
cg link --backend /tmp/dj.db --frontend /tmp/ios.db --db /tmp/ios-link.db
cg path "page:swift:CheckoutView" "table:catalog_order" --db /tmp/ios-link.db
```

The path runs from the checkout sheet's `body` through the store to the URLSession method, its
`http:POST /api/orders/` endpoint, the Django route and handler, and the table it writes.

## Not covered yet

- Building Xcode projects (`.xcodeproj` / workspaces) or iOS-only targets on Linux: point
  `CODEGRAPH_SWIFT_INDEX_STORE` at an Xcode store instead.
- `URLComponents` paths and query items, typed endpoint enums other than Moya, URLs built in helpers, base URLs from
  `Info.plist` / `.xcconfig`.
- OS versions as filters (they are recorded, see above); App Intents and widgets as their own entry
  kinds; `navigationDestination(for:)` matched to `NavigationLink(value:)`; macro and package-plugin output as
  generated code.
- Moya: `MultiTarget`, paths built in helpers; Fluent: relations (`$todo.$tags`), raw SQL, query chains split across
  variables.
