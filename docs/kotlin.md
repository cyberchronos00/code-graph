# Kotlin (Android, Kotlin Multiplatform, Ktor, Spring)

cg indexes Kotlin (`.kt`, `.kts`) with a tree-sitter syntax layer (`pip install tree-sitter tree-sitter-kotlin`). No JDK
or Gradle build is needed, so any checkout indexes as-is. `cg coverage` then reports Kotlin as **heuristic**: references
are resolved by name, as for Rust and C / C++ without their compiler indexers. With a scip-java index of the build the
call edges are compiler-resolved and coverage reports **exact** ([Exact mode](#exact-mode)).

## What is in the graph

| Area | Facts |
|---|---|
| Declarations | packages, classes, interfaces, objects, companion objects, top-level and extension functions, methods (`class:` / `function:` / `method:` ids by fully qualified name) |
| Calls | `CALLS` resolved through the enclosing class and its supertypes, the receiver's parameter / property type (`api.order()` with `api: OrdersApi`), imports, the same package and finally the method name alone (not for a receiver of a library type, `Headers.build { }` or `client: HttpClient`): one method of that name gives an edge with `binding: "name"`, two to five give a `candidate` edge to each (`binding: "candidate"`, flagged by `cg tests` / `impact`, left out of platform divergence; [#83](https://github.com/cyberchronos00/code-graph/issues/83)); constructor calls as `INSTANTIATES`; interface / superclass methods → overrides (`IMPLEMENTED_BY` / `OVERRIDDEN_BY`) |
| Ktor server | `routing { route("/a") { get("/{id}") { } } }` and `fun Route.x()` extensions → `route:GET /a/{id}`; type-safe resources `get<Articles.Id> { }` with the path from `@Resource("{id}")` and its `parent` resource (or enclosing resource class); each handler lambda is its own node; `authenticate("jwt") { }` is recorded as the route's guard |
| Spring | `@RestController` / `@Controller` with `@RequestMapping` prefixes, `@GetMapping` ... and `@RequestMapping(method = [...])`; `@PreAuthorize`, `@Secured`, `@RolesAllowed` on the class or method as guards; `SecurityFilterChain` URL rules (`requestMatchers("/admin/**").hasRole("ADMIN")`, `anyRequest().authenticated()`, the Kotlin DSL `authorize("/admin/**", hasRole("ADMIN"))`; first match wins, `permitAll` adds none) as guards on the routes they match (`security` attribute names the file); `@Scheduled` (`scheduled`) and `@KafkaListener` / `@RabbitListener` / `@JmsListener` / `@SqsListener` / `@EventListener` (`listener`) entry points |
| Tables | Spring Data repositories (`interface OwnerRepository : JpaRepository<Owner, Int>`, `CrudRepository`, coroutine / reactive / Mongo variants): a call on a repository-typed receiver (`owners.findById(id)`, inherited methods included) → `READS_TABLE` / `WRITES_TABLE` by method name (`find` / `get` / `count` / `exists` ... read, `save` / `delete` ... write) on the entity's `@Table(name)` or Spring Boot's snake_case default; Exposed `object Users : IntIdTable("users")` / `Table()` (default name: the object name without `Table`) with `Users.selectAll()`, `insert`, `update`, `deleteWhere` ... (confidence `resolved`, `via` names the call) |
| HTTP clients | Retrofit interface methods (`@GET("v1/orders/{id}")`, `@HTTP(method=, path=)`) joined with the base URL from `Retrofit.Builder().baseUrl(...)`: the base URL of the builder chain that creates the interface (`.create(OrdersApi::class.java)`), else the project's only one; `baseUrl(BuildConfig.API_URL)` resolves through a `buildConfigField("String", "API_URL", ...)` in the Gradle files; Ktor client `client.get("...")` and builder blocks `client.get { url("...") }`, `client.request { method = HttpMethod.Post; url("...") }`; OkHttp `Request.Builder().url("...")` with the verb from the builder chain. `"$base/x"` keeps `{base}` as the origin, so `cg link` still matches the path |
| Android | `AndroidManifest.xml` activities (`ui_page`), services / receivers / providers (`listener`) with their lifecycle methods; deep links (`<data scheme host path*>`) as `page:` nodes; `Worker` / `CoroutineWorker` / `JobService` subclasses (`queue_job`); Compose Navigation `composable("orders/{id}")` / typed `composable<OrderRoute>(deepLinks = ...) { }` and Navigation 3 `entry<OrderKey>(metadata = ...) { }` as `page:kotlin:<route>` (the lambda's calls belong to the page; the route may be a string constant, `composable(route = Destinations.TASKS_ROUTE)`, and `$NAME` / `${Obj.NAME}` constants in route and `navigate` strings are put in) and `navigate("orders/1")` / `navigate(OrderRoute(id))` as `NAVIGATES_TO` |
| Multiplatform | files in KMP source sets (`androidMain`, `iosMain`, `jsMain`, `wasmJsMain`, `macosMain`, `linuxMain`, `mingwMain` ..., and the per-platform test sets `iosTest`, `androidUnitTest`, `androidInstrumentedTest`) carry platform conditions (`cg platforms`, `--platform`); `expect` declarations link to each `actual` (`IMPLEMENTED_BY`, ids `function:pkg.name@android`); the project's targets come from the `kotlin { }` block (`androidTarget()`, `iosArm64()`, `jvm("desktop")`, `wasmJs()` ...) |
| Tests | files under `src/test`, `src/androidTest`, `*Test` source sets and `*Test.kt` are test code; `@Test` / `@ParameterizedTest` / `@RepeatedTest` / `@TestFactory` functions are `test` entries with the framework (`junit5`, `junit4`, `kotlin-test`, `testng`, `kotest`) from the file's imports, counted in `cg tests`; `src/androidTest` tests are listed as UI tests, and a transitive test that runs through an `*Activity` is left out unless `--through-roots` ([#87](https://github.com/cyberchronos00/code-graph/issues/87)) |

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

Each scip-java release carries a Kotlin compiler plugin that loads into a narrow range of Kotlin versions:

| scip-java | Kotlin (checked on Gradle builds) | Install |
|---|---|---|
| 0.12.x (`com.sourcegraph`) | up to 2.1 | `cs install scip-java` |
| 0.13.x (`org.scip-code`) | 2.2.0 - 2.2.10 | the launcher from [github.com/scip-code/scip-java/releases](https://github.com/scip-code/scip-java/releases), saved as `~/tools/scip-java-0.13.1/scip-java` (or `~/.local/bin/scip-java-0.13.1`) and made executable |
| none yet | 2.2.20 and newer | the heuristic layer stays; coverage says which release would be needed |

Several releases can be installed side by side: cg reads the Kotlin version the build declares (`kotlin("jvm") version`,
`id("org.jetbrains.kotlin.*") version`, the `kotlin` entry of `gradle/libs.versions.toml`, `kotlin_version` in
`gradle.properties`, `<kotlin.version>` in `pom.xml`), runs the release that supports it first, and tries the next one
when the compiler plugin does not load. `CODEGRAPH_SCIP_JAVA` takes one path or several joined by `:`. The index
stats record `kotlin_version`, the `indexer` used and the failed `attempts`. scip-java 0.13 writes SCIP 0.9 typed
ranges (`single_line_range`, `multi_line_enclosing_range`); cg reads both forms, also in the generic `--scip` importer.
`install.sh --with kotlin` installs 0.12 with coursier and the 0.13.1 launcher (checksum-verified) as
`~/.local/bin/scip-java-0.13.1` (0.13 ships only a POSIX `sh` launcher, so `install.ps1` prints the WSL / `--scip`
routes instead). `cg doctor <project>` lists the installed releases with their Kotlin ranges and names the one that fits the build's
Kotlin version, or says that none does.

### Mixed Kotlin / Java modules

scip-java indexes the Java sources of the build together with the Kotlin ones. Since the Kotlin plugin takes the
index, it imports the Java documents too: Java classes (nested ones included) and methods become `java` nodes (ids as
the generic SCIP importer makes them, `method:demo.Formatter::bold`), and Kotlin -> Java, Java -> Kotlin and Java ->
Java calls and constructor calls become exact edges. A Java call of a top-level Kotlin function through its file
facade (`AppKt.build()`) goes to the Kotlin function. The Java caller is the innermost Java method or class around the
call (SCIP enclosing ranges). `cg coverage` reports Java as `scip` (imported from the Kotlin build's index) and the
Kotlin stats carry `java: {documents, classes, methods, references, references_external}`. Without an index the Java
files stay `unsupported`, as before.

`cg coverage` names the mode and the reason: `kotlin: 38 files exact: scip-java index (--scip)`, or for heuristic mode
why the exact layer did not run (no Gradle / Maven build file, no JDK, scip-java not installed, not opted in, or the
scip-java run failed, with the last line of its output). Kotlin files the index does not contain keep their heuristic
calls and are counted in the reason. `cg index` stats carry `exact_vs_heuristic`: how many of the heuristic call edges
the compiler confirms (precision) and how many compiler edges the heuristic layer had found (recall).

Limitations: no released scip-java loads into Kotlin 2.2.20 or newer (0.13.x is built against 2.2.0 and fails with
`NoSuchMethodError` on 2.2.20+ and `AnalyzerRegistrar is incompatible` on 2.3+; 0.12.x fails on 2.2+); the build
keeps the heuristic layer and coverage names the Kotlin version. Android modules are not indexed: they need the Android SDK for
the build to run (`SDK location not found` in the reason), and even with the SDK scip-java's Gradle plugin compiles
no Android variant (`compileDebugKotlin`), so their files keep the heuristic layer. cg lists the modules that apply the
Android Gradle plugin (`android_modules` in the stats) and names them in the reason, as `skipped modules` when the
rest of the build was indexed. Builds whose settings use `repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)`
(the Android template default) reject the repository scip-java's Gradle plugin adds; the reason says so. Java fields and Java-only builds are not covered by this
layer (index a Java-only build with scip-java and `--scip`). Property accessors are not call edges (a property read
reports the synthetic getter, which may share its symbol with a declared `fun getX()`). Custom getters / setters (`val label get() = ...`) have no node of their own: their calls come from the class, and `impact` lists such a class as a caller, labelled `(in a property)`.

## Roadmap

- Exact mode on Kotlin 2.2.20+ builds (once a scip-java release supports them) and on Android modules (an init
  script attaching the compiler plugins to the variant compile tasks, and allowing the plugin's repository).
