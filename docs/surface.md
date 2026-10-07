# Attack surface

`cg surface` reads facts the index already holds and answers three questions: what does the code expose, what does it
connect to, and where are credentials and transport protection weak. Nothing is rescanned: the checks that need source
text (disabled verification, insecure gRPC channels, risky IPC settings) run once at index time and leave facts on the
graph. A secret value is never stored or printed: a finding names the kind and the location, and the index keeps only a
redacted marker for a credential literal ([No secret values in the index](#no-secret-values-in-the-index)).

```bash
cg surface --db out/graph.db                          # findings, grouped by type then protocol
cg surface --db out/graph.db --inbound --outbound     # plus both surface listings
cg surface --db out/graph.db --format sarif > cg.sarif
cg surface --db out/graph.db --fail-on hardcoded,unverified   # exit 1 in CI
```

## What it lists

| surface | items | per item |
|---|---|---|
| `--inbound` | HTTP routes, webhooks, WebSocket / Socket.IO / SSE, gRPC / GraphQL / tRPC / JSON-RPC handlers, message consumers, TCP / UDP listeners, Unix sockets, modelled IPC handlers (Electron, Tauri, Android) | guard state, whether it reaches a write (as `cg routes --writes`), protocol, transport, bind exposure for listeners |
| `--outbound` | every `external:` system ([External systems](external.md)) | address source, credential source, TLS, entry points that reach it |

Guard state is `guarded`, `secret-checked` (a signature or shared-secret check), `inline-guarded` (an unconditional
check inside the action), `public` (a route in the [public-by-design table](#public-by-design-routes)), `unguarded`, or
`unchecked` when the protocol records no guards. Socket.IO handlers in a
client bundle are not an inbound surface and are left out.

## Findings

| finding | triggers | severity |
|---|---|---|
| `hardcoded` | an external system or SDK call with a literal credential (`credential_source=literal`, `credential_literal`, an edge with `literal_credential`); a DSN with a password in it; a config key named like a secret (`password`, `token`, `secret`, `*_key`) with a literal value, or an `env('X', 'literal')` default for a secret-looking variable | high |
| `plaintext` | an external system with `tls=false` and a non-loopback host (`http://`, `ws://`, `redis://`, `amqp://`, `ftp://`, `ldap://`, plain SMTP, a Docker TCP host); also Telnet / TFTP, MQTT on 1883 and SMTP on 25 at `heuristic`; and a gRPC channel or port created without TLS (protocol `grpc`, see below) | medium |
| `unverified` | a webhook route that reads a provider header and never verifies it ([Protocol links](protocols.md)) | high |
| `unguarded` | a route or handler with no guard after the [guard rules](#how-unguarded-decides): the `cg routes --unguarded` rule plus framework defaults, checks at the top of the handler and the public-route table; `--strict` keeps route guards only | medium when nothing looks like a check and a write is reached; low otherwise; `heuristic` confidence when unclear |
| `exposed-listener` | a TCP / UDP listener bound to all interfaces (`0.0.0.0`, `::`, no host) | medium |
| `tls-off` | certificate verification or SSH host key checking switched off in code or configuration (table below) | high |
| `ipc-exposed` | local IPC open to callers it should not trust (table below), for what `unguarded` does not already report | medium |
| `ssrf` | the host or the whole URL of an outbound HTTP call is built from request input and no allow-list check precedes it (see "Outbound URLs from request input"); a DNS lookup of request input is reported at the lower severity | high (DNS lookup of input: medium) |

### How `unguarded` decides

`cg surface` starts from the `cg routes` guard state and then, unless `--strict` is given, applies these rules in order. A route that gets a guard from a rule is not reported; a rule that cannot decide leaves it reported at `heuristic` confidence and low severity, with the reason in the detail (`unclear: ...`).

| step | rule | by framework |
|---|---|---|
| 1. project defaults | a guard that applies to every route of the project is added to the route | Django REST framework: `REST_FRAMEWORK` `DEFAULT_PERMISSION_CLASSES` of the settings file (an `AllowAny` default is not a guard; a view with `permission_classes = []` is explicitly public); Laravel: global `$middleware` and `$middlewareGroups` of `app/Http/Kernel.php` or `withMiddleware()` of `bootstrap/app.php`, by the group of the route file; Nest: `APP_GUARD` providers (already recorded), removed from a route that has `@Public()` or a similar opt-out decorator (`SkipAuth`, `NoAuth`, `AllowAnonymous`, ...); Express: `app.use(auth)` that comes before the route in the same router and whose mount path covers it (`app.use('/admin', auth)` guards `/admin/*` only); Express `session()`, `cookie-session` and `express-session` are never auth |
| 2. public by design | the route matches the [public table](#public-by-design-routes) | all |
| 3. check in the handler | within the first 14 code lines of the handler (not past its end): an `if` / `unless` whose condition mentions a session, user, token, permission, role, admin flag, `Authorization` or API key and that returns, throws, aborts, redirects or answers 401 / 403; or a leading call such as `requireAuth()`, `authorize()`, `verify...()`, `check...()` or `abort_unless()` whose name or argument mentions the user, auth, token, role, permission, signature or key. A Socket.IO endpoint is checked in its connection handler first (`io.use(...)` or `on('connection')`), then in its own first statements. Laravel `auth()->check()`, `Auth::user()` and `$request->user()` are inline guards | all; PHP, Python and TypeScript |
| 4. unclear | a middleware or decorator whose name looks like a check but is not in the presets, a webhook receiver whose verification is not recognised, a call in the handler whose name looks like a check, a GraphQL endpoint (access is per field) | `heuristic`, low |

Severity: `medium` when no step applied, nothing in the handler looks like a check, and the handler reaches a database write (`cg routes --writes`); `low` for a read-only handler, a [metrics endpoint](#public-by-design-routes) and every `heuristic` case. `--strict` reports every route with no route-level guard at medium severity, as before part 2d.

Per-framework notes: Django resolves a DRF view routed by a plain `path()` as a DRF view, so the settings default applies; Laravel resolves the route file's group (`web`, `api`) and the global stack; Nest reads `@Public()`, `@SkipAuth()`, `@AllowAnonymous()`, `@NoAuth()` and similar decorators as opt-outs; Express guards depend on registration order, because `app.use(auth)` guards only routes registered after it.

### Public-by-design routes

These patterns are matched on the route path (segments are case-insensitive, parameters are ignored). A match makes the route `public` and removes it from `unguarded`. `metrics` is the exception: it stays reported, at low severity.

| class | examples of matching path segments |
|---|---|
| sign-in / sign-up / password reset | `login`, `logout`, `signin`, `signup`, `register`, `forgot-password`, `reset-password`, `verify-email`, `csrf` |
| health check | `health`, `healthz`, `ready`, `readyz`, `live`, `livez`, `ping`, `status`, `up`, `alive` |
| robots / sitemap / favicon / manifest | `robots.txt`, `sitemap*.xml`, `favicon.ico`, `manifest.json`, `security.txt`, `humans.txt`, `ads.txt` |
| static assets, media, thumbnails | `static`, `assets`, `public`, `media`, `_next`, `locales`, `fonts`, `images`, `thumbnails`, `avatars`, `emoji`, `logo` |
| jwks / `.well-known` | `.well-known`, `jwks` |
| OAuth / OIDC callback or token endpoint | `oauth`, `oidc`, `openid`, `callback`, `authorize`, `sso`, `saml`, `/token` |
| API schema / documentation | `swagger`, `redoc`, `openapi`, `api-docs`, `/api/schema` |
| utility endpoint | `version`, `ip`, `geolocation` |
| scheduled job endpoint | `cron` (the job checks its own key) |
| tRPC adapter, Auth.js / NextAuth handler | `/api/trpc/*` (each procedure carries its own middleware), `[...nextauth]` |
| embed pages, shared link, site root | `embeds`, `/s/`, `/share/`, `/public/`, `/` |
| test / fixture code | `test`, `tests`, `__tests__`, `e2e`, `fixtures`, `*.test`, `*.spec` |
| metrics (low severity, still reported) | `metrics`, `prometheus`, `stats` |

The table is a heuristic about names. A route named `/status` that is not public is hidden by it; add nothing to the graph for that, but use `--strict` to see every route without a route-level guard. A public route that is not in the table needs a `surface.ignore` entry.

### Disabled verification (`tls-off`)

The index records each hit on the function or method that contains it (the module node at file level, a
`file:config:<path>` node for scripts and config files), as `attrs.insecure_transport`. Test files and test callers are
skipped and counted (`tests_skipped` in the index stats). `.env` files are never read: only `.env.example` style files,
compose files, Dockerfiles, `package.json` scripts, CI files, shell scripts, `ssh_config` and `ansible.cfg`.

| language | TLS verification off | SSH host key not checked |
|---|---|---|
| Python | `verify=False` (requests, httpx, urllib3), aiohttp `ssl=False`, `ssl._create_unverified_context`, `CERT_NONE`, `check_hostname = False` | paramiko `AutoAddPolicy` / `WarningPolicy`, asyncssh `known_hosts=None` |
| TypeScript / JavaScript | `rejectUnauthorized: false` (https, tls, undici, got, axios `httpsAgent`), `strictSSL: false`, `process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0'` | ssh2 `hostVerifier: () => true` |
| PHP | `CURLOPT_SSL_VERIFYPEER` false, `CURLOPT_SSL_VERIFYHOST` 0, Guzzle `'verify' => false`, `Http::withoutVerifying()`, stream context `verify_peer(_name) => false` | `StrictHostKeyChecking=no` in strings |
| Kotlin / Java | trust-all `X509TrustManager` (empty `checkServerTrusted`), a `HostnameVerifier` that returns true, OkHttp `hostnameVerifier { _, _ -> true }` | JSch `setConfig("StrictHostKeyChecking", "no")` |
| Rust | `danger_accept_invalid_certs(true)` / `danger_accept_invalid_hostnames(true)` | |
| Go | `InsecureSkipVerify: true` | `ssh.InsecureIgnoreHostKey()` |
| Dart | `badCertificateCallback = (...) => true` | |
| scripts, config | `NODE_TLS_REJECT_UNAUTHORIZED=0` in `.env.example`, compose, Dockerfile, `package.json` scripts and CI files (process-wide; the detail says so) | `StrictHostKeyChecking=no` / `UserKnownHostsFile=/dev/null` in shell scripts, CI files and in-repo `ssh_config`; `host_key_checking = False` in `ansible.cfg` and `ANSIBLE_HOST_KEY_CHECKING=False` (heuristic) |

SSH host keys are reported under `tls-off` with protocol `ssh` and a detail that says "SSH host key"; TLS hits have the
protocol of the system they resolve to (`https`, ...) or `tls`. A hit inside a call that is already a `CONNECTS_TO` edge
(`cg external`) or an `HTTP_CALLS` edge (`cg api-calls`) also puts `tls_verify: false` on that edge and its external
system, and both commands print `tls verify off`. Verification left on (`verify=True`, `rejectUnauthorized: true`,
`StrictHostKeyChecking yes`, a pinned `hostVerifier`) is not a hit. A line inside a comment is not a hit.

### Outbound URLs from request input (`ssrf`)

An index-time pass (`cg_code_graph/ssrf_input.py`) walks each non-test function: it collects request-input sources,
follows local variables assigned from them (also template / f-string / concatenation / `sprintf` / `.format` / `%`,
`new URL(x)` and `urljoin`), and looks at the URL argument of outbound calls and DNS lookups. `cg surface` only reads the
result; nothing is rescanned.

| language | request input | outbound sinks | DNS lookups |
|---|---|---|---|
| TypeScript / JavaScript | `req.query` / `body` / `params` / `headers` / `cookies`, `getQuery(event)`, `readBody(event)`, `getRouterParam(event, 'k')`, `c.req.query()` / `param()` / `json()`, `request.nextUrl.searchParams.get`, `await request.json()`, Nest `@Query()` / `@Body()` / `@Param()` / `@Headers()` parameters, Elysia `{ query, body, params }` | `fetch`, `$fetch`, axios, got, ky, needle, superagent, undici, node `http` / `https` | `dns.lookup`, `dns.resolve*`, `dns.promises.*` |
| PHP | `$request->input` / `query` / `get` / `post` / `json` / `all` / `route` / `header` / `cookie`, `request('k')`, `$_GET` / `$_POST` / `$_REQUEST` / `$_COOKIE`, route parameters of controller actions | Laravel `Http::`, Guzzle clients, `curl_init` / `CURLOPT_URL`, `file_get_contents`, `fopen` | `gethostbyname`, `dns_get_record` |
| Python | Django `request.GET` / `POST` / `data` / `query_params` / `headers` / `META`, Flask `request.args` / `form` / `json` / `values` / `headers`, FastAPI / Starlette handler parameters, Django URL kwargs | requests, httpx, aiohttp (module calls and sessions / clients), `urllib` `urlopen` | `socket.gethostbyname`, `socket.getaddrinfo` |

Each use is recorded as `url_from_input = {source, key, part, via, checked}`: `source` is `query`, `body`, `param`,
`header`, `path` or `cookie`; `key` is the input name (`*` when the whole container is used); `part` is what the input
controls; `via` is `direct` (same function) or `helper` (one level: a handler passes the value to a function whose URL
uses that parameter, found through the `CALLS` edge); `checked` is true when a comparison with a constant allow-list
(`in ALLOWED_HOSTS`, `ALLOWED.has(u.host)`, `in_array($host, self::ALLOWED)`) or a `startsWith` / `startswith` / `str_starts_with` test against a literal `https://...` prefix precedes the call. Only names are stored,
never a value from a request.

| `part` | meaning | reported |
|---|---|---|
| `url` | the whole URL, a template that starts with the input, or `new URL(x)` / `urljoin(base, x)` (an absolute `x` replaces the host) | yes |
| `host` | the host in `https://${x}/...` or a DNS lookup name | yes |
| `path`, `query` | input after a fixed host (`https://api.example/${id}`, `...?q=` + x) | no, recorded only |
| any, with `checked` | an allow-list check precedes the call | no, recorded only |

The fact sits on the `HTTP_CALLS` / `CONNECTS_TO` edge of the call when the call has one (`cg api-calls` and
`cg external` print `url from input (query.url, url)`; `host checked` and `via helper` are added when they apply). A call
with no edge keeps the fact on the enclosing function, as an `insecure_transport` fact of kind `ssrf` or `dns-input`.
The finding has `confidence` `resolved` for a direct flow and `heuristic` through a helper, and its entry points are the
routes that reach the function. A DNS lookup of input is an `ssrf` finding with the detail "DNS lookup of request input"
at medium severity (it resolves a chosen name but sends no request body). `--finding ssrf`, `--fail-on ssrf`,
`surface.ignore`, SARIF and the MCP tool need no special handling. Test files and test callers are skipped and counted
(`tests_skipped`).

```
$ cg surface --db out/graph.db --finding ssrf
== ssrf (3) - Outbound request to a host chosen by request input ==
  dns
    MEDIUM [resolved] function:shop.views.mirror
      DNS lookup of request input (query.host)
      at shop/views.py:36
      reached from http_route(1)
  http
    HIGH [heuristic] function:shop.feeds.pull_feed
      the whole outbound URL comes from request input (body.feed) via helper, passed by shop.views.import_feed
      at shop/feeds.py:5
      reached from http_route(1)
    HIGH [resolved] function:shop.views.cover
      the whole outbound URL comes from request input (query.url)
      at shop/views.py:14
      reached from http_route(1)

$ cg external --db out/graph.db
external:https:covers.bookstore.test:443  [exact]
    <- function:src/app.ts#coverByIsbn @ src/app.ts:15  (http)  [url from input (param.isbn, path)]
```

### Insecure gRPC (`plaintext`, protocol `grpc`)

A second producer under the `plaintext` name: Python `grpc.insecure_channel` / `add_insecure_port`, TypeScript
`credentials.createInsecure()` / `ServerCredentials.createInsecure()`, Java and Kotlin `ManagedChannelBuilder...usePlaintext()`,
Go `grpc.WithInsecure()` / `insecure.NewCredentials()`, Rust tonic `Channel::from_static("http://...")`, Dart
`ChannelCredentials.insecure()`. A `unix:` target (already a local socket) and a loopback target are not findings; the
index counts them (`grpc_unix`, `grpc_loopback`). A target that is not a literal is reported at `heuristic`.

### IPC exposure (`ipc-exposed`)

| trigger | protocol | source |
|---|---|---|
| `window.postMessage(msg, '*')` | `postmessage` | the `SENDS_TO` edge's `target_origin: "*"` ([Protocol links](protocols.md)) |
| a Unix socket whose recorded `mode` is world-writable (`0o666`, `0o777`, `o+w`) | `unix` | `attrs.mode` on `endpoint:unix:<path>` |
| `manifest.json` `externally_connectable` with wildcard `matches` (`*://*/*`, `<all_urls>`) or `ids: ["*"]` | `extension` | index-time pass |
| `runtime.onConnectExternal` / `onMessageExternal` listener with no `sender.id` / `origin` / `url` check | `extension` | index-time pass |
| Electron `webPreferences` with `nodeIntegration: true`, `contextIsolation: false` or `webSecurity: false` | `electron` | index-time pass |

A message listener without an origin check, an `onMessageExternal` listener without a sender check, and an exported
Android component without a permission are `unguarded` findings already ([Link, payload, and guards](limitations.md#link-payload-and-guards)):
each item is reported under one finding only. A sender with a concrete origin, a `0o600` socket, a pinned manifest and an
Electron window with the secure defaults are not findings.

Every finding carries `finding`, `severity`, `confidence`, `protocol`, `node`, `file`, `line`, `entry_points`,
`detail` and `fingerprint`. `entry_points` lists the entry kinds that reach the finding (`kind`, `count`, `sample`; at
most five kinds, the total in `entry_point_total`). On a combined graph (`cg link`) each finding also names its `repo`.
The fingerprint hashes finding, node id and file, never the line, so it survives edits above the finding.

An `unguarded` finding as it prints after part 2d, on the `tests/surface_fixture` apps (the `/health` route is `public` and not listed; the second route reads only):

```text
$ cg surface --db out/graph.db --finding unguarded
attack surface: 2 finding(s) (1 medium, 1 low)
inbound surface: 9 item(s) (1 guarded, 1 public, 1 secret-checked, 4 unchecked, 2 unguarded); outbound surface: 6 system(s)

== unguarded (2) - Inbound handler without a guard ==
  http
    MEDIUM [resolved] route:ANY /admin/purge/
      ANY /admin/purge/ has no auth guard and reaches a write
      at py/bookshop/urls.py:7
      reached from http_route(1)
      fingerprint 4f95d4747c5550f1
    LOW [resolved] route:ANY /hooks/github-open/
      ANY /hooks/github-open/ has no auth guard
      at py/bookshop/urls.py:9
      reached from http_route(1)
      fingerprint 36091b3f583debb1
```

An unclear case adds its reason to the detail and has `heuristic` confidence, for example `ANY /graphql/ has no auth guard (unclear: GraphQL endpoint: access is checked per field)`.

```text
$ cg surface --db out/graph.db --finding hardcoded
attack surface: 2 finding(s) (2 high)
inbound surface: 7 item(s) (1 guarded, 1 secret-checked, 2 unchecked, 3 unguarded); outbound surface: 4 system(s)

== hardcoded (2) - Hardcoded credential ==
  postgres
    HIGH [exact] external:postgres:db.bookstore.example:5432
      literal password in code or configuration for db.bookstore.example:5432 (value not shown)
      at bookshop/settings.py:6
      reached from no entry point
      fingerprint 75454517a93c3363
  saas
    HIGH [exact] external:saas:postmark
      literal key in code or configuration for postmark (value not shown)
      at bookshop/notify.py:6
      reached from http_route(1)
      fingerprint 3412ee3e89b46e48
```

Disabled verification, insecure gRPC and IPC exposure on the test fixtures:

```text
$ cg surface --db out/graph.db --finding tls-off
== tls-off (8) - TLS or SSH host verification disabled ==
  ssh
    HIGH [exact] function:bookshop.catalog.push_stock
      SSH host key not checked (AutoAddPolicy / WarningPolicy)
      at bookshop/catalog.py:40
      reached from no entry point
      fingerprint 765683799095b51d
  tls
    HIGH [resolved] function:bookshop.catalog.sync_catalog
      certificate verification is switched off (verify=False)
      at bookshop/catalog.py:9
      reached from no entry point
      fingerprint 4a56ab0842be36a3

$ cg surface --db out/graph.db --finding plaintext --protocol grpc
== plaintext (2) - Plaintext protocol ==
  grpc
    MEDIUM [resolved] function:bookshop.catalog.inventory_channel
      client channel without TLS: inventory.bookstore.example:50051
      at bookshop/catalog.py:54
      reached from no entry point
      fingerprint 718a42ac53b9e2ce

$ cg surface --db out/graph.db --finding ipc-exposed
== ipc-exposed (8) - IPC endpoint open to untrusted callers ==
  extension
    MEDIUM [exact] file:config:extension-open/manifest.json
      externally_connectable.matches accepts any web page (*://*/*)
      at extension-open/manifest.json:6
      reached from message_handler(1)
      fingerprint f9f6fad48a93744a
  unix
    MEDIUM [exact] endpoint:unix:/run/bookshop/agent-open.sock
      Unix socket /run/bookshop/agent-open.sock is chmod 0o666: any local user can connect
      at agent/sock.py:10
      reached from message_handler(1)
      fingerprint 113ead5ceff1f789
```

## Options

| option | meaning |
|---|---|
| `--inbound` / `--outbound` | list that surface; findings are limited to its direction |
| `--protocol P` | one protocol (`http`, `ws`, `grpc`, `tcp`, `redis`, `postgres`, `saas`, ...) |
| `--finding F` | one finding type |
| `--min-confidence resolved` | drop `heuristic` findings and items (`exact` keeps only exact) |
| `--format text\|json\|sarif` | `json` is the full structure; `sarif` is SARIF 2.1.0 |
| `--fail-on f1,f2` | exit 1 when any listed finding remains after ignores |
| `--max-items N` | cap on listed findings and items (default 200); totals stay in the summary |
| `--strict` | `unguarded` counts route guards only: no framework defaults, no handler checks, no public-route table, medium severity |
| `--show-ignored` | list the findings `surface.ignore` accepted |

SARIF has one rule per finding type (`cg.surface.<finding>`), `level` from severity (high `error`, medium `warning`),
repo-relative locations, and the fingerprint in `partialFingerprints`. Ignored findings appear as results with a
`suppressions` entry only with `--show-ignored`.

## Gates

`--fail-on` exits 1 when a listed finding remains, 0 otherwise, 2 for an unknown name. It judges every finding type
after ignores, `--protocol` and `--min-confidence`; `--finding`, `--inbound` and `--outbound` only change what is printed.

```yaml
# GitHub Actions
- run: cg index . --db out/graph.db
- run: cg surface --db out/graph.db --format sarif > cg.sarif && cg surface --db out/graph.db --fail-on hardcoded,unverified
- uses: github/codeql-action/upload-sarif@v3
  if: always()
  with: { sarif_file: cg.sarif }
```

## Ignores

An accepted risk goes in `.cg.yaml`. The reason is required; `cg config validate` rejects an entry without one.

```yaml
surface:
  ignore:
    - {finding: unguarded, path: "app/Http/Controllers/HealthController.php", reason: public health probe}
    - {finding: plaintext, id: "external:redis:cache:6379", reason: compose-internal network}
    - {finding: hardcoded, fingerprint: 75454517a93c3363, reason: rotated test key}
```

`finding` is a finding type, plus one of `path` (a file, a directory or a glob, repo-relative), `id` (a node id or glob)
or `fingerprint`. Every key given must match. The file is read again from the indexed root on each run, so an ignore
applies without re-indexing. Ignored findings are counted in the summary and listed with `--show-ignored`. On a
combined graph an ignore applies to the findings of the repo whose `.cg.yaml` holds it.

## MCP

The `attack_surface` tool takes `inbound`, `outbound`, `protocol`, `finding`, `min_confidence`, `strict`,
`show_ignored` and `max_items`. The reply is the text report; the structured reply has the full result under
`surface` next to the `completeness` object ([MCP server](mcp.md)).

## No secret values in the index

A credential literal is never kept in the graph database. At index time a literal under a key that looks like a
credential (`password`, `secret`, `api_key`, `passphrase`, `dsn`, a token or key value, ...) is stored as `redacted:hmac:<first 8 hex of HMAC-SHA-256(salt, value)>`: it shows that a literal exists and tells two values apart
within the graph, and it cannot be reversed. The salt is `secrets.token_hex(16)`, created with the database and stored in
the `meta` table under `redact_salt`; re-indexing and `cg refresh` keep it, a new database gets a new one. The salt is not
secret from someone who holds the database, but a marker from a leaked screenshot, log or SARIF file cannot be matched
against a table of common passwords without it, and the same password in two graphs gives two markers. Fingerprints are
comparable inside one graph only: a `cg link` combined graph keeps the markers of its sources and has no salt of its own,
and comparing a secret across repos is not supported. Indexes made before part 2d hold `redacted:sha256:`; both prefixes
count as redacted, and re-indexing replaces the old form. Only the last word of the key (the last dotted segment, split on `_`, `-` and camelCase) decides: it is a credential when it is `password`, `passwd`, `pwd`, `pass`, `passphrase`, `secret` or `dsn`, when the name ends in an explicit compound (`api_key`, `access_key`, `secret_key`, `private_key`, `signing_key`, `encryption_key`, `client_secret`, `webhook_secret`, `auth_token`, `api_token`, `access_token`, `refresh_token`, `bearer_token`), or for Laravel's `app.key` / `APP_KEY`. A bare `key` or `token` as last word is redacted by default, as before. Its value is kept only when the word before it (a dotted segment counts) is one of `primary`, `foreign`, `sort`, `partition`, `route`, `cache`, `idempotency`, `remember`, `csrf`, `xsrf`, `public`, `i18n`, `translation`, `lookup`, `unique`, `index`, `storage`, `prefix` (`primary_key`, `cache.key`, `remember_token`), and the value never decides, because a token can have any shape (lowercase words, dotted, `key-` prefixed). `services.ledger.token` and `LEDGER_TOKEN` therefore stay redacted for any value that has a digit, an upper-case letter or a `base64:` form. `AUTH_GUARD`, `AUTH_PASSWORD_BROKER` and `try_it_credentials_policy` are not credentials, and `*_PUBLIC_KEY`, `*_KEY_ID`, `*_KEY_PATH` and `*_KEY_FILE` never are. This covers the Laravel config `value` and `env('X', 'default')` default attrs. A password
inside any `scheme://user:password@host` string in a node or edge attr gets the same marker, so the host stays
readable. Non-secret values (`'currency' => 'EUR'`, a hostname default) are stored as before, and `hardcoded` still
sees a marker as a literal. `cg node config:...` and a direct database read show the marker, never the value. The
external-system attrs of #42 parts 3a / 3b and the Python and TypeScript config readers keep addresses, key names and
locations only, so they have nothing to redact; a test indexes the surface fixtures and fails if a `SURF-` marker
appears in any table. The `doc` text of a node is swept as well, so an example DSN in a comment keeps its host and loses
its password. A map keyed by source identifiers (`reexports`) is left alone: an exported class named `ApiKey` is a name.

## Validation

Counts, sampled true / false positives per finding type and recall on three intentionally vulnerable apps are in the
[validation log](validation-log.md#attack-surface-47). `unguarded` is the noisiest type on large apps, and still needs review after part 2d ([Known limitations](limitations.md#attack-surface)).

## Not detected yet

Numeric validation limits are not produced by this version ([Known limitations](limitations.md#attack-surface)). New
detectors add a finding type; the shape above does not change.
