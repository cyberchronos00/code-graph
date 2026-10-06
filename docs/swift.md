# Swift

What `.swift` adds beyond [Install](install.md), [CLI specs](cli.md#query-targets-specs), and [Graph schema](schema.md): heuristic vs the Swift index store, the `CG_SWIFT_*` flags, and the ids those pages do not spell out. Toolchain fit: `cg doctor -h`. Why a file stayed heuristic: `cg coverage`.

## Modes

| mode | calls | needs |
|---|---|---|
| heuristic (default) | name resolution, labelled `heuristic` | tree-sitter (`tree-sitter`, `tree-sitter-swift`). No Xcode or `swift` |
| exact | compiler-resolved `CALLS` / `INSTANTIATES`; declarations and framework facts stay the syntax layer | a Swift toolchain, opt-in ([Exact mode](#exact-mode)) |

`--min-confidence resolved` (or `exact`) hides every heuristic call edge. SwiftPM `.build` / `.swiftpm`, Xcode `DerivedData`, `Pods`, and Carthage are skipped.

tree-sitter-swift 0.7.3 rewrites some sources before parse (same length, so lines and names stay). Index stats count `files_with_<rewrite>`. What still fails is in `cg coverage --details`.

| rewrite | when | stat |
|---|---|---|
| `#sourceLocation` lines blanked | directive lines | `source_location_directives` |
| `#_name` parsed as `#line` | `#_sourceLocation` and similar | `underscore_macros` |
| `#if` / `#endif` lines dropped | a block that is only attributes (`@Test` under `#if os`) | the declaration keeps that platform |
| length-preserving patches | `()` as a value, `@convention(c)`, a cast before `??`, `try` before `await` in a condition, a binary operator starting a continuation line | names stay the original source |
| members pulled back into the type | the grammar closes the type early | `declarations_recovered_into_type` |

## Exact mode

`libIndexStore` replaces heuristic call and initializer edges for each file the store covers. `swift build` runs the package manifest and its plugins, so it is opt-in. Coverage reports `Swift index store (swift build)`, or the reason the layer did not run.

| source | how |
|---|---|
| `CG_SWIFT_INDEX=1` | root `Package.swift`: `swift build --enable-index-store` into `<cache root>/swift-build/` (default `~/.cache/cg`; never the checkout). Reused while sources, manifests, and the toolchain match. `CG_NO_CACHE=1` forces a run; `CG_INDEXER_TIMEOUT` caps it |
| `CG_SWIFT_INDEX_STORE` | an existing store (Xcode `DerivedData/…/Index.noindex/DataStore`, or `Index/DataStore` before Xcode 14; CI `.build/<triple>/debug/index/store`). Another checkout's paths map by longest common suffix |
| `CG_SWIFT`, `CG_LIBINDEXSTORE` | `swift` and `libIndexStore` when they are not on `PATH` or next to `swift` |

A failed build keeps a `partial` store; the coverage reason includes the first `error:` line. Stats include `exact_vs_heuristic` and `heuristic_kept_not_compiled`.

| still heuristic | why |
|---|---|
| no toolchain, not opted in, or no `Package.swift` | coverage says which; set `CG_SWIFT_INDEX_STORE` to an Xcode store |
| Apple-only targets on Linux | SwiftUI, UIKit, and tests that need Apple frameworks are not in that store |
| inactive `#if` | edges stay, `via: "not-compiled"` |
| files the store omits | those files keep heuristic calls, counted in the reason |

## Query specs

`Type.method` follows [query targets](cli.md#query-targets-specs). Swift-only shapes:

| spec or id | selects |
|---|---|
| `method:<Type>.body` | a SwiftUI `body`. A computed property, observers, or `lazy var` is also `method:<Type>.<name>` (`swift_kind`) |
| `page:swift:<View>` | `NavigationLink(destination:)`, `.navigationDestination`, `.sheet` / `.fullScreenCover` / `.popover`, `TabView`, or the `WindowGroup` root (`ROUTES_TO` `body`, `NAVIGATES_TO` from the caller). Each `case` of `navigationDestination(for:)` is the target of `NavigationLink(value:)` and of `navigate(to:)` / `push` / `append` |
| `field:<Type>.<name>` | a stored `let` / `var`: `READS_PROP` / `WRITES_PROP` for `self.x`, a bare `x` in the type, and `v.x` when `v`'s type is known. `cg readers Type.prop` / `cg writers Type.prop` |
| availability | `@available(iOS 17, *)` is node `attrs.available` (shown `[iOS 17+]`; members inherit the type). `if` / `guard #available` and the `else` of `#unavailable` set it on the edge. A check the deployment target already meets is `available_declared`, not a filter (`availability_always_true`) |

`impact` shows an accessor as `(get)` / `(set)` / `(willSet)` / `(didSet)` and a candidate as `(candidate)`. The second of a `func` and a `static func` with one name has id suffix `~static` or `~instance`. In heuristic mode, two to five methods of one selector are `candidate` edges (`binding: "candidate"`), flagged by `cg tests` / `impact` and left out of platform divergence. An SDK-typed receiver binds only a project extension of that type, or of a protocol / open class such as `View`. Package code binds only its module and `Package.swift` dependencies (`candidates_outside_module`). Exact mode keeps every index-store reference, including ones outside that module.

## Framework facts

| area | what you can query |
|---|---|
| SwiftUI / UIKit | pages above; `pushViewController` / `present` the same way. An untyped `.case` matches when one destination type has that case; `default:` covers the rest |
| HTTP | URLSession and Alamofire `AF.request` when the URL is built in the same function. `"\(base)/x"` keeps `{base}` so `cg link` can match. A base-like constant, or an `Info.plist` key filled from `.xcconfig`, sets the origin (`attrs.base`, or `attrs.base_candidates` per configuration) |
| Moya | `TargetType` → one `http:` node per `case` (`how: "moya target"`). `provider.request(.case)` calls it |
| Fluent | `schema = "todos"` → `table:todos`. `query` / `find` read; `save` / `create` / `update` / `delete` and `database.schema(…).create()` write |
| Vapor | `app.get`, `routes.post`, `grouped` / `group` prefixes, `RouteCollection.boot` → `route:`. Middleware on `grouped` is the guard |
| Tests | XCTest `test*` (`xctest`) and Swift Testing `@Test` (`swift-testing`, `@Suite` on the type). `*UITests` is UI; a path through `@main` is omitted unless `--through-roots` |
| Platforms | `#if os` / `canImport` / `targetEnvironment` / `@available(..., unavailable)` (`cg platforms`, `--platform`). Targets come from the Xcode project or `Package.swift` `platforms:` |

```bash
cg index examples/bookstore-django --db /tmp/dj.db
cg index examples/bookstore-ios    --db /tmp/ios.db
cg link --backend /tmp/dj.db --frontend /tmp/ios.db --db /tmp/ios-link.db
cg path "page:swift:CheckoutView" "table:catalog_order" --db /tmp/ios-link.db
```
