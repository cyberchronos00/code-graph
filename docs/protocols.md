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
- `QUEUE_ROUTES` (job queue endpoint -> a task routed to it, not propagating): a receiver in `cg protocols` and
  `cg link`. It does not propagate because a send to a queue runs only the job it names, not every job of the queue
  ([job queues](#job-queues)).
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
| `path` | URL segments, `{param}` / `{rest*}` / embedded params, one literal segment in common (the `cg link` route matcher) | http, ws, sse |
| `mqtt` | `/` levels, `+` one level, `#` the rest (last only, also the parent level) | mqtt |
| `nats` | `.` tokens, `*` one token, `>` one or more (last only) | nats |
| `amqp` | `<exchange>/<routing key>`: the same exchange, then the key by topic-exchange rules (`.` words, `*` one word, `#` zero or more, anywhere); `queue:<name>` exact | amqp |
| `glob` | shell-style `*` / `?` / `[...]` over the whole name | kafka (regex subscriptions written as globs), redis-pubsub (PSUBSCRIBE) |
| `template` | whole-name `{param}` templates | socketio |
| `dotted` | `.` segments with `{param}` | pusher |
| `exact` | same name | bridges, nest-*, jobs, events, llm_tool, redis-stream |
| `mcp` | `<server>/<name>`: `*` for a client that does not name the server, `{param}` URI templates | mcp_tool, mcp_resource, mcp_prompt ([ai-tools.md](ai-tools.md)) |

## Existing kinds in the same view

Node ids are unchanged; `cg protocols` reads them through adapters (no extra nodes or edges are written):

| kind | protocol | senders | receivers | matches |
|---|---|---|---|---|
| `http` | http (`http:WS ...`: ws; `stream: sse`: sse) | HTTP_CALLS | - | MATCHES_ROUTE -> route |
| `route` | http, ws (`route:WS`), sse (`stream: sse`), graphql (`route:GRAPHQL`; senders through its `endpoint:graphql:` twin) | (tests: TEST_HTTP) | ROUTES_TO | <- MATCHES_ROUTE |
| `channel` | pusher | BROADCASTS_ON (events) | - | <- MATCHES_CHANNEL |
| `channel_sub` | pusher | - | SUBSCRIBES_CHANNEL (client code) | MATCHES_CHANNEL -> channel |
| `message` | nest-rpc, nest-event, nest-ws, grpc (Nest `@GrpcMethod`) | DISPATCHES (ClientProxy.send / emit) | HANDLED_BY | - |
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
| `no_consumer` | a job queue that jobs are sent to; the repo starts workers of that framework and none consumes it ([job queues](#job-queues)) |
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
`protocol_links(pattern?, protocol?, side?, unmatched?, listeners?)`. `--listeners` lists every listening TCP / UDP
socket with its exposure, bind address and handler (raw sockets, below).

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

### Socket.IO in JS / TS (#32 part 1)

`codegraph/realtime_events.py` writes the same `endpoint:socketio:<namespace>#<event>` nodes for Node servers, browser /
Node clients and Nest gateways, so a TS client links with a Python or Node server. A file's process (`server` or
`client`, recorded as `process`) comes from its `socket.io` / `socket.io-client` imports, or else from the nearest
`package.json` dependencies.

- objects: `new Server(..)`, `require('socket.io')(..)` and `io.of('/ns')` (server); `io(url)`, `io.connect(..)`
  and `new Manager(..).socket('/ns')` (client, namespace from the URL path, `/` for a bare origin); Nest
  `@WebSocketServer()` fields (the gateway's namespace); parameters and fields typed `Socket` / `Namespace` /
  `Server`; the socket parameter of `X.on('connection', (socket) => ..)` (X's namespace).
- send: `emit` (role `emit`), `emitWithAck` and `timeout(ms).emit` (role `request`), `send` (event `message`).
  `.to(room)` / `.in(room)` / `.except(room)` / `.broadcast` are recorded as `room` / `broadcast`. A ternary event
  name gives both events (heuristic).
- receive: `on` / `once`, including chained `socket.on('a', ..).on('b', ..)`. `connect`, `disconnect`,
  `connection` and the other reserved events are not endpoints.
- wrappers: a function that emits its own parameter (`clientSend(event, room, ..)`) is resolved at its call sites,
  through CALLS edges or, when there are none, through calls on a receiver named after its class (heuristic).
- guards: `X.use(mw)` middleware is recorded on the server receivers of X's namespace.
- Nest: each `message:ws:` handler of a gateway gets an `endpoint:socketio:<ns>#<pattern>` RECEIVED_BY with its
  guards (`via` = the message node), unless the project uses `@nestjs/platform-ws` without
  `@nestjs/platform-socket.io` or calls `useWebSocketAdapter(new WsAdapter(..))` (the raw WebSocket adapter).

## WebSocket connections and Server-Sent Events (#32 part 2)

WebSocket connections are routes: a client endpoint `http:WS <path>` matches a `route:WS <path>` (entry kind
`websocket`) with the HTTP path matcher, in the repo and across repos with `cg link`. SSE streams stay HTTP routes
and calls, marked `stream: sse`; `cg protocols --protocol sse` lists them (and `--protocol http` no longer does).

| side | WebSocket (`ws`) | SSE (`sse`) |
|---|---|---|
| server | `ws`: `new WebSocketServer({ path, port, server })` / `new WebSocket.Server(..)`, handler of `wss.on('connection', h)` (`codegraph/realtime_ws.py`); express-ws / Elysia `app.ws(path, h)`, @fastify/websocket `{ websocket: true }`, Hono `upgradeWebSocket(h)` (express plugin); Python `websockets.serve(handler, host, port)`; FastAPI / Starlette `@app.websocket`, Django Channels (unchanged); Nest `@WebSocketGateway` on `@nestjs/platform-ws` (`codegraph/plugins/nest/plugin.py`) | a route whose handler sets `Content-Type: text/event-stream` (header, `media_type=`, `mimetype=`; not an `Accept` header), returns `EventSourceResponse(..)` or calls Hono `streamSSE(..)`; Nest `@Sse()` |
| client | browser `new WebSocket(url)`, `ReconnectingWebSocket`, `Sockette`; Dart `web_socket_channel` (unchanged) | `new EventSource(url)` (and polyfills), `fetchEventSource(url, { method })` |

- A `ws` server without a `path` option takes any path: it is `route:WS /` with `any_path` (and `ports` when the port
  is a literal) and is not matched by path. A `noServer` server takes the path its upgrade handler checks before
  `handleUpgrade` (`pathname === '/x'`, a variable assigned from `new URL(..).pathname` or `url.parse(..).pathname`,
  `case '/x':` in a switch on that path, or `req.url.startsWith(p)` as `p/{rest*}`, heuristic). A server stored under
  a path key in a same-file object or `Map` (`{ '/x': wss }`, `new Map([['/x', wss]])`) takes that key when the table
  is indexed by the pathname immediately before `handleUpgrade`. Servers started by tests
  are not entry points. An inline connection callback without its own node leaves
  `handler_unresolved` rather than routing to the module.
- Nest `@WebSocketGateway` is `route:WS <path>` (`framework: nest`, entry kind websocket) when the project depends on
  `@nestjs/platform-ws` and not `@nestjs/platform-socket.io`, or when `useWebSocketAdapter(new WsAdapter(..))` is found.
  `{ path: '/events' }` and `(8080, { path: '/events' })` are `/events` (`ports: [8080]` when the port is a literal).
  No `path` is `route:WS /` with `any_path` (not matched by path). A non-literal path resolves a same-file constant;
  otherwise it stays unresolved (`/{path}`, heuristic). The route points at `handleConnection` when that method exists,
  otherwise `handler_unresolved` (never the class). `@SubscribeMessage` `message:ws:` nodes stay as they are, and a
  Socket.IO gateway project gets no `route:WS`.
- Client URLs: `${proto}://${location.host}/x` and `wss://${host}/x` are read as `{host}/x` (the page's or a
  configured server, like `${origin}/x` for HTTP); `ws://localhost:9100` keeps its origin (`origin_kind: other`, not
  matched). A same-origin relative URL is not matched across repos unless it is under `/api/`, as for HTTP.
- Not yet: message names inside a connection (`switch (msg.type)` / `send(JSON.stringify({ type }))`), SSE `event:`
  names, and Python WebSocket clients.

## Socket.IO and WebSocket in Dart / Kotlin / Swift / Rust (#32 part 3)

`codegraph/realtime_native.py` adds the same `endpoint:socketio:<namespace>#<event>` endpoints as the JS / TS
extractor, from a source scan of each language's function nodes:

| language | library | receive (RECEIVED_BY) | send (SENDS_TO) |
|---|---|---|---|
| Rust | socketioxide (server) | `socket.on("e", h)` inside `io.ns("/ns", on_connect)` (the namespace's connect handler, and handlers it registers) | `socket.emit`, `.to("room").emit` / `.within(..)` (room recorded), an ack closure as last argument (request) |
| Rust | rust_socketio (client) | `ClientBuilder::new(url).namespace("/ns").on("e", h)` | `client.emit("e", ..)` on the built client |
| Dart | socket_io_client | `socket.on('e', h)` / `once` on `io.io(url)` / `io(url, opts)` (`'$base/ns'` gives the namespace) | `emit`, `emitWithAck` / ack callback (request) |
| Kotlin / Java | socket.io-client-java | `socket.on("e") { }` / `on("e", listener)` on `IO.socket(url)` | `emit("e", ..)`, with an `Ack` as last argument (request) |
| Swift | socket.io-client-swift | `socket.on("e") { }` on `manager.defaultSocket` / `socket(forNamespace:)` | `emit`, `emitWithAck(..)` (request) |

Event names follow literals, string templates (`"$prefix:x"`, `"\(prefix):x"`) and `Owner.CONST` constants of the
same file; a name that stays unknown is not an endpoint. A handler passed by name (`h`, `::onMessage`,
`self.onMessage`) receives the event; a closure leaves the enclosing function as the receiver.

WebSocket routes and clients outside JS / TS:

| side | Kotlin | Swift | Rust |
|---|---|---|---|
| server (`route:WS`, entry kind `websocket`) | Ktor `webSocket("/x") { }` in `routing { route(..) { } }` | Vapor `app.webSocket("x") { req, ws in }` (and on route groups) | an axum route whose handler takes a `WebSocketUpgrade` argument |
| client (`http:WS`) | Ktor `client.webSocket(url)` / `ws` / `wss`; OkHttp `newWebSocket(Request.Builder().url(u).build(), l)` | `URLSession.webSocketTask(with: url)` | (no Rust HTTP client model) |

Rust routes now carry `uri` / `method` like the other plugins (`:id` / `<id>` → `{id}`, `*rest` / `{*rest}` /
`<rest..>` → `{rest*}`), so `cg link` matches Rust servers with clients in other repos.

## Webhook receivers (#37 part 1)

A webhook receiver is a route a third party calls; its only protection is usually a signature check.
`codegraph/webhooks.py` scans each route's handler, the functions it calls or dispatches to (two levels) and its
middleware functions:

| result | when |
|---|---|
| verified | Stripe `constructEvent` / `construct_event`; svix / standardwebhooks `new Webhook(secret).verify(..)`; @octokit/webhooks `verify` / `verifyAndReceive`; Twilio `validateRequest` / `RequestValidator(..).validate`; Shopify `webhooks.validate`; an HMAC (`createHmac`, `hmac.new`, `hash_hmac`, Rust `Hmac::new_from_slice`, Java `Mac.getInstance("Hmac..")`) with a constant-time comparison (`timingSafeEqual`, `compare_digest`, `hash_equals`, ...) or a provider signature header (then `constant_time: false` without the comparison); a provider token header (`X-Gitlab-Token`) compared in constant time; a route middleware named like `VerifyWebhookSignature` |
| unverified | the handler reads a provider header (`Stripe-Signature`, `X-Hub-Signature-256`, `X-GitHub-Event`, `X-Gitlab-Event`, `X-Twilio-Signature`, `svix-signature`, ...) and nothing above verifies it |

The route gets `attrs.webhook = {provider, verified, how, check (file:line), replay_protection, events}`. The provider
comes from the library or the header (an event header wins), else from the route path or handler name
(`/mailgun_webhook`), else `hmac`. `cg routes` lists a verified check as a guard (`webhook signature (..)`, counted
as SECRET-CHECKED) and flags an unverified receiver `WEBHOOK UNVERIFIED (<provider>)`, which `--unguarded` keeps.
Headers read by shared helpers (called from more than six functions, such as audit loggers) do not count.

Events: comparisons of the event type with a literal become `endpoint:webhook:<provider>:<event>` (protocol `webhook`,
`cg protocols --protocol webhook`), RECEIVED_BY the function holding the comparison:

- `switch (event.type) { case 'x': }`, `if (event.type === 'x')`, Python `event["type"] == "x"` and `match`, PHP
  `$event->type`, `data_get($event, 'type')` and `match ($event) { 'x', 'y' => .. }`;
- the event variable is the result of the verification call (exact) or a payload named `event`, `payload`, `body`,
  `data`, ... (heuristic); keys `type`, `event`, `event_type`, `eventType`, GitLab `object_kind`; aliases
  (`$type = data_get($this->event, 'type')`) and event headers (`name = req.headers['x-github-event']`) are followed;
- a branch that calls only one or two project functions (shared helpers, predicates such as `is*` / `get*` and
  logging left out), not called in the other branches, makes them receivers too: they run only for that event
  (`how: event branch`, `via` = the dispatching function), so `impact` on `markInvoicePaid` lists `stripe:invoice.paid`;
- @octokit/webhooks `webhooks.on('push', fn)` receives `github:push` directly.

Provider events are sent from outside, so they are never `no_sender`.

Senders (#37 part 2) send `endpoint:webhook:<this project>:<event>`:

| sender | event name |
|---|---|
| svix `svix.message.create(appId, { eventType })` (JS / TS, Python `event_type=`, PHP `eventType:`) | `eventType` |
| spatie/laravel-webhook-server `WebhookCall::create()->..->dispatch()` | an `'event'` / `'type'` key of the chain's payload, else `{event}` |
| a function that POSTs (`fetch`, axios, requests, `session.send`, httpx, Laravel `Http::`, Guzzle `->post` / `->request('POST', ..)`, curl) with a signature header it writes (`'X-Acme-Signature': sig`, `headers['X-Sig'] = ..`; a bare `Signature` key only inside a headers block) and an HMAC or a signing helper (`createSignature(..)`, `$signatureGenerator->generate(..)`) there, or an HMAC in a function it calls | an `event` / `type` / `triggerEvent` / `eventType` literal in the function; else, when a parameter is named like `event` / `triggerEvent` / `eventType`, the literal each caller passes (the caller sends, `via` = the function); else `{event}` (heuristic) |

`cg link` pairs a sender with a receiver of the same `<app>:<event>`, and with a receiver whose provider is only a
signature scheme (`hmac`, `svix`, `standard-webhooks`) and the same event name (heuristic). Not yet: Laravel Cashier
and spatie/laravel-webhook-client conventions, Kotlin `when` / Rust `match` dispatch, event names held in a stored
subscription (`webhook.event_type`), and pairing through subscriber URLs in seed data or config.

## Local IPC in JS / TS: workers, BroadcastChannel, postMessage, extensions (#38 part 1)

`codegraph/local_ipc.py` links messages between execution contexts of one web app or browser extension:

| protocol | endpoint | senders | receivers |
|---|---|---|---|
| `worker` | `<script>` (repo path) | `w.postMessage(..)` on `w = new Worker(new URL('./w.ts', import.meta.url))` / `new Worker('w.js')` / `new SharedWorker(..)` / Vite `import W from './w?worker'; new W()` (also `this.w`, and a getter function that returns it); comlink `wrap(w)` | the script's `self.onmessage = h` / `addEventListener('message', h)`; comlink `expose(obj)` |
| `worker` | `<script>:out` | the script's `self.postMessage(..)` | `w.onmessage = h` / `w.addEventListener('message', h)` |
| `worker` | `service-worker` | `navigator.serviceWorker.controller.postMessage(..)`, `registration.active.postMessage(..)`, a variable taken from `navigator.serviceWorker.controller` / `.ready` | `self.addEventListener('message', h)` in a file that handles `install` / `activate` / `fetch` |
| `worker` | `service-worker:out` | `client.postMessage(..)` in the service worker (heuristic) | `navigator.serviceWorker.addEventListener('message', h)` / `.onmessage` |
| `broadcastchannel` | `<channel name>` (literal or constant) | `.postMessage` on a `new BroadcastChannel(name)` | `.onmessage` / `.addEventListener('message', h)` on any channel of that name |
| `postmessage` | `<type>` or `*` | `window.parent / opener / top / frames[i] / iframe.contentWindow / event.source / window .postMessage(msg, origin)`; `<type>` is the payload's `type` / `action` / `event` / `kind` / `messageType` / `cmd` literal or constant, else `*` (heuristic); `target_origin: "*"` when posted to any origin | `window.addEventListener('message', h)` / `window.onmessage = h` outside workers: one endpoint per type the handler compares (`event.data.type === 'x'`, `case 'x':` under `switch (..type)`; heuristic), else `*` (all types). Guard: `origin check` when the handler reads `event.origin` or compares `event.source`, else `[]` (`unguarded`) |
| `extension` | `<type>` or `*`; `port:<name>` | `chrome.runtime.sendMessage(msg)`, `chrome.tabs.sendMessage(tab, msg)` (and `browser.*`); `runtime.connect({ name })` / `tabs.connect(tab, { name })` | `runtime.onMessage.addListener(h)` (types as for postmessage); `runtime.onMessageExternal` records a `sender.id` / `.origin` / `.url` check as its guard; `runtime.onConnect.addListener(h)` per `port.name === 'x'` compared |
| `native-messaging` | `<host name>` | `runtime.connectNative('com.app.host')` / `sendNativeMessage(..)` | the in-repo program named by the `path` of a host manifest (`"type": "stdio"`, `"name"`) |

`postmessage` and `extension` match message types as globs, so a `*` listener receives every type; a sender whose
type is unknown reaches only `*` listeners, and a `port:` connection only `port:` listeners. Types are read from
string literals, constants and enum members (`case MessageType.SAVE:`), in the listener and in the functions it
hands the message to (one level; the callee receives them, `via` the listener; type guards named `is*` / `has*` /
... are skipped). Port messages (`port.postMessage`), `MessageChannel` ports, message
types of worker messages, and manifest exposure facts (`externally_connectable`, `web_accessible_resources`) are not
recorded yet. Electron / Tauri IPC and web-to-native bridges are `cg bridges` (codegraph/bridges.py).

## Unix domain sockets, named pipes / FIFOs and D-Bus (#38 part 2)

`codegraph/local_sockets.py` links processes on one machine that talk over a socket path, a pipe name or a D-Bus
member:

| protocol | endpoint | listeners / services | connectors / clients |
|---|---|---|---|
| `unix` | `<path>` | Python `socket(AF_UNIX).bind(p)`, `asyncio.start_unix_server(cb, p)` (the callback), `socketserver.Unix*Server(p, Handler)` (`Handler.handle`), `multiprocessing.connection.Listener(p)`, `uvicorn.run(app, uds=p)`, aiohttp `UnixSite`; Node `server.listen(p)` / `.listen({ path })` with a path (a number stays `tcp`); Rust `UnixListener::bind` / `UnixDatagram::bind` (std, tokio); C `bind()` after `strncpy(addr.sun_path, p, ..)` / `snprintf`, `uv_pipe_bind`; PHP `stream_socket_server('unix://p')`; gRPC `server.add_insecure_port('unix:..')` | Python `socket(AF_UNIX).connect(p)`, `asyncio.open_unix_connection(p)`, `multiprocessing.connection.Client(p)`, aiohttp `UnixConnector(path=p)`, httpx `HTTPTransport(uds=p)`; Node `net.connect(p)` / `createConnection({ path })`, `http.request({ socketPath })`; Rust `UnixStream::connect`; C `connect()` after `sun_path`, `uv_pipe_connect`; PHP `stream_socket_client('unix://p')`; gRPC `unix:` targets (`grpc.insecure_channel('unix:///run/x.sock')`, tonic `Endpoint::try_from(..)` / `GreeterClient::connect(..)`) |
| `pipe` | `<name>` | Windows named pipes `\\.\pipe\<name>`: tokio `ServerOptions::new()..create(p)`, Node `listen(p)`, C `CreateNamedPipe(p)`; FIFOs: `os.mkfifo(p)` / `mkfifo(p)` (the creating function receives) | tokio `ClientOptions::new()..open(p)`, Node `connect(p)`; FIFO writers: `open(p, 'w')`, `os.open(p, os.O_WRONLY)`, C `open(p, O_WRONLY)` / `fopen(p, "w")` on a path some code passes to mkfifo |
| `dbus` | `<interface>.<Member>` | zbus `#[interface(name = "..")]` impl methods (snake_case -> PascalCase, `#[zbus(name = "..")]`), dbus-next / dasbus `ServiceInterface` `@method()`, dbus-python `@dbus.service.method('iface')`; `#[zbus(signal)]` / `@signal()` members are senders (`role: emit`) | zbus `#[proxy(interface = "..")]` trait methods, dbus-next `call_<member>` on `get_interface('iface')`, dbus-python `dbus.Interface(obj, 'iface').Member()`, GDBus `g_dbus_connection_call(.., "iface", "Member", ..)`, sd-bus `sd_bus_call_method` |

Paths are read from literals, constants (same file, else a unique project constant, also through
`os.path.join` / `path.join` / pathlib `/`), environment variables (`env:NAME`) and templates (`{run_dir}/app.sock`,
heuristic); `unix` and `pipe` match templates. A listener records `mode` when its function chmods the path. A path
that comes from configuration only is counted under `unix_path_unknown` in the index stats. Abstract sockets
(`\0name`), socket activation (systemd `.socket` units) and D-Bus service files are not read.

## Android intents and AIDL (#38 part 3)

`codegraph/android_ipc.py` links Android components of one repository (Kotlin, and Java files when they are indexed):

| protocol | endpoint | senders | receivers |
|---|---|---|---|
| `intent` | `<component class FQN>` | `Intent(ctx, Foo::class.java)` / `new Intent(ctx, Foo.class)`, `setClass(ctx, Foo::class.java)`, `ComponentName(ctx, Foo::class.java)`, and `ComponentName(pkg, "com.x.Foo")` (passed to a consuming call or `setComponent`) / `setClassName(pkg, ..)` with a literal or constant naming an in-repo class; nothing inside an assertion; `via` is the call that uses the intent (`startActivity`, `startService`, `startForegroundService`, `bindService`, `sendBroadcast`, `PendingIntent.getActivity` / `getBroadcast` / `getService`, ...) | the component's entry method: `onReceive` (receiver); `onStartCommand` / `onBind` (unless it only returns null) / `onHandleIntent` / `onMessageReceived` / `onCreate` (service); `onCreate` (activity); else the class. `component` is the manifest tag, or the superclass |
| `intent-action` | `<action>` | `Intent("com.x.ACTION")` / `Intent(ACTION_CONST)`, `setAction(..)`, `action = ..` in an `Intent().apply { }` | `<intent-filter><action android:name>` on a manifest receiver / service / activity; `IntentFilter(X)` / `addAction(X)` in code (the file's `onReceive`). Fan-out |
| `aidl` | `<package.IFace>.<method>` | calls of the interface's methods in code that names `IFace` (`IFace.Stub.asInterface(binder).m()`, `bridge?.m()`, `RemoteCallbackList` items; resolved when called on a receiver, heuristic for bare calls in a lambda), outside the interface's own Stub; mockk `every { }` / `verify { }` and Mockito `when(..)` / `verify(x).` are not calls | `override fun m(..)` in `object : IFace.Stub()` / `class X : IFace.Stub()` / `extends IFace.Stub` |

Receivers of components declared in a manifest carry their exposure: `exported` (the `android:exported` attribute,
else true when the component has an intent filter, the pre-Android-12 default) and `permission` (the component's
`android:permission`, else the application's). The endpoint records the permission (`permission <name>`) or
`not exported` as its guard; an exported component without a permission can be started by any app, so `cg protocols`
flags it `unguarded`. Launcher activities (an `android.intent.action.MAIN` filter) are public by design and are not
checked.

Methods come from the `.aidl` files (`package`, `interface`, `oneway` methods). Platform actions (`android.*`,
`com.google.android.*`, `com.google.firebase.*`, `Intent.ACTION_*` and other SDK constants) are not recorded: no
repository sends them. Content providers (`content://` authorities), `Messenger` services, the `Class` constants given
to `setClass(ctx, CLASS)`, and intent extras are not covered yet.

## Child processes (#38 part 3)

| protocol | endpoint | senders | receivers |
|---|---|---|---|
| `process` | `<program file>` (or a console script / `artisan <command>` name) | application code that starts an in-repo program: the process starts `plugins/python/subproc.py` and `codegraph/process_runs.py` link (`subprocess` with `python -m pkg.mod` / a script / a console script, Node `spawn` / `fork` / `execa` of a project script or `bin`, Rust `Command` of a cargo bin, PHP `artisan`, Dart `Process.run`), with `role: spawn`; Electron `utilityProcess.fork(script)`; the parent's `child.send(m)` / `child.postMessage(m)` on a forked child | the program's entry (`__main__` block, module, `main`, command); the child's `process.on('message', h)` / `process.parentPort.on('message', h)` |
| `process` | `<program file>:out` | the child's `process.send(m)` / `process.parentPort.postMessage(m)` | the parent's `child.on('message', h)` |

Process starts in tests stay `TEST_CALLS` (via `subprocess`) and get no endpoint; `python -c` snippets are calls,
not programs. `cluster.fork()` re-runs the same program and is not linked. Node `worker_threads` use the `worker`
protocol: `new Worker(path.join(__dirname, 'w.js'))` (also through `pathToFileURL(..)` or a local holding the path),
`worker.on('message', h)` in the parent, `parentPort.on('message', h)` / `parentPort.postMessage(..)` in the worker.

## Raw TCP / UDP sockets

`endpoint:tcp:<port>` / `endpoint:udp:<port>` (codegraph/sockets.py), from a source scan of every language with function
nodes. The only name both ends share is the port, so a pair means "same port in this graph", not proof that the
programs talk to each other.

| language | listen (RECEIVED_BY) | connect / send (SENDS_TO) |
|---|---|---|
| Python | `sock.bind((h, p))`, `socket.create_server`, `socketserver.TCPServer((h, p), Handler)` / UDP / Threading / Forking, `asyncio.start_server(cb, h, p)`, `loop.create_server(F, h, p)`, `create_datagram_endpoint(F, local_addr=)` | `connect((h, p))`, `sendto(d, (h, p))`, `socket.create_connection`, `asyncio.open_connection(h, p)`, `remote_addr=` |
| JS / TS | `net.createServer(cb).listen(p, h)` (also `server.listen`), `dgram.createSocket(..)` + `.bind(p, h)` (handler: `on('message', fn)`) | `net.connect(p, h)` / `createConnection({ port, host })`, `socket.send(m, [o, l,] p, h)` |
| Rust | `TcpListener::bind(a)`, `UdpSocket::bind(a)` (std, tokio, async-std) | `TcpStream::connect(a)`, `send_to(b, a)`, `connect(a)` on a bound `UdpSocket` |
| Kotlin | `ServerSocket(p)`, `DatagramSocket(p)` / `MulticastSocket(p)`, Ktor `aSocket(..).tcp().bind(h, p)`, `*Channel.open()` + `bind` | `Socket(h, p)`, `DatagramPacket(.., h, p)`, Ktor `.connect(h, p)`, `DatagramChannel.send(b, a)` |
| C / C++ | `bind()` in a function that sets `htons(p)`, libuv `uv_ip4_addr(h, p, &a)` + `uv_tcp_bind` / `uv_udp_bind` | `connect()` / `sendto()` with `htons(p)`, `uv_tcp_connect` / `uv_udp_send` |
| Swift | `NWListener(using: .tcp / .udp, on: p)` | `NWConnection(host:, port:, using:)` |
| Dart | `ServerSocket.bind(h, p)`, `RawDatagramSocket.bind(h, p)` | `Socket.connect(h, p)`, `RawDatagramSocket.send(d, a, p)` |
| PHP | `stream_socket_server('tcp://h:p')`, `socket_bind($s, h, p)` | `stream_socket_client`, `fsockopen('udp://h', p)`, `socket_connect`, `socket_sendto` |

TCP vs UDP comes from the API (`SOCK_DGRAM` in the function, else the file's only socket type, for BSD-style calls).
A plain Node program that uses `net` / `dgram` / `tls` without a framework or a tsconfig is now indexed with allowJs.

Port values: literals (`"127.0.0.1:6379"`, `8125`), format strings (`format!("0.0.0.0:{port}")`, f-strings, template
literals, concatenation), the last assignment in the function, fallbacks (`cli.port.unwrap_or(DEFAULT_PORT)`,
`config.port || 8125`, `?:`), constants of the file or a unique one of the project (`#define TEST_PORT 9123`,
`pub const DEFAULT_PORT: u16 = 6379`), `self.x` / `this.x` fields, parameter defaults, CLI option defaults (clap
`default_value_t`, argparse / click `default=`) and environment reads. `os.environ.get("PORT", 8125)` gives port 8125
with `port_envs: [PORT]`. Without a default the endpoint is `env:PORT`, matched (MATCHES_ENDPOINT, heuristic) to the
endpoints whose port is read from the same key. When the port is a parameter of the enclosing function, that function
is a wrapper, and its call sites (CALLS / INSTANTIATES edges, two levels) are resolved instead:
`Client::connect("127.0.0.1:6379")` -> `TcpStream::connect(addr)`. Port 0 (ephemeral) and unresolved ports are
counted in the `sockets` index stats (`ephemeral`, `listen_unresolved`, `connect_unresolved`, `send_unresolved`,
`call_site_unresolved`, with samples). They are not linked.

The receiver of a listener is its handler when one is named: a callback, `Handler.handle`, a protocol factory's
`data_received` / `datagram_received`, or the function the listener is passed to (`server::run(listener, ..)`).
Otherwise it is the function that listens. The RECEIVED_BY edge records `bind_address` and `exposure`: `all`
(0.0.0.0, ::, or no address, as in `server.listen(port)` / `ServerSocket(port)`), `loopback` or `specific`. The node
lists `bind_addresses`, an overall `exposure`, `port_envs` and the `multicast_group` a UDP socket joins in the same
function (`addMembership`, `IP_ADD_MEMBERSHIP` + `inet_aton`, `join_multicast_v4`, `joinGroup`). Confidence is
`resolved` for literal / constant / call-site ports and `heuristic` for fallbacks, defaults and env keys.

```bash
cg protocols --db g.db --listeners      # every listening socket: port, exposure, bind address, handler
cg protocols --db g.db --protocol udp   # endpoints with senders and receivers
```

On the validation corpora: tokio `examples/` pairs `hello_world` with `graceful-shutdown` / `chat` on 6142, `proxy`
with the 8080 servers, and `udp-client` with `echo-udp`. mini-redis pairs the three examples with the server binary's
listener (`server::run`) on `DEFAULT_PORT` 6379. statsd's Python example client reaches the Node UDP server on 8125.
libuv's tests pair on `TEST_PORT` 9123. Numbers are in docs/validation.md.

## UDP application protocols: mDNS, OSC, CoAP, SSDP

These carry a name both ends share, so they get their own endpoints (codegraph/udp_apps.py) next to the UDP port:

| protocol | endpoint name | receive (RECEIVED_BY) | send (SENDS_TO) |
|---|---|---|---|
| `mdns` | service type without `.local.` (`_http._tcp`) | python-zeroconf `ServiceInfo(type, name, port=..)` registered, bonjour-service `publish({ type })`, Swift `NWListener.Service(type:)` / `NetService(.., type:)`, Android `NsdServiceInfo` + `registerService`, JmDNS `ServiceInfo.create`, bonsoir `BonsoirService` | `ServiceBrowser` / `AsyncServiceBrowser`, `ServiceInfo` without a port (a lookup), `find({ type })`, `NWBrowser(for: .bonjour(type:))`, `searchForServices(ofType:)`, `discoverServices`, `addServiceListener`, `BonsoirDiscovery` |
| `osc` | address pattern (`/filter`) | python-osc `dispatcher.map('/addr', handler)` | python-osc `send_message('/addr', ..)`, node-osc / osc.js `send` |
| `coap` | resource path (`/time`) | aiocoap `add_resource(['time'], R())` (the `render_*` method, else the resource class) | aiocoap `Message(uri='coap://host/time')`, Californium `CoapClient(uri)`, node-coap `coap.request(uri)` |
| `ssdp` | search target / USN (`urn:...`) | node-ssdp `addUSN`, ssdpy `SSDPServer(device_type=)` | node-ssdp `search`, ssdpy `m_search`, async_upnp_client `search_target=` |

mDNS advertises on the receiving side (`role: advertise`) and browses on the sending side (`role: browse`). OSC
address patterns match as globs (`/filter*`), CoAP paths with the HTTP path matcher. python-osc's servers and clients
are also UDP sockets. Names may be literals, f-strings, concatenations (unknown parts become `{name}`) or a name
assigned one. Calls in comments and docstrings (doctest examples) are skipped and counted as `in_comment`; the
per-protocol counts are under `sockets.applications` in the index stats. On the corpora: python-zeroconf's examples
pair the `_http._tcp` registration with its browser; aiocoap's `server.py` resources `/time` and `/other/block`
pair with `clientGET.py` and `clientPUT.py`.

## gRPC (protobuf contracts)

`endpoint:grpc:<package>.<Service>/<Method>` (codegraph/rpc.py), one per `rpc` of a `service` in the project's
`.proto` files (`node_modules`, `vendor`, `third_party`, build and dot directories are skipped; comments are masked).
The node records `service`, `package`, `method`, `request` / `response` message types, `streaming` (`unary`,
`server`, `client`, `bidi`), `declared_in` (`file:line`), `declared_also` when the same service is declared again,
and `contract: proto`. Servers and clients come from a source scan of the files with function nodes; generated
code (`_pb2*.py`, `*_pb.js`, `*.pb.h` / `.cc`, `*.grpc.pb.*`, `.pb.swift`, `.pbgrpc.dart`, `*_grpc.pb.go`, ..) is
skipped. A pair means both sides name the same contract method, so it holds across languages.

| language | server (RECEIVED_BY the implementing method) | client (SENDS_TO from the calling function) |
|---|---|---|
| Python (grpcio) | `class X(pb2_grpc.SvcServicer)` (exact), `add_SvcServicer_to_server(X(), server)` or a variable assigned `X()` (resolved) | `stub = pb2_grpc.SvcStub(channel)`, then `stub.Method(..)` |
| JS / TS (@grpc/grpc-js, Connect) | `server.addService(pkg.Svc.service \| SvcService, { method: handler, method, method(..) {} })`, an object or class named there; Connect `router.service(Svc, impl)` | `new pkg.Svc(addr, creds)` (proto-loader), `new SvcClient(..)` (generated / ts-proto), Connect `createClient(Svc, transport)`, typed fields `client: SvcClient` |
| Rust (tonic) | `impl svc_server::Svc for X` (in a file that mentions tonic or `_server`) | `SvcClient::connect(..)` / `new` / `with_interceptor`, then `client.method(..)` |
| Kotlin / Java | `class X : SvcGrpcKt.SvcCoroutineImplBase()`, `extends SvcGrpc.SvcImplBase` | `SvcGrpc.newStub` / `newBlockingStub` / `newFutureStub`, `SvcCoroutineStub(channel)` |
| C++ (grpc++) | `class X : public Svc::Service` (also `AsyncService`, `CallbackService`, `WithAsyncMethod_*`); methods declared in the class and defined out of line (`X::Method`) in the same directory | `stub_ = Svc::NewStub(ch)` (also a member initializer), `stub_->Method(..)`, `stub_->async()->Method(..)`, `AsyncMethod` / `PrepareAsyncMethod` |
| Dart | `class X extends SvcServiceBase` | `SvcClient(channel)` |
| Swift (grpc-swift) | `Pkg_SvcAsyncProvider`, `Pkg_Svc.SimpleServiceProtocol` | `Pkg_SvcAsyncClient(..)`, `Pkg_Svc.Client(..)` |
| PHP | `extends SvcStub`, `implements SvcInterface` | `new SvcClient(..)` |

Method names match the contract without case and underscores (`get_feature`, `getFeature`, `GetFeature`). A method
of a server class that is not in the contract gets no edge. A stub handed to a helper (`def get_one(stub, p):
stub.GetFeature(p)`) is resolved when the file's own stubs belong to one service with that method, and is
`heuristic` when only one service in the project has the method. When two packages declare a service of the same
short name, the qualifier (`fleet_pb2_grpc`, `routeguide::`, the import) picks the package; otherwise no edge. The
`rpc` index stats count services, methods, server classes and methods, registrations, stubs and client calls, with
samples of `server_class_without_methods`, `stub_without_variable`, `handler_unresolved` and
`client_call_outside_function`. NestJS `@GrpcMethod` / `ClientGrpc` handlers stay `message:grpc` nodes (protocol
`grpc` in the same view).

A top-level client call in a script (no enclosing function) is sent from the file's module node, with
`how: ... (module level)`; this holds for all four RPC protocols.

## Thrift (IDL contracts)

`endpoint:thrift:<file stem>.<Service>/<method>` (codegraph/rpc.py), one per function of a `service` in the
project's `.thrift` files (`#`, `//` and `/* */` comments masked). A service that `extends` another keeps the
inherited methods on the declaring service (`shared.SharedService/getStruct`), and a server or client of the child
links to them. The node records `request` (the argument list), `response`, `streaming: oneway` for `oneway`
functions (else `unary`), `throws` (exception types) and `declared_in`.

| language | server (RECEIVED_BY) | client (SENDS_TO) |
|---|---|---|
| Python | `Svc.Processor(handler)` (the handler's class, resolved), `class X(Svc.Iface)` (exact) | `client = Svc.Client(protocol)`, then `client.method(..)` |
| JS / TS (thrift) | `thrift.createServer(Svc, { method: fn })`, `new Svc.Processor(handler)` | `thrift.createClient(Svc, conn)`, `new Svc.Client(..)` |
| C++ | `class X : public SvcIf` / `SvcCobSvIf` (exact) | `SvcClient client(protocol)`, `make_shared<SvcClient>(..)` |
| Rust | `impl SvcSyncHandler for X` (`handle_method`) | `SvcSyncClient::new(..)`, factory functions returning one |
| PHP | `implements ..SvcIf` | `new ..SvcClient(..)` |
| Dart | `implements Svc` | `SvcClient(..)` |

Java (`implements Svc.Iface`, `Svc.Client client`) is scanned but Java has no plugin yet, so it adds no edges. Node
files that mention `thrift` count as network code for the TS plugin's unreachable checks.

## tRPC (router trees)

`endpoint:trpc:<path>`, one per procedure of a router tree, with `path` the dotted key chain from the root router
(`post.create`). Routers are `const x = createTRPCRouter({..})` / `router({..})` / `t.router({..})`, plain objects of
procedures (`{..} satisfies TRPCRouterRecord`) and `mergeRouters(..)`. An entry is a procedure chain ending in
`.query` / `.mutation` / `.subscription`, a nested router, or a mount: an identifier resolved through its import
(relative, `~/` / `@/` aliases, index files, `import { a as b }`), which may also be a procedure variable
(`export const get = authedProcedure.query(..)` mounted as `get,`). Roots are routers that nothing mounts. The TS
extractor gives each inline resolver its own function node `<routerVar>.<path>` (`inline_handler`), so the resolver
body's calls are attributed to it and the endpoint is RECEIVED_BY it; a named handler (`.query(getPosts)`) is used
as is. The node records `procedure`, `router`, `root` and `declared_in`.

Clients are `x.<path>.useQuery` / `useSuspenseQuery` / `useInfiniteQuery` / `useMutation` / `useSubscription` /
`query` / `mutate` / `fetch` / `prefetch` / `ensureData` / `queryOptions` / `mutationOptions` / ..(..) on a known
path, and direct calls `caller.<path>(..)` when the root variable is a server caller (`createCaller(..)`,
`createTRPC*`). Only paths that a router declares link; others get nothing. On cal.com, 174 procedures in 33
routers, 226 client calls; 221 of 224 client chains in the app code name a known path.

## JSON-RPC 2.0 (method names)

`endpoint:jsonrpc:<method>`, in files that name a JSON-RPC library or a `jsonrpc` payload (MCP files, which use
their own SDK, are skipped). Servers: jayson `jayson.server({..})` / `new jayson.Server(methods)` (an object or a
variable holding it), `addMethod("x", fn)`, `onRequest` / `onNotification`, Python `@method`, `@method(name="x")`,
`@dispatcher.add_method`, Rust jsonrpsee `#[rpc(server, namespace = "ns")] trait` with `#[method(name = "x")]` /
`#[subscription]` (endpoint `ns_x`, exact on `impl TraitServer for T`) and `register_method("x", closure)`
(heuristic, RECEIVED_BY the registering function). Clients: `.request` / `.call` / `.notify` / `.sendRequest` /
`.sendNotification("x", ..)`, jsonrpcclient `request("x")`, and request payloads `{"jsonrpc": "2.0", "method":
"x"}` in any language, which also covers calls to an external JSON-RPC API.

## GraphQL (root fields)

`endpoint:graphql:<Query|Mutation|Subscription>.<field>`, one per root field (`codegraph/graphql.py`). Object-type
fields (`Book.author`) are not endpoints yet.

**Schema.** SDL comes from `.graphql` / `.graphqls` / `.gql` files, from `gql` / `graphql` / `/* GraphQL */` template
literals, and from Python `gql("""...""")` strings, including `extend type Query` and custom root names
(`schema { query: RootQuery }`). Each declared root field gets an endpoint with `root`, `field`, `type` (the return
type) and `declared_in`. `served: schema` marks the field as received even when cg finds no resolver function (a
default resolver, or a schema copy in a client repo). `cg protocols` therefore counts a declared field as received,
and it never reports `no_sender` for one that has no resolver.

**Resolvers** (RECEIVED_BY):
- **JS / TS resolver maps.** Supported shapes are `{Query: {books: fn, author(..) {..}}, Mutation: {..}}`, spreads
  (`...bookQueries`, looked up in the same file or the only file that declares the variable), shorthand and
  identifier values (`stats: statsResolver`), and `Subscription: {x: {subscribe}}`. Apollo cache `typePolicies` are
  skipped.
- **graphene.** The `query=` / `mutation=` / `subscription=` classes of `graphene.Schema(..)`,
  `build_federated_schema(..)` and similar calls, with the root fields of all their in-project bases (saleor's
  `Query(AccountQueries, ProductQueries, ...)`). A field `x = graphene.Field(..)` is resolved by `resolve_x` (also
  in a base), or by `resolver=fn`. A mutation field `x = CreateItem.Field()` is resolved by the mutation's own
  `perform_mutation` / `mutate`; an inherited one is `heuristic` and records `inherited_from`. For a subscription
  field, `subscribe_x` is tried first. Python names become camelCase unless `auto_camelcase=False`.
- **strawberry.** The roots of `strawberry.Schema(..)`: `@strawberry.field` / `mutation` / `subscription` methods
  (`name=` honoured), and `x: T = strawberry.field(resolver=fn)`. Names are camelCase.
- **ariadne.** `QueryType()` / `MutationType()` / `SubscriptionType()` / `ObjectType("Query")` with
  `@query.field("x")`, `@subscription.source("x")` and `set_field("x", fn)`.
- **Nest.** `@Resolver` + `@Query` / `@Mutation` / `@Subscription` already give `route:GRAPHQL Query.x`
  ([ts-frameworks.md](ts-frameworks.md)). The endpoint twin `endpoint:graphql:Query.x` is RECEIVED_BY the same
  method and is not an entry point itself. `cg protocols` shows one entry, the route, with the twin's senders and
  the route's guards (`unguarded` is checked as for HTTP routes). The route kind is now reported under `graphql`,
  not `http`.

A root field declared in graphene or strawberry code without a resolver function is `served` by the framework (the
default resolver).

**Operations** (SENDS_TO, one per root field requested, with attrs `operation`, `operation_kind` and `fields`, the
field's first-level selection with fragment spreads and inline fragments expanded):
- Apollo / urql hooks `useQuery` / `useLazyQuery` / `useMutation` / `useSubscription` / `useSuspenseQuery` /
  `useBackgroundQuery(DOC)` (generic type arguments allowed).
- Any call with a `{query | mutation | document: DOC}` object (`client.query`, `client.mutate`,
  `server.executeOperation`, test helpers), except the cache calls (`readQuery`, `writeQuery`, ...). Objects nested
  in options such as `refetchQueries: [{query: X}]` are not sends.
- urql / graphql-request `.query(DOC)`, `.mutation(DOC)`, `request(url, DOC)`, and inline `useQuery(gql`...`)`.
- graphql-codegen hooks `use<Op>Query` / `LazyQuery` / `SuspenseQuery` / `Mutation` / `Subscription(..)`, matched
  by operation name and kind. The generated hook itself (`*.generated.ts`, `__generated__/`) is a wrapper: its
  callers send.
- Python: documents assigned to a name (`QUERY = """query ..."""`, `gql("""...""")`) that are passed to a call
  (`client.execute(QUERY)`, `api_client.post_graphql(QUERY, ..)`). These are `heuristic`; calls from tests become
  TEST_CALLS.

`DOC` is a variable bound to a document: in the same file, the only one with that name, or the one its import names.
Fields marked `@client` (Apollo local state) and `__typename` / `__schema` are skipped. Fields whose name is
interpolated (`${x}`, f-string `{field}`) are counted as `dynamic_root_fields`, not sent. A requested root field that
no schema in the graph declares and no resolver receives gets `no_receiver`. In a client-only repo whose schema lives
in the server repo, index both and run `cg link`: endpoint ids are shared, so the two sides join.

On saleor (graphene, 8385ca6), the checked-in schema.graphql declares 448 root fields and 446 of them get a resolver:
333 `.Field()` mutations (71 through an inherited `perform_mutation` / `mutate`), plus `resolve_*` methods and
`resolver=` functions. The two without one are federation's `_entities` / `_service`. The tests post 6,106 distinct
root-field requests (TEST_CALLS). On saleor-dashboard (Apollo + codegen, f9093f2), 629 root-field requests come from
the callers of 672 generated hooks and from `client.query({query})` calls, and every requested field is declared in
its schema copy.

## Job queues

`codegraph/jobs.py` (#36) adds two endpoint protocols for Celery, RQ, Dramatiq, Bull / BullMQ, Laravel queues
and Symfony Messenger:

- `endpoint:job:<framework>:<name>`: one per task. RECEIVED_BY the function that runs it; SENDS_TO (role `enqueue`,
  or `schedule`) from the code that enqueues it. `attrs.queue` is the queue it is routed to (the framework default
  when none is named) and `attrs.processes` the worker processes that consume that queue.
- `endpoint:queue:<framework>/<queue>`: one per named queue. SENDS_TO from enqueue sites whose queue is known,
  QUEUE_ROUTES to the tasks routed to it. `attrs.consumers` lists the worker processes that consume it, with
  `consumers_at`. A consumed queue is `served` (a worker takes any job off it), so a producer in one repo and a
  worker in another link by queue name.

Task names are what crosses repos: index both repos and run `cg link`. Endpoint ids are shared, so a
`send_task("billing.charge")` in a web app joins the `@app.task(name="billing.charge")` of the worker repo.

**Celery** (any Python project that imports celery):
- Tasks: `@app.task` / `@shared_task` / `@celery.task` / `@periodic_task` (`name=`, `queue=`; the name defaults to
  the function's dotted path).
- Sends: `.delay` / `.apply_async(queue=)` / `.s` / `.si` / `.signature` / `.delay_on_commit` on a task, and
  `send_task("name", queue=)` / `app.signature("name")` by name.
- Queues: `task_routes` / `CELERY_TASK_ROUTES` globs (`{"billing.export.*": {"queue": "exports"}}`).
  `task_default_queue` changes the default from `celery`.
- Settings constants (`queue=settings.X`) resolve from `X = "lit"`, or from `os.environ.get("E", "lit")` (heuristic),
  in the same file or in one non-test settings / config module.
- In a Django project the plugin's `job:<task>` nodes (with their DISPATCHES / SCHEDULES and `queue_job` entries)
  stay as they were. The endpoint gets `job_node` and is no entry point itself. A send the plugin already records
  as DISPATCHES is not repeated. `cg protocols` shows one entry, the job node (protocol `celery`), with the twin's
  queue and cross-repo senders. `--protocol job` lists these adapted job nodes too.

**RQ / django-rq:**
- `Queue("name")` variables, `q.enqueue(func | "dotted.path" | f"pkg.mod.{x}")`, `enqueue_call(func=)`,
  `enqueue_in` / `enqueue_at`, `django_rq.enqueue`, `get_queue("x").enqueue`, and `@job("x")` functions with
  `.delay()`.
- The job is named by the function's dotted path. A path or f-string template whose module is in the repo gets the
  module's top-level functions as receivers (heuristic for templates).
- When the function lives in another repo (RQ needs no marker on the worker side), `cg link` adds RECEIVED_BY from
  `job:rq:<path>` to the other repo's function of that path (heuristic; `rq_import_paths` in the link stats).

**Dramatiq:** `@dramatiq.actor` / `@actor` (`actor_name=`, `queue_name=`). Sends are `.send()` /
`.send_with_options(queue_name=)`, plus `actor=x` keyword registrations (authentik's `ScheduleSpec(actor=..)`,
role `schedule`, heuristic).

**Worker processes** come from Procfile, docker-compose / compose files, systemd `.service`, supervisor `.conf`,
Dockerfile, shell scripts, Makefile / justfile, pyproject (poe tasks), fly / render / k8s yaml and entrypoints:
- `celery -A x worker -Q a,b` (default queue: `celery`, or `task_default_queue`);
- `rq worker a b` and `manage.py rqworker a b` (default: `default`);
- `dramatiq pkg.mod -Q a` (default: `default`).

The process is named by the Procfile key, the compose service, the supervisor program or the file name.

**Check `no_consumer`:** jobs are sent to the queue, the repo starts workers of that framework, and none of them
consumes the queue. Examples are a `task_routes` queue missing from `-Q`, and saleor's `observability` queue, which
its only worker command (pyproject `celery worker`, default queue) does not consume.

**Bull / BullMQ** (JS / TS files importing `bull` / `bullmq`):
- Queues: `new Queue(name)` / `new Bull(name)` with a string, a TS enum member or an `as const` object member
  (`new Queue(QueueName.Emails)`). A function passing its parameter to `new Queue(param)` is a factory
  (outline's `createQueue`), and `createQueue("tasks")` calls bind the assigned variable and the getter function
  around the call (`taskQueue()`).
- Sends: `q.add(name, data)` sends to `job:bull:<queue>:<name>` and the queue; `q.add(data)` and `addBulk` send
  to the queue only.
- Receivers: `q.process(name, fn)` receives `<queue>:<name>`. Queue-wide processors (`new Worker(queue, fn)`,
  `q.process(fn)`) receive the template `<queue>:{name}`, so a named sender in another repo links to them; a named
  processor is more specific and wins.
- Nest `@Processor` / `@Process` / `WorkerHost` job nodes get the same twins (`job:<queue>:<name>` ->
  `endpoint:job:bull:<queue>:<name>`, `WorkerHost` -> `<queue>:{name}`), merged into the job node by `cg protocols`.

**Laravel** (the plugin's `job:<Class>` nodes, which keep their DISPATCHES and `queue_job` entries):
- Twins `endpoint:job:laravel:<FQCN>`.
- A job's queue comes from `public $queue = 'x'`, `$this->onQueue('x')` / `$this->queue = 'x'` in the class, or
  `#[OnQueue('x')]`.
- Queue sends: the plugin's dispatches send to the job's queue. `X::dispatch(..)->onQueue('x')`,
  `dispatch(new X)->onQueue('x')`, `self::dispatch` and each job of `Bus::batch([...])->onQueue('x')` send to `x`
  instead.
- Workers: `config/horizon.php` supervisors (`'queue' => ['high', 'default']`, process `horizon:<supervisor>`) and
  `artisan queue:work --queue=a,b` / `queue:listen` in process files (default queue `default`).

**Symfony Messenger:**
- Handlers: `#[AsMessageHandler]` on a class (`__invoke`, or `method:`) or a method, and `MessageHandlerInterface`.
  Each receives `endpoint:job:messenger:<message FQCN>`, the type of its first parameter or `handles:`
  (resolved through `use` / `namespace`).
- Sends: `$bus->dispatch(new X(..))` (any `$..bus..` receiver) and `$this->dispatchMessage(new X)`.
- Queues: transports are queues. `framework.messenger.routing` in `config/packages/*.yaml` routes a message to
  `queue:messenger/<transport>`. `fromTransport:` restricts a handler. Workers come from `messenger:consume a b`.

Not covered yet (follow-up issue): Huey, arq, apalis, Spring JMS, graphile-worker, Agenda; project wrappers around
the frameworks (netbox `JobRunner.enqueue`, immich `@OnJob({name, queue})` with `jobRepository.queue({name})`,
outline's task classes run by name from the `tasks` queue); actors held in attributes
(`self.sync_task.send_with_options`); Nest producers whose processor is in another repo (the plugin records
DISPATCHES only to processors it sees); Messenger `#[AsMessage]` routing and Messenger stamps.

## Message brokers

`codegraph/brokers.py` (#35) links producers and consumers that talk through a broker. They usually live
in different services, so `cg link` matches them across repositories:

| endpoint | sent by | received by |
|---|---|---|
| `kafka:<topic>` | kafkajs `producer.send({topic, messages})` / `sendBatch({topicMessages})`, node-rdkafka and confluent-kafka `produce(topic)`, kafka-python / aiokafka `producer.send(topic)` / `send_and_wait`, faust `topic.send()` | kafkajs `consumer.subscribe({topic | topics})` (the handler is the `run({eachMessage})` callback, `attrs.group` from `kafka.consumer({groupId})`), `consumer.subscribe([..])`, `KafkaConsumer(topic, group_id=)`; `/regex/` subscriptions become globs when they are simple |
| `amqp:<exchange>/<key>` | amqplib / amqp-connection-manager `channel.publish(ex, key)`, pika `basic_publish(exchange=, routing_key=)`, aio-pika `exchange.publish(msg, routing_key=)`, php-amqplib `basic_publish($msg, ex, key)` | the consumer of a queue bound to the exchange (`bindQueue` / `queue_bind` / `queue.bind`): `consume(q, fn)`, `basic_consume(queue=, on_message_callback=)`, `queue.consume(cb)`, php-amqplib `basic_consume(q, .., $callback)`. Server-named queues (`assertQueue('')`, `queue_declare(queue="")`, `list($q,,) = queue_declare("")`) are followed through their variable. Fanout and headers exchanges receive `<exchange>/#`; a binding key from argv or a loop variable is `#` (heuristic) |
| `amqp:queue:<name>` | `sendToQueue(q)`, `publish("", q)` (the default exchange) | `consume(q)` of a named queue |
| `redis-pubsub:<channel>` | ioredis / node-redis / redis-py `publish(ch, msg)`, Laravel `Redis::publish`, Predis / phpredis | `subscribe(a, b)` (ioredis handler: `.on('message', h)`), node-redis `subscribe(ch, listener)`, redis-py `pubsub().subscribe(**{ch: handler})`, `psubscribe` globs, Laravel `Redis::subscribe([..], fn)` |
| `redis-stream:<key>` | `xadd(key, ..)` | `xreadgroup` / `xread` (`'STREAMS', key`, `{key: id}`, node-redis `{key, id}`), `attrs.group` |
| `mqtt:<topic>` | MQTT.js / paho-mqtt / aiomqtt / php-mqtt `publish(topic, payload)` | `subscribe(topic | [..] | {topic: qos})` with the `on('message', h)` / `on_message = h` handler, paho `message_callback_add(topic, h)` |
| `nats:<subject>` | nats.js / nats-py `publish(subject)`, `request(subject)` (role `request`) | `subscribe(subject, {callback, queue})` / `subscribe(subject, cb=, queue=)` (`attrs.group`) |

The protocol of a `publish(` / `subscribe(` call is decided in this order:
1. the variable holding the client (`const client = mqtt.connect(..)`, `new Redis()`, `nc = await nats.connect()`,
   `r.pubsub()`);
2. the one messaging library the file imports;
3. the object name (`this.redis`, `mqttClient`, `nc`) among the libraries the project uses.

A call on the repository's own wrapper class (zigbee2mqtt's `this.mqtt.publish(..)` on `class Mqtt`) or on
`this` is skipped and counted as `wrapper_calls_skipped`, because the wrapper rewrites the topic. Names come from
literals, constants (same file, imported upper-case constants, TS enums), locals and fields assigned one, and
templates (`orders.{id}`, heuristic). `process.env.X ?? "d"` and `os.getenv("X", "d")` give the default
(heuristic), and an env key without a default gives `env:X`. Receivers whose name is unknown, or starts with an
unknown part (`{base_topic}/#` would take every topic), are counted, not recorded. Receivers in test files are
skipped.

Kotlin and Rust (part 2) use the same endpoints:

- **Kotlin:** Spring `@KafkaListener(topics = [..], groupId = ..)` / `topicPattern`, `@RabbitListener(queues = [..])`
  and `@RabbitListener(bindings = [QueueBinding(value = Queue(..), exchange = Exchange(.., type = ..), key = [..])])`;
  kafka-clients `KafkaProducer.send(ProducerRecord(topic, ..))`, `KafkaTemplate.send(topic, ..)` and
  `KafkaConsumer.subscribe(listOf(..))` (also bare inside `KafkaConsumer(props).apply { .. }`, `attrs.group` from
  `GROUP_ID_CONFIG`); the RabbitMQ Java client (`basicPublish`, `queueDeclare().queue`, `queueBind`, `basicConsume`,
  `exchangeDeclare(.., BuiltinExchangeType.X)`), kourier (named arguments, `queueDeclared.queueName`) and
  `RabbitTemplate.convertAndSend`; Paho / HiveMQ, jnats and Jedis / Lettuce clients held in a variable.
- **Rust:** lapin (`basic_publish`, `queue_declare` / `queue.name()`, `queue_bind`, `basic_consume`,
  `exchange_declare(.., ExchangeKind::X)`), rdkafka (`send(FutureRecord::to(topic))`, `subscribe(&[..])`), and
  async-nats / nats, rumqttc / paho-mqtt and redis-rs `publish` / `request` / `subscribe` / `queue_subscribe` /
  `psubscribe` / `xadd` when the file imports exactly one of them.
- Names: Kotlin `"a.$x"` / `"${x}"` templates, `System.getenv("X") ?: "d"` and other `?:` defaults, companion /
  `object` constants (`Topics.INVOICES`, looked up in the file that declares the class), Rust `format!("a.{}", x)`,
  `std::env::var("X").unwrap_or(..)` / `unwrap_or_else(|_| ..)`, `&x` / `.as_str()` / `.to_string()`.

Not covered yet (follow-up #137): SQS / SNS / EventBridge / Google Pub/Sub / Azure Service Bus, STOMP and
Spring `@MessageMapping`, ZeroMQ, JetStream streams and consumers, Kafka and AMQP names from config files, queue
bindings declared in infrastructure as code (serverless, SAM / CDK, Terraform event source mappings, KEDA),
broker nodes (#40) on the endpoints, the repository's own wrapper classes (their callers' topics), Rust amqprs
(argument builders, counted as unresolved), Java (Java sources are not indexed), Dart / Swift / C++ clients, and Go
(no Go plugin).

## Not covered yet

- Message brokers beyond #35 part 1 (see above) and the rest of local IPC (#38: content providers, child processes, Dart isolates, XPC); job queue frameworks beyond Celery / RQ / Dramatiq / Bull / Laravel / Messenger; Socket.IO in `.svelte` / `.vue` files, `ws` / SSE message names, rooms as their own endpoints, Python and Rust
  WebSocket clients (#147).
- Guards on Bull processors are not recorded on the adapted `job` nodes, so `unguarded` is not checked for them.
- `schema_mismatch` needs `schema` on both sides; no extractor records message types yet.
- Broker / host nodes (#30 / #40) are not attached to endpoints yet.
- RPC contracts (#33, rest in #132): Go gRPC servers and clients (no plugin); `.proto` files only outside the
  indexed root (`buf` remote modules, a sibling repository: index both and `cg link`); Nest `@GrpcMethod` handlers
  are not merged with the contract endpoints; DEFINES / USES_SCHEMA edges to message types. Thrift, tRPC and
  JSON-RPC: Java RMI and Java Thrift (no Java plugin), tRPC `lazy()` routers, jayson methods wrapped in
  `jayson.Method(..)`, LSP well-known methods, PHP top-level clients (no module node).
- GraphQL (#34 part 1): object-type field endpoints and `operation:` nodes, operations that live only in `.graphql`
  files (Relay, codegen documents with no hook call), hand-written hooks that wrap a codegen hook (their callers),
  Lighthouse (PHP), async-graphql / juniper (Rust), Spring for GraphQL / DGS, Apollo Kotlin / iOS, graphql_flutter,
  TypeGraphQL / Pothos code-first schemas, guards and `@deprecated` field checks.
- Sockets (#39): QUIC / ALPN, WebRTC data channels, message-type framing on a port, Go (no plugin), servers whose port
  comes only from a config file, and `env:` endpoints across repositories in `cg link` (follow-up issue).
