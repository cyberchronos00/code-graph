# How we validate

Public-project checks, not a tutorial. Each area was indexed from a shallow clone (default
branch, October 2026) with `python -m codegraph.cli index <repo>` on one 8-core Linux box. Wall
time includes a cold extractor cache, not a one-off `dart compile exe`.

A **spot check** is 20 routes (or every route when there are fewer) sampled from the graph and
compared by hand with the cited source line: method, full path (every prefix), parameters, and
handler symbol. A pass means those rows match. Gaps sit next to the table that produced them.

Full scoreboards, timings and the "other corpora unchanged" notes are in
[Validation log](validation-log.md). This page is the method plus one headline per area.

## Headlines

| area | headline | log |
|---|---|---|
| Django / Python | 7 apps; sampled routes 20/20 (saleor is GraphQL: 9 routes, resolvers are endpoints) | [Django](validation-log.md#django--python) |
| Flutter / Dart | 5 apps; flutter/samples 12/12 HTTP and 13/13 go_router routes; lichess 18/20 | [Flutter](validation-log.md#flutter--dart) |
| Laravel channels | UNIT3D, koel, invoiceninja, pixelfed, coolify: publishers matched each `ShouldBroadcast` event | [Channels](validation-log.md#laravel-broadcasting-and-tests) |
| Laravel tests | TEST_HTTP spot check 40/40 (10 per app) | same section |
| Nuxt | breeze-nuxt and elk, no `npm install`, no `.nuxt` | [Nuxt](validation-log.md#nuxt-layouts-and-clean-checkouts) |
| Presets / guards | `cg starters` and `cg routes --unguarded`, no `.cg.yaml` | [Presets](validation-log.md#presets-starter-queries-and-route-guards) |
| Monorepo | one index per app (`immich/server`, `cal.com/apps/web`, …) | [Monorepo](validation-log.md#monorepo-apps) |
| Generated files | Capacitor copies and OpenAPI / protoc output excluded; `cg coverage` lists them | [Generated](validation-log.md#generated-and-copied-files) |
| Platforms | ripgrep, alacritty, libuv, curl, dart-lang/http, localsend, social-app | [Platforms](validation-log.md#platform-specific-code) |
| Kotlin / Swift | heuristic calls, then scip-java and the Swift index store | [Kotlin](validation-log.md#kotlin), [Swift](validation-log.md#swift) |
| Bridges | Capacitor, React Native, Flutter, Cordova, Pigeon, Electron, Tauri | [Bridges](validation-log.md#web--native-bridges) |
| Protocols | Socket.IO, WS/SSE, webhooks, IPC, sockets, gRPC, GraphQL, jobs, brokers | [Protocols](validation-log.md#protocol-links-shared-endpoint-model-31) |
| TS frameworks | immich, Nest samples, Ghost, dub, Next examples, realworld `cg link` | [TS frameworks](validation-log.md#typescript--javascript-frameworks) |
| External | Prisma / ORM datasources, third-party HTTP hosts, cloud SDKs | [External](validation-log.md#external-systems-40) |
| AI tools | MCP / LLM tool endpoints on public harnesses | [AI tools](validation-log.md#ai-harnesses-llm-tools-mcp-servers-and-agents-66) |
| Parity | Bitwarden and Element X, name match and `--structure` | [how far to trust missing](parity.md#how-far-to-trust-missing) |

## How to read a gap

- A handler marked external (Django `LoginView`, allauth) was compared and left unresolved on
  purpose.
- `{?}` in a path is a setting or f-string cg did not evaluate (netbox `BASE_PATH`).
- A route count that matches the decorator count is a census, not a spot check. The spot check
  is the hand sample.
- Exact mode (Kotlin scip-java, Swift index store) is a second index of the same ids. Heuristic
  edges that the compiler does not confirm stay in the log's precision / recall note.

## More log sections

These are the same runs, split by change. Open the log for the table.

| topic | log |
|---|---|
| Overrides, inherited specs, class hierarchy | [Overrides](validation-log.md#overrides-in-tests--reaches-inherited-specs-typescript-class-hierarchy) |
| Receiver types, interfaces, container bindings | [Receivers](validation-log.md#receiver-types-on-inherited-calls-in-kotlin-swift-dart-and-php-62) |
| Swift selectors and tests | [Selectors](validation-log.md#swift-selectors-static-vs-instance-sdk-receivers-70), [Swift Testing](validation-log.md#swift-testing-xctest-and-kotlin-test-cases-71) |
| Sockets, gRPC, Thrift, tRPC, JSON-RPC | [Sockets](validation-log.md#raw-tcp--udp-sockets-and-udp-application-protocols-39), [gRPC](validation-log.md#grpc-services-from-proto-contracts-33) |
| GraphQL, job queues, brokers | [GraphQL](validation-log.md#graphql-root-fields-34-part-1), [Jobs](validation-log.md#job-queues-celery-rq-dramatiq-bull-laravel-messenger-36), [Brokers](validation-log.md#message-brokers-kafka-amqp-redis-mqtt-nats-35-part-1) |
| Webhooks and local IPC | [Receivers](validation-log.md#webhook-receivers-37-part-1), [Senders](validation-log.md#webhook-senders-37-part-2), [IPC](validation-log.md#local-ipc-in-js--ts-38-part-1) |
| Plain JavaScript, C macros | [JS](validation-log.md#plain-javascript-without-tsconfig--jsconfig-136), [C/C++](validation-log.md#cc-file-level-macro-statements-and-lock-annotations-131) |
| Datasources and HTTP hosts | [Prisma](validation-log.md#prisma-datasources-as-external-systems-41), [HTTP hosts](validation-log.md#third-party-http-hosts-as-external-nodes-42-part-1), [Cloud SDKs](validation-log.md#cloud--saas-sdks-as-external-nodes-42-part-2) |

## Re-run

```bash
cg index <repo> --db out/check.db
cg coverage --db out/check.db
cg routes --unguarded --db out/check.db
cg protocols --unmatched --db out/check.db
```

Language pages link the log section for their numbers. A census (decorator count equals route
count) is not a spot check; the spot check is the hand sample.
