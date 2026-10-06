# Java

What `.java` adds beyond [Install](install.md), [CLI specs](cli.md#query-targets-specs), and [Graph schema](schema.md): the heuristic syntax layer and the ids those pages do not spell out. Toolchain fit: `cg doctor`. Why a file stayed heuristic: `cg coverage`.

## Modes

| mode | calls | needs |
|---|---|---|
| heuristic (default) | receiver type, imports, same package, then a unique name. Labelled `heuristic` | tree-sitter (`tree-sitter`, `tree-sitter-java`). No JDK |

`--min-confidence resolved` (or `exact`) hides those call edges. Gradle `build/`, Maven `target/`, and IDE folders are skipped.

A file that is not valid UTF-8 is read as Latin-1 and still indexed. A syntax error is recorded on `cg coverage --details` and does not drop the rest of the project. Declarations inside the error span may be missing; that affects answers whose nodes sit in that file.

## What is extracted

| node | id |
|---|---|
| file | `file:java:<path>` |
| package | `package:com.example` |
| class, interface, enum, record | `class:com.example.Foo` (`attrs.java_kind`) |
| nested type | `class:com.example.Foo.Bar` |
| anonymous class | `class:com.example.Foo.1` (or `Foo.place.1` when the `new` sits in a method) |
| method | `method:com.example.Foo.bar` |
| constructor | `constructor:com.example.Foo.<init>` |
| field | `field:com.example.Foo.name` |
| enum constant | `enum_case:com.example.Color.RED` |

Overloads share one method id, the same way Kotlin does, so a later exact layer can map a scip-java symbol onto it. Nested types use dots, not `$`.

`CONTAINS` links a package to its types and a type to its members, fields, and enum constants. `EXTENDS` / `IMPLEMENTS` link a type to a project supertype. `IMPLEMENTED_BY` / `OVERRIDDEN_BY` link a method to the method that implements or overrides it. Every one of those edges is `heuristic`.

Calls (`CALLS`) bind in this order:

1. Receiver type: a local, a parameter, a field, `this`, or `super`, including `this.field.method()`.
2. A chain whose type is known: `a.b().c()`, `p.child().line()`, `new Foo().bar()`. The return type (or the constructed type) has to be one project class, and every overload of `b` / `child` has to agree on it.
3. A static call `Foo.bar()` when `Foo` is in scope, including a qualified name `com.example.Foo.bar()`.
4. An explicit import, a wildcard import, or a static import.
5. The same package.
6. One project method of that name. Two to five are `candidate` edges. A receiver whose type is known and is not a project class (`java.util.Collections.emptyList()`, a primitive) is not guessed by name. A parameter or chain typed as an interface edges that interface method, not each implementing class.

`new Foo()` is `INSTANTIATES` plus `CALLS` on `constructor:….Foo.<init>`. A method reference `Foo::bar`, `this::bar`, `expr::bar` (from `expr`'s type), or `Foo::new` is `REFERENCES_FN` when the target is in the project. A lambda's calls stay on the enclosing method. An explicitly typed lambda parameter (`(Pricing x) -> x.line()`) is a receiver type; an inferred one is not.

## Exact mode

Opt-in, because it runs the build. scip-java runs Gradle `clean compileTestJava compileTestKotlin compileTestKotlinJvm` or Maven `clean verify -DskipTests`, and its own docs warn that this cleans build caches. The JDK that runs the build must be 17, 21, or 25. Java 8 and 11 are unsupported build JDKs. A JDK older than 17 cannot start scip-java.

One scip-java run per project root is cached (`~/.cache/cg/scip`; `CG_NO_CACHE=1` forces a run; `CG_INDEXER_TIMEOUT` caps it). The Java and Kotlin plugins both read that index.

| source | when it is used |
|---|---|
| `--scip FILE` | documents include `.java` (this plugin) or `.kt` / `.kts` (the Kotlin plugin). A mixed index is used by both. Go and other languages still use the generic importer |
| `CG_JAVA_SCIP_FILE` | a prebuilt index. Wins over `CG_KOTLIN_SCIP_FILE` when both are set |
| `CG_KOTLIN_SCIP_FILE` | the same prebuilt index, when `CG_JAVA_SCIP_FILE` is unset |
| `CG_JAVA_SCIP=1` or `CG_KOTLIN_SCIP=1` | either name starts the one run. `CG_KOTLIN_SCIP` keeps working for Kotlin-only trees |

Symbols map onto the ids in [What is extracted](#what-is-extracted): packages, nested classes, anonymous classes (`$anon` becomes `Foo.1` or `Foo.method.1`), enum cases, constructors `<init>`, fields, and generic methods. Overload disambiguators (`add(+1)`) collapse onto the shared method id. Exact `CALLS`, constructor `INSTANTIATES`, and field `REFERENCES` replace the heuristic call edges in files the index covers. A file the index misses stays heuristic, and `cg coverage` says how many. From Java, `FooKt.bar()`, `@file:JvmName`, `Foo.Companion.x()`, `@JvmStatic`, and `getX` / `isX` / `setX` map to the Kotlin node. `cg doctor`'s java row names scip-java, the JDK, and the opt-in: `install.sh --with java`, then `CG_JAVA_SCIP=1`.

## Kotlin interop

Heuristic mode resolves calls across the two plugins:

- Kotlin → Java through an import or the same package (`orders.find(id)` when `orders` is a Java `OrderService`).
- Java → Kotlin: `FooKt.bar()` for a top-level function, including `@file:JvmName("Foo")`; `Foo.Companion.x()`; `@JvmStatic` called as `Foo.x()`; `getX()` / `setX()` / `isX()` on a Kotlin property, as `READS_PROP` / `WRITES_PROP` or `CALLS` with `property: read|write`, the same edges the Kotlin layer uses.

Exact mode maps the Java documents of the shared index onto these ids, including fields. The Kotlin layer does not create a second node for the same Java symbol.

In `examples/bookstore-spring`, `OrderEvents.onPlaced` calls Java `OrderService.place`, so `cg impact table:store_books` lists the Kotlin listener.

## Spring

`cg_code_graph/plugins/jvm/spring.py` reads Java and Kotlin declarations (annotations with arguments, parameter types, supertypes, string-literal call arguments) and emits one set of Spring facts. A Java `SecurityFilterChain` bean guards Kotlin controllers, and a Kotlin `SecurityFilterChain` guards Java controllers. Detection (`org.springframework.boot` or `spring-boot-starter-*` in `build.gradle`, `build.gradle.kts`, or `pom.xml` at the project root or one module down) applies `presets/spring.yaml`. `cg coverage` then reports `frameworks: spring`.

| fact | what is recorded |
|---|---|
| Routes | Class and method `@RequestMapping` / `@GetMapping` / `@PostMapping` / `@PutMapping` / `@DeleteMapping` / `@PatchMapping`. A bare `@GetMapping` keeps the class prefix (`GET /api/books`); `@GetMapping("/")` keeps the trailing slash. `{id:\d+}` becomes `{id}`. `server.servlet.context-path` in profile-less `application.properties` or `application.yml` (not a test resource, not `application-*.properties` / `application-*.yml`) prefixes every route |
| Guards | `@PreAuthorize`, `@PostAuthorize`, `@Secured`, `@RolesAllowed`. `SecurityFilterChain` rules (`requestMatchers` / `antMatchers` / `mvcMatchers` / `pathMatchers`, Kotlin `authorize`) — first chain by `@Order` then source order whose `securityMatcher` matches; a chain with none matches every route; a `RequestMatcher` bean matches none. Java finds the chain on a `@Bean` method whose return type is `SecurityFilterChain` |
| Beans | `@Service`, `@Component`, `@Repository`, `@Controller`, `@RestController`, `@Bean`. An injected interface is `BOUND_TO` the single implementing bean, or the `@Primary` / `@Qualifier` one (constructor parameter or `@Autowired` / `@Inject` / `@Resource` field) |
| Tables | JPA `@Entity`: `@Table(name)` or Spring Boot snake_case. Spring Data repository calls (`find*` / `save*` / …) and `@Query` table names are `READS_TABLE` / `WRITES_TABLE` |
| Entry points | `@Scheduled` (`scheduled`), `@KafkaListener` / `@RabbitListener` / `@JmsListener` / `@SqsListener` / `@EventListener` (`listener`), `CommandLineRunner` / `ApplicationRunner.run` (`main`) |
| HTTP clients | `RestTemplate`, `RestClient`, `WebClient`, `@FeignClient`, `@HttpExchange` → `http:` nodes and `HTTP_CALLS`, the same shape other plugins emit, so `cg link` can match them. URI templates keep `{id}`. One `baseUrl("https://host")` or `create("https://host")` on the same call is prefixed onto a relative template |
| Tests | JUnit 4, JUnit 5, TestNG (`entry_kind: test`). `MockMvc.perform(get("/api/books/{id}", 1))`, `WebTestClient`, and `TestRestTemplate` become `TEST_HTTP` edges to the route |

Kotlin projects keep the same routes, guards, and tables as before. Exposed tables stay in the Kotlin plugin. A Kotlin Spring repo gets this preset because detection sees the Gradle or Maven file.

## Limits

- Lombok members (`@Getter`, `@RequiredArgsConstructor`) are not invented.
- Overload arity is not part of the id. A call binds the shared method node. When those overloads return different types, a chain stops: there is no single type for the next call.
- A receiver whose type is not in the project does not fall back to a name match.
- An inferred lambda parameter (`xs.forEach(x -> x.line())`) has no type, so that call falls through to a unique-name match and can edge every project method of that name.
- A field chain `a.b.c()` binds `c` when each field's type is a project class. A call chain does the same from return types.
- Gradle `build.gradle.kts` and `settings.gradle.kts` are Kotlin sources. The Kotlin plugin already treats `.kts` as Kotlin, so a mixed tree counts those scripts as Kotlin, not Java.
- Android XML layouts are not read. See [Kotlin](kotlin.md) for the Kotlin side of a mixed repo and the shared scip-java run.
- Spring WebFlux functional routes (`RouterFunctions.route()`), reactive repositories beyond the forms above, Micronaut, Quarkus, Jakarta servlets, and JDBC / `JdbcTemplate` raw SQL are not extracted.
