# Swift (iOS / macOS apps, SwiftUI, Vapor)

cg indexes Swift (`.swift`) with a tree-sitter syntax layer (`pip install tree-sitter tree-sitter-swift`). No Xcode,
Swift toolchain or build is needed, so any checkout indexes as-is, on Linux CI too. `cg coverage` reports Swift as
**heuristic**: references are resolved by name, as for Kotlin, Rust and C / C++ without their compiler indexers.

## What is in the graph

| Area | Facts |
|---|---|
| Declarations | classes, structs, enums, actors, protocols, functions, methods, initializers (`class:` / `function:` / `method:` ids by type-qualified name); extensions merge into the type they extend; a SwiftUI `body` property is a `method:<View>.body` node |
| Calls | `CALLS` resolved through the enclosing type, its extensions and supertypes, the receiver's parameter / property type (`api.book(id)` with `let api: BooksAPI`) and a project-wide unique name; initializer calls as `INSTANTIATES`; protocol / superclass members → conformances and overrides (`IMPLEMENTED_BY` / `OVERRIDDEN_BY`) |
| Entry points | `@main` types and `App` conformances (`main`); `UIApplicationDelegate` / scene delegate / `UIViewController` / `AppIntent` / `Widget` lifecycle callbacks; `BGTaskScheduler.shared.register` handlers (`queue_job`); XCTest `test*` methods (`test`) |
| SwiftUI / UIKit | views reached by `NavigationLink(destination:)`, `.navigationDestination { }`, `.sheet` / `.fullScreenCover` / `.popover { }`, `TabView` and the `WindowGroup` root become `page:swift:<View>` (ROUTES_TO its `body`) with `NAVIGATES_TO` edges; UIKit `pushViewController(V(), ...)` / `present(V(), ...)` likewise |
| HTTP clients | URLSession (`data(from:)`, `data(for:)`, `dataTask`, `upload(for:)`) with the URL built in the same function (`URL(string: "...")`, `baseURL.appendingPathComponent("...")`, `"\(baseURL)/..."`) and `request.httpMethod = "POST"`; Alamofire `AF.request(url, method: .post)`. `\(base)` keeps `{base}` as the origin, so `cg link` still matches the path |
| Vapor | `app.get("orders", ":id") { }`, `routes.post("x", use: handler)`, `grouped("v1")` / `group("v1") { v1 in }` prefixes, middleware passed to `grouped(...)` (`User.authenticator()`, `User.guardMiddleware()`) as the route's guards, `RouteCollection.boot(routes:)` controllers → `route:GET /v1/orders/{id}`; closure handlers are nodes of their own |
| Platforms | `#if os(iOS)` / `#elseif os(macOS)` / `#else` blocks, `canImport(UIKit)` (ios) / `canImport(AppKit)` (macos), `targetEnvironment(macCatalyst)` (macos; `simulator` is unknown) feed the platform tags (`cg platforms`, `--platform`); a function defined once per branch keeps one node per platform; the project's targets come from `Package.swift` `platforms:` |
| Tests | files under `Tests/`, `*Tests/` and `*Tests.swift` are test code |

SwiftPM build output (`.build`, `.swiftpm`), Xcode `DerivedData`, CocoaPods `Pods` and `Carthage` are skipped
(`codegraph/presets/swift.yaml`).

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

## Roadmap

Tracked in [#23](https://github.com/cyberchronos00/code-graph/issues/23):


- Exact mode from the compiler's index store (`swift build -index-store-path`, Xcode DerivedData) through the SCIP
  import path, with an exact-vs-heuristic measurement.
- Moya `TargetType`, `URLComponents` paths and query items, base URLs from `Info.plist` / `.xcconfig`.
- Fluent models, migrations and queries as tables; `@available` / `#available` version checks as platform
  conditions; App Intents / widgets as their own entry kinds.
