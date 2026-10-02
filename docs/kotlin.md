# Kotlin (Android, Kotlin Multiplatform, Ktor, Spring)

cg indexes Kotlin (`.kt`, `.kts`) with a tree-sitter syntax layer (`pip install tree-sitter tree-sitter-kotlin`). No JDK
or Gradle build is needed, so any checkout indexes as-is. `cg coverage` reports Kotlin as **heuristic**: references are
resolved by name, as for Rust and C / C++ without their compiler indexers.

## What is in the graph

| Area | Facts |
|---|---|
| Declarations | packages, classes, interfaces, objects, companion objects, top-level and extension functions, methods (`class:` / `function:` / `method:` ids by fully qualified name) |
| Calls | `CALLS` resolved through the enclosing class and its supertypes, the receiver's parameter / property type (`api.order()` with `api: OrdersApi`), imports, the same package and a project-wide unique name; constructor calls as `INSTANTIATES`; interface / superclass methods → overrides (`IMPLEMENTED_BY` / `OVERRIDDEN_BY`) |
| Ktor server | `routing { route("/a") { get("/{id}") { } } }` and `fun Route.x()` extensions → `route:GET /a/{id}`; each handler lambda is its own node; `authenticate("jwt") { }` is recorded as the route's guard |
| Spring | `@RestController` / `@Controller` with `@RequestMapping` prefixes, `@GetMapping` ... and `@RequestMapping(method = [...])`; `@PreAuthorize`, `@Secured`, `@RolesAllowed` on the class or method as guards; `@Scheduled` (`scheduled`) and `@KafkaListener` / `@RabbitListener` / `@JmsListener` / `@SqsListener` / `@EventListener` (`listener`) entry points |
| HTTP clients | Retrofit interface methods (`@GET("v1/orders/{id}")`, `@HTTP(method=, path=)`) joined with the base URL from `Retrofit.Builder().baseUrl(...)` when the project has one; Ktor client `client.get("...")`; OkHttp `Request.Builder().url("...")` with the verb from the builder chain. `"$base/x"` keeps `{base}` as the origin, so `cg link` still matches the path |
| Android | `AndroidManifest.xml` activities (`ui_page`), services / receivers / providers (`listener`) with their lifecycle methods; deep links (`<data scheme host path*>`) as `page:` nodes; `Worker` / `CoroutineWorker` / `JobService` subclasses (`queue_job`); Compose Navigation `composable("orders/{id}")` / `composable<OrderRoute>` as `page:kotlin:<route>` and `navController.navigate(...)` as `NAVIGATES_TO` |
| Multiplatform | files in KMP source sets (`androidMain`, `iosMain`, `jsMain`, `wasmJsMain`, `macosMain`, `linuxMain`, `mingwMain` ...) carry platform conditions (`cg platforms`, `--platform`); `expect` declarations link to each `actual` (`IMPLEMENTED_BY`, ids `function:pkg.name@android`) |
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

## Roadmap

- Exact mode through scip-java (Gradle / Maven builds) with Kotlin-aware symbol names.
- Spring Data repositories and Exposed tables as table reads / writes; `SecurityFilterChain` matchers as guards.
- Type-safe navigation with arguments (`composable<Route>(deepLinks = ...)`), Ktor type-safe resources.
