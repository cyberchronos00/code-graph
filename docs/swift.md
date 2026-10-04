# Swift (iOS / macOS apps, SwiftUI, Vapor)

cg indexes Swift (`.swift`) with a tree-sitter syntax layer (`pip install tree-sitter tree-sitter-swift`). No Xcode,
Swift toolchain or build is needed, so any checkout indexes as-is, on Linux CI too. `cg coverage` reports Swift as
**heuristic** then: references are resolved by name, as for Kotlin, Rust and C / C++ without their compiler indexers.
With a Swift toolchain, calls come from the compiler's index store instead ([exact mode](#exact-mode)).

Every heuristic edge is labelled `heuristic`, so `--min-confidence resolved` or `exact` (`impact`, `reaches`, `tests`,
`routes`, ...) keeps none of them; the answer then says that callers were filtered by the threshold and how many.
Those thresholds need the index store.

## What is in the graph

| Area | Facts |
|---|---|
| Declarations | classes, structs, enums, actors, protocols, functions, methods, initializers (`class:` / `function:` / `method:` ids by type-qualified name); extensions merge into the type they extend; a SwiftUI `body` property is a `method:<View>.body` node; a computed property, a stored property with `willSet` / `didSet` and a `lazy var` with an initializer are `method:<Type>.<name>` nodes (`swift_kind` `computed property` / `property observers` / `lazy property`, attrs `property` and, for separate `get` / `set` / `willSet` / `didSet` clauses, `accessors`); a plain stored instance property is a `field:<Type>.<name>` node (next row) |
| Stored properties (subscripts, inferred key paths) | `items[i] = v` / `self.map[k] = v` is a WRITES_PROP with `via: item`, and `items[i]` is a read. A key path with an inferred root (`\.items` in `map(\.items)` or `ForEach(id:)`) binds the one project type with a stored `items`, as `heuristic` with `binding: name`. It is skipped for `@Environment(\.x)` / `.environment(\.x, …)` / focused values and for standard-library names (`offset`, `count`, `id`, …). |
| Stored properties | `field:<Type>.<name>` nodes (attrs `property: stored`, `binding: let \| var`, `wrapper` for `@State` / `@Published` / `@ObservedObject` / a project property wrapper; static ones stay constants). `self.x`, a bare `x` inside the type and `v.x` where the type of `v` is known (a parameter, a stored property, a local `let v = T(...)`) are `READS_PROP` / `WRITES_PROP` edges (`resolved`, attrs `receiver`, `accessor` inside a property's `get` / `set` / `didSet`, `storage: wrapper` for `_x = State(initialValue: ...)`); an unknown receiver binds nothing. In-place mutations are writes with `via`: `mutating` for a mutating standard-library method (`items.append(x)`, `flag.toggle()`, `set.insert(x)` …) or a project `mutating func` called on the field, `inout` for `&x`, `binding` for `$x` (a Binding / projection handed out, which can write it). A key path `\Type.x` is a read with `via: keypath`, since whether it writes is unknown. `@AppStorage("k")` / `@SceneStorage("k")` fields carry `key`. Test code's accesses are `TEST_USES` (`orig` READS_PROP / WRITES_PROP). `cg readers Type.prop` / `cg writers Type.prop` (and the MCP `readers` / `writers` tools) list the sites with receiver and entry kinds, test code last ([#88](https://github.com/cyberchronos00/code-graph/issues/88)). IceCubesApp: 2,392 fields, 5,383 reads (14 key paths), 1,750 writes (136 mutating, 239 binding, 2 inout), 54 `@AppStorage` keys. Call edges are unchanged, except that an in-place mutation of an observed property now also calls its `didSet` (+6) |
| Calls | `CALLS` resolved through the enclosing type, its extensions and supertypes, the receiver's parameter / property type (`api.book(id)` with `let api: BooksAPI`) and finally the selector alone (a unique method, or `candidate` edges to up to five; see below); initializer calls as `INSTANTIATES` (inside a `switch` case, `if` / `else`, `guard` or `?:` with attrs `branch`, such as `case .settings`, `if editing`, `else of if editing`, and `branch_line`; kept in exact mode); the calls inside a property node's body come from it (`accessor: get | set | willSet | didSet` on the edge, shown by impact as `(didSet)`), and a read of a computed or lazy property or a write of a computed or observed one (`cart.label`, an implicit `self.summary`, `cart.items = [1]`, `total += 1`) is a `CALLS` edge to it with `property: read | write`, under the receiver rules of method calls (an unknown receiver binds only a name no stored property in the project shares); protocol / superclass members → conformances and overrides (`IMPLEMENTED_BY` / `OVERRIDDEN_BY`) |
| Entry points | `@main` types and `App` conformances (`main`); `UIApplicationDelegate` / scene delegate / `UIViewController` / `AppIntent` / `Widget` lifecycle callbacks; `BGTaskScheduler.shared.register` handlers (`queue_job`); tests (`test`): XCTest `test*` methods of `XCTestCase` subclasses (`xctest`) and Swift Testing `@Test` functions, parameterized ones included (`swift-testing`); `@Suite` types carry `attrs.suite` |
| SwiftUI / UIKit | views reached by `NavigationLink(destination:)`, `.navigationDestination { }`, `.sheet` / `.fullScreenCover` / `.popover { }`, `TabView` and the `WindowGroup` root become `page:swift:<View>` (ROUTES_TO its `body`) with `NAVIGATES_TO` edges; value navigation: the views per case of `navigationDestination(for: Route.self) { switch r { case .detail: DetailView() } }` are the targets of `NavigationLink(value: Route.detail(...))` and of `navigate(to:)` / `push` / `append` on a router or path (`routerPath.navigate(to: .detail(id))`; an untyped `.case` only when one destination type has that case, a `default:` branch for the enum's other cases); UIKit `pushViewController(V(), ...)` / `present(V(), ...)` likewise |
| HTTP clients | URLSession (`data(from:)`, `data(for:)`, `dataTask`, `upload(for:)`) with the URL built in the same function (`URL(string: "...")`, `baseURL.appendingPathComponent("...")`, `"\(baseURL)/..."`, `URLComponents(string: "...")` + `.path = "..."` or `.scheme` / `.host` / `.path`) and `request.httpMethod = "POST"`; Alamofire `AF.request(url, method: .post)`. `\(base)` keeps `{base}` as the origin, so `cg link` still matches the path, unless the base is known (next row) |
| Base URLs | `{baseURL}` is resolved from a constant with a base-like name (`static let apiBaseURL = URL(string: "https://api.example.com/v2")!`), or from an `Info.plist` key read in Swift (`Bundle.main.object(forInfoDictionaryKey: "SERVER_URL")`, `infoDictionary?["SERVER_URL"]`) whose `$(VAR)` comes from the `.xcconfig` files (`#include`s followed, `/$()/` read as `//`). One value: the endpoint gets that origin and path prefix (`origin_kind` api, `attrs.base` with value and source). One value per configuration (Debug / Release): `origin_kind` env with `attrs.base_candidates`, linked by path like an API call. An absolute URL on a configured base's origin also counts as an API call |
| Moya | `enum API: TargetType` (or an `extension API: TargetType`): `baseURL` (`URL(string: "...")`, otherwise `{baseURL}`), `path` and `method` read per `case` from `switch self` (with or without `return`, `default:` included) or as a single value → one `http:<METHOD> <path>` node per case with `HTTP_CALLS` from the enum (`how: "moya target"`); `provider.request(.case)` / `requestPublisher` / `provider.rx.request` → `HTTP_CALLS` from the calling function, the target type taken from `MoyaProvider<API>` on the provider variable or a case name unique among targets. Edges carry `target: "API.case"`; a relative base links to the backend's routes |
| Fluent | `Model` classes with `static let schema = "todos"` (or `static var schema: String { "todos" }`) → `table:todos` (`MAPS_TO_TABLE`); a migration's `database.schema("todos")...create()` / `.update()` / `.delete()` → `WRITES_TABLE` from `prepare` / `revert` (`via: "migration create"`); `Todo.query(on:)` and `Todo.find(...)` → `READS_TABLE` (`WRITES_TABLE` when the chain ends in `delete` / `update` / `set`); `todo.save(on:)` / `create` / `update` / `delete(on:)` with `todo` typed by a parameter, property or `let todo = Todo(...)` / `Todo.find(...)` in the function → `WRITES_TABLE` (`resolved`) |
| Vapor | `app.get("orders", ":id") { }`, `routes.post("x", use: handler)`, `grouped("v1")` / `group("v1") { v1 in }` prefixes, middleware passed to `grouped(...)` (`User.authenticator()`, `User.guardMiddleware()`) as the route's guards, `RouteCollection.boot(routes:)` controllers → `route:GET /v1/orders/{id}`; closure handlers are nodes of their own |
| Platforms | `#if os(iOS)` / `#elseif os(macOS)` / `#else` blocks, `canImport(UIKit)` (ios, tvos, visionos; macos in a Catalyst app) / `canImport(AppKit)` (macos), `targetEnvironment(macCatalyst)` (macos when the app builds for Catalyst; `simulator` is unknown) and `@available(macOS, unavailable)` / `@available(iOS, unavailable)` / `@available(*, unavailable)` on a declaration feed the platform tags (`cg platforms`, `--platform`); version forms keep the code everywhere and record the minimum OS versions: `@available(iOS 17, *)` / `@available(iOS, introduced: 15)` as node `attrs.available` (`{"iOS": "17"}`, members inherit their type's, shown as `[iOS 17+]`), `deprecated` as `attrs.deprecated`, and the references inside `if #available(...)`, after `guard #available(...)` and in the `else` of `if #unavailable(...)` as edge `attrs.available`; a function defined once per branch keeps one node per platform, and a call to it also reaches the other branches' definitions (`platform_variant_of`); the project's targets come from the Xcode project (`SDKROOT`, `SUPPORTED_PLATFORMS`, Mac Catalyst, target membership) or `Package.swift` `platforms:` ([platforms.md](platforms.md#apple-platforms-74)) |
| Tests | files under `Tests/`, `*Tests/` and `*Tests.swift`, and files importing `XCTest` or `Testing`, are test code; their test cases count in `cg tests` ([channels-and-tests.md](channels-and-tests.md)); tests in a `*UITests` folder are listed as UI tests, and a transitive test that runs through the `@main` type is left out unless `--through-roots` ([#87](https://github.com/cyberchronos00/code-graph/issues/87)). Swift 6.2 raw identifiers (`` struct `Pricing tests` ``, `` @Test func `sums the items`() ``) are read as written |

SwiftPM build output (`.build`, `.swiftpm`), Xcode `DerivedData`, CocoaPods `Pods` and `Carthage` are skipped
(`codegraph/presets/swift.yaml`).

## Exact mode

The Swift compiler records every definition and reference in an index store. cg reads it through `libIndexStore`
(shipped with every Swift toolchain, Linux included) and replaces the heuristic `CALLS` / `INSTANTIATES` edges of each
file the store covers; declarations and framework facts stay those of the syntax layer. `cg coverage` then reports
Swift as **exact** (`Swift index store (swift build)`), or names the reason it stayed heuristic.

| Setting | Effect |
|---|---|
| `CODEGRAPH_SWIFT_INDEX=1` | for a SwiftPM package (`Package.swift`), cg runs `swift build --enable-index-store` into its cache (`<cache root>/swift-build/`, `~/.cache/codegraph` by default; `cg clean ROOT` removes it), never into the checkout. The store is reused while the sources, manifests and toolchain are unchanged; otherwise `swift build` runs again (incrementally, in the same build directory). `CODEGRAPH_NO_CACHE=1` forces the run, `CODEGRAPH_INDEXER_TIMEOUT` caps it |
| `CODEGRAPH_SWIFT_INDEX_STORE=/path/to/store` | an existing store: Xcode's `DerivedData/<project>/Index.noindex/DataStore` (`Index/DataStore` before Xcode 14), a CI build's `.build/<triple>/debug/index/store`. Paths from another checkout are mapped by their longest common suffix |
| `CODEGRAPH_SWIFT=/path/to/swift`, `CODEGRAPH_LIBINDEXSTORE=/path/to/libIndexStore.so` | toolchain and library, when they are not on `PATH` / next to `swift` (`~/tools/swift-*`, `/usr/share/swift`, `/opt/swift`, and on macOS the selected Xcode's default toolchain are searched) |

The build is opt-in because `swift build` runs the package manifest and its plugins. Without a toolchain, without the
opt-in, or for an Xcode project without `Package.swift`, the index completes in heuristic mode and `cg coverage` says
why. A failed build keeps whatever the compiler indexed before the failure (`partial`), with the first `error:` line in
the coverage reason; files the store does not cover keep heuristic edges, and `cg coverage` counts them.

What a Linux build covers: only the targets that compile there. SwiftUI / UIKit modules, iOS-only apps and test
targets that need Apple frameworks are not in a Linux store (use the store from an Xcode build for those). Code in an
inactive `#if` branch is not compiled, so its heuristic edges are kept with `via: "not-compiled"`.

Heuristic call resolution, as measured against the store (docs/validation.md): an initializer call on a type the
project only extends (`String(decoding:as:)`, `Data(...)`, `URL(...)`, `JSONDecoder()`) is an edge only when one of
the extension's initializers fits the argument labels (`calls_sdk_initializer` counts the others); initializer
calls go to the overloads whose labels fit (defaults, closures and variadics may be left out, a trailing closure
fills a closure parameter), the first of per-`#if` variants; and a call of a standard-library collection method
(`first`, `filter`, `reduce`, `sorted`, ...) on a receiver of unknown type is not resolved to a same-named project
method. In exact mode, initializers declared in an extension of an SDK type are `INSTANTIATES` / `CALLS` edges too.

Member calls ([#70](https://github.com/cyberchronos00/code-graph/issues/70)) bind only when the full selector fits:
the argument labels and arity of the call have to match one of the declaration's signatures (the same rule as for
initializers), so overloads that differ by labels are told apart and `format(currency:)` does not reach
`format(amount:)`. `value.m()` does not reach a `static func m` and `Type.m()` reaches only static members. The
receiver is typed from the enclosing function's parameters and locals, the type's properties and chains of them
(`other.region!`, `self.service`, `Client.shared`); `a?.b` and `a!.b` read as `a.b`, and nested types are found by
their simple name and through module-qualified extensions (`extension Models.Notification.NotificationType`).
A receiver whose value is an SDK type reaches only members the project declares in an extension of an SDK type:
SwiftUI modifier chains (`Text("x").font(...).cardStyle()` → `extension View`), `Font.body`, `Font.system(...)`,
`NotificationCenter.default`, `UIApplication.shared`, closure parameters typed with SDK types; short unlabelled
selectors (`.run()`) on such values stay unbound. On a receiver cg cannot type, the selector decides: a call whose
selector is a common SDK member (`contains(_:)`, `contains(where:)`, `.accessibilityIdentifier(_:)`,
`.accessibilityLabel(_:)`, Font `.weight(_:)`, `resume(returning:)`, `post(name:object:)`, `open(_:)`,
`draw(_:at:)`, `read(_:maxLength:)`, ... ; `calls_sdk_selector` counts them) is not bound; otherwise it binds when
exactly one project instance method fits the selector, at heuristic confidence: `lookup["x"]!.contains(normalized:
0.5, y: 0.5)` reaches `Region.contains(normalized:y:)` even though `contains` is a collection method name. An
ambiguous selector stays unresolved (`calls_unresolved`).

Module visibility ([#90](https://github.com/cyberchronos00/code-graph/issues/90)): cg reads every local
`Package.swift` (the root and up to three folders down): its targets, the folder each compiles (`path:`, else
`Sources/<name>` / `Tests/<name>`) and their `dependencies:` (target names, `.target(name:)`, `.product(name:package:)`
of local packages, transitively). Code in a package target binds only to declarations of its own module and the
targets it depends on, never to an app, extension, preview or test target's: `store.solo` in a package does not
reach a `static var solo` that only an app preview declares, and an `extension URL` in the app is not a member the
package can call. A member of a test or app type that implements a protocol requirement (or overrides a member) of
a type the package sees stays a candidate: dynamic dispatch reaches it. A qualified supertype is resolved as written
(`final class Fake: StatusEditor.AutocompleteService.Client`), so a conformer of one of several nested `Client`
protocols implements the right one. Code outside every package target (the Xcode
app, its extensions and previews) sees every package. `candidates_outside_module` counts the declarations left out.
Exact mode (index store) is not filtered.

Since [#83](https://github.com/cyberchronos00/code-graph/issues/83):
- A prefix operator is not part of the receiver. `#expect(!Preview.matches(a, b))` and `if !Chrome.shouldAutoPresent()`
  reach the static methods.
- `Module.function()` reaches a free function of that target folder (`Styleguide.registerFonts()` →
  `Sources/Styleguide/`).
- A property or local built with a generic initializer is typed by it (`@State var gate = SlotGate<Image>()`, so
  `gate.cancel()` reaches `SlotGate.cancel`).
- A local is typed by its nearest declaration above the call: `let x = T(...)` / `T<...>(...)`, `let x: T`, or a
  literal (`var inside = false` is a `Bool`). A local declaration shadows a parameter or property of the same name.
- A receiver whose SDK type is known does not reach a project extension of another concrete SDK type (`CGImage`,
  `String`, `Data`, `Image`, ...). Extensions of protocols and open classes (`View`, `UIView`, a package's `Reducer`)
  still apply. That covers a local (`var inside = false`, `let p = Path()`), an
  SDK initializer call (`UIGraphicsPDFRenderer(bounds: r).pdfData { }`) and the closure parameter of `Path { p in }`,
  `GeometryReader { proxy in }`, `ScrollViewReader` and `Canvas`. `inside.toggle()` no longer reaches
  `HUDController.toggle()`, `p.close()` does not reach an app's `close()`, and `pdfData { }` does not reach
  `extension CGImage`. A known receiver type binds exactly or not at all.
- A call whose receiver type is unknown is bound by its selector alone. One fitting project method gives an edge with
  `binding: "name"` (`calls_by_name_only`). Two to five fitting methods give a `candidate` edge to each, with
  `binding: "candidate"` and `candidates: N` (`call_candidate_edges`); 0.8.0 dropped these calls. More than five
  are dropped (`calls_too_ambiguous`). `cg tests`, `impact` and the MCP `callers` mark a test or caller reached
  through a candidate edge `(candidate)`. Platform divergence leaves both kinds out of `missing_callee`.
- A `func` and a `static func` of one name in one type are separate nodes. The one declared second has the id suffix
  `~static` / `~instance`, so `Self.weight(from:to:)` reaches the static one and `weight(forExtraIndex:)` the instance
  one. Overloads of one static-ness still share a node.
- A call on a continuation line after a binary operator keeps its edge, on its own line. Lines starting with `<` / `>`
  are a parse error in the grammar. `a + f(x)` parses as `(a + f)(x)`; that call used to be dropped.

Since [#73](https://github.com/cyberchronos00/code-graph/issues/73), valid Swift that tree-sitter-swift 0.7.3 does not
parse is rewritten in place before parsing. The rewrite keeps the byte length and the line count, so every offset and
line stays the file's, and names are read from the original source. `index` stats count the files per rewrite
(`files_with_<rewrite>`):
- `#sourceLocation(file:line:)` / `#sourceLocation()` directive lines are blanked (`source_location_directives`). A
  test between them stays a method of its suite.
- A `#_name` macro expression (`sourceLocation: SourceLocation = #_sourceLocation`) parses as `#line`
  (`underscore_macros`). The `@Test` functions after such a default are tests again.
- An `#if` block that holds only attributes (`#if os(macOS)` / `@Test` / `#endif` above a `func`) loses its directive
  lines, so the attribute reaches the declaration. The declaration gets the block's platform (`platforms: ["macos"]`).
- `()` as a value (`.done(())`, `{ _ in () }`) and as an empty pattern (`case .done():`), `@convention(c)`, a cast
  before `??` (`x as? String ?? ""`), `try` before `await` in a condition (`if let n = try? await f()`) and a binary
  operator at the start of a continuation line (`typealias Client = A` / `  & B`, `let t = a` / `  * b`). `()` inside
  `<...>` generic arguments and typealias lines is a type and stays.
- Members after a construct the grammar still rejects (an unknown `#warningx(...)` macro between members, which
  closes the type early) are recovered into their type: the type's body ends in a MISSING `}`, and the declarations
  up to the stray `}` that follows become its members again (`declarations_recovered_into_type`).

Whatever still does not parse is listed by `cg coverage`, per file with its line spans and the declarations lost
there ([completeness.md](completeness.md#syntax-errors)). On IceCubesApp, isowords and Alamofire, the files with
syntax errors went from 7 / 18 / 8 to 3 / 5 / 1, with no declaration lost. The forms left: a function type as a
value (`(@Sendable (T) -> V).self`, `(@convention(c) (Any?) -> NSObject).self`, `os_log as (...) -> Void`),
`isolated deinit`, `if await store.isEmpty {`, a `case .a where x, .b where y:` with several guarded patterns, a
`/Enum.case` case-path literal and `#error(...)` inside `#if`.

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
- `URLComponents` whose path comes from a variable or an endpoint enum (literal paths are read), typed endpoint enums other than Moya, URLs built in helpers, per-scheme base
  URLs set outside `.xcconfig` (build settings inside `.pbxproj`).
- OS versions as filters (they are recorded, see above); App Intents and widgets as their own entry
  kinds; value navigation through a variable (`NavigationLink(value: item)`, `path.append(route)`); macro and package-plugin output as
  generated code.
- Heuristic receiver types: method return types (`a.load().run()`), dictionary / array element types
  (`lookup["x"]`), generic constraints and protocol conformances of SDK types (a project `extension View` is matched
  for any SDK value), the SDK's own members with the same selector as a project extension.
- Stored properties: a mutating call (`items.append(x)`) is a read of `items`, a compound assignment (`n += 1`) a
  write only; `$binding` projections, key paths (`\.name`, `@Bindable` / `@Binding` writes through a view) and
  receivers of unknown type are not modelled. Kotlin / TypeScript / Python stored properties are not fields yet.
- Moya: `MultiTarget`, paths built in helpers; Fluent: relations (`$todo.$tags`), raw SQL, query chains split across
  variables.
