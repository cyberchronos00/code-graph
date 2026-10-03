# Graph schema

The graph is a single SQLite file. This page lists the tables, node kinds, edge kinds, confidence levels and entry kinds.


```sql
nodes(id PK, kind, name, fqn, file, line, end_line, module, doc, lang, entry_kind, attrs JSON)
edges(id, src, dst, kind, file, line, confidence, conf_rank, attrs JSON)   -- file:line = evidence
edge_kinds(kind PK, propagates, description)    -- propagates=1: src depends on dst (used by reaches)
node_entry(node_id, entry_kind, entry_count, sample_entry)   -- which entry kinds reach each node
meta(key, value)                                 -- corpus, commit, stats
```

**Node kinds:** class, interface, trait, method (incl. functions), property, external_class (vendor placeholder), route, command, schedule, job,
event, listener, observer, admin (Filament surface), table, column, connection, config, env, script (migrations/routes/config files),
channel (`channel:<pattern>`, a broadcast channel; attrs `pattern`, `visibility`, `declared`, `callback` / `handler`).
**test** (every language): one test case (`test:<file>::<name>`, Python `test:<module>.<Class>.<method>`; attrs
`framework` = phpunit, pest, vitest, jest, playwright, cypress, pytest, unittest; `suite`; Python adds `params`
(parametrize argument names, literal ids or values, `cases`), `marks`, `testcase` (the TestCase base) and
`inherited_from`; entry kind `test` for Python). Every node declared in test code carries `attrs.test`. Test edges
never propagate (see below).
TS/Vue (lang='ts'): module (TS file), page, component, layout, app (Vue SFCs), composable, store, function, class, type,
http (client endpoint `http:<METHOD> <path template>`; attrs `origin`, `origin_kind`, `base` when a configured base URL
was folded in, `test_only` when only tests call it), channel_sub (a client channel subscription `channel_sub:<name>`;
attrs `visibility`, `events`, `clients`, `test_only`), i18n.
Rust / C / C++ (lang='rust', 'c', 'cpp'; see [native.md](native.md)): crate, mod (Rust module `mod:<path>`), file
(C/C++ translation unit or header), function, method, struct, enum, union, class, typedef, type_alias, trait, field,
variant / enumerator, const, static, global, macro, ffi (extern block declaration), plus fact nodes `feature:<pkg>/<f>`,
`cfg:<atom>`, `define:<MACRO>`, `unsafe:<crate>` and `env:<KEY>`. **module** is the Rust module path or the C/C++
directory, and `reaches` groups by it.
Python (lang='python'): module, class, function, method, field (model/schema fields), script (an entry point:
`script:<module>` for an `if __name__ == "__main__":` block or a `pkg/__main__.py`, `script:<group>:<name>` for a
packaging entry point such as a console script; attrs `via`, `group`, `script`, `target`, `declared_in`), plus Django
route, table, column, config (`settings.X`), env, job (Celery task), listener (signal receiver), command (management
command), admin, event (signal). See [python.md](python.md#entry-points-and-function-references).
Dart (lang='dart'): module (library), class (incl. enums, mixins, extensions), method, function, http (client endpoint, incl.
`WS` for WebSockets), page (Navigator/go_router/auto_route routes, `page:dart:<package>:<path>`), env (`String.fromEnvironment`, dotenv).
Web / native bridges ([bridges.md](bridges.md), no lang): endpoint (`endpoint:<protocol>:<module>#<method>`, protocol
capacitor, react-native, flutter; `endpoint:flutter-event:<channel>`; desktop processes: `endpoint:electron-ipc:<channel>`,
`endpoint:electron-preload:<key>#<member>`, `endpoint:tauri:<command>`; attrs `protocol`, `transport`, `namespace`,
`method`, `platforms_received`, `side`, `checks`, `missing_on`, `external`, `package`, `base_method`,
`sender_platforms`, `unregistered` for a Tauri command missing from `generate_handler!`). Module / mod / crate nodes of
files taking part in Electron / Tauri IPC carry `attrs.process` (main, preload, renderer, webview, core). Java / Objective-C receivers (and Kotlin / Swift methods the plugins missed) are method nodes
with `attrs.bridge_stub` (lang java, objc, kotlin or swift).
TS server frameworks (NestJS, Next.js, Express-style; see [ts-frameworks.md](ts-frameworks.md)) reuse the backend kinds:
route (`route:<METHOD> <uri>`, also `GRAPHQL Query.x` and `ACTION <file>#<fn>` for server actions), schedule, job (`job:<queue>`),
event, listener, message (`message:<transport>:<pattern>`: microservice, WebSocket, gRPC), command (`command:nest:<name>`),
config, env, table, column. Route attrs: `uri`, `method`, `framework`, `middleware`, `guards` / `interceptors` / `pipes`,
`body_dto` / `body_fields`, `query_dto` / `query_fields`, `version`, `uri_variants` (Next rewrites), `unmounted`, `wrapped_by`,
`handler_unresolved`. Route paths use `{param}`, `{param?}`, `{rest*}` (one or more segments) and `{rest*?}` (zero or more).
**module** is derived from the path or namespace (e.g. `Http/Controllers/Admin`, `Services`, `Console/Commands`, `Domain/X`). **doc** holds the PHPDoc text.

**Edge kinds** (✓ = propagates in `reaches`/`impact`):
CALLS✓, IMPLEMENTED_BY✓ (interface method → impl), OVERRIDDEN_BY✓ (parent → override), BOUND_TO✓ (container binding),
ROUTES_TO✓, USES_MIDDLEWARE✓, HANDLED_BY✓ (command → handle), SCHEDULES✓, DISPATCHES✓, LISTENED_BY✓,
READS_COLUMN✓, WRITES_COLUMN✓, MENTIONS_COLUMN✓ (heuristic: a literal equal to a distinctive column name, e.g. in validation rules), READS_TABLE✓, WRITES_TABLE✓,
USES_CONNECTION✓, REGISTERS_CONNECTION✓, READS_CONFIG✓, WRITES_CONFIG✓, READS_ENV✓, REFERS_TO✓ (config value → connection), CONFIGURED_BY✓, CONFIG_CONTAINS✓,
TS: EXTENDS / IMPLEMENTS (class → project class or interface), OVERRIDDEN_BY / IMPLEMENTED_BY (base class or
`implements`-ed class member → override), IMPORTS, RENDERS✓ (template component usage), USES_COMPOSABLE✓, USES_STORE✓, HTTP_CALLS✓ (→ http endpoint), MATCHES_ROUTE✓
(http endpoint → backend route, combined DB only), USES_LAYOUT, USES_I18N, REFERENCES_TYPE.
Native: USES_TYPE✓, ACCESSES_FIELD✓ (field or enum variant), USES_VALUE✓ (const/static/global/object macro),
REFERENCES_FN✓ (function taken as a value: callbacks, dispatch tables, serde/clap attributes), USES_UNSAFE✓,
GATED_BY✓ (→ feature/cfg/define), INCLUDES✓ (file → header); IMPLEMENTED_BY / OVERRIDDEN_BY also carry trait and
virtual dispatch (`attrs.dispatch`), and IMPLEMENTS links a Rust type to its trait.
Python/Django: REFERENCES_FN✓ (function taken as a value; `attrs.how` = collection, callback, assignment, decorator or
entry point), CALLS through a dispatch table or plugin list (`attrs.via` = collection) and decorator applications
(`attrs.via` = decorator), USES_SCHEMA (handler → ninja Schema / DRF serializer, attrs.role request|response).
Dart/Flutter: EMITS_STATE (bloc → state), HANDLES_STATE (UI → state check), NAVIGATES_TO (UI → page), PARSES_JSON (→ model).
Broadcasting: AUTHORIZES_CHANNEL✓ (auth route → channel), BROADCASTS_ON (event → channel; attrs `name`, `visibility`,
`site`), SUBSCRIBES_CHANNEL✓ (client code → channel_sub), MATCHES_CHANNEL✓ (channel_sub → channel; attrs
`visibility_mismatch`), LISTENS_FOR (channel_sub → event).
Web / native bridges ([bridges.md](bridges.md)): SENDS_TO✓ (JS / Dart code → endpoint; attrs `role` = invoke, `via`,
`module_at`, `external`, `process`), RECEIVED_BY✓ (endpoint → native handler; attrs `platform`, `via`, `process`).
Tests: TEST_CALLS, TEST_USES (test code → code; `attrs.orig` = the original edge kind; Python test → fixture and
fixture → fixture with `via` = fixture / autouse fixture and `attrs.fixture`), TEST_HTTP (test → route),
TEST_VISITS (browser test → page). None of them propagate, so tests never count as callers.
Generated files (only with `--include-generated`; [generated.md](generated.md)): every node from a generated, copied or
vendored file carries `attrs.generated = {kind: generated | copied | vendored, reason[, copy_of]}`; COPY_OF (a copied
module → the module of its source file, e.g. a Capacitor `android/.../assets/public/` copy → its `webDir` file).
Platform-specific code ([platforms.md](platforms.md)): nodes and edges under a platform condition carry
`attrs.platforms` (the targets they are built for), `platform_expr` (the source condition), `platform_at` and
`platform_unknown` (targets on which the condition could not be evaluated). An edge copied onto a sibling variant
(platform files, conditional-import alternatives, per-`cfg` / per-`#if` definitions) has `attrs.platform_variant_of` =
the variant the call resolved to; a Rust call into an item gated for another target has `attrs.via = cfg-inactive`;
a Dart conditional import / export is an IMPORTS edge with `attrs.conditional` and `attrs.condition`.
MAPS_TO_TABLE, HAS_RELATION, CONTAINS, EXTENDS, IMPLEMENTS, USES_TRAIT, INSTANTIATES, INJECTS, REFERENCES (`X::class`), OBSERVED_BY, BINDS, DEFINES.

**Confidence:**
- `exact`: syntactically certain, e.g. a static call, `new X`, `$this->m()` or a literal key.
- `resolved`: needed type or name resolution, e.g. typed properties/params, constructor-promoted deps, inferred variable types, return types, or model → table.
- `heuristic`: a unique-method-name fallback, or a column-name literal.

**Entry kinds:**
- Runtime: `http_route`, `channel_auth` (a broadcast channel's authorization callback), `websocket` (Django Channels routes), `scheduled`, `queue_job` (Laravel jobs, Celery tasks), `listener` (Laravel listeners, Django signal receivers), `message_handler` (Nest microservice / WebSocket / gRPC handlers, MCP server tools / resources / prompts in Python).
- Operator: `artisan_command`, `management_command` (Django `manage.py <name>`), `cli_command` (nest-commander, click / typer / Flask CLI commands), `admin_panel` (Filament, Django admin).
- `observer`.
- Program entry: `main` (Rust bin targets, `#[tokio::main]`, C/C++ `main`; Python `__main__` blocks, `pkg/__main__.py`,
  console and GUI scripts), `ffi_export` (`#[no_mangle]`).
- Library API: `public_api` (pub items of a Rust lib crate, exported C/C++ functions, Python plugin entry-point groups such as `pytest11`). `reaches` shows a LIBRARY API group.
- Dev/build: `test`, `bench`, `example`, `build_script`. `reaches` shows a DEV/BUILD-ONLY group.
- UI (TS/Nuxt/Next, Flutter): `ui_page` (Nuxt and Next pages, Flutter routes), `ui_global` (app.vue, layouts, plugins, `*.global.ts` middleware, Flutter `main`). `reaches` shows a UI group.

Tagging is a forward closure from each entry node over propagating edges. `reaches` classifies each dependent as:
- **runtime**, if any runtime entry reaches it;
- **operator-only**, if only commands or admin panels reach it (one-off import or provisioning);
- **library**, if only `public_api` entries reach it (and no runtime entry);
- **dev**, if only tests, benches, examples or build scripts reach it;
- **none**, if no entry point reaches it.

Combined DB only: `payload_checks(endpoint, route, kind, severity, message, client_at, server_at, details JSON)` holds the
request/response field check (see [architecture.md](architecture.md#payload--field-check)).

