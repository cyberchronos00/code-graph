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
**test** (every language): one test case (`test:<file>::<name>`; attrs `framework` = phpunit, pest, vitest, jest,
playwright, cypress; `suite`). Test edges never propagate (see below).
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
TS: IMPORTS, RENDERS✓ (template component usage), USES_COMPOSABLE✓, USES_STORE✓, HTTP_CALLS✓ (→ http endpoint), MATCHES_ROUTE✓
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
Tests: TEST_CALLS, TEST_USES (test code → code; `attrs.orig` = the original edge kind), TEST_HTTP (test → route),
TEST_VISITS (browser test → page). None of them propagate, so tests never count as callers.
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

