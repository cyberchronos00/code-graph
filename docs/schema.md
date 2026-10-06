# Graph schema

The graph is one SQLite file. Node and edge details for a language live on that language's page; this page is the index of tables and kinds.

## Tables

```sql
nodes(id PK, kind, name, fqn, file, line, end_line, module, doc, lang, entry_kind, attrs JSON)
edges(id, src, dst, kind, file, line, confidence, conf_rank, attrs JSON)  -- file:line is the evidence
edge_kinds(kind PK, propagates, description)  -- propagates=1: src depends on dst (reaches / impact)
node_entry(node_id, entry_kind, entry_count, sample_entry)
meta(key, value)  -- corpus, commit, stats
```

On a combined DB only, `payload_checks(endpoint, route, kind, severity, message, client_at, server_at, details JSON)` holds the request/response field check ([payload / field check](architecture.md#payload--field-check)).

`module` is the path or namespace (`Http/Controllers/Admin`, a Rust module path, a C/C++ directory). `doc` holds the doc comment. `reaches` groups by `module`.

## Node kinds

| area | kinds | notes |
|---|---|---|
| PHP / shared | `class`, `interface`, `trait`, `method` (functions too), `property`, `external_class`, `route`, `command`, `schedule`, `job`, `event`, `listener`, `observer`, `admin`, `table`, `column`, `connection`, `config`, `env`, `script` | `channel:<pattern>` has `pattern`, `visibility`, `declared`, `callback` / `handler` |
| Tests | `test` | id `test:<file>::<name>` (Python `test:<module>.<Class>.<method>`). Attrs: `framework` (phpunit, pest, vitest, jest, playwright, cypress, pytest, unittest), `suite`; Python adds `params`, `marks`, `testcase`, `inherited_from`. Nodes in test code have `attrs.test` |
| TypeScript / Vue | `module`, `page`, `component`, `layout`, `app`, `composable`, `store`, `function`, `class`, `type`, `http`, `channel_sub`, `i18n` | `http:<METHOD> <path>`; `channel_sub:<name>`. See [TypeScript / JavaScript frameworks](ts-frameworks.md) for Nest, Next and Express kinds (`route`, `message`, `job`, …) |
| Rust, C, C++ | `crate`, `mod`, `file`, `function`, `method`, `struct`, `enum`, `union`, `class`, `typedef`, `type_alias`, `trait`, `field`, `variant`, `enumerator`, `const`, `static`, `global`, `macro`, `ffi` | Fact nodes: `feature:`, `cfg:`, `define:`, `unsafe:`, `env:`. [Rust, C and C++](native.md) |
| Python | `module`, `class`, `function`, `method`, `field`, `script`, plus Django `route`, `table`, `column`, `config`, `env`, `job`, `listener`, `command`, `admin`, `event` | [entry points and function references](python.md#entry-points-and-function-references) |
| Dart | `module`, `class`, `method`, `function`, `http`, `page`, `env` | Pages: Navigator, go_router, auto_route |
| Protocols | `endpoint` | `endpoint:<protocol>:<name>`. Existing `http`, `route`, `channel`, `message`, `job`, `event` ids stay and are read as endpoints. [Protocol links](protocols.md) |
| AI | `endpoint`, `agent` | `endpoint:llm_tool:`, `endpoint:mcp_tool:`, `endpoint:mcp_resource:`, `endpoint:mcp_prompt:`; `agent:<name>`. [AI tools](ai-tools.md) |
| External | `external` | `external:<protocol>:<target>`. Attrs name host, port, TLS and credential source, never the secret. [External systems](external.md) |
| Bridges | `endpoint` | Capacitor, React Native, Flutter, Electron IPC, Tauri. ObjC and missed Java / Kotlin / Swift receivers are `method` nodes with `attrs.bridge_stub`. [Web / native bridges](bridges.md) |

Route paths use `{param}`, `{param?}`, `{rest*}` and `{rest*?}`.

## Enum cases and constants

Every language indexes them as `CONTAINS` children of the type or module. A reference is an edge only where the binding is certain. Nothing `CALLS` these nodes. Query across languages with `kind IN ('enum_case', 'variant', 'enumerator')`. C# has no plugin. Java enum constants are `enum_case` nodes. Dart enums stay `class` nodes. `cg coverage --details` adds a `values:` line.

| language | case | constant | edge |
|---|---|---|---|
| Swift | `enum_case` | `constant` | `USES_VALUE` |
| Kotlin | `enum_case` | `constant` | `USES_VALUE` |
| Java | `enum_case` | | a constant is a node; `USES_VALUE` is not emitted yet |
| TypeScript / JavaScript | `enum_case` | `constant` | `USES_VALUE` |
| Python | `enum_case` | `constant` | `USES_VALUE` |
| PHP | `enum_case` | `constant` | `USES_VALUE` |
| Rust | `variant` | `const`, `static` | `ACCESSES_FIELD`, `USES_VALUE` |
| C / C++ | `enumerator` | `global`, object-like `macro` | `USES_VALUE` |

Ids and which spellings resolve: the language pages ([Swift](swift.md), [Kotlin](kotlin.md), [Java](java.md), [Rust, C and C++](native.md), [Python](python.md)).

## Edge kinds

`✓` propagates in `reaches` / `impact`.

| group | kinds |
|---|---|
| Calls and types | `CALLS`✓, `IMPLEMENTED_BY`✓, `OVERRIDDEN_BY`✓, `BOUND_TO`✓, `EXTENDS`, `IMPLEMENTS`, `USES_TRAIT`, `INSTANTIATES`, `INJECTS`, `REFERENCES`, `REFERENCES_TYPE` |
| HTTP and jobs | `ROUTES_TO`✓, `USES_MIDDLEWARE`✓, `HANDLED_BY`✓, `SCHEDULES`✓, `DISPATCHES`✓, `LISTENED_BY`✓, `OBSERVED_BY`, `BINDS` |
| Data | `READS_COLUMN`✓, `WRITES_COLUMN`✓, `MENTIONS_COLUMN`✓, `READS_TABLE`✓, `WRITES_TABLE`✓, `USES_CONNECTION`✓, `REGISTERS_CONNECTION`✓, `READS_CONFIG`✓, `WRITES_CONFIG`✓, `READS_ENV`✓, `REFERS_TO`✓, `CONFIGURED_BY`✓, `CONFIG_CONTAINS`✓, `MAPS_TO_TABLE`, `HAS_RELATION`, `CONTAINS`, `DEFINES` |
| UI | `RENDERS`✓, `USES_COMPOSABLE`✓, `USES_STORE`✓, `HTTP_CALLS`✓, `MATCHES_ROUTE`✓ (combined DB), `USES_LAYOUT`, `USES_I18N`, `NAVIGATES_TO` |
| Properties | `READS_PROP`✓, `WRITES_PROP`✓ (Swift, Kotlin, Python, TypeScript, PHP; see the language pages) |
| Native | `USES_TYPE`✓, `ACCESSES_FIELD`✓, `USES_VALUE`✓, `REFERENCES_FN`✓, `USES_UNSAFE`✓, `GATED_BY`✓, `INCLUDES`✓ |
| Boundaries | `SENDS_TO`✓, `RECEIVED_BY`✓, `MATCHES_ENDPOINT`✓, `CONNECTS_TO`✓, `CREDENTIAL_FROM`, `OFFERS_TOOL`✓, `HANDS_OFF_TO`✓ |
| Broadcast | `AUTHORIZES_CHANNEL`✓, `BROADCASTS_ON`, `SUBSCRIBES_CHANNEL`✓, `MATCHES_CHANNEL`✓, `LISTENS_FOR` |
| Tests | `TEST_CALLS`, `TEST_USES`, `TEST_HTTP`, `TEST_VISITS` (none propagate) |
| Other | `IMPORTS`, `COPY_OF` (with `--include-generated`), `EMITS_STATE`, `HANDLES_STATE`, `PARSES_JSON`, `USES_SCHEMA` |

Platform conditions, generated-file markers and bridge attrs sit on `nodes.attrs` / `edges.attrs`. The language and boundary pages list the keys (`platforms`, `generated`, `process`, `via`, `dispatch`).

## Confidence

| value | when |
|---|---|
| `exact` | syntactically certain (`new X`, `$this->m()`, a literal key) |
| `resolved` | type or name resolution (typed params, inferred variables, model → table) |
| `heuristic` | a unique-method-name fallback, or a column-name literal |

## Entry kinds

Tagging walks forward from each entry over propagating edges. `reaches` then labels a node **runtime** (any runtime entry), **operator-only** (commands or admin only), **library** (`public_api` only), **dev** (tests, benches, examples, build scripts only) or **none**.

| group | kinds |
|---|---|
| Runtime | `http_route`, `channel_auth`, `websocket`, `scheduled`, `queue_job`, `listener`, `message_handler`, `llm_tool` |
| Operator | `artisan_command`, `management_command`, `cli_command`, `admin_panel` |
| Other | `observer` |
| Program | `main`, `ffi_export` |
| Library | `public_api` |
| Dev / build | `test`, `bench`, `example`, `build_script` |
| UI | `ui_page`, `ui_global` |

Which framework produces which kind: [Python](python.md), [TypeScript / JavaScript frameworks](ts-frameworks.md), [Channels and tests](channels-and-tests.md), [AI tools](ai-tools.md), [Rust, C and C++](native.md).
