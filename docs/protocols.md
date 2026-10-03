# Protocol links

cg pairs code that sends a message with the code that receives it, whatever the protocol: HTTP calls and routes,
broadcast channels, NestJS microservice messages, job queues, application events, web / native bridges, desktop IPC
and named endpoints of messaging protocols (Socket.IO today; MQTT, NATS, AMQP, Kafka and Redis pub/sub have their
matchers registered for the plugins that extract them). `impact`, `reaches`, `path` and `downstream` cross these
boundaries in one repo and, after `cg link`, between two repos; `cg protocols` lists the endpoints with their senders,
receivers, guards and checks.

## Model

```
sender code  -SENDS_TO->  endpoint:<protocol>:<name>  -RECEIVED_BY->  handler
endpoint (send side)  -MATCHES_ENDPOINT->  endpoint (receive side)       names differ but match by the protocol's rules
```

- `endpoint:<protocol>:<name>`: `name` is the normalised topic / subject / event / method, with parameters as
  `{param}` (`${order.id}` / `{{id}}` / `/:id` become `{id}`). Attrs: `protocol`, `transport` (tcp, udp, quic, local,
  ipc), `pattern` (a wildcard or template name), `side` (send, receive, both: which roles this graph has), `guards`
  (receivers: the checks protecting them, `[]` = known to have none), `namespace`, `event`, `schema`, `test_only`.
  A received endpoint of a network protocol is a `message_handler` entry point, so `reaches` on a consumer's table
  lists it even when the producer is outside the graph.
- `SENDS_TO` (code -> endpoint, propagating): attrs `role` (send, publish, emit, request, invoke, enqueue), `library`,
  `process` (server / client), `room`, `ack`, `schema`. From test code: `TEST_CALLS` with `orig` = SENDS_TO.
- `RECEIVED_BY` (endpoint -> handler, propagating): attrs `library`, `process`, `platform` (bridges).
- `MATCHES_ENDPOINT` (send-side endpoint -> receive-side endpoint, propagating): attrs `sender_name`, `pattern`,
  `segments`, `ambiguous` (number of equally specific receivers). File / line: where the receiver registers.
  Confidence: `resolved` for a wildcard or `{param}` fit, `heuristic` when a sender template fitted a receiver literal
  or the match is ambiguous. Identical ids need no edge: the combined DB of `cg link` keeps one node per id.

Matching runs at the end of `cg index` (both ends in one repo) and in `cg link` over the combined graph. `cg link`
adds `protocols` to its stats (per protocol: endpoints, send, receive, matched, match_edges, no_receiver, no_sender,
ambiguous, external) only when the graphs hold endpoints of these protocols, so the HTTP and channel output of existing graphs
is unchanged. Names matching `.cg.yaml` `protocols.external` (of either repo) count as `external`, not as
`no_receiver` / `no_sender`, in the index and link stats as in `cg protocols`.

Socket.IO is directional: one endpoint per event holds both directions, and every send and receive edge records
its `process` (`server` / `client`). A server `emit` reaches client handlers and a client `emit` server handlers
only, so a client that emits `chat` and also handles `chat` itself is `no_receiver` and `no_sender` until a server
handles it (`compatible()` in codegraph/protocols; a side without a known process matches either).

## Registry

`codegraph/protocols/` holds one `Protocol` per protocol: `name`, `transport`, default `ports`, URL `schemes`, a
name normaliser, a `matcher`, `fanout` (pub/sub: every matching receiver gets the message; request / routing
protocols link the most specific receivers and flag ties as ambiguous), `entry` (receivers are entry points), `guards`
(receivers record guards, so `unguarded` is checked) and `source` (`endpoint` for the generic model, `adapter` for the
kinds below, `bridges`). A plugin registers its own with `register(Protocol(...))` and reports facts with the builder
helpers:

```python
from codegraph.protocols import protocol_receive, protocol_send
protocol_send(builder, "mqtt", "devices/42/state", fn_id, file, line, "exact", role="publish", library="paho-mqtt")
protocol_receive(builder, "mqtt", "devices/+/state", handler_id, file, line, "exact", guards=[])
```

Shared matchers (`codegraph/protocols/matchers.py`):

| matcher | rules | protocols |
|---|---|---|
| `path` | URL segments, `{param}` / `{rest*}` / embedded params, one literal segment in common (the `cg link` route matcher) | http, ws |
| `mqtt` | `/` levels, `+` one level, `#` the rest (last only, also the parent level) | mqtt |
| `nats` | `.` tokens, `*` one token, `>` one or more (last only) | nats |
| `amqp_topic` | `.` words, `*` one word, `#` zero or more (anywhere) | amqp |
| `glob` | shell-style `*` / `?` / `[...]` over the whole name | kafka (regex subscriptions written as globs), redis-pubsub (PSUBSCRIBE) |
| `template` | whole-name `{param}` templates | socketio |
| `dotted` | `.` segments with `{param}` | pusher |
| `exact` | same name | bridges, nest-*, jobs, events, llm_tool |
| `mcp` | `<server>/<name>`: `*` for a client that does not name the server, `{param}` URI templates | mcp_tool, mcp_resource, mcp_prompt ([ai-tools.md](ai-tools.md)) |

## Existing kinds in the same view

Node ids are unchanged; `cg protocols` reads them through adapters (no extra nodes or edges are written):

| kind | protocol | senders | receivers | matches |
|---|---|---|---|---|
| `http` | http (`http:WS ...`: ws) | HTTP_CALLS | - | MATCHES_ROUTE -> route |
| `route` | http, ws (`route:WS`), graphql (`route:GRAPHQL`) | (tests: TEST_HTTP) | ROUTES_TO | <- MATCHES_ROUTE |
| `channel` | pusher | BROADCASTS_ON (events) | - | <- MATCHES_CHANNEL |
| `channel_sub` | pusher | - | SUBSCRIBES_CHANNEL (client code) | MATCHES_CHANNEL -> channel |
| `message` | nest-rpc, nest-event, nest-ws, grpc | DISPATCHES (ClientProxy.send / emit) | HANDLED_BY | - |
| `job` | bull, laravel-queue, celery | DISPATCHES (Laravel: to the job's handler, `via` job), SCHEDULES | HANDLED_BY | - |
| `event` | laravel-event, nest-event-emitter, django-signal | DISPATCHES | LISTENED_BY / HANDLED_BY | - |
| `endpoint` | capacitor, react-native, flutter, flutter-event, pigeon, electron-ipc, electron-preload, tauri | SENDS_TO | RECEIVED_BY | - |

`cg link`, `cg channels` and `cg bridges` stay the HTTP, Pusher and bridge views of the same data, with their output
unchanged.

## Checks

A side is judged only when the graph holds some endpoint of that protocol on the other side, so a backend indexed
alone does not report every route as `no_sender`, nor a client every call as `no_receiver`.

| check | meaning |
|---|---|
| `no_receiver` | sent, nothing in the graph receives it (directly or through a match) |
| `no_sender` | received, nothing sends it: a dead handler, or the producer is outside the analysed repos |
| `test_sender_only` | received, sent from tests only |
| `ambiguous` | a sender matched several receivers equally well (HTTP: several routes; request protocols: ties) |
| `schema_mismatch` | senders and receivers name different message types (when both are known) |
| `unguarded` | a receiver reachable from outside (HTTP / WebSocket / GraphQL routes, server-side Socket.IO handlers) with no auth guard: routes are classified as in `cg routes --unguarded`; a guard a plugin records on a receiver (a Socket.IO `connect` handler that rejects) counts as auth; Nest message handlers (`nest-rpc`, `nest-event`, `nest-ws`, `grpc`) record their `@UseGuards` (handler and class) and `APP_GUARD` guard classes, classified like route guards (`app.useGlobalGuards()` binds the HTTP app only, so it does not count) |
| `external` | declared in `.cg.yaml` (`protocols.external`), a third-party HTTP origin, a signal the framework itself sends (Django's `post_save`, `request_finished`, ...: never `no_sender`), or a bridge module implemented outside the repo |

Bridge endpoints keep the checks of [bridges.md](bridges.md) (`missing_on`, `no_receiver`, `no_sender`,
`unregistered`, `external`). Endpoints a known outside party handles are declared once:

```yaml
protocols:
  external: ["kafka:audit.*", "socketio:/#audit:*", "http:GET /status"]   # <protocol>:<name glob>
```

## `cg protocols`

```bash
cg protocols --db combined.db                         # one line per protocol: endpoints, send / receive, linked, checks
cg protocols --db combined.db --protocol socketio     # one block per endpoint
cg protocols 'orders.*' --db combined.db              # name, id, substring or glob; <= 6 hits: senders' entry points
cg protocols --db combined.db --unmatched --side send # only endpoints with a check or an external peer
```

`--json` returns `summary`, `endpoints` (id, kind, protocol, name, side, linked, checks, external, guards, senders,
test_senders, receivers, matches, at, transport) and `registry` (every registered protocol with its matcher). MCP:
`protocol_links(pattern?, protocol?, side?, unmatched?)`.

## Socket.IO (python-socketio, Flask-SocketIO)

`endpoint:socketio:<namespace>#<event>`, from module-level `socketio.AsyncServer()` / `Server()` /
`flask_socketio.SocketIO(app)` (server) and `socketio.AsyncClient()` / `Client()` / `SimpleClient()` (client) objects,
used in their module or imported by name:

- receive: `@sio.on('event', namespace='/ns')`, `@sio.event` (the function name is the event), `sio.on('event',
  handler)`, class namespaces (`socketio.Namespace` / `AsyncNamespace` `on_<event>` methods, namespace from
  `register_namespace(Cls('/ns'))`);
- send: `sio.emit('event', data, to=room, namespace='/ns')`, `sio.call(...)` (role request), `sio.send(data)` (event
  `message`); f-string names become templates (`f"order:{status}"` -> `order:{status}`, matched to `order:shipped`
  with confidence heuristic);
- guards: a server namespace's `connect` handler that raises `ConnectionRefusedError` or returns `False` is recorded
  on every server receiver of that namespace; server receivers without one record `guards: []` and are `unguarded`.
- `connect` / `disconnect` / `connect_error` are lifecycle callbacks, not endpoints.

`tests/protocol_fixtures` has two services: `orders-api` (FastAPI routes emitting through an `AsyncClient`) and
`worker` (Django models written by `AsyncServer` handlers). After `cg link --backend worker.db --frontend api.db`,
`cg path "route:POST /orders" table:orders` runs route -> handler -> `SENDS_TO endpoint:socketio:/orders#order:created`
-> `RECEIVED_BY` -> `WRITES_TABLE`.

## Not covered yet

- Extraction for the other registered protocols (MQTT, NATS, AMQP, Kafka, Redis pub/sub) and the rest of the epic's
  children (#32-#39); Socket.IO in TypeScript / Dart / Swift / Kotlin, `ws` / SSE message names, rooms as their own
  endpoints (#32).
- Guards on Bull processors are not recorded on the adapted `job` nodes, so `unguarded` is not checked for them.
- `schema_mismatch` needs `schema` on both sides; no extractor records message types yet.
- Broker / host nodes (#30 / #40) are not attached to endpoints yet.
