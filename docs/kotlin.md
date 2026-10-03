# Kotlin (Android, Kotlin Multiplatform, Ktor, Spring)

cg indexes Kotlin (`.kt`, `.kts`) with a tree-sitter syntax layer (`pip install tree-sitter tree-sitter-kotlin`). No JDK
or Gradle build is needed, so any checkout indexes as-is. `cg coverage` then reports Kotlin as **heuristic**: references
are resolved by name, as for Rust and C / C++ without their compiler indexers. With a scip-java index of the build the
call edges are compiler-resolved and coverage reports **exact** ([Exact mode](#exact-mode)).

## What is in the graph

| Area | Facts |
|---|---|
| Declarations | packages, classes, interfaces, objects, companion objects, top-level and extension functions, methods (`class:` / `function:` / `method:` ids by fully qualified name) |
| Calls | `CALLS` resolved through the enclosing class and its supertypes, the receiver's parameter / property type (`api.order()` with `api: OrdersApi`), imports, the same package and a project-wide unique name; constructor calls as `INSTANTIATES`; interface / superclass methods → overrides (`IMPLEMENTED_BY` / `OVERRIDDEN_BY`) |
| Ktor server | `routing { route("/a") { get("/{id}") { } } }` and `fun Route.x()` extensions → `route:GET /a/{id}`; type-safe resources `get<Articles.Id> { }` with the path from `@Resource("{id}")` and its `parent` resource (or enclosing resource class); each handler lambda is its own node; `authenticate("jwt") { }` is recorded as the route's guard |
| Spring | `@RestController` / `@Controller` with `@RequestMapping` prefixes, `@GetMapping` ... and `@RequestMapping(method = [...])`; `@PreAuthorize`, `@Secured`, `@RolesAllowed` on the class or method as guards; `SecurityFilterChain` URL rules (`requestMatchers("/admin/**").hasRole("ADMIN")`, `anyRequest().authenticated()`, the Kotlin DSL `authorize("/admin/**", hasRole("ADMIN"))`; first match wins, `permitAll` adds none) as guards on the routes they match (`security` attribute names the file); `@Scheduled` (`scheduled`) and `@KafkaListener` / `@RabbitListener` / `@JmsListener` / `@SqsListener` / `@EventListener` (`listener`) entry points |
| Tables | Spring Data repositories (`interface OwnerRepository : JpaRepository<Owner, Int>`, `CrudRepository`, coroutine / reactive / Mongo variants): a call on a repository-typed receiver (`owners.findById(id)`, inherited methods included) → `READS_TABLE` / `WRITES_TABLE` by method name (`find` / `get` / `count` / `exists` ... read, `save` / `delete` ... write) on the entity's `@Table(name)` or Spring Boot's snake_case default; Exposed `object Users : IntIdTable("users")` / `Table()` (default name: the object name without `Table`) with `Users.selectAll()`, `insert`, `update`, `deleteWhere` ... (confidence `resolved`, `via` names the call) |
| HTTP clients | Retrofit interface methods (`@GET("v1/orders/{id}")`, `@HTTP(method=, path=)`) joined with the base URL from `Retrofit.Builder().baseUrl(...)`: the base URL of the builder chain that creates the interface (`.create(OrdersApi::class.java)`), else the project's only one; `baseUrl(BuildConfig.API_URL)` resolves through a `buildConfigField("String", "API_URL", ...)` in the Gradle files; Ktor client `client.get("...")` and builder blocks `client.get { url("...") }`, `client.request { method = HttpMethod.Post; url("...") }`; OkHttp `Request.Builder().url("...")` with the verb from the builder chain. `"$base/x"` keeps `{base}` as the origin, so `cg link` still matches the path |
| Android | `AndroidManifest.xml` activities (`ui_page`), services / receivers / providers (`listener`) with their lifecycle methods; deep links (`<data scheme host path*>`) as `page:` nodes; `Worker` / `CoroutineWorker` / `JobService` subclasses (`queue_job`); Compose Navigation `composable("orders/{id}")` / typed `composable<OrderRoute>(deepLinks = ...) { }` and Navigation 3 `entry<OrderKey>(metadata = ...) { }` as `page:kotlin:<route>` (the lambda's calls belong to the page) and `navigate("orders/1")` / `navigate(OrderRoute(id))` as `NAVIGATES_TO` |
| Multiplatform | files in KMP source sets (`androidMain`, `iosMain`, `jsMain`, `wasmJsMain`, `macosMain`, `linuxMain`, `mingwMain` ...) carry platform conditions (`cg platforms`, `--platform`); `expect` declarations link to each `actual` (`IMPLEMENTED_BY`, ids `function:pkg.name@android`); the project's targets come from the `kotlin { }` block (`androidTarget()`, `iosArm64()`, `jvm("desktop")`, `wasmJs()` ...) |
| Tests | files under `src/test`, `src/androidTest`, `*Test` source sets and `*Test.kt` are test code; `@Test` functions are `test` entries |

Gradle build output (`build/`, including KSP / kapt sources under `build/generated`), `.gradle` and IDE folders are
skipped (`codegraph/presets/kotlin.yaml`).

## Example

`examples/bookstore-android` is a Compose + Retrofit client of `examples/bookstore-django`:

```bash
cg index examples/bookstore-django  --db /tmp/dj.db
cg index examples/bookstore-android --db /tmp/android.db
cg link --backend /tmp/dj.db --frontend /tmp/android.db --db /tmp/android-link.db
cg path "page:kotlin:checkout/{bookId}" "table:catalog_order" --db /tmp/android-link.db
```

The path runs from the Compose destination through the screen, view model and repository to the Retrofit method,
its `http:POST /api/orders/` endpoint, the Django route and handler, and the table it writes.

## Exact mode

A [scip-java](https://sourcegraph.github.io/scip-java/) index of the Gradle / Maven build replaces the name-based call
edges with compiler-resolved ones: overloads, lambdas (`it.area()`), extension functions, same-named methods on
different classes and interface calls go to the declaration the compiler picked. Declarations, framework facts and the
graph ids stay those of the syntax layer (SCIP symbols are matched to them by file, name line and name), so queries and
`cg link` work the same in both modes. The layer is used when one of these provides an index:

| Source | How |
|---|---|
| `cg index --scip index.scip` | an index with Kotlin documents is taken by the Kotlin plugin instead of the generic SCIP importer |
| `CODEGRAPH_KOTLIN_SCIP_FILE=/path/index.scip` | a prebuilt index (e.g. from CI) |
| `CODEGRAPH_KOTLIN_SCIP=1` | cg runs `scip-java index` at the project root through the native runner cache (`~/.cache/codegraph/scip`, keyed by the sources and build files; `CODEGRAPH_NO_CACHE=1` forces a run, `CODEGRAPH_INDEXER_TIMEOUT` caps it) |

Running scip-java runs the project's Gradle / Maven build, and so its build scripts; that is why it is opt-in rather
than automatic. It needs a JDK (`JAVA_HOME` or `java` on `PATH`) and scip-java (`CODEGRAPH_SCIP_JAVA`, `PATH`,
`~/tools`, `~/.local/bin` or the coursier bin directory; `cs install scip-java` or the standalone launcher).

`cg coverage` names the mode and the reason: `kotlin: 38 files exact: scip-java index (--scip)`, or for heuristic mode
why the exact layer did not run (no Gradle / Maven build file, no JDK, scip-java not installed, not opted in, or the
scip-java run failed, with the last line of its output). Kotlin files the index does not contain keep their heuristic
calls and are counted in the reason. `cg index` stats carry `exact_vs_heuristic`: how many of the heuristic call edges
the compiler confirms (precision) and how many compiler edges the heuristic layer had found (recall).

Limitations: scip-java 0.12.3 bundles a semanticdb-kotlinc compiler plugin built for Kotlin 2.1; a build on Kotlin
2.2 or newer fails with `Plugin com.sourcegraph.semanticdb_kotlinc.AnalyzerRegistrar is incompatible` (coverage says
so and the heuristic layer is kept). Android application modules need the Android SDK for the build to run. Property
accessors are not call edges (a property read reports the synthetic getter, which may share its symbol with a declared
`fun getX()`).

## Roadmap

- Exact mode on Kotlin 2.2+ builds (a newer semanticdb-kotlinc) and on Android modules without a full SDK setup.
- Navigation routes built from `const val` strings (`composable(Destinations.TASKS_ROUTE)`).
