# Kotlin

What `.kt` / `.kts` add beyond [Install](install.md), [CLI specs](cli.md#query-targets-specs), and [Graph schema](schema.md): heuristic vs scip-java, the env flags, and the ids those pages do not spell out. Toolchain fit: `cg doctor -h`. Why a file stayed heuristic: `cg coverage`.

## Modes

| mode | calls | needs |
|---|---|---|
| heuristic (default) | name resolution, labelled `heuristic` | tree-sitter (`tree-sitter`, `tree-sitter-kotlin`). No JDK or Gradle |
| exact | compiler-resolved `CALLS` / `INSTANTIATES`; declarations and framework facts stay the syntax layer | scip-java, opt-in ([Exact mode](#exact-mode)) |

`--min-confidence resolved` (or `exact`) hides the heuristic call edges. Gradle `build/` (including KSP / kapt), `.gradle`, and IDE folders are skipped. tree-sitter-kotlin 1.1.0 rewrites some sources before parse (same length, so lines and names stay). A file that still errors is split into members (`files_reparsed_by_member`, `members_dropped_by_reparse`); dropped members are in `cg coverage --details`.

| rewrite | when | stat |
|---|---|---|
| `suspend` blanked, or `;` plus spaces when it starts a statement | `val b = suspend { }`, `to suspend { }`, `suspend { }.runCatching(...)` | `suspend_lambdas_rewritten` |
| one leading space → `;` | `get` / `set` call right after a `val` / `var` (Ktor routing) | `accessor_like_calls_rewritten` |
| `dynamic` parsed as `dynamiC` | a call or function named `dynamic` | `keyword_named_calls_rewritten` |

## Exact mode

A scip-java index replaces name-based call edges. Ids stay the syntax layer's, so queries and `cg link` match both modes. Running scip-java runs the project's Gradle / Maven build, so it is opt-in.

| source | how |
|---|---|
| `cg index --scip index.scip` | Kotlin documents go to this plugin, not the generic SCIP importer |
| `CG_KOTLIN_SCIP_FILE` | a prebuilt index |
| `CG_KOTLIN_SCIP=1` | cg runs `scip-java index` (cache `~/.cache/cg/scip`; `CG_NO_CACHE=1` forces a run; `CG_INDEXER_TIMEOUT` caps it) |

| scip-java | Kotlin (Gradle builds checked) | install |
|---|---|---|
| 0.12.x | up to 2.1 | `cs install scip-java` |
| 0.13.x | 2.2.0–2.2.10 | launcher; `CG_SCIP_JAVA` is one path or several joined by `:` |
| none | 2.2.20 and newer | stays heuristic |

cg reads the version the build declares, tries the matching release first, then the next if the compiler plugin does not load. Index stats record `kotlin_version`, `indexer`, and failed `attempts`. `cg doctor <project>` names the release that fits. `install.sh --with kotlin` installs the supported pair (Windows: `cg doctor`).

Java in that index becomes `java` nodes with exact Kotlin ↔ Java and Java → Java calls, including `AppKt.build()`. Without the index, `.java` files are indexed by the [Java](java.md) plugin (heuristic). A `--scip` file that this layer does not consume still imports Java documents through the generic importer. Java exact mode, and Spring facts shared with Java, are coming in #164. Stats `exact_vs_heuristic` is precision (heuristic call edges the compiler confirms) and recall (compiler edges the heuristic layer had found). Property read / write edges stay and are left out of that pair; a property read is not a call in the SCIP index.

| still heuristic | why |
|---|---|
| Android modules | the build needs the SDK, and scip-java does not compile `compileDebugKotlin` (`android_modules` in the stats; named in the coverage reason) |
| `FAIL_ON_PROJECT_REPOS` | Android template default rejects the repository the scip-java Gradle plugin adds |
| files the index omits | those files keep heuristic calls, counted in the reason |

## Query specs

`Class.method` and `Class::method` follow [query targets](cli.md#query-targets-specs). Kotlin-only shapes:

| spec or id | selects |
|---|---|
| `method:<Type>.<name>`, `function:<pkg>.<name>` | a member, or a top-level / extension function (`receiver` on an extension) |
| `function:pkg.name@android` | one platform's `actual` (`IMPLEMENTED_BY` from `expect`). Source sets (`androidMain`, `iosMain`, `iosTest`, …) carry platform tags — `cg platforms`, `--platform` |
| `page:kotlin:orders/{id}` | Compose Navigation, typed routes, or Navigation 3 `entry`. A nested destination keeps its outer class (`page:kotlin:SettingsRoute.Standard`); a wrapper of `composable<T>` still yields a page (`attrs.wrapper`) |
| `field:<Type>.<name>` | a stored `val` / `var` (constructor property included): `READS_PROP` / `WRITES_PROP` for `x`, `this.x`, and `v.x` when `v`'s type is known. `cg readers Type.prop` / `cg writers Type.prop` |
| `enum_case:<Enum>.NAME`, `constant:<Type>.NAME` | `USES_VALUE` when the spelling is certain (`Color.RED`, a companion's `K.A`). An `object`'s `val`s stay constants |
| `route:GET /a/{id}` | Ktor `routing` / `fun Route.x()`, or a Spring mapping |
| `http:METHOD path` | Retrofit (`@GET` / `@HTTP` plus `baseUrl`), Ktor client, or OkHttp. `"$base/x"` keeps `{base}` so `cg link` can match the path |

A custom `get` / `set`, `by lazy`, or other delegate is a `method:` / `function:` node (`kotlin_kind: property`). Reads and writes are `CALLS` (`property: read` / `write` / `read_write`); calls inside the accessor carry `accessor`. `impact` prints a callable reference as `(ref: callback)` (`REFERENCES_FN`, `how: callback`, both modes), an accessor body as `(get)` / `(set)` / `(lazy)` / `(delegate)`, and a stored-property initializer as `(in a property)`. Two to five methods of one name are `candidate` edges (`binding: "candidate"`): flagged by `cg tests` / `impact`, left out of platform divergence. A library-typed receiver (`Headers.build { }`, `client: HttpClient`) is not bound by name.

## Framework facts

| area | what you can query |
|---|---|
| Ktor | each handler lambda is its own node; `authenticate("jwt")` is the route's guard; `get<Articles.Id>` takes the path from `@Resource` and its parent |
| Spring | class `@RequestMapping` prefixes; `@PreAuthorize` / `@Secured` / `@RolesAllowed` as guards; the first `SecurityFilterChain` whose `securityMatcher` matches (by `@Order`, then source order; a chain with none matches every route; a `RequestMatcher` bean is skipped, `security_chains_unknown_matcher`); `@Scheduled` and `@KafkaListener` / `@RabbitListener` / `@JmsListener` / `@SqsListener` / `@EventListener` entry points |
| Tables | Spring Data calls on a repository receiver (`JpaRepository`, `CrudRepository`, coroutine / reactive / Mongo) and Exposed `selectAll` / `insert` / `update` / `deleteWhere` → `READS_TABLE` / `WRITES_TABLE`. Table name: `@Table` or Spring Boot snake_case; Exposed uses the name argument, or the object name without `Table` |
| Android | manifest activities (`ui_page`), services / receivers / providers (`listener`), deep links, `Worker` / `CoroutineWorker` / `JobService` (`queue_job`); `navigate(...)` → `NAVIGATES_TO` |
| Tests | `src/test`, `src/androidTest`, `*Test` source sets, `*Test.kt`. Framework from imports: `junit5`, `junit4`, `kotlin-test`, `testng`, `kotest`. `androidTest` counts as UI; a path through an `*Activity` is omitted unless `--through-roots` |

```bash
cg index examples/bookstore-django  --db /tmp/dj.db
cg index examples/bookstore-android --db /tmp/android.db
cg link --backend /tmp/dj.db --frontend /tmp/android.db --db /tmp/android-link.db
cg path "page:kotlin:checkout/{bookId}" "table:catalog_order" --db /tmp/android-link.db
```
