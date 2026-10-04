# Changelog

All notable changes to code-graph are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, minor releases may change
commands, output and the graph schema; such changes are listed under **Changed**.

## [Unreleased]

### Changed

- Kotlin receiver types (#96): a function's locals (`val repo = OrderRepo()`, `val api: OrdersApi = ..`),
  `by lazy { OrderRepo() }` properties and test doubles (`mockk<AuthSdkSource> { .. }`, `spyk<..>`, `mock<..>()`)
  type their receivers, and an initializer is read only after its own `=` (`val same = a == Foo()` and a `= X(..)`
  inside a `mockk<T> { }` block no longer type the property). bitwarden-android: 1,729 `heuristic` name /
  candidate edges out (mostly a mocked call fanned out to every class with that method name), 201 in, `recv`
  3,407 -> 4,467; a random 20 of the edges added checked against the source, 20 correct. ktor-samples: 22 name /
  candidate edges out, 2 in (the postgres `ArticleService` and kweet `DAOFacade` calls bind through their types); KaMPKit:
  `viewModel by lazy { BreedViewModel(..) }` drops the wrong `BreedRepository.refreshBreeds` candidates;
  nowinandroid unchanged.
- Dispatch narrowing (#96): `impact` / `tests` / `reaches` on an override `Sub.method` leave out the calls into
  the base method it overrides whose receiver type is known and cannot be a `Sub`, as for an inherited spec (note
  `Sub.run overrides Base.run (its callers narrowed to Sub: ...)`, JSON `override_narrowed`). Python receivers
  through an unannotated factory whose returns are all one project class (`def make(): return B()`; `return
  None` aside, both arms of `A() if x else B()` considered) now have a type. flask / httpie unchanged; pip +3 and
  beets +4 edges through factories (checked correct); openai-agents-python +92 (test helpers such as
  `_agents_client()` returning the Vercel sandbox client) and -2 name-only `heuristic` test calls on a receiver that
  now has a type.
- Callbacks passed as JSX props stay untargeted (#96, decided): a function-typed `Props` property is a member node
  only when a class or object literal implements the interface; `docs/limitations.md` explains why.
- TypeScript mixins (#96): in `class Client extends mix(Base).with(Users, Posts)` (or `Users(Posts(Base))`) merged
  with `interface Client extends UsersMix, PostsMix`, the members of the mixin class expressions
  (`const Users = (b) => class extends b {..}`) implement the merged interface's members (`IMPLEMENTED_BY`,
  `resolved`, `via: mixin`), and those interfaces' function-typed properties become member nodes, so calls on the
  client get a target. mattermost-mobile: 317 mixin implementations (all 215 REST client members, plus the database
  operator handlers), +307 members, +419 calls, +1,258 test calls; other repos unchanged.
- TypeScript object literals as interface implementations (#96): an object literal where a project interface or
  object type alias is expected (a typed variable, a factory's return value, an argument, `satisfies`) links its
  function members to the interface's with `IMPLEMENTED_BY` (`exact`, `via: object_literal`), and the interface's
  function-typed properties become member nodes, so calls through them get a target. A class passed as a value
  where a constructor of the interface is expected (`register(Following)`) gets structural `IMPLEMENTED_BY` as for
  `new X()`. social-app IMPLEMENTED_BY 34 -> 111 (+36 members, +27 calls, +39 test calls); mattermost-mobile
  0 -> 46 (the keyboard state machine's `guard` / `action` transitions, the resume-gate strategies; +105 test
  calls); immich server 5 -> 9. No edges lost (8 REFERENCES_TYPE / 1 TEST_USES moved from interfaces to their
  members).
- Swift availability against the deployment target (#100): the lowest deployment target per OS is read from
  Xcode build settings (`.pbxproj`, `.xcconfig`, XcodeGen YAML), XcodeGen `deploymentTarget:` and `Package.swift`
  `platforms:` (index stats `deployment_targets`). An `@available` / `#available` requirement it already meets always
  holds, so the declaration or branch gets no `attrs.available` (declarations keep `available_declared`), and a check
  met on every OS it names is listed as `availability_always_true` (file, line, requirement, target).
  IceCubesApp (iOS 18): 6 `#available(iOS 17.4, *)` checks always true. element-x-ios: lowest iOS target 16.0 (a
  bundled package), nothing met.
- Generated Swift (#100): files with a Sourcery, SwiftGen, swift-openapi-generator or Mockolo banner, and R.swift's
  `R.generated.swift`, are classified as generated. element-x-ios: 9 files (6 Sourcery, 3 SwiftGen); the other
  Swift repos surveyed have none.
- Generated test code (#100), any language: a generated file under a test folder or test-target directory (`test/`,
  `tests/`, `__tests__/`, `spec/`, `androidTest/`, `*Tests/` ...) runs, so by default it stays in the graph as test
  code with `attrs.generated` (`test: true`). Only generated non-test sources leave the graph. `cg coverage` lists
  the kept files apart (`N generated test files indexed as tests`); a `.cg.yaml` `generated.paths` glob still
  excludes anything. element-x-ios: the Sourcery preview and accessibility test lists (3 files, 479 test cases) are
  back; the Sourcery mocks and SwiftGen files in the app target stay out.
- Swift previews run as tests (#100): `X_Previews._allPreviews` / `X_Previews.previews`, and in a test file a string
  naming a preview provider (`performAccessibilityAudit(named: "X_Previews")`), call `X_Previews.previews`, so
  `cg tests` reaches the views the previews build. element-x-ios: 480 edges to 249 preview providers;
  `cg tests AppLockScreen` 0 → 2 direct tests.

- `cg platforms divergence` (#91): references from test code whose platforms are only the project default are
  listed apart, as `missing_callee_tests` (FROM TEST CODE WHOSE PLATFORMS ARE THE PROJECT DEFAULT, with
  `platform_source`). The test code must have no `#if` and no test-target membership narrowing it. An unguarded
  call into code excluded on a platform means the test target isn't built there. `--kind missing_callee_tests` and
  the MCP `platform_divergence(kind=...)` select them.
  - Kingfisher: missing_callee 36 → 1, plus 35 test findings listed apart. libuv: 32 → 29, plus 3 (`test/`).
  - Non-test findings are unchanged. A test target with its own `SUPPORTED_PLATFORMS` was already narrowed by
    Xcode target membership (#74).

### Fixed

- Kotlin (#104): a file that tree-sitter-kotlin cannot parse is re-parsed member by member. Each top-level
  declaration and class / object / interface member is parsed alone inside the file's skeleton, and only a member
  that still errors is dropped (and reported as lost in `cg coverage --details`). Before, one unsupported construct
  (a parenthesized function type with a receiver, a `$$"..."` string, a context parameter) often swallowed the rest
  of the class or file. A suspend lambda that starts a statement (`suspend { ... }.runCatching(state)`) now parses
  too.
  - element-x-android: declarations lost to parse errors 193 → 67; 36 files re-parsed, 48 members dropped; nodes
    36,998 → 37,172, edges 125,864 → 126,774. Methods misread as top-level functions (`function:….present`) and
    locals misread as constants are now the class's methods or gone.
  - bitwarden-android and tauri (errors in class headers) and the clean Kotlin repos are unchanged.

- Kotlin properties (#105): `count++`, `--cart.count` and `cart.count += 1` count as a read and a write. A property
  node with a setter or delegate gets one `CALLS` edge with `property: read_write` (before: `read` for `++` / `--`,
  `write` for `+=`; `--c.n` was missed, since it parses as `(--c).n`); a stored property gets both `READS_PROP` and
  `WRITES_PROP`. A `var x by remember { }` local now shadows a property named `x`, and `{ x = 1` / `f(x)` no longer
  hide the property reads and writes they are. Property `CALLS` / `TEST_CALLS`: bitwarden-android +116 (104 reads,
  12 writes), nowinandroid +50, KaMPKit +2 and `calledCount++` read → read_write; none lost. Stored property refs:
  +6 / +3 (bitwarden-android / ktor-samples).

- Kotlin (#104): Ktor routing such as `val x = ...` followed by `get("/path") { }` no longer parses as that
  property's getter, and a call or function named `dynamic` no longer hits the Kotlin/JS type keyword. Both errors
  swallowed the enclosing function. The parsed copy is rewritten at the same length, so lines, offsets and names stay
  as written; the index stats count `accessor_like_calls_rewritten` and `keyword_named_calls_rewritten`.
  - ktor-samples: files with syntax errors 3 → 0, nodes 1,481 → 1,504 (7 more routes; `FileInfo`, `listSuspend`,
    the openapi sample's `User` and `configureRouting`). Other Kotlin corpora surveyed are unchanged.

- C / C++ heuristic mode (#92): a call binds to a `static` (usually `static inline`) function or static variable
  defined in a header the calling file includes, directly or through other headers. Before, only the calling file's
  own statics were candidates. libuv: `uv__queue_insert_tail` 0 → 35 incoming CALLS; CALLS 26,016 → 26,378.
  Three Windows calls of `uv__stream_init` now bind to the `src/win/stream-inl.h` inline instead of the Unix
  definition, and divergence missing_callee goes 35 → 32. No other edge is lost. Exact (clang) mode is unchanged.

## [0.10.0] - 2026-10-04

Stored properties as `field` nodes with `READS_PROP` / `WRITES_PROP` edges in Swift, Kotlin, Python, TypeScript /
JavaScript, React / Vue state and PHP, with `cg readers` / `cg writers Type.prop` (#88 phase 1). Two heuristic checks
built on them: `cg roundtrip` (#88 phase 2) and `cg lint async-state` (#88 phase 3). Also: Swift value navigation
and `URLComponents` endpoints (#68), Socket.IO direction and Nest guards in protocol links (#69), MCP servers built
inside functions (#76), Python external-system clients and LLM providers (#77), the Kotlin suspend-lambda parse fix
(#81), Kotlin property accessors as nodes (#89), plain JavaScript packages from `package.json` (#94), opt-in
`cg parity --structure` (#93), `cg parity` own-platform symbols (#107), and web view work (#82).

**Upgrade notes**

- New field nodes. Re-index to get them; graphs grow noticeably. Examples: IceCubesApp 2,392 Swift fields, Element X
  8,462 Kotlin fields, koel 1,218 PHP stored properties. In detail:
  - Swift, Kotlin, Python and TypeScript stored properties, plain JS `this.x` fields (`declared: this`), React
    `useState` / `useReducer` and Vue `ref` / `reactive` state (`property: state`), Pinia options state
    (`hook: pinia`) and Vue `data()` keys (`hook: data`) are `field:<Type>.<name>` nodes.
  - PHP declared and constructor-promoted properties are `property:Class::$x` nodes with `property: stored`.
  - The `field` kind already existed for other languages. Kotlin properties with a custom `get()` / `set()`,
    `by lazy` or a delegate are `method:` / `function:` nodes with `kotlin_kind: property` (#89).
- New edge kinds `READS_PROP` and `WRITES_PROP`, from the accessing function to the field. Their attributes:
  - `receiver` and `accessor` on reads and writes;
  - `via` on writes: `mutating`, `inout`, `binding`, `item`, `value`, `setter`, `copy`, `compound`, `unset`;
    `via: keypath` on a read;
  - `storage` on Swift `_x = State(...)` writes, `binding: name` on heuristic binds;
  - field attributes `wrapper` and `key` (`@AppStorage`).
  On the corpora checked for each language, the edges that existed before are unchanged.
- New or extended attributes on existing edges:
  - `INSTANTIATES` and JSX `RENDERS` carry `branch` / `branch_line` (the enclosing switch case / if / else / guard /
    ternary).
  - Kotlin calls inside property accessors come from the accessor node (`accessor: get | set | lazy | delegate`), and
    property reads / writes are `CALLS` with `property: read | write` (#89).
  - MCP endpoints built inside a function carry `nested_in` / `handler_name` (#76).
  - Python model calls go to new `external:llm:<provider>` nodes (#77).
- New commands and MCP tools:
  - `cg readers Type.prop` and `cg writers Type.prop` (MCP `readers`; `writers` also takes `Type.prop`);
  - `cg roundtrip Type.prop` (MCP `roundtrip`);
  - `cg lint async-state [--rules ...]` (MCP `lint_async_state`);
  - `cg parity --structure` / `--write-map` (#93, off by default).
  - `.cg.yaml` gains `lossy:`. Roundtrip and lint findings are labelled `heuristic`, and nothing they find is added
    to the graph.
- Output that changes for existing projects:
  - Plain JS packages index the `package.json` entry dirs and `bin` scripts (#94); eslint nodes go from 4,929 to
    8,995.
  - Socket.IO links follow the emit direction, and the per-protocol link stats count `external` (#69).
  - `cg parity` stops reporting the source app's own platform as `platform_only` (#107).
  - `impact` and `cg tests` follow functions reached only through a Kotlin property (#89).
- The cache version stays 3. The TypeScript and Dart fact caches include the extractor code, so the first index
  after the upgrade re-extracts those projects anyway.

### Added

- Python stored attributes (#88): `self.x = ...` and annotated class-level attributes (dataclass / pydantic) are
  `field:<Class>.<attr>` nodes.
  - Accesses through an inferred receiver type are READS_PROP / WRITES_PROP.
  - `self.items.append(x)` is a write with `via: mutating`; `self.cache[k] = v` is a write with `via: item`.
  - All other edges are unchanged on flask, httpie, beets, netbox and openai-agents-python.
- TypeScript stored class fields (#88): class properties and constructor parameter properties are `field:` nodes,
  with checker-resolved READS_PROP / WRITES_PROP edges. `via` is `mutating` or `item`, as for Python.
- React / Vue state (#88): `useState` / `useReducer` and Vue `ref` / `shallowRef` / `reactive` variables are
  `property: state` field nodes of their component / composable.
  - References are READS_PROP.
  - `setX(...)`, `x.value = ...` and `state.p = ...` are WRITES_PROP, with `via` = setter / value / property /
    mutating.
- Kotlin stored properties (#88): a class / enum body `val` / `var` without accessors and a constructor `val` /
  `var` are `field:<Type>.<name>` nodes.
  - `x` / `this.x` and `v.x` with a known type of `v` are READS_PROP / WRITES_PROP edges.
  - `items.add(x)` and similar calls are writes with `via: mutating`; `_state.value = x` is a write with `via: value`.
  - `cg readers` / `cg writers` and the MCP tools cover them.
  - Corpus results: nowinandroid 469 fields; Element X 8,462 fields, 8,626 reads, 311 writes; Bitwarden 9,080
    fields. All other edges are unchanged on five Kotlin corpora.
- Swift stored properties: in-place mutations are now writes (#88). These are WRITES_PROP edges with `via`:
  - `mutating`: `items.append(x)`, `flag.toggle()`, or a project `mutating func` called on the field.
  - `inout`: `&x`.
  - `binding`: `$x`.
  A key path `\Type.x` is a READS_PROP with `via: keypath`. `@AppStorage("k")` and `@SceneStorage` fields carry `key`.
  An in-place mutation of an observed property also calls its `didSet`, and on a lazy var it still calls the initializer.
  IceCubesApp: 136 mutating, 239 binding and 2 inout writes, 14 key paths, 54 storage keys.
- `cg roundtrip Type.prop` (#88 phase 2): a heuristic check for state that round-trips through a lossy transform.
  - It lists each write through a lossy call and each read that seeds UI state, and pairs them with any wider range
    drawn next to the read.
  - Lossy calls are the built-in clamp / min / max / round / truncating casts / `fit*` names, `.cg.yaml` `lossy:`
    names, and `@cg-lossy` functions. Data flow covers the statement, an earlier local, and one hop through callers.
  - Seeds are init / constructor, onAppear / .task, remember, useState(initial), mounted and State(initialValue:).
  - It has text, `--json` and MCP `roundtrip` output; findings are labelled heuristic and nothing is added to the
    graph.
  - Fixtures cover Swift, Kotlin, React and Python. On outline, Element X, Bitwarden, social-app, isowords,
    Alamofire, IceCubesApp and elk it finds 14 lossy writes and 1 round trip.
  - The hop through a caller only looks at the argument passed for the parameter, so `rooms[Int(i)] = Room(r)` no
    longer marks `Room`'s fields lossy.
  - A read is not a read-back when the writer uses what it just built a few lines later, when the property holds a
    callback, or when the property is only used as an index. With that, mattermost-mobile, koel, solidtime,
    Bitwarden iOS, elk and nowinandroid have 38 lossy writes and 0 round trips (5 false positives removed). Element X
    iOS has 6 lossy writes and 2 round trips. One is plausible: a permission level that is `max` of two power levels
    seeds an editable setting. The other is an `Int(...)` count conversion. The Element X
    round trip above (a harmless `PdfPage.renderHeight`) is gone too.
- `cg lint async-state` (#88 phase 3), four rules (`--rules` selects a subset). Rule `stale-async-result` flags an awaited result written to stored
  or UI state with no cancellation check and no token / ID / generation comparison between the await and the write.
  - The async blocks covered are Swift `Task` and async funcs, Kotlin `launch` / `async`, React `useEffect`, JS / TS
    `async` functions and `.then`, and Python `async def`.
  - It has text, `--json` and MCP `lint_async_state` output; findings are heuristic.
  - The awaited request must depend on an input (a parameter, prop, state or loop variable). A block wrapped in a
    single-flight helper (`bundleAsync`, `dedupe`, `debounce`, `throttle`, `once`) is skipped.
  - Fixtures cover Swift, Kotlin, React and Python. Hand-checked precision is 8 of 12 (67%): IceCubesApp 4 of 5,
    outline 2 of 3, social-app 2 of 4. Element X, Bitwarden and isowords have no findings.
  - Rule `two-writers`: state written with real values both by lifecycle code (init, onAppear, `.task`,
    useEffect, LaunchedEffect) and by async code after an await or a completion callback. Defaults and flag resets
    are skipped. Hand check: IceCubesApp 3 findings (1 clear true positive, plus 2 like / bookmark flags re-seeded
    on appear); social-app 2 (1 true positive); outline 1 (1 true positive). That is 3 of 7 strictly, 5 of 7
    counting the flags.
    - Tightened after a wider sample: constructor injection (`self.x = x`, `State(initialValue:)`) is not a
      lifecycle writer, and an async write with the same expression as the lifecycle one, or derived from the state
      itself, is skipped, as are `&x` / `.store(in:)`. On 14 corpora (the ones above plus nowinandroid, Element X iOS,
      mattermost-mobile, koel, Bitwarden iOS and solidtime) it has 8 findings: 4 true positives, 3 borderline and
      1 false positive (50% strictly, 88% counting borderline). Before tightening it was 4 of 20 strictly.
  - Rule `incomplete-cache-key`: a store into a cache / memo / LRU (`set`, `put`, `setObject(_:forKey:)`, `c[k] = v`)
    whose key leaves out a parameter or instance field that the cached value is computed from, following locals and
    string interpolation. Pass-through setters (`put(key, value)`) and payload parameters are skipped. It has 0
    findings on the 14 corpora, after 14 false positives were fixed during development.
  - Rule `echo-suppression`: a guard field (`isApplyingRemote`, `lastSent…`, `skipNext…`, `suppress…`) checked with
    an early return in an observer and set around writes of some state. It reports writes of that state from async
    code or a callback that do not set the guard. On the 14 corpora it found 1 guard pattern (a presence
    de-duplication) and 0 findings.
- Kotlin stored-property refs no longer treat `{ x = …` (an assignment opening a body) or `f(x)` (an argument) as a
  local `x`. Element X gains 1,149 field refs and Bitwarden 418; all other edges are unchanged.
- Kotlin `copy()` guesses (`binding: name`) are `heuristic` in the edge confidence and in the web view (dotted
  edges, legend counts); a known receiver is `resolved`. A test covers both.
- #88 phase 1, remaining items:
  - JSX `RENDERS` and `new X` `INSTANTIATES` edges carry `branch` / `branch_line` (switch case, if / else, ternary,
    `&&`). Kotlin composable calls and constructor calls do the same inside `if` / `else` / `when` entries.
  - Plain JS classes: `this.x = ...` without a declaration is a field (`declared: this`). commander.js has 88.
  - Pinia options store state (`hook: pinia`) and Vue Options API `data()` keys (`hook: data`) are field nodes.
    Reads and writes come from `this.x`, `const s = useStore(); s.x`, and `useStore().x`.
  - Kotlin data class `copy(x = v)` is a write with `via: copy`. It binds `resolved` with a known receiver type,
    otherwise to the single data class with every named field (`heuristic`, `binding: name`).
  - Swift: `items[i] = v` is a write with `via: item`, and `items[i]` is now a read. A key path with an inferred
    root (`\.items`) binds by a unique stored-property name (`heuristic`). A subscript on a computed property is
    now a CALLS edge with `property: read`, as other computed-property reads are.
  - PHP class properties: declared and constructor-promoted properties are `property: stored` nodes.
    `$this->x` (exact) and typed `$v->x` (resolved) reads and writes are READS_PROP / WRITES_PROP, with `via` =
    compound / item / unset. koel: 1,218 stored properties (852 promoted). laravel.io: 204.
- Visual view: edge confidence by dash pattern and width as well as colour (exact solid 2 px, resolved dashed,
  heuristic dotted), with every edge and border colour at least 3:1 on the canvas (checked by a unit test), and
  legend chips that hide / show resolved and heuristic edges client-side with the status counts updated (#82 item 7).
  The legend lists only the kinds (with counts), shapes and edge styles present in the view, and a click on a kind
  dims everything else (#82 item 8).
- Visual view: the URL hash is written as you select a node, open clusters or change the layout, a **copy link**
  button copies it, and each new query is a history entry so Back returns to the previous one (#82 item 11).
- Visual view (#82 items 9, 10, 12-15): the panel opens on the target with a view summary, has a sticky header
  with copy buttons (id, FQN, CLI), *open in editor*, node actions (impact / downstream / path; also a right-click
  menu), grouped and collapsible evidence edges, a wrap toggle, and can be resized or collapsed. Keyboard shortcuts
  (`/`, `f`, `+` `-` `0`, arrow keys along the edges, `Enter` / `Space`, `l`, `?`). PNG (2x) and JSON export. Dark
  theme (follows the system, or the theme button). Node colours are eight colour-blind-safe families with shapes per
  kind; a list view of the subgraph; `role="img"` summary on the canvas; axe-core clean. fcose refines kept
  positions after an expand / collapse, `pixelRatio` is capped at 2, a truncated result shows a banner, and
  `shoot.mjs` records and enforces performance budgets.

- Swift stored properties and construction branches (#88, phase 1): a stored instance property is a
  `field:<Type>.<name>` node (`binding`, property `wrapper`), with `READS_PROP` / `WRITES_PROP` edges from `self.x`,
  bare `x` and `v.x` with a known type of `v` (`storage: wrapper` for `_x = State(...)`); new `cg readers Type.prop`
  and `cg writers Type.prop` (also MCP `readers` / `writers`). An `INSTANTIATES` edge inside a `switch` case / `if` /
  `else` / `guard` / ternary carries `branch` and `branch_line`, in exact mode too. IceCubesApp: 2,392 fields,
  5,506 reads, 1,373 writes, 277 of 686 constructions with a branch; call edges are identical.

- Swift value navigation (#68): the views per case of `navigationDestination(for: Route.self) { switch ... }` are
  `NAVIGATES_TO` targets of `NavigationLink(value: Route.x(...))` and of `navigate(to:)` / `push` / `append` on a
  router or navigation path (`routerPath.navigate(to: .x)`). IceCubesApp: pages 21 → 36, NAVIGATES_TO 21 → 92.
- Swift URLSession calls whose URL is built with `URLComponents` (`URLComponents(string:)` + `.path`, or `.scheme` /
  `.host` / `.path` set one by one) are HTTP_CALLS to that URL (#68).

- MCP servers built inside a function (#76): nested `@mcp.tool()` / `resource` / `prompt` handlers in a factory
  function or a test body are endpoints received by the enclosing function (`nested_in`, `handler_name`), and
  low-level `@server.call_tool()` handlers resolve enum-member branches (`case GitTools.STATUS:`) and servers built
  inside `async def serve()`. python-sdk: MCP endpoints 584 → 955, handled tools 124 → 162, unmatched client calls
  (`no_receiver`) 30 → 10; modelcontextprotocol/servers (Python): 0 → 12 git tools.

- External systems (#77): Python client constructors with an address in their arguments (`psycopg2.connect(host=)`,
  `redis.Redis(host=)` / `from_url`, `smtplib.SMTP_SSL`, `pymongo.MongoClient`, `ldap3.Server`, `boto3` S3
  `endpoint_url` ...) are CONNECTS_TO from the calling function; model calls become `external:llm:<provider>` nodes
  with their models and API-key env var; `impact external:...` / `impact table:...` list the code using the system
  instead of "no recorded callers".

- Kotlin properties that run code are nodes (#89): custom `get()` / `set(value)`, `by lazy { }` and delegated
  properties become `method:<Type>.<name>` / `function:<package>.<name>` (`kotlin_kind: property`); calls inside
  them come from that node (`accessor: get | set | lazy | delegate`), and reads / writes are `CALLS` edges with
  `property: read | write` on the method-call receiver rules. `impact` and `cg tests` follow a function reached only
  through a property.

- Plain JavaScript packages without `src/` / `app/` or a config file list (#94): the `package.json` `main` /
  `module` / `bin` / `exports` / `files` entries name the source dirs, and a `bin` script outside the source dirs is
  a source file, so tests that run the CLI in a subprocess link to it. eslint: `lib/` and `bin/` were not indexed
  at all (nodes 4,929 → 8,995, no existing node or edge lost); 60 tests now reach `lib/cli.js` `execute` through
  `bin/eslint.js` (`script_without_node` 1 → 0). node-express-boilerplate: its `bin/createNodejsApp.js` beside `src/` gains a node (+5 nodes, nothing
  lost); capacitor unchanged. A root `jsconfig.json` alone enables the TypeScript plugin.

- `cg parity --structure` (#93): symbols still missing after the name rules are paired by shared localization keys,
  string literals, endpoints, called member names and already-paired callees (IDF-weighted, mutual best, role and UI
  checks), and word tail / head rename rules learned from the non-exact pairs are applied to the rest. Inferred matches
  carry `score` / `evidence` or the rule and are listed apart; `--write-map` writes them as a `--map` file. Off by
  default, so the existing output is unchanged. Bitwarden: 101 / 124 inferred matches per direction, Element X
  Android → iOS 91. The false "missing" rate in random samples is not lower yet (see docs/parity.md).

### Fixed

- Web view: the first fit shows every node. Before, a wide drawing was zoomed to the target and its neighbours,
  which cut the leftmost column off (IceCubesApp `impact MastodonClient.get`). With no cluster open, the layer gaps
  now narrow (down to 170 px) to fit the canvas. When labels would still be under 11 px they hide, and a
  "readable zoom" button zooms to the target. An opened or folded cluster stays where it was on screen.
  `shoot.mjs` fails when a node is outside the canvas on first load (#82).
- Visual view: a deep impact view no longer grows past 30 top-level items. The layers share one budget, and the
  widest fold further (IceCubesApp `impact MastodonClient.get`: 44 → 28 items, `post` 32 → 29; #82 item 6 / 15).
- `cg parity` (#107): symbols tagged with the source app's own platform (one that tags at least half of the compared
  symbols) are no longer `platform_only` when the target graph does not build it. Element X iOS → Android:
  platform-only 3,551 → 0, missing 209 → 3,760 of 4,184. Bitwarden output unchanged in both directions.

- Kotlin (#81): a suspend lambda used as an expression (`val b = suspend { 1 }`, `X to suspend { ... }`) no longer
  breaks the parse and drops the enclosing class's functions; ktor-samples: 34 more `@Test` functions (162 → 196),
  files with syntax errors 6 → 3. No change on nowinandroid, KaMPKit, spring-petclinic-kotlin.
- Protocol links (#69): Socket.IO matching follows the direction (a client `emit` reaches server handlers, a server
  `emit` client handlers), so a client handler no longer counts as the receiver of a client emit; the index and
  `cg link` per-protocol stats apply `.cg.yaml` `protocols.external` (new `external` count) as `cg protocols` does.
- Nest microservice / gateway / gRPC handlers record their guards (`@UseGuards` on the handler or class, `APP_GUARD`
  providers), and `cg protocols` checks `unguarded` for `nest-rpc`, `nest-event`, `nest-ws` and `grpc` (#69).

## [0.9.0] - 2026-10-03

Enum cases and constants as nodes with `USES_VALUE` references (#84), `cg parity` port gap reports (#85), scoped
`cg tests` (#87), Swift `#if` type variants and SwiftPM module visibility (#86, #90), Apple platforms from the Xcode
project (#74), CLI output cleanup (#75), Python host-aware routes (#59), tests that run programs in a subprocess
(#60), more bridges (#61), receiver-aware dispatch in Kotlin / Swift / Dart / PHP (#62), fewer platform divergence
false positives (#63), packaging housekeeping (#65) and Kotlin navigation constants (#67).

**Upgrade notes**

- The cache version goes from 2 to 3 (#84): the first index after the upgrade re-runs every indexer instead of
  reusing cached results. `cg clean --stale` removes the version-2 entries.
- CLI output changed (see **Changed**): `cg coverage` prints a summary by default (`--details` for the full
  report); `cg stats` and `cg node` print text, with one JSON document under `--json`; `cg search` paths are
  root-relative; `cg tests` scopes and sections its list; `cg platforms divergence` adds a "not listed" line and
  the counts JSON gains `missing_callee_skipped_no_target`; `cg doctor <root>` adds `project:` lines. The JSON that
  `cg index` prints and the `cg link --report` Markdown are unchanged.
- Graph ids: Python routes registered for a host or subdomain carry ` @<host>` in their id (#59); a Swift type
  defined in a second `#if` branch is `class:T@<line>` (#86).

### Added

- Enum cases and constants (#84): Swift, Kotlin, TypeScript / JavaScript, Python and PHP index enum cases as
  `enum_case` and constants as `constant` nodes (CONTAINS from their type or module), with `USES_VALUE` edges where
  the binding is certain (`Type.case`, `.case` with a known contextual type, `Type.NAME`, imports, `self::NAME`, ...).
  Rust and C / C++ keep their kind names; docs/schema.md maps them. `cg coverage --details` gains a `values:` line
  (`values` in `--json`); the web view gets enum_case / constant kind chips.
- `cg parity --db SRC --against TGT` (#85): the types, functions, enum cases and constants of one graph with no
  counterpart in another (an iOS app and its Android port), grouped by folder, with explicit / exact / normalized /
  fuzzy / moved match confidence, a `--map` rename file and `--strip-prefix`. docs/parity.md.
- Python web routes (#59): host-aware route ids for Flask `host=` / `subdomain=` and Starlette `Host` routes, `<path:p>`
  / `{p:path}` as `{p*}`, test linking narrowed by request host and by the routes a test registers,
  `app.dependency_overrides` on `TEST_HTTP` edges, `Depends(Class(...))` reading `__call__`, `app.add_middleware`,
  Flask-Classful views and registrations in loops over literal lists.

- Kotlin (#67): Compose Navigation routes held in string constants (`composable(Destinations.TASKS_ROUTE)`, constants
  inside route and `navigate("${Screens.TASKS}/$id")` strings) become pages and `NAVIGATES_TO` edges; `cg doctor
  <root>` lists the installed scip-java releases with their Kotlin ranges and the one that fits the build;
  `install.sh --with kotlin` also installs the scip-java 0.13.1 launcher (Kotlin 2.2.0 - 2.2.10); `install.ps1` says
  how to get it on Windows (WSL, or `--scip`).

- Packaging follow-up (#65):
  - `cg setup --prune [--dry-run]` removes extractor installs of an older lock file; installs into one directory take
    `<dir>/.install.lock`, so concurrent first runs do not run npm / composer / dart pub there twice.
  - `cg doctor <root>` prints `project:` checks: root or per-package tsconfigs, the Gradle / Maven build file and
    Kotlin version (Android modules named), a root `Package.swift` or the Xcode projects needing an index store.

- Tests that run the project's programs in a subprocess (#60):
  - Rust `CARGO_BIN_EXE_x` / `cargo_bin("x")`, Node `child_process` / `execa`, PHP `Process` / `exec` running
    `php artisan x`, and Dart `Process.run` / `TestProcess.start` link to the entry point (`codegraph/process_runs.py`).
    Laravel feature tests' `$this->artisan('x')` link to the command.
  - Python: argument lists built with `append` / `extend` / `insert` / `+=`, installed runners (`scripttest`,
    `pytester`, `sh`, plumbum), `-c` snippets that only import a project module, and scripts copied from a project
    file or template (`manage.py-tpl`) before they run.

- Bridges (#61): React Native native events (`RCTDeviceEventEmitter.emit` / `sendEvent` / `sendEventWithName` / Expo
  `sendEvent` to `NativeEventEmitter` / `DeviceEventEmitter` `addListener`, `endpoint:react-native-event:<event>`),
  Capacitor plugin events (`notifyListeners` to `Plugin.addListener`, `endpoint:capacitor-event:<Plugin>#<event>`),
  and Cordova plugins (`cordova.exec` to `CordovaPlugin.execute` actions / `CDVPlugin` methods, services from
  `plugin.xml` / `config.xml`, `endpoint:cordova:<Service>#<action>`). Bridge calls with a dynamic module / method /
  event name are listed by `cg bridges` as `unresolved` (`stats.bridges.dynamic`). `cg bridges --protocol` accepts
  every bridge protocol.

- Dispatch (#62): Kotlin, Swift, Dart and PHP calls that land on an inherited method record the receiver's class
  (`attrs.recv`), so `impact` / `reaches` / `tests` on `Sub.method` leave out calls on sibling subclasses as they
  already did for TypeScript and Python. `overrides:` / `overridden by:` lines show the file when two declarations
  share a name (two `FeedAPI` interfaces).

- Platforms (#63): fewer false `cg platforms divergence` findings. Explicit platform-file imports (`from './X.ios'`)
  no longer link the sibling variants; platform test files (`x.web.test.ts`) are tagged with their platform; Swift
  calls bound by name to a project initializer on an SDK type, C functions of separate programs (files with their
  own `main()`) and references to code built for no declared target are not reported as missing callees.

### Changed

- `cg tests` and the MCP `tests_covering` (#87) keep transitive tests near the target: `--max-depth` (default 3)
  hops through application code, UI / snapshot tests in their own section (`--unit-only` leaves them out), tests
  through app roots (`@main`, Kotlin `*Activity`, `--exclude-root`) left out unless `--through-roots`, and a
  "not listed:" line counting what was left out.
- CLI output (#75):
  - `cg coverage` prints a short summary by default: one line per language that isn't fully indexed, syntax error
    counts, files per platform target and blind spots. `--details` gives the full report, which is what `--all-files`
    also prints. `cg index` shows the same summary on stderr.
  - `cg stats` and `cg node` print text, and with `--json` one JSON document. Before, they mixed JSON and text on
    stdout. `node` lists an edge from the same site once, with a count.
  - `cg search` prints root-relative paths.

### Fixed

- Swift `#if` variants (#86): a type defined once per `#if` / `#elseif` / `#else` branch keeps each definition as
  its own node (`class:T@<line>`) with its own members, instead of dropping or merging the second; `#if` conditions
  with parentheses and `!( ... )` are parsed as written; a member a variant gets from a protocol requirement or from
  its SDK superclass is not reported missing. Kotlin common code binds to the `expect` class, and per-platform test
  source sets carry their platform. Divergence missing_callee: Kingfisher 148 → 37, SwiftUIX 220 → 42.
- SwiftPM module visibility (#90): code in a SwiftPM package target binds only to its own module and its dependencies
  (`plugins/swift/packages.py` reads every local `Package.swift`), not to declarations that exist only in an app,
  extension, preview or test target. Index-store precision: isowords 0.938 → 0.970, Alamofire 0.964 → 0.967.
- Specs (#75): `Class.method` and `Class::method` both work in every language, and the `cg tests` help example no
  longer suggests a form that matches nothing in Swift or Kotlin. Other fixes:
  - The coverage install hint for a tree-sitter language names only the modules that are missing.
  - `detected.languages` lists every language a plugin indexed. An Xcode app with no root `Package.swift` used to
    get `{}`.
  - `impact --min-confidence resolved` on a heuristic graph says the callers were filtered by the threshold.
  - `cg routes` on a SwiftUI (or other) app with screens and no HTTP routes points to the screens.
  - A repo whose root is not a JS project (no `package.json`), with `web/tsconfig.json`, now gets its TypeScript
    indexed.

- Apple platforms (#74): an Xcode project's targets come from its `project.pbxproj` (`SUPPORTED_PLATFORMS`,
  `SDKROOT`, `SUPPORTS_MACCATALYST`), so an Apple-only app no longer gets windows / linux as "desktop default"
  targets, and SwiftPM local packages are read when there is no root manifest. tvOS, watchOS and visionOS are their
  own targets (`os(visionOS)` no longer counts as iOS; C `TARGET_OS_IPHONE` holds on all four and still names ios in
  the desktop default; a Kotlin Multiplatform project keeps its `kotlin { }` targets over its iosApp project). `#else` and negated conditions list the project's other
  targets, not every platform cg knows. In a Mac Catalyst app, `os(iOS)` and `targetEnvironment(macCatalyst)` hold on
  macos and `os(macOS)` does not; without a Catalyst build `targetEnvironment(macCatalyst)` is no target. Files only
  some Xcode targets compile (build phases, synchronized folders and their exceptions) are tagged with those targets'
  platforms. Calls into a Swift method defined per `#if` branch reach every variant (`variant_platforms`), and the
  divergence check counts any variant. IceCubesApp: targets macos (Catalyst), ios, visionos from the project; the 4
  `missing on: macos` findings for `ToolbarItems.close` are gone.

## [0.8.2] - 2026-10-03

Swift properties as graph nodes (#72), Swift sources that did not parse (#73) with a per-file syntax error listing in
`cg coverage` for every language, and the first round of the web view polish (#82 P0).

### Added

- Swift computed properties, stored properties with `willSet` / `didSet` and `lazy var`s with an initializer are
  `method:<Type>.<name>` nodes: the calls inside them come from the property (with `accessor: get | set | willSet |
  didSet`, shown by impact as `(didSet)`) instead of the type, and reads (writes, for observers and setters) of them are
  `CALLS` edges with `property: read | write`, in the heuristic and the exact (index store) layer. `impact` and
  `cg tests` now reach code and tests that go through a computed property. A Swift / Kotlin type node that calls the
  target itself (a stored property's initializer, a Kotlin custom getter) is listed by `impact` as a caller, labelled
  `(in a property)` (#72).
- `cg coverage` lists every file that parsed with syntax errors, in every language (Swift, Kotlin, Rust, C / C++,
  TypeScript, Dart, Python, PHP), with its error line spans and the declarations lost there (`--json`:
  `syntax_errors`, `syntax_error_files`, `parsed_with_errors`, `decls_lost`). An answer that involves such a file is
  marked partial and names it (#73).

### Changed

- The visual view (`serve`, `viz-export`) opens on a landing page with graph stats, a fuzzy search and the starter
  queries as cards (the presets menu is gone), draws impact, downstream and path left to right in layers with large
  caller layers folded into counted clusters that expand in place, keeps labels at 11 px or more, fits its toolbar
  from 1024 px and loads without console errors or warnings; `shoot.mjs` checks this at 1280 and 1920 (#82).

### Fixed

- Swift: tests whose suite has a `sourceLocation: SourceLocation = #_sourceLocation` default, tests between
  `#sourceLocation(...)` directives and an `@Test` inside `#if os(...)` / `#endif` are tests of their suite again
  (the last with its platform). `()` values and patterns, `@convention(c)`, `x as? T ?? y`, `if let x = try? await f()`
  and continuation lines that start with a binary operator no longer break the parse, and members after a macro the
  grammar does not know are recovered into their type. On IceCubesApp the `EditorStore` class is whole again (31
  free functions are 50 methods). Files with syntax errors: IceCubesApp 7 → 3, isowords 18 → 5, Alamofire 8 → 1 (#73).

## [0.8.1] - 2026-10-03

Fixes only: Swift and Kotlin call binding regressions from 0.8.0 (#83), bare free-function names in every language,
Python attribute inference on self-referencing attributes (#78) and hash-seed independent Laravel fallback columns
(#79).

### Fixed

- A bare free-function name (`cg tests formatPrice`, `cg impact initConnection`) resolves in every language, as
  `cg search` finds it; it matched only TypeScript, Python and Dart names before, so Swift, Kotlin, PHP, Rust and C
  free functions needed the `function:` id.
- Python indexing finishes on projects whose attributes are re-assigned from expressions over themselves (langgraph:
  did not finish in 400 s, now 14 s): attribute types are memoised per class and attribute, a cycle back to an
  attribute being inferred is unknown, and one inference has a work budget. The stats report `inference_limits`.
- Laravel graphs no longer depend on the hash seed: a property read in a `??` chain on a receiver that can be several
  models (`Income|Expense $document`) went to the column of whichever model came first in a set; it now reads one
  column per model in one step (edges `ambiguous`, `candidates`), narrowed to the tables that declare the column.
- Swift heuristic calls (#83): a prefix operator is no longer read as part of the receiver
  (`#expect(!Preview.matches(a, b))` and `if !Chrome.shouldAutoPresent()` were dropped); `Module.function()` reaches
  a free function of that target folder; a property or local built with a generic initializer (`SlotGate<Image>()`)
  types its calls; a call on a continuation line after a binary operator keeps its edge, on its own line.
- Swift heuristic calls (#83): a receiver of a known SDK type (a local `var inside = false` / `let p = Path()`, an
  initializer call `UIGraphicsPDFRenderer(bounds:).pdfData { }`, the parameter of `Path { p in }` and similar
  builders) no longer binds by name to a project method or to an extension of another concrete SDK type; a call bound
  by its selector alone carries `binding: "name"` and is left out of platform divergence.
- Swift: a `func` and a `static func` of one name in one type are separate nodes (the one declared second gets the id
  suffix `~static` / `~instance`), so each call reaches its own overload (#83).
- Swift and Kotlin (#83): a call whose receiver type is unknown and whose name fits two to five project methods gets a
  low-confidence candidate edge to each (`binding: "candidate"`, `candidates: N`) instead of being dropped; `cg tests`,
  `impact` and the MCP `callers` mark what they reach through one `(candidate)`, platform divergence leaves them out
  and `exact_vs_heuristic` counts them separately. Kotlin receivers of a library type (`Headers.build { }`,
  `client: HttpClient`) no longer bind by name, and a constructor-call receiver (`WishController().add(x)`) binds to
  that class.

## [0.8.0] - 2026-10-03

What the graph sees beyond code calling code: AI harnesses (LLM tools, MCP servers and clients, agents) and external
systems (databases, caches, brokers, mail, directories, object stores) become nodes; Swift heuristic calls match full
selectors; `cg tests` counts Swift Testing, XCTest and Kotlin tests; `cg clean` manages the cache.

### Changed

- Cache file names: SCIP outputs and their locks, rust-analyzer configs and the TS / Dart facts caches now carry the
  project key (and the TS / Dart facts the cache version) in their names, so `cg clean` can find a project's entries.
  Each project's SCIP output and TS / Dart facts are rebuilt once on the first index after the update; the entries
  written by 0.7.1 and earlier are left behind until `cg clean --stale` removes them (#80).
- The cache root is resolved the same way everywhere: the TS / Dart caches and the SCIP runner now honour
  `$XDG_CACHE_HOME` and `%LOCALAPPDATA%` like the extractors, and `$CODEGRAPH_CACHE_DIR` everywhere (#80).
- `cg tests`: the test-case total includes test functions (Swift, Kotlin), and the empty-result hint names the actual
  reason instead of "tests/ or *.spec files were not indexed" (#71).

### Added

- AI harnesses: LLM tools and MCP primitives as protocol endpoints (`endpoint:llm_tool:<name>`,
  `endpoint:mcp_tool|mcp_resource|mcp_prompt:<server>/<name>`, entry kind `llm_tool`) from FastMCP / MCPServer /
  low-level servers, MCP client calls, OpenAI / Anthropic schema literals, Agents SDK `@function_tool` and
  `Agent(tools, handoffs)` (`agent:<name>`, OFFERS_TOOL, HANDS_OFF_TO), LangChain, LlamaIndex and hand-written agent
  loops (dict registries, `if` / `match` on the name; dynamic dispatch reported, not linked), model calls recorded
  for #40; `cg tools` / MCP `llm_tools`; cg's own MCP tools are endpoints now instead of decorator references
  ([docs/ai-tools.md](docs/ai-tools.md), [#66](https://github.com/cyberchronos00/code-graph/issues/66)).
- External systems: `external:<protocol>:<target>` nodes for databases, caches, brokers, mail relays, directories,
  file-transfer hosts and object stores from Laravel connections, env keys read by code, Python settings dicts and
  URLs, `.env.example` values, docker-compose services and DSNs (CONNECTS_TO, CONFIGURED_BY, CREDENTIAL_FROM with
  the location of the secret, never its value; TLS); one node per system across linked repos; `cg external` / MCP
  `external_systems` ([docs/external.md](docs/external.md), [#40](https://github.com/cyberchronos00/code-graph/issues/40)).
- Swift heuristic member calls match the full selector (argument labels, arity) and static vs instance; SDK
  values (SwiftUI modifier chains, `Font`, `NotificationCenter.default`, `UIApplication.shared`) reach only project
  extensions and SDK selectors on untyped receivers (`contains(_:)`, `resume(returning:)`, `.accessibilityIdentifier(_:)`)
  stay unbound, so they no longer make false hubs; a labelled selector one project method declares is kept through
  optional / force-unwrapped / untyped receivers again (`region!.contains(normalized:y:)`, dropped in 0.7.1)
  ([docs/swift.md](docs/swift.md), [#70](https://github.com/cyberchronos00/code-graph/issues/70)).
- `cg tests` counts Swift Testing `@Test` functions (parameterized `@Test(arguments:)` included, with display names,
  tags, traits and `@Suite` nesting), XCTest `test*` methods of `XCTestCase` subclasses only, and Kotlin `@Test` /
  `@ParameterizedTest` functions (JUnit 5 / 4, kotlin.test, TestNG, Kotest) as test cases per framework; files
  importing `XCTest` / `Testing` are test code, Swift 6.2 raw identifiers (`` func `sums items`() ``) parse, and an
  empty answer says whether the graph has no test code, test files without recognised cases, or cases that do not
  reach the target
  ([docs/channels-and-tests.md](docs/channels-and-tests.md), [#71](https://github.com/cyberchronos00/code-graph/issues/71)).
- `cg clean ROOT` removes a project's cache entries (and those of every project indexed below ROOT), `--stale` the
  entries no cg reads again (older cache versions and layouts, orphaned `.tmp` / `.lock` files), `--all` the whole
  cache except the extractors (`--extractors` too); `--db` deletes a graph DB with its `-wal` / `-shm`, `--dry-run`
  lists without deleting, and a cache root of `/` or `$HOME` is refused. `cg doctor` shows the cache size per kind.
  Every cache user now resolves one root (`$CODEGRAPH_CACHE`, `$CODEGRAPH_CACHE_DIR`, `$XDG_CACHE_HOME/codegraph`,
  `~/.cache/codegraph`, `%LOCALAPPDATA%\codegraph`); SCIP outputs and TS / Dart facts carry the project key and cache
  version in their names, so each re-runs once after the update
  ([docs/cli.md](docs/cli.md#clean), [#80](https://github.com/cyberchronos00/code-graph/issues/80)).

## [0.7.1] - 2026-10-03

### Fixed

- Python 3.11: `cg index` failed for every language with a `SyntaxError` on Python 3.11 (the supported minimum),
  because one line of the Kotlin plugin used an f-string form that needs Python 3.12 and the indexer loads every
  language plugin. The line is rewritten; the whole package was byte-compiled and the test suite run under Python 3.11,
  and no other 3.12+ syntax or API was found. `tests/test_python_compat.py` compiles every module for Python 3.11
  on any interpreter (`ast` feature version, a tokenizer check for the PEP 701 f-string forms) and with a Python 3.11
  when one is installed.
- `cg doctor` imports every cg module: one that does not import on the running Python is listed under `cg modules:`
  with the error and `file:line`, the languages whose plugins it breaks (all of them when the indexer cannot load) are
  marked `broken`, and doctor exits with status 1 (it reported a healthy installation before). `cg index` names the
  module and points to `cg doctor` instead of printing a traceback.
- `cg doctor` reports Rust as heuristic, with `rustup component add rust-analyzer` as the fix, when `rust-analyzer`
  on PATH does not run (an empty rustup proxy) instead of exact; tools that do not run are marked `[does not run]`.
- `cg --help` describes every command (index, detect, path, reaches, siblings, writers, impact, stats, node,
  downstream and api-calls had none).

### Added

- Protocol links: one endpoint model for every sender / receiver pair (`endpoint:<protocol>:<name>`, SENDS_TO /
  RECEIVED_BY, new MATCHES_ENDPOINT for wildcard and template matches at index and link time), a protocol registry
  with shared matchers (path, MQTT, NATS, AMQP topic, glob, template) and builder helpers for plugins, checks
  (no_receiver, no_sender, ambiguous, schema_mismatch, unguarded, `.cg.yaml` `protocols.external`), and
  `cg protocols` / MCP `protocol_links` over HTTP routes, Pusher channels, Nest messages, jobs, events and bridges
  with their ids unchanged; first new protocol: python-socketio / Flask-SocketIO events
  ([docs/protocols.md](docs/protocols.md), [#31](https://github.com/cyberchronos00/code-graph/issues/31)).

- Swift heuristic precision against the index store 0.78 -> 0.94 (Alamofire, isowords, vapor/template): SDK
  initializers on types the project only extends are no longer `INSTANTIATES` edges, initializer calls go to the
  overloads whose argument labels fit, and standard-library collection methods on unknown receivers are not matched to
  same-named project methods; exact mode keeps initializers declared in extensions of SDK types. Base URLs: `{baseURL}`
  resolves from base-like constants and from Info.plist keys with `.xcconfig` values (one value: api origin; one per
  configuration: `env` with `base_candidates`) ([docs/swift.md](docs/swift.md),
  [#58](https://github.com/cyberchronos00/code-graph/issues/58)).

- SCIP index health: `cg coverage` warns (`warnings`) when an imported index has occurrences but no readable
  positions, no definitions, or (Kotlin / Rust / C exact layers) no definition matched a declaration, instead of
  adding nothing silently; `cg doctor --scip FILE` (CLI and MCP `doctor(scip=...)`) checks index files
  ([docs/install.md](docs/install.md)).

- Kotlin exact mode on Kotlin 2.2 and mixed Kotlin / Java builds: the Java documents of the scip-java index the Kotlin
  plugin consumes become `java` nodes with exact Kotlin -> Java, Java -> Kotlin (file facades `AppKt.f()` included)
  and Java -> Java call / constructor edges, and `cg coverage` reports Java as `scip`. cg reads the build's Kotlin
  version and picks between installed scip-java releases (0.12 for Kotlin <= 2.1, 0.13 for 2.2.0 - 2.2.10; several
  side by side, the next one tried when the compiler plugin does not load); newer Kotlin versions keep the heuristic
  layer with a reason naming the version. SCIP 0.9 typed ranges (scip-java 0.13) are read, also by the generic `--scip`
  importer. Android modules (Android Gradle plugin) are listed and named in the coverage reason, as skipped modules
  when the rest of the build was indexed, and a build whose settings forbid project repositories gets that reason
  ([docs/kotlin.md](docs/kotlin.md#exact-mode), [#57](https://github.com/cyberchronos00/code-graph/issues/57)).

## [0.7.0] - 2026-10-03

### Added

- Packaging: `pyproject.toml` with the `cg` and `cg-mcp` commands (version from `codegraph.__version__`), so
  `uv tool install git+https://github.com/cyberchronos00/code-graph` / `pipx install git+...` install cg without a
  checkout and `uv tool upgrade codegraph` / `pipx upgrade codegraph` (releases) / `install.sh --update` update it. `install.sh` / `install.ps1`
  (install, `--update`, `--version`, `--with rust,c,kotlin,swift`, `--uninstall`; no sudo). The TypeScript / PHP /
  Dart extractor dependencies install into the user cache on first use or with `cg setup`. `cg doctor` (CLI and MCP):
  tool versions, extractor dependencies, exact or heuristic mode per language with the reason and the install command.
  `.cg.yaml` `rust.targets` controls the per-target rust-analyzer runs ([docs/install.md](docs/install.md),
  [#64](https://github.com/cyberchronos00/code-graph/issues/64)).
- Platform follow-ups: Rust exact mode runs rust-analyzer once more per target the `cfg` conditions name (up to 3,
  `CODEGRAPH_RUST_TARGETS`), so references under another target's `cfg` are exact edges (`attrs.exact_target`) instead
  of the name-based fallback. Swift `@available` / `#available` / `#unavailable` versions are recorded as minimum OS
  versions (`attrs.available`, labelled `[iOS 17+]`), `@available(*, unavailable)` as built on no target. Re-exports
  in variant files (TS `export {a as b} from`, `export {x}`, `export *`, `export const X = Y`; Dart `export ... show`
  and top-level tear-offs) count as definitions in API surface / missing-callee findings. C heuristic mode: functions
  generated by project macros (`DEFINE_GETTER(width)` -> `get_width`), definitions after a region tree-sitter cannot
  parse, and `#define`s it half-parses get nodes; a call to a function defined once per platform directory
  (`src/unix/x.c`, `src/win/x.c`) reaches every definition instead of none. A platform file re-exporting its sibling
  (`export * from './X.ios'`) is not a missing-callee finding. `#[path = "../x.rs"]` module files keep a normalised
  path ([#56](https://github.com/cyberchronos00/code-graph/issues/56)).
- Dispatch through TypeScript interfaces: interface members are nodes (method signatures; function-typed properties
  of implemented interfaces), so a call on an interface-typed value has a target, linked by IMPLEMENTED_BY to the
  implementing class members (`implements`, through base classes and interface `extends`, and structurally where
  `new X()` is used as the interface). `impact` / `reaches` / `tests` on an inherited `Sub.method` leave out the
  calls whose receiver cannot be a `Sub` (TypeScript checker types, Python inferred instances and collection
  elements, edge `attrs.recv`), with a shorter note that counts them. A method-to-method container binding (Nest
  `useClass`, Laravel `bind`) is a dispatch hop: the abstract method is shown under `overrides:`, not as a caller
  ([#55](https://github.com/cyberchronos00/code-graph/issues/55)).
- Bridges: Pigeon APIs (`endpoint:pigeon:<Api>#<method>`) from the `@HostApi()` / `@FlutterApi()` definitions: Dart
  calls on the host API (fields, variables, Riverpod `Provider<Api>`) to the Kotlin / Java / Swift implementation,
  including methods inherited from an `ImplBase` superclass, and native `@FlutterApi` calls to the Dart class
  implementing it. Native → Dart MethodChannel calls (`channel.invokeMethod("m")` in Kotlin / Java / Swift /
  Objective-C) link to the Dart `setMethodCallHandler` testing `call.method == 'm'` (endpoint `direction = to_app`,
  `platforms_sending`). TypeScript monorepos without a root `tsconfig.json` index their per-package tsconfigs as one
  program (capacitor, capacitor-plugins). A Kotlin primary constructor with a default value no longer hides the class
  from the bridge scanner.
- Python tests that run the project's programs in a subprocess link to the entry point (`TEST_CALLS`,
  `via: subprocess`): `python -m pkg.cli` (also `-mpkg`, `-Im`, `-X dev`), `python -c "<code>"` (what the snippet
  calls), script paths, the console scripts the packaging metadata declares (also via `shutil.which`), in
  `subprocess.*`, `asyncio.create_subprocess_*` and `os.system / popen / exec*`, with the argument list evaluated
  through local variables, helper return values and shell strings. CLI helpers whose program is a parameter are
  followed to their call sites through up to 5 calls (pytest's `runpytest_subprocess`, Django's `run_django_admin`).
  `CliRunner().invoke(app)` on a typer app references its commands. pytest: 0 -> 1,260 of 3,512 tests reach an entry
  point; pylint 0 -> 10; Django 0 -> 100.

- Python web routes: apps / routers received as a parameter (`def register_routes(app)`, bound through the call
  that passes a known app or the pytest fixture of that name), returned by a factory (`app = create_app()`,
  `app.mount("/admin", make_admin())`) or built from a Flask subclass defined in a function; fastapi-utils
  `@cbv` / `InferringRouter`, classy-fastapi `Routable`, flask-restful / flask-restx resources; Flask
  `MethodView` / `View` `methods`, endpoint-only `add_url_rule`, `@app.endpoint`, `view_functions[...]`, werkzeug
  `Rule` / `Submount`, `subdomain=` / `defaults=`, blueprints registered twice and the built-in static route;
  Starlette `Host` / `app.host()` as a `host` attribute instead of a path. FastAPI dependencies now record what
  they check (statuses raised, security schemes, nested dependencies), and one that rejects with 401 / 403 counts as
  an auth guard in `cg routes`. pallets/flask: 223 -> 305 of 331 test requests linked.

- Swift exact mode: the compiler's index store, read through the toolchain's `libIndexStore` (Linux included), replaces
  the heuristic call / constructor edges of every file it covers. cg runs `swift build --enable-index-store` into its
  cache for a SwiftPM package with `CODEGRAPH_SWIFT_INDEX=1` (reused while sources, manifests and toolchain are
  unchanged), or reads an existing store (Xcode DerivedData, CI) with `CODEGRAPH_SWIFT_INDEX_STORE`; a failed build
  keeps what compiled, inactive `#if` code keeps its heuristic edges (`via: "not-compiled"`), and `cg coverage` reports
  `exact` or the reason for heuristic mode. Moya `TargetType` enums give one endpoint per case plus
  `provider.request(.case)` call sites; Fluent models, migrations and `query` / `find` / `save` give tables with reads
  and writes. `@available(macOS, unavailable)` / `@available(iOS, unavailable)` are platform conditions, and calls to a
  function defined per `#if os(...)` branch reach every branch (`platform_variant_of`). Overloaded methods now own their bodies' calls in heuristic mode, and `cg coverage` skips SwiftPM /
  Xcode build directories (`.build`, `.swiftpm`, `DerivedData`, `Carthage`). Measured on Alamofire, isowords and the
  Vapor template in docs/validation.md.
- Kotlin exact mode: a scip-java index of the Gradle / Maven build (`--scip`, `CODEGRAPH_KOTLIN_SCIP_FILE`, or an
  opted-in `scip-java index` run with `CODEGRAPH_KOTLIN_SCIP=1` through the native runner cache) replaces the
  name-based call edges with compiler-resolved ones on the syntax layer's ids; `cg coverage` reports which mode ran
  and why (no build file, no JDK, scip-java missing, not opted in, run failed), index stats carry exact-vs-heuristic
  precision / recall. More Kotlin facts: Spring Data repositories and Exposed tables as `READS_TABLE` /
  `WRITES_TABLE`, `SecurityFilterChain` URL rules as route guards, typed `composable<Route>(...) { }` and
  Navigation 3 `entry<Key> { }` pages, Ktor type-safe resources `get<Res> { }`, Ktor client builder blocks
  (`client.get { url(...) }`, `client.request { method = ... }`), Retrofit base URLs per interface and from
  `buildConfigField`.
- Desktop process boundaries on the bridge endpoint model: Electron IPC (`endpoint:electron-ipc:<channel>`,
  `ipcRenderer.invoke` / `send`, `webContents.send` → `ipcMain.handle` / `on`, `ipcRenderer.on`; enum and `const`
  channels, wrappers named after the Electron objects, union-typed channels), the context bridge
  (`endpoint:electron-preload:<key>#<member>`, `window.<key>.<member>()` → `contextBridge.exposeInMainWorld`) and
  Tauri commands (`endpoint:tauri:<command>`, `invoke('cmd')` → `#[tauri::command]`, check `unregistered` for a
  command missing from `generate_handler!`, plugin commands `plugin:x|cmd`). Module nodes carry the process role
  (main, preload, renderer; webview, core); `cg bridges --protocol electron-ipc | electron-preload | tauri`. A Tauri
  app without a root `Cargo.toml` has its `src-tauri` crate indexed. Swift `canImport(...)` / `targetEnvironment(...)`
  are platform conditions; project targets come from `Package.swift` `platforms:` and the Kotlin Multiplatform
  `kotlin { }` block, and Tauri 2 mobile projects add android / ios
  ([#21](https://github.com/cyberchronos00/code-graph/issues/21)).

- `tests` and `reaches` follow overrides like `impact`: on a base or interface method they include the tests and the
  dependents of its overrides, marked `via override` (`via_override` in JSON and MCP). `Sub.method` for a method a
  class inherits without redefining it resolves through its ancestors to the inherited definition (`B.run ->
  inherited from Base.run`), following only the overrides below `Sub`. TypeScript classes get EXTENDS / IMPLEMENTS
  and OVERRIDDEN_BY / IMPLEMENTED_BY edges, so the override relation works for them too
  ([#53](https://github.com/cyberchronos00/code-graph/issues/53)).

- Web / native bridges: Capacitor plugin calls (`registerPlugin`, `Plugins.X`, `@capacitor/*` package exports,
  `nativePromise`), React Native / Expo native module calls (`NativeModules`, `TurboModuleRegistry`,
  `requireNativeModule`) and Flutter method / event channel calls link through a shared
  `endpoint:<protocol>:<module>#<method>` node (SENDS_TO / RECEIVED_BY, the protocol endpoint model of #31) to their
  Kotlin, Java, Swift and Objective-C receivers per platform (`@PluginMethod`, `CAPPluginMethod` / `CAP_PLUGIN`,
  `@ReactMethod`, `RCT_EXPORT_METHOD` / `RCT_EXTERN_METHOD`, Expo `Function`, `call.method` handlers). `cg bridges` /
  MCP `bridges` list methods missing on a platform, without a receiver, without a sender or implemented outside the
  repo; `impact`, `downstream`, `tests` and `--platform` cross the bridge, and Kotlin / Swift / Java `Class.method`
  specs resolve. Local `file:` / workspace packages resolve without `node_modules`, and platform regions understand
  guard clauses (`if (Platform.OS !== 'ios') return`) and JS without semicolons
  ([#20](https://github.com/cyberchronos00/code-graph/issues/20)).

### Fixed

- `cg coverage` no longer tells you to install Node.js when the TypeScript plugin did not run because the indexed
  root is not a TypeScript project (no tsconfig.json / jsconfig.json, nothing in package.json): it names that reason
  and the directories holding a tsconfig.json to index instead; a run that found no source files points at the
  tsconfig `include` / `files`. The install hint stays for a skipped plugin (Node.js or the extractor missing).

## [0.6.0] - 2026-10-03

### Added

- `.cg.yaml` `apps` for monorepos: one `cg index <root>` indexes each app (`<db>.<app>.db`) and links each frontend /
  backend pair (`<db>.<frontend>+<backend>.db`, `links` per frontend, default every backend), with the same graphs
  as indexing and linking the apps one by one; `--no-apps` indexes the root as one project. `.cg.yaml` `include`
  indexes directories a built-in skip leaves out (a skipped directory name such as `build/` or
  `node_modules/@acme/sdk`, generated files). The TypeScript and Dart extractors take their directory skip lists from
  the presets with the index config instead of built-in copies, so `skip_dirs.keep` (also for hidden directories)
  and `include` reach them. `cg config show` lists `include`, the apps and their link pairs
  ([#17](https://github.com/cyberchronos00/code-graph/issues/17)).
- FastAPI / Starlette and Flask routes: app, router and blueprint objects, `include_router` / `mount` /
  `register_blueprint` prefix chains across files (prefixes from constants and settings attributes), verb and `route`
  decorators, `add_api_route` / `add_url_rule` (incl. `MethodView.as_view()`), Starlette route lists, websockets and
  path parameters become route nodes with `ROUTES_TO` to the handler. `Depends()` / `Security()` dependencies and
  Flask view decorators are the route's access (`cg routes --unguarded`). `TestClient(app)` / `app.test_client()`
  requests link to these routes (also through `url_for()` / `url_path_for()` names and f-string settings prefixes),
  so `cg tests <handler>` lists them, and their route decorators are no longer `python_decorator_routes` blind spots
  ([#16](https://github.com/cyberchronos00/code-graph/issues/16)).

### Fixed

- Python: a module named `tests.py` that application code imports and that defines no test case is application code,
  so its calls count as callers in `impact` and its functions are no longer test code. A Django app's `tests.py` with
  test cases stays test code ([#49](https://github.com/cyberchronos00/code-graph/issues/49)).
- Python: calls on the items of a list of instances are followed through longer chains: a copy of a module-level
  list, filtered in a setup helper, returned in a dict and filtered again before the loop (`for fw in fws:
  fw.contribute()`) now gets `CALLS` (via collection) to each item's method, so `impact` and `tests` on the base
  method and its overrides find the loop ([#50](https://github.com/cyberchronos00/code-graph/issues/50)).
- Python files that use Python 3.14's unparenthesized `except A, B:` are parsed on older interpreters too (re-parsed
  with the parentheses added) instead of counting as parse failures
  ([#16](https://github.com/cyberchronos00/code-graph/issues/16)).
- Rust / C / C++: processes indexing the same project at the same time all get the exact layer. One rust-analyzer /
  scip-clang run per cache key (a per-key lock; the other processes wait and read the cached SCIP file), private
  temporary output files and an atomically written rust-analyzer config. Before, all but one concurrent run could fall
  back to the heuristic layer. `cg coverage` names the reason when an installed exact indexer fails
  ([#24](https://github.com/cyberchronos00/code-graph/issues/24)).
- Query specs accept `file#name` for TypeScript / JavaScript / Vue symbols (`src/app.ts#listOrders`,
  `app.ts#listOrders`, `src/svc.ts#OrderService.create`), with the file part matched exactly or as a path suffix, in
  every query that resolves specs (`impact`, `tests`, `reaches`, `downstream`, `path`, the MCP tools)
  ([#28](https://github.com/cyberchronos00/code-graph/issues/28)).
- `cg index` stats: `generated.files` counts every file the classifier labelled, the same number `cg coverage`
  lists, with `by_language`, `by_reason` and `by_kind`. The old count (files whose nodes the final pass removed) is
  `files_with_dropped_nodes` (`files_with_nodes` with `--include-generated`)
  ([#27](https://github.com/cyberchronos00/code-graph/issues/27)).
- `impact` keeps the override relation apart from the callers: a base method is listed as `overrides:` instead of
  as a caller of its override, and `impact` on an abstract or base method lists the code that calls its overrides
  (`via override`). The MCP structured content carries the relation as `overrides`
  ([#26](https://github.com/cyberchronos00/code-graph/issues/26)).

## [0.5.0] - 2026-10-02

### Added

- Swift support (heuristic tree-sitter layer, `pip install tree-sitter-swift`, no Xcode needed): classes, structs,
  enums, actors, protocols and extensions, functions and methods with name-based call resolution; Vapor routes with
  `grouped` / `group` prefixes, middleware guards and `RouteCollection` handlers; URLSession and Alamofire endpoints
  for `cg link`; SwiftUI (`NavigationLink`, `.navigationDestination`, `.sheet`, `TabView`, `WindowGroup`) and UIKit
  navigation as pages; `@main`, app-delegate, view-controller and background-task entry points; XCTest tests;
  `#if os(...)` blocks as platform conditions. `cg coverage` reports Swift as heuristic. New sample
  `examples/bookstore-ios` links to the Django sample
  ([#10](https://github.com/cyberchronos00/code-graph/issues/10)).
- Kotlin support (heuristic tree-sitter layer, `pip install tree-sitter-kotlin`): classes, objects, interfaces,
  top-level / extension functions and methods with name-based call resolution; Ktor (`routing` / `route` /
  `authenticate`) and Spring (`@RestController`, `@GetMapping` ..., `@PreAuthorize` / `@Secured`) routes and guards,
  `@Scheduled` and message listeners; Retrofit, Ktor client and OkHttp endpoints for `cg link`; Compose Navigation
  pages and `navigate(...)`; AndroidManifest components and deep links, WorkManager workers; KMP source sets as platform
  conditions and `expect` → `actual`. `cg coverage` reports Kotlin as heuristic. New sample `examples/bookstore-android`
  links to the Django sample.
- Framework presets and a fuller project config: each detected language and framework applies a curated preset
  (auth and secret guards for Laravel, Django, DRF, django-ninja, NestJS, Next.js, Express-style servers and Nuxt;
  shared skip lists used by every plugin and the coverage scan), recorded in the index stats; `.cg.yaml` adds
  `exclude`, `skip_dirs`, `frameworks`, `auth` / `secret` patterns, `gates`, `plans` and `viz.presets`; `cg config
  show|validate` lists every effective value with its source; `routes` names the rule behind each auth guard; starter
  queries derived from the graph (`cg starters`, MCP `starters`, the visual view's preset menu)
  ([#6](https://github.com/cyberchronos00/code-graph/issues/6)).
- Generated, copied and vendored files are detected and kept out of the graph by default: `.gitattributes`
  `linguist-generated` / `linguist-vendored`, Capacitor `webDir` copies under `android/` and `ios/` (each mapped to
  its source file), Cordova `platforms/*/www`, `.openapi-generator/FILES`, `.nuxt` / `.next` / `.svelte-kit` build
  output, Flutter plugin registrants and `ephemeral/`, `*.g.dart` / `*.pb.go` / `*_pb2.py`-style names and "generated
  by ... do not edit" header banners; `cg coverage` lists them by reason, `.cg.yaml` `generated.paths` / `vendored` /
  `keep` adjust the rules, and `--include-generated` indexes them labelled `attrs.generated` with `COPY_OF` edges from
  copies to their sources, shown as "(generated)" / "(copy of ...)" in `impact`
  ([#8](https://github.com/cyberchronos00/code-graph/issues/8)).
- Platform-specific code: symbols and references under Rust `#[cfg]` / `cfg!`, C / C++ `#if` platform macros and
  `win/` / `unix/` paths, Dart `Platform.isX` / `kIsWeb` / conditional imports and React Native `Platform.OS` /
  `Platform.select` / `.ios.ts` files carry the targets they are built for; variants are linked to each other;
  `--platform TARGET` on `reaches`, `impact`, `downstream`, `path`, `routes` and `search` (MCP `platform`, with the
  filter and the unevaluated conditions in every reply); `cg platforms [divergence]` and MCP `platforms` /
  `platform_divergence` list targets, variants that leave a target uncovered, API differences and references to
  code not built on a target; per-target coverage; `.cg.yaml` `platforms`
  ([#7](https://github.com/cyberchronos00/code-graph/issues/7)).

### Changed

- Coverage counts no longer include generated and copied files, and the `book.g.dart` nodes of `bookstore-flutter`
  are excluded by default (90 / 171 → 87 / 164 nodes / edges)
  ([#8](https://github.com/cyberchronos00/code-graph/issues/8)).
- Rust exact mode adds calls into items gated for another target (`via: cfg-inactive`), and a `#[cfg]` on a match
  arm gates the whole arm; C / C++ heuristic calls to a function defined once per `#if` branch link every definition;
  React Native imports resolve through the platform suffixes
  ([#7](https://github.com/cyberchronos00/code-graph/issues/7)).

### Fixed

- Starter queries and `cg routes --writes` stay fast on large connected graphs: the route report walks the graph
  once for all write targets (a single multi-target traversal limited to what the routes reach) instead of one
  recursive query per table, with the same output. saleor: starters 57.5 s → 0.4 s, index 124.4 s → 68.3 s. The starters
  keep to their 20 s budget: one that does not finish in time is left out and listed in `starters_skipped`
  ([#25](https://github.com/cyberchronos00/code-graph/issues/25)).
- The test suite skips the tests that index PHP code when the PHP extractor's Composer dependencies are not
  installed (as it already did for the TypeScript extractor), so a fresh checkout reports skips instead of failures
  ([#10](https://github.com/cyberchronos00/code-graph/issues/10)).
- Python calls through a collection follow copies and helper-built collections: elements of `copy.copy()` /
  `copy.deepcopy()` / `.copy()`, of the value a project function returns and of a constant key of a returned dict
  or tuple (`plan = setup(); for p in plan["plugins"]: p.index()`) keep their types, so `impact` and `tests` reach the
  callers again when a codebase makes fresh copies of a plugin list
  ([#9](https://github.com/cyberchronos00/code-graph/issues/9)).
- Routers, apps and controllers built inside TypeScript test files (`*.spec.ts`, `*.test.ts`, `test/`,
  `__tests__/`) no longer become application routes for the Express / Koa / Fastify / Hono, NestJS and Next.js
  layers; the files stay indexed as tests (immich `server/`: 295 routes, all guarded, instead of 297 with 2 test
  routes) ([#19](https://github.com/cyberchronos00/code-graph/issues/19)).
- A pytest `testpaths` entry that names the application package (`testpaths = ["app"]`, as in saleor) no longer
  turns the whole package into test code: only the files pytest collects there, `conftest.py` and `tests/`
  directories are tests, so `impact`, `reaches` and the starter queries work on such projects again
  ([#18](https://github.com/cyberchronos00/code-graph/issues/18)).
- Indexing several projects in one process (the MCP `index` tool called again, scripts) gives the same graph as a
  fresh run: every run uses its own plugin instances, so the PHP gate predicates of one project no longer land in the
  next project's graph, and the Express route-key and Rust trait-method caches are rebuilt per project
  ([#9](https://github.com/cyberchronos00/code-graph/issues/9)).

## [0.4.0] - 2026-10-02

### Added

- Completeness reporting: `cg coverage` shows discovered vs indexed files per language and why the rest were not
  indexed (`parse_failed`, `skipped_oversize`, `unmapped`, `excluded`), lists source types no plugin reads yet, and
  `--all-files` lists every file per bucket ([#11](https://github.com/cyberchronos00/code-graph/issues/11)).
- Blind-spot detectors find route and handler registrations no plugin models (NestJS `applyDecorators` wrappers,
  dynamic Django `urlpatterns`, routes registered in loops, Python registration decorators and registries), with
  `file:line` samples ([#11](https://github.com/cyberchronos00/code-graph/issues/11)).
- `routes`, `impact`, `callers`, `reaches`, `tests_covering` and `plan_check` say when an answer may be partial, and
  every MCP reply carries a structured `completeness` object, so agents know when to fall back to normal search; new
  `callers` MCP tool ([#11](https://github.com/cyberchronos00/code-graph/issues/11)).
- Python source roots detected from the project layout (src/, lib/, packaging config, several package roots,
  namespace packages, nested projects); `.cg.yaml` `python.source_roots` and `cg index --python-root`
  ([#13](https://github.com/cyberchronos00/code-graph/issues/13)).
- `cg coverage` and the MCP `coverage` tool list Python source roots with their origin and module counts, and flag
  missing configured roots and files outside every root ([#13](https://github.com/cyberchronos00/code-graph/issues/13)).
- Python entry points and function references: `__main__` blocks, `__main__.py` and packaging entry points
  (PEP 621, Poetry, flit, setup.cfg, setup.py) as `script` entry nodes; MCP tools and click / typer / Flask CLI
  commands as entry points; functions used in dispatch tables, as callbacks or through decorators get `REFERENCES_FN`
  edges, and calls through dispatch tables resolve, so `impact` reaches them
  ([#14](https://github.com/cyberchronos00/code-graph/issues/14)).
- Python tests: pytest and unittest cases (Django / DRF TestCase included) are test nodes with their framework,
  parametrize values and marks; fixtures are followed through `conftest.py` chains, autouse, `usefixtures` and
  `pytest_plugins`; Django, DRF, FastAPI and Flask test-client requests are found and Django / DRF / django-ninja ones
  link to their routes. `cg tests` lists Python tests, the header and `cg coverage` count test cases per framework,
  and test code never counts as a caller in `impact` / `callers` / `reaches`
  ([#15](https://github.com/cyberchronos00/code-graph/issues/15)).

### Changed

- Each Python file gets one canonical module name, chosen by the project's own imports; other importable names stay
  aliases ([#13](https://github.com/cyberchronos00/code-graph/issues/13)).
- Edges made by Python test code (calls, references, collection calls) are `TEST_CALLS` / `TEST_USES`, as for PHP and
  TypeScript, so Python `CALLS` counts now cover application code only
  ([#15](https://github.com/cyberchronos00/code-graph/issues/15)).

### Fixed

- Django: DRF router route names use the queryset model as default basename and `@action(url_name=...)`, and
  `include("app.urls")` takes the included module's `app_name` as namespace, so `reverse("app:name")` lookups match
  ([#15](https://github.com/cyberchronos00/code-graph/issues/15)).
- The TypeScript, Dart and SCIP (Rust, C / C++) caches are keyed by file content instead of size and mtime, so an
  edit that keeps the file size and timestamp is always re-indexed. Caches from older versions are discarded
  automatically ([#12](https://github.com/cyberchronos00/code-graph/issues/12)).

## [0.3.0] - 2026-10-01

### Added

- Laravel broadcast channels: `Broadcast::channel` callbacks as entry points, the channel auth route, `broadcastOn()`
  channel names, and Echo / pusher-js subscriptions in the frontend linked across repos; new `channels` query and
  MCP tool.
- Tests as first-class nodes: PHPUnit, Pest, Vitest, Jest, Playwright and Cypress tests are indexed with
  non-propagating `TEST_*` edges, so they show which code they cover without counting as callers; new `tests`
  query and `tests_covering` MCP tool.
- Routes behind signature or shared-secret middleware are marked `SECRET-CHECKED` in `routes`.
- Frontend base URLs from `runtimeConfig` / env are folded into endpoint paths, so more HTTP calls link to their
  backend routes.
- `api-calls` accepts `*` globs.
- Animated preview of the visual view in the README.

### Changed

- Nuxt apps are found at the repo root, in `app/` or in `src/`, and clean checkouts without a `.nuxt/` directory
  index fully.
- `coverage` works on linked (multi-repo) graphs.

### Fixed

- A dangling symlink skips only that file instead of dropping the whole language from the index.

## [0.2.0] - 2026-10-01

### Added

- Python / Django plugin (including django-ninja, DRF, Channels and Celery) and Dart / Flutter plugin, with
  cross-repo checks of request and response fields.
- NestJS, Next.js and Express / Fastify / Koa / Hono layers, and plain JavaScript.
- Rust, C and C++ through SCIP indexers (exact mode) with a tree-sitter fallback.
- `routes` query: middleware, guards and auth per route, filterable to routes that write data without auth; it also
  names routes a stricter `min_confidence` hides.
- Per-language coverage (exact / heuristic / skipped / unsupported) via `cg coverage` and the MCP `coverage` tool;
  empty replies carry a coverage note telling agents when to fall back to normal search.
- `impact` lists external snapshot clients from the plans directory.
- Flags parameters a page sends that a request helper drops.
- Demo videos (setup, terminal, visual view, a Cursor CLI agent over MCP, and the same agent without code-graph with
  a measured comparison) and Cursor CLI setup docs.

### Changed

- A missing language toolchain no longer fails the index; that language is reported by `coverage` instead.
- `search` matches middleware and guard names, and empty results explain why.
- MCP `index` on a combined graph re-indexes every repo by default, maps a root to its repo(s), and refuses unknown
  roots and empty results.
- `plan_check` returns a compact summary; MCP replies use repo-relative paths.

### Fixed

- Malformed plan items and invalid plan YAML are reported instead of crashing.

## [0.1.0] - 2026-10-01

First open-source release.

### Added

- Deterministic code graph for Laravel (PHP) and Nuxt / Vue (TypeScript): calls with type inference, routes and
  middleware, Eloquent models, tables and columns, migrations, DB connections, config / env, commands, scheduler,
  jobs, events and listeners, Filament panels, Nuxt pages, layouts, auto-imports, components and Pinia stores. Every
  edge carries `file:line` evidence and a confidence level (`exact`, `resolved`, `heuristic`).
- `link` matches frontend HTTP calls (fetch, `$fetch`, axios) to backend routes across repos.
- CLI queries: `reaches`, `impact`, `downstream`, `path`, `writers`, `siblings`, `node`, `stats`, `api-calls`,
  `resolutions`, grouping answers into runtime, operator-only and gated callers.
- Gate scenarios for feature flags, and value facts (request keys, settings, fallback chains).
- SCIP import for other languages (experimental).
- MCP server so AI agents can ask the same questions.
- Local visual view (`serve`) and static export (`viz-export`).
- Planned-change layer: describe a change in `plans/*.yaml` and check it for completeness and conflicts, with
  verify mode, baselines and a plan overlay in the visual view.
- Fictional bookstore sample apps, an example plan, `scripts/reproduce.sh`, docs, MIT license, contributing guide
  and security policy.

[Unreleased]: https://github.com/cyberchronos00/code-graph/compare/v0.10.0...HEAD
[0.10.0]: https://github.com/cyberchronos00/code-graph/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/cyberchronos00/code-graph/compare/v0.8.2...v0.9.0
[0.8.2]: https://github.com/cyberchronos00/code-graph/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/cyberchronos00/code-graph/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/cyberchronos00/code-graph/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/cyberchronos00/code-graph/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/cyberchronos00/code-graph/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/cyberchronos00/code-graph/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/cyberchronos00/code-graph/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/cyberchronos00/code-graph/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/cyberchronos00/code-graph/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/cyberchronos00/code-graph/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/cyberchronos00/code-graph/releases/tag/v0.1.0
