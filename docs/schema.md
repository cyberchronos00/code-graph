# Graph schema

The graph is one SQLite file. Node and edge details for a language live on that language's page; this page is the index of tables and kinds.

## Tables

```sql
nodes(id PK, kind, name, fqn, file, line, end_line, module, doc, lang, entry_kind, attrs JSON)
edges(id, src, dst, kind, file, line, confidence, conf_rank, attrs JSON)  -- file:line is the evidence
edge_kinds(kind PK, propagates, description)  -- propagates=1: src depends on dst (reaches / impact)
node_entry(node_id, entry_kind, entry_count, sample_entry)
meta(key, value)  -- corpus, commit, stats, redact_salt (per-graph HMAC salt, 32 hex; see surface)
```

On a combined DB only, `payload_checks(endpoint, route, kind, severity, message, client_at, server_at, details JSON)` holds the request/response field check ([payload / field check](architecture.md#payload--field-check)).

`module` is the path or namespace (`Http/Controllers/Admin`, a Rust module path, a C/C++ directory). `doc` holds the doc comment. `reaches` groups by `module`.

## Node kinds

| area | kinds | notes |
|---|---|---|
| PHP / shared | `class`, `interface`, `trait`, `method` (functions too), `property`, `external_class`, `route`, `command`, `schedule`, `job`, `event`, `listener`, `observer`, `admin`, `table`, `column`, `connection`, `config`, `env`, `script` | `channel:<pattern>` has `pattern`, `visibility`, `declared`, `callback` / `handler` |
| Tests | `test` | id `test:<file>::<name>` (Python `test:<module>.<Class>.<method>`). Attrs: `framework` (phpunit, pest, vitest, jest, playwright, cypress, pytest, unittest), `suite`; Python adds `params`, `marks`, `testcase`, `inherited_from`. Nodes in test code have `attrs.test` |
| TypeScript / Vue | `module`, `page`, `component`, `layout`, `app`, `composable`, `store`, `function`, `class`, `type`, `http`, `channel_sub`, `i18n` | `http:<METHOD> <path>`; `channel_sub:<name>`. See [TypeScript / JavaScript frameworks](ts-frameworks.md) for Nest, Next, Express and React Router / Remix kinds (`route`, `page`, `layout`, `message`, `job`, …) |
| Rust, C, C++ | `crate`, `mod`, `file`, `function`, `method`, `struct`, `enum`, `union`, `class`, `typedef`, `type_alias`, `trait`, `field`, `variant`, `enumerator`, `const`, `static`, `global`, `macro`, `ffi` | Fact nodes: `feature:`, `cfg:`, `define:`, `unsafe:`, `env:`. [Rust, C and C++](native.md) |
| Python | `module`, `class`, `function`, `method`, `field`, `script`, plus Django `route`, `table`, `column`, `config`, `env`, `job`, `listener`, `command`, `admin`, `event` | [entry points and function references](python.md#entry-points-and-function-references) |
| Dart | `module`, `class`, `method`, `function`, `http`, `page`, `env` | Pages: Navigator, go_router, auto_route |
| Protocols | `endpoint` | `endpoint:<protocol>:<name>`. Existing `http`, `route`, `channel`, `message`, `job`, `event` ids stay and are read as endpoints. [Protocol links](protocols.md) |
| AI | `endpoint`, `agent` | `endpoint:llm_tool:`, `endpoint:mcp_tool:`, `endpoint:mcp_resource:`, `endpoint:mcp_prompt:`; `agent:<name>`. [AI tools](ai-tools.md) |
| External | `external` | `external:<protocol>:<target>` (`external:saas:<provider>` for Stripe, SendGrid, Mailgun, Postmark, Resend, Twilio, Vonage, MessageBird, Plivo, APNs, Web Push and Expo push; `external:gcp:<service>[:resource]`, `external:azure:keyvault[:vault-host]`, `external:k8s:<api-group>`, `external:docker:<socket-or-host:port>`). Attrs name host, port, TLS, `auth` (`ambient`, `explicit`, `unknown`) and credential source (`env`, `literal`, `file`, `ambient`; `credential_literal` for a key in the code, `credential_file` for a key path in the code), never the secret. `CONNECTS_TO` also carries `namespace` for a literal Kubernetes namespace. `CONNECTS_TO` carries `via`, `op`, `auth`. [External systems](external.md) |
| Bridges | `endpoint` | Capacitor, React Native, Flutter, Electron IPC, Tauri. ObjC and missed Java / Kotlin / Swift receivers are `method` nodes with `attrs.bridge_stub`. [Web / native bridges](bridges.md) |

Route paths use `{param}`, `{param?}`, `{rest*}` and `{rest*?}`. A Laravel `route` node may carry `attrs.inline_guards`: objects `{name, kind, at, conditional}` with `kind` one of `policy`, `permission`, `secret`, `role`. `cg routes --json` returns the same list. See [PHP](php.md#inline-guards).

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
| Boundaries | `SENDS_TO`✓, `RECEIVED_BY`✓, `MATCHES_ENDPOINT`✓, `REGISTERS_CALLBACK` (function -> the app's own route whose URL it registers with an external party; `attrs.body_key`, `attrs.endpoint`, `attrs.via`), `CONNECTS_TO`✓, `CREDENTIAL_FROM`, `OFFERS_TOOL`✓, `HANDS_OFF_TO`✓ |
| Broadcast | `AUTHORIZES_CHANNEL`✓, `BROADCASTS_ON`, `SUBSCRIBES_CHANNEL`✓, `MATCHES_CHANNEL`✓, `LISTENS_FOR` |
| Tests | `TEST_CALLS`, `TEST_USES`, `TEST_HTTP`, `TEST_VISITS` (none propagate) |
| Other | `IMPORTS`, `COPY_OF` (with `--include-generated`), `EMITS_STATE`, `HANDLES_STATE`, `PARSES_JSON`, `USES_SCHEMA` |

Mass-assignment facts: a `WRITES_COLUMN` edge from `update($request->validated())`, `create($data)`, `fill(...)`, `new Model($data)` + `save()` and similar calls has `attrs.mass_assignment = true`, `attrs.via` (`update(validated())`, `create(all())`, `update(array_merge(validated()))`, `new Order(validate())`), `attrs.keys_from` (the rule sources and the model declarations that filtered the keys, e.g. `["UpdateBookRequest::rules", "Book::$fillable"]`) and, when the array reached the call through a parameter, `attrs.param` (`$data`). Confidence is `resolved`; `$request->all()` / `input()` give `heuristic`. `cg node` prints `via … keys from A ∩ B`.

Webhook facts: a sender `endpoint:webhook:*` node may carry `attrs.subscriber_urls` (`[{url, at}]`); a `SENDS_TO` edge from a stored subscription has `attrs.via = "stored subscription"` and `attrs.source`; `REGISTERS_CALLBACK` has `attrs.body_key`, `attrs.endpoint` (the outbound `http:` endpoint, when known) and `attrs.via = "SDK webhook registration"` for an SDK call; a route's `attrs.webhook` adds `via = "driver"` and `drivers` when its checks come from run-time resolved driver implementations. `cg link` adds `MATCHES_ROUTE` / `MATCHES_ENDPOINT` with `attrs.via = "subscriber url"`. See [protocols](protocols.md#subscriptions-callbacks-and-drivers-152-part-b).

Insecure transport facts: a function, method, module or `file:config:<path>` node may carry `attrs.insecure_transport`, a list of `{kind, line, lib, detail, confidence}` (`kind` is `tls-verify-off`, `ssh-hostkey-off`, `grpc-plaintext`, `ipc-extension-manifest`, `ipc-extension-external` or `ipc-electron`). A `CONNECTS_TO` / `HTTP_CALLS` edge or external node whose call disables verification has `attrs.tls_verify = false`. A listening `endpoint:unix:*` node has `attrs.mode`. Outbound URLs built from request input (`cg surface` finding `ssrf`, [surface](surface.md#outbound-urls-from-request-input-ssrf)): an `HTTP_CALLS` / `CONNECTS_TO` edge carries `attrs.url_from_input = {source, key, part, via, checked}` (`source` is `query`, `body`, `param`, `header`, `path` or `cookie`; `key` is the request-input name, never a value; `part` is `host`, `url`, `path` or `query`; `via` is `direct` or `helper`; `checked` is true after an allow-list comparison), `cg api-calls` and `cg external` print it as `url from input (<source>.<key>, <part>)`; a call with no edge puts the same dict as `url_from_input` in an `insecure_transport` fact of kind `ssrf` or `dns-input` (a DNS lookup of input), with `caller` (the function that passed the value) for a helper flow. Credential-looking config values (Laravel config `value`, `env_default`) are stored as `redacted:hmac:<8 hex>`, an HMAC-SHA-256 of the value keyed by the graph's `meta` key `redact_salt` (`secrets.token_hex(16)`, created with the database, kept on re-index and `cg refresh`, not present on a `cg link` combined graph; markers from older indexes read `redacted:sha256:<8 hex>`); see [surface](surface.md#no-secret-values-in-the-index). Guard names that `cg surface` derives from project settings when it classifies a route (read from the source at report time, never stored in the graph): `drf-default:<PermissionClass>` (`REST_FRAMEWORK` `DEFAULT_PERMISSION_CLASSES`), `laravel-global:<Middleware>` and `laravel-group:<group>:<Middleware>` (`Kernel.php`, `bootstrap/app.php`). Nest `APP_GUARD` providers were already recorded by the plugin; a Nest `@Public()` opt-out removes them for that route. A Django `route` node for a DRF view reached by a plain `path()` carries `attrs.drf = true`; an empty `permission_classes` list is recorded as the guard `AllowAny`. A `cg surface` inbound item has the guard state `public` (a route in the public-by-design table) in addition to `guarded`, `secret-checked`, `inline-guarded`, `unguarded` and `unchecked`, and may carry `public` (the reason) and `unsure` (why a finding is only a heuristic).

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
