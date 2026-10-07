# Known limitations

code-graph is beta. This page is the cross-cutting ceiling: how far an answer reaches when the graph is incomplete.
Language gaps are on the language pages, linked at the end.

## Types and dispatch

- Types are flow-insensitive: one type set per variable per function. PHP generics are outside the current scope.
- An unresolvable receiver falls back to a unique method name (`heuristic`, with a stoplist).
- A Builder passed as a generic `Builder` parameter loses its model.
- A trait, interface, generic, or virtual call reaches every implementation or override in the graph.
- The walk narrows to the receiver's class only where that type is known: the TypeScript checker, a Python inferred instance, or a Kotlin / Swift / Dart / PHP typed value.
- Generic substitution such as `Repo[B]().get()` returning `T` stays unresolved.
- A callback passed as a JSX attribute is a reference from the JSX site. It stays unlinked from the component's prop, so a shared component does not become a hub of every call site.

## Gate scenarios

One scenario per index ([gate scenarios](configuration.md#gate-scenarios)).

- The analysis is per function and context-insensitive. Parameters are TOP, except literal-argument method summaries.
- A flag passed as data, or stored on a property (`$this->x`), stays live. That is the conservative choice.
- The same canonical expression is the same atom. Bare null or empty checks assume the inputs are present.
- A branch with more than 12 atoms is assumed feasible.
- Middleware that aborts when a flag is off is not a gate.
- An edge is gated only when the gated function itself makes the reference.

## Index and answers

- A missing toolchain degrades that language: `skipped`, or `heuristic` when the exact indexer is absent. The rest of the index still runs.
- `cg coverage` and the MCP `coverage` tool print the mode, the reason, and an install hint ([Completeness](completeness.md)).
- Route lists, caller lists, `reaches`, `tests`, and plan checks add a `coverage note:` when a blind spot or an unindexed file in the answer's languages and directories could affect them.
- Empty replies and unknown symbols end with a coverage line. MCP replies carry a `completeness` object.
- Blind-spot detectors are the list in [blind spots](completeness.md#blind-spots).
- Code generated at build or run time, and files outside the indexed root, are absent from the graph and from that list.
- Generated or copied files need a marker from [Generated files](generated.md): gitattributes, a banner, or a known build path. A generator with none of those is source until `generated.paths` names it.
- Dangling symlinks are skipped with a per-file warning. The TypeScript walker does not follow symlinked directories.
- References taken from a SCIP index are attributed to the nearest enclosing definition by source range.
- Go is a SCIP recipe (`plugins/stubs`), not a language plugin. Without a SCIP index its files are `unsupported`. Java is a language plugin ([Java](java.md)): heuristic by default, exact with scip-java when `CG_JAVA_SCIP=1` (one run shared with Kotlin). Spring facts are extracted with Kotlin from the shared JVM module.

## Link, payload, and guards

- `cg link` matches method and path ([cross-repo link](architecture.md#cross-repo-link)). Nest DTO fields and Fastify schema fields are stored and are not compared. Laravel FormRequest and inline validation keys, Elysia body schemas, and client body or query keys are compared when the route has a request schema.
- An endpoint whose origin was not traced matches by suffix at `heuristic`.
- PHP outbound HTTP covers Laravel `Http` / `Factory` / `PendingRequest` and Guzzle. Symfony HttpClient, `curl_*` and `file_get_contents` are not client endpoints. A base URL that does not resolve is kept (`origin_kind` `unknown`, `heuristic`) and is not matched by host. The sample base comes from `.env.example` or an `env()` default; `.env` is not read. A client verb `ANY` matches only when one route fits the path (`heuristic`).
- Mail and SMS API clients (`external:saas:<provider>`) are found when the client is built and used in one file. A client created in a factory and passed in is not followed, a `send()` on an untyped receiver is not guessed, and Laravel mailers other than `mailgun` / `postmark` / `resend` / `ses` are skipped (SMTP is a separate system).
- Push, Firebase, Kubernetes, Docker and key-management clients (#42 part 3b) are found by regex on the client built and used in one file. Firebase server SDKs only: browser and mobile client SDKs are not systems. A Firebase init in another file is used only when every init in the project agrees on the credential, a collection, vault or secret name that is not a literal or env key is left off the node id, and a Kubernetes or Docker client passed in from a factory is not followed. A kubeconfig, service-account or key path written in the code is a `credential_source` of `file` with no `CREDENTIAL_FROM`. Left out: Rust, Kotlin, Swift and Dart SDK libraries (`aws-sdk-*`, `google-cloud-*`, Firebase Android / iOS, Soto, `firebase_*`), Expo push from Python and PHP, and AWS KMS or GCP Secret Manager from PHP beyond the call shapes above.
- TS Node HTTP clients: `http` / `https` options must be an object literal (or a const object) with a readable `hostname` / `host`; `undici` `Client` / `Pool` are followed when built from a variable or class field initialiser; `agent`, `socketPath` and raw `net` / `tls` sockets are not. A host that is not a literal, `process.env` value or const map is a `ts_unrecognised_http_client` blind spot.
- Payload checks run for a single route match ([payload / field check](architecture.md#payload--field-check)). Ambiguous matches are skipped. An opaque or partial body (a spread, `Object.assign`, `FormData`, a value built in another function) does not produce `request_missing_required` or `request_unknown_field`. `sometimes`, `nullable`, and conditional rules do not count as missing. A route parameter is not a body or query key. A Laravel handler's query / body reads count as known keys ([which reads](php.md#request-keys-the-handler-reads)), and an unknown query key is `low` and only reported when the route has explicit validation. An Elysia model-map value is followed through one const hop (same module or one import / re-export); a longer chain or a computed map stays unknown, and an unresolved Elysia model name is not an empty schema.
- Webhook receivers include Cashier `handle<Event>` methods on a `WebhookController` subclass, spatie/laravel-webhook-client jobs and profiles (a custom validator only when that class checks a signature), verifying middleware by alias, class name or body, Kotlin `when` and Rust `match` on an event header or field, signature headers named by a constant in the same class or module or an import, spatie route attributes, and dotted plugin `router.post("name.action")` paths ([Webhook verification](protocols.md#webhook-verification)). A sender that reads its event from a stored subscription sends the events the repo declares (validation enum or list, config, seed data) and keeps `{event}` when none is declared; subscriber URLs in seed data, config and `.env.example` pair with routes in `cg link`; a callback URL built from the app's own base becomes `REGISTERS_CALLBACK`; and a payment webhook dispatched through a driver resolved at run time takes its checks and events from every implementation of the method. A callback URL built in a helper the function only calls, and drivers that share no interface or base class, are not followed.
- Server shapes are the declared schema or a returned literal. Framework error bodies (validation 422, auth 401, 500 pages) are absent.
- Enum checks need `choices=` or a `Literal` / `Enum` annotation.
- A route guard is a fact the framework plugin recorded on the route: middleware, Nest guards, `Depends`, `auth=`, permission classes.
- Laravel checks the action runs before its own work are inline guards, printed under `guards:` as `inline:`. That includes `authorize`, `Gate` with an abort, `abort_if` / `abort_unless` with a permission, a FormRequest `authorize()` that returns more than `true`, `$this->middleware(...)` / `HasMiddleware::middleware()` on the controller (`only` / `except`, including resource and invokable actions), a project check that rejects or whose result the caller aborts on, and a shared-secret `hash_equals` or `===` of `config()` / `env()` against a header or input. A discarded `can()` / `Gate::allows()` result, a check after a write, and a `===` between two request values are not guards. A check in a branch is `conditional`. See [PHP](php.md#inline-guards).
- A check inside the handler in other frameworks (`if (!req.user)`, `request.user.is_authenticated`) is not a guard.
- Project-wide defaults are not copied onto each route: Laravel kernel groups, Django `MIDDLEWARE`, DRF `DEFAULT_PERMISSION_CLASSES`. Controller `$this->middleware(...)` and `HasMiddleware::middleware()` are copied onto that controller's routes as inline guards.
- `cg routes --unguarded` hides a route with an unconditional inline check. `--unguarded --strict` uses route guards only. Preset lists decide which middleware names count as auth ([framework presets](configuration.md#framework-presets)).
- A project-specific guard name counts when it matches `auth.extra_patterns` or `--auth-pattern`.
- A sent-but-not-forwarded key is an object literal passed to a helper whose request keys are known, one call level deep. Spreads, runtime keys, and opaque objects are omitted, so no gap is reported for them.
- A realtime subscription wrapper (the channel is a parameter of the function that calls `echo.private(name)`) is expanded at its call sites, with one extra wrapper level. A third level, a call-site argument that is not a literal or template (a runtime value), a wrapper stored in an object or passed as a callback, and pusher-js `subscribe` wrappers stay unresolved: no `channel_sub` is emitted and `cg coverage --details` counts the call site.

## Attack surface

- `cg surface` reads recorded facts plus a regex pass (`insecure_transport`) over source and config files. The pass finds disabled TLS and SSH host-key verification, plaintext gRPC channels, `postMessage` `'*'`, wildcard `externally_connectable`, and Electron `webPreferences`. It reads text, not types: a flag set through a variable, a wrapper, or a non-literal target is not seen, and a gRPC target that is not a literal is counted but not reported. Test files are skipped and counted. `.env` files are never read. Numeric validation limits are not detected yet.
- `ssrf` is a text-level taint walk per function (`cg_code_graph/ssrf_input.py`), not a type analysis. It detects request input (Express / Nest / Next / h3 / Hono / Elysia, Laravel / `$_GET`, Django / Flask / FastAPI parameters) that picks the host or the whole URL of `fetch` / axios / got / undici / node http, Laravel `Http` / Guzzle / curl / `file_get_contents`, and requests / httpx / aiohttp / `urlopen` calls, and DNS lookups of input; it follows local variables, template / concatenation / `sprintf` / `new URL` / `urljoin`, and one helper level through a `CALLS` edge. It does not follow a value through a second helper, a class field, a queue or a database, an assignment that spans several lines, or a client built elsewhere (`axios.create`, a Guzzle `base_uri`), and an SDK `endpoint` / bucket host from input is not read. An allow-list is recognised only as a comparison with a constant or a name containing `allow` / `trusted` before the call; a regex or a `startswith` check is not seen, so a validated call can still be reported. Nuxt `server/` handlers give facts but no entry points, because they are not modelled as routes.
- `hardcoded` sees the credentials the external-system and SDK extractors flag as literal, DSNs with a password, and Laravel config literals. A literal in a Python or TypeScript settings value that no extractor ties to a system is not reported, and the config check is by key name (`password`, `token`, `secret`, `*_key`).
- `plaintext` needs a known non-loopback host and `tls=false`. A host from an environment variable with no example value is not reported. SMTP on port 587 is not called plaintext, because STARTTLS cannot be seen.
- `unguarded` and `unverified` follow the guard facts of [Link, payload, and guards](#link-payload-and-guards). Protocols that record no guards show `unchecked` and are never reported unguarded. Routes that are public by design need a `surface.ignore` entry.
- On a combined graph two repos naming the same external address share one node, so a credential literal recorded on the node belongs to the first repo; literal credentials passed at a call site keep their own repo and file.

## Plans, value facts, and the visual view

- Plan completeness rules are the named set: writers, readers, callers, clients, mirrors, identity columns, text mentions, and declared precedents. Anything else needs a plan entry or a `precedents` regex ([Planned changes](plans.md)).
- `role: guard` sees a graph read of the declared column by the guard method or a direct callee. Whether that branch blocks the path is left to the reader.
- `--verify` reports changed and unchanged spans against the baseline, plus the planned edges. The free-text `intent` stays with the reviewer.
- External clients come from the snapshot file. Private repos are not indexed for that list.
- Findings come from a hand-refreshed snapshot of issue evidence lines, mapped to the innermost function span.
- Plan text mentions match exact names (identity columns, relation names) inside `plans.text_mention_dirs`.
- Value-fact concept match is the head word of the target, or of the first source key. `--within` is a substring filter ([Value facts](value-facts.md)).
- The "returns" form treats source order as fallback order. Locals keep their last assignment. Helper inlining stops at depth 4.
- A setting → API response → client chain is not followed. Response fields are not nodes.
- The visual view is comfortable at a few hundred nodes. Above about 1,500 it keeps the closest ones (`truncated`). The server binds to localhost and has no auth ([Visual view](viz.md)).

## Language pages

| area | page |
|---|---|
| Python roots, references, routes | [Python](python.md) |
| PHP properties | [PHP](php.md) |
| TypeScript servers | [TypeScript / JavaScript frameworks](ts-frameworks.md) |
| Rust, C, C++ | [Rust, C and C++](native.md) |
| Kotlin | [Kotlin](kotlin.md) |
| Java | [Java](java.md) |
| Swift | [Swift](swift.md) |
| Platforms and variants | [Platforms](platforms.md) |
| Channels and tests | [Channels and tests](channels-and-tests.md) |
| Protocols | [Protocol links](protocols.md) |
| Bridges | [Web / native bridges](bridges.md) |
