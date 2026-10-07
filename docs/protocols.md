# Protocol links

cg pairs a sender with the code that receives it: HTTP, channels, Nest messages, job queues,
events, bridges, and named endpoints of messaging protocols. `impact`, `reaches`, `path` and
`downstream` cross those edges in one repo and, after `cg link`, between two. `cg protocols`
lists endpoints with senders, receivers, guards and checks.

## Model

```
sender  -SENDS_TO->  endpoint:<protocol>:<name>  -RECEIVED_BY->  handler
send-side endpoint  -MATCHES_ENDPOINT->  receive-side endpoint
```

| piece | meaning |
|---|---|
| `endpoint:<protocol>:<name>` | normalised topic / subject / event / method; `${id}`, `{{id}}`, `/:id` become `{id}`. Attrs: `protocol`, `transport`, `pattern`, `side` (send, receive, both), `guards` (`[]` = known none), `namespace`, `event`, `schema`, `test_only`. A network receiver is a `message_handler` entry. |
| `SENDS_TO` | code → endpoint. `role`: send, publish, emit, request, invoke, enqueue. From tests: `TEST_CALLS` with `orig` = SENDS_TO. |
| `RECEIVED_BY` | endpoint → handler. |
| `QUEUE_ROUTES` | queue endpoint → one task. Does not propagate: a send runs that job, not every job on the queue. |
| `MATCHES_ENDPOINT` | names differ but the matcher fits. `resolved` for a wildcard or `{param}`; `heuristic` for a template fitted to a literal, or an ambiguous tie. Identical ids need no edge. |

Matching runs at the end of `cg index` and again in `cg link`. Link stats add `protocols` only
when the graphs hold these endpoints. Names in `.cg.yaml` `protocols.external` count as
`external`, not `no_receiver` / `no_sender`.

Socket.IO is directional: one endpoint per event, and every edge records `process` (`server` /
`client`). A server `emit` reaches client handlers only, and the reverse. A side with no known
process matches either.

## Registry

`cg_code_graph/protocols/` holds one `Protocol`: `name`, `transport`, `ports`, URL `schemes`, a
normaliser, a `matcher`, `fanout` (every match) or most-specific-with-ties, `entry`, `guards`, `source` (`endpoint`, `adapter`, `bridges`). Plugins call `register` and the builders:

```python
from cg_code_graph.protocols import protocol_receive, protocol_send
protocol_send(builder, "mqtt", "devices/42/state", fn_id, file, line, "exact", role="publish", library="paho-mqtt")
protocol_receive(builder, "mqtt", "devices/+/state", handler_id, file, line, "exact", guards=[])
```

| matcher | rules | protocols |
|---|---|---|
| `path` | URL segments, `{param}` / `{rest*}`, one shared literal segment | http, ws, sse, coap |
| `mqtt` | `/` levels, `+` one level, `#` the rest | mqtt |
| `nats` | `.` tokens, `*` one, `>` the rest | nats |
| `amqp` | same exchange, then topic key (`.` / `*` / `#`); `queue:<name>` exact | amqp |
| `glob` | `*` / `?` / `[...]` | kafka, redis-pubsub, osc |
| `template` | whole-name `{param}` | socketio, unix, pipe |
| `dotted` | `.` segments with `{param}` | pusher |
| `exact` | same name | bridges, nest-*, jobs, events, llm_tool, grpc, thrift, trpc, jsonrpc, graphql, redis-stream, tcp, udp |
| `mcp` | `<server>/<name>`; `*` if the client omits the server | mcp_tool, mcp_resource, mcp_prompt ([AI tools](ai-tools.md)) |

## Existing kinds

`cg protocols` reads these through adapters. Node ids stay as they are; `cg link`,
`cg channels` and `cg bridges` keep their own views.

| kind | protocol | send | receive |
|---|---|---|---|
| `http` / `route` | http, ws, sse, graphql | HTTP_CALLS / TEST_HTTP | ROUTES_TO, MATCHES_ROUTE |
| `channel` / `channel_sub` | pusher | BROADCASTS_ON | SUBSCRIBES_CHANNEL |
| `message` | nest-rpc, nest-event, nest-ws, grpc | DISPATCHES | HANDLED_BY |
| `job` | bull, laravel-queue, celery | DISPATCHES, SCHEDULES | HANDLED_BY |
| `event` | laravel-event, nest-event-emitter, django-signal | DISPATCHES | LISTENED_BY / HANDLED_BY |
| `endpoint` | capacitor, react-native, flutter, pigeon, electron, tauri | SENDS_TO | RECEIVED_BY |

## Checks

A side is judged only when the graph has some endpoint of that protocol on the other side.

| check | meaning |
|---|---|
| `no_receiver` | sent; nothing here receives it |
| `no_sender` | received; nothing sends it (or the producer is outside the repos) |
| `test_sender_only` | received, sent from tests only |
| `ambiguous` | several receivers matched equally |
| `schema_mismatch` | both sides name a message type, and they differ (no extractor records types yet) |
| `no_consumer` | jobs are sent to a queue, workers of that framework run, none consumes it |
| `unguarded` | an outside-reachable receiver with no auth guard (HTTP / WS / GraphQL routes, server Socket.IO). Nest message handlers record `@UseGuards` and `APP_GUARD`; `app.useGlobalGuards()` is HTTP only |
| `external` | `.cg.yaml`, a third-party HTTP origin, a framework signal (`post_save`, …), or a bridge module outside the repo |

Bridge checks stay in [Web / native bridges](bridges.md): `missing_on`, `unregistered`, plus
`no_receiver` / `no_sender` / `external`.

```yaml
protocols:
  external: ["kafka:audit.*", "socketio:/#audit:*", "http:GET /status"]
```

## `cg protocols`

```bash
cg protocols --db combined.db
cg protocols --db combined.db --protocol socketio
cg protocols 'orders.*' --db combined.db
cg protocols --db combined.db --unmatched --side send
cg protocols --db g.db --listeners          # TCP/UDP listeners: port, exposure, bind, handler
```

`--json`: `summary`, `endpoints` (id, kind, protocol, name, side, linked, checks, external,
guards, senders, receivers, matches, at, transport), `registry`. MCP:
`protocol_links(pattern?, protocol?, side?, unmatched?, listeners?)`.

```text
$ cg protocols --db combined.db --protocol socketio
socketio: 2 endpoint(s), 1 linked  (no_receiver 0, no_sender 0)
socketio:/orders#order:created  side: both
    sent by orders.create @ api/routes.py:40 [exact, client]
    received by worker.on_order @ worker/handlers.py:12 [exact, server]
```

`cg path "route:POST /orders" table:orders` on the `tests/protocol_fixtures` link runs route →
handler → `SENDS_TO` → `RECEIVED_BY` → `WRITES_TABLE`. Corpus numbers:
[protocol links](validation-log.md#protocol-links-shared-endpoint-model-31).

## Webhook verification

A receiver is a route a third party calls. `cg_code_graph/webhooks.py` walks the handler, two levels
of calls, and its middleware.

| result | when |
|---|---|
| verified | Stripe `constructEvent`, svix `verify`, GitHub `verify`, Twilio `validateRequest`, Shopify `webhooks.validate`, or an HMAC plus a constant-time compare (or a provider signature header), or a route middleware that verifies (see below) |
| unverified | the handler reads `Stripe-Signature`, `X-Hub-Signature-256`, `svix-signature`, … and nothing above checks it |

A verifying middleware marks the route verified. `how` names the middleware and `check` points at the verifying call (the route line when only the name is known). Signs of one:

- an alias such as `verify.<provider>.signature` or `verify-<provider>-webhook` (`.` and `-` read as `_`);
- a resolved middleware class whose name matches, also when it is reached through an alias;
- a middleware body with a known verification: an HMAC plus constant-time compare, Stripe `Webhook::constructEvent`, Twilio `RequestValidator` `validate` (`->`, `::` or `.`).

A route is never labeled `SECRET-CHECKED` and `WEBHOOK UNVERIFIED` together. A provider header read with no verifying middleware or call stays `WEBHOOK UNVERIFIED`.

`cg routes` shows a verified check as a guard and flags `WEBHOOK UNVERIFIED`. Provider events
are never `no_sender`. A signed POST from this repo is a sender of
`endpoint:webhook:<this project>:<event>`.

Framework conventions (#152):

| convention | receiver |
|---|---|
| Laravel Cashier | a controller that extends `Laravel\Cashier\Http\Controllers\WebhookController`, or a subclass of one. `handleCustomerSubscriptionCreated` receives `customer.subscription.created`; `handleInvoicePaymentActionRequired` receives `invoice.payment_action_required` (`heuristic`). The route is verified because Cashier checks the signature before those methods. A class that does not extend that controller is not a Cashier receiver |
| spatie/laravel-webhook-client | `Route::webhooks('payments/hooks', 'payments')` plus the matching `name` in `config/webhook-client.php`. The job's `handle` and the profile's `shouldProcess` receive the route. Verified when `signing_secret` is set and `signature_validator` is omitted or is `DefaultSignatureValidator`. A custom validator is verified only when that class checks a signature, and the guard is labeled `custom validator`. No validator and no secret stays unverified. A config name that is not in the file is left unbound |
| spatie/laravel-route-attributes | `#[Prefix]` / `#[Middleware]` on the class combine with the same attributes on the method. `#[Get]` / `#[Post]` / `#[Put]` / `#[Patch]` / `#[Delete]` / `#[Any]` / `#[Route]` are Laravel routes and merge with `routes/*.php` when the method and path already exist. Inline guards apply. See [PHP](php.md#route-attributes) |
| plugin RPC | `router.post("github.webhooks", handler)` (a dotted literal with no leading slash) is `POST /github.webhooks`. `router.post("/path")` and `router.post("/acme.events")` stay ordinary HTTP routes. See [TypeScript frameworks](ts-frameworks.md#express-koa-fastify-hono-elysia) |
| Kotlin `when` / Rust `match` | same event literals as `switch` / PHP `match` (`"push" ->`, `"a", "b" ->`, `Some("push") =>`, `"a" \| "b" =>`) when the subject is an event header or event field (`headers["X-GitHub-Event"]`, `event["type"]`, including `.as_str()`). A `when` / `match` on another string is not an event |
| signature constant | a class or module constant whose value is a signature header counts in that class or module, or where it is imported (`self::NAME`, `\App\Billing\Notifier::NAME`, `import { NAME }`). An unresolved name is not a header |

Cashier builds the method as `handle` plus StudlyCase of the event type after `.` is replaced with `_`. The method name does not record which separator was used. Published Stripe event types are matched back, so `invoice.payment_action_required` is not read as `invoice.payment.action.required`. A `handle<Studly>` method that matches no published type is the Studly words joined with `.`, which can hide underscores.

### Subscriptions, callbacks and drivers (#152 part B)

**Stored event names.** A sender whose event name is read from a stored subscription (`$subscription->event`, `webhook.event_type`, a `sub.events` loop, a `triggerEvent` passed from a row) sends one `endpoint:webhook:<this project>:<event>` per event the repo declares for those rows, each `heuristic` with `attrs.via = "stored subscription"` and `attrs.source` naming the list. The first of these sources that names events wins:

1. a validation list: an enum or const list named like a webhook / subscription event list, or tied to the rows by a model `$casts` entry, `Rule::enum` / `Rule::in`, `z.enum`, or an `event` / `event_type` field type (PHP, TypeScript, Python);
2. a config `events` list (`config/webhooks.php`, a TS or Python settings file that mentions webhooks);
3. seeder, factory, fixture and migration data (`'event' => 'order.paid'`, `'events' => [...]`, `->enum('event', [...])`, `->default(...)`, JSON / YAML fixtures).

A sender with no declared list keeps `{event}` (heuristic). Test files are not read as a source.

**Subscriber URLs.** An absolute URL that seed data, config or `.env.example` gives as a subscriber target (`'url' => 'https://bookstore-api.test/webhooks/orders'`, `WEBHOOK_URL=...`, a TS config object) is recorded on the sender endpoints as `attrs.subscriber_urls` (`url`, `at`). `cg link` matches each URL path against the other repos' routes with the same matcher as client calls (method `POST`) and adds a `heuristic` `MATCHES_ROUTE` from the sender endpoint to the route (`attrs.via = "subscriber url"`). Receiver endpoints on that route that carry the same event get a `heuristic` `MATCHES_ENDPOINT`. The link report lists each URL under "Webhook subscriptions paired by URL", or as `unpaired` with the reason. A paired route no longer counts as "without a client call".

**Callback URLs.** A URL built from the app's own base (`config('app.url') . '/api/webhooks/payments/' . $store->slug`, `url('/...')`, `route('webhooks.payments', $store)`, `env('APP_URL')`, TypeScript `${process.env.APP_URL}/...`, Python `settings.BASE_URL + '/...'`, an f-string or `urljoin`) that resolves to one of the app's own routes and is sent out adds `REGISTERS_CALLBACK` from the building function to that route (`attrs.body_key`, and `attrs.endpoint` when the outbound call is a known HTTP endpoint). "Sent out" is a body key such as `callback_url`, `notify_url` or `webhook_url` next to an outbound HTTP call, or the `url` of an SDK registration (`client.webhooks.create(url=...)`, `->webhooks->create(['url' => ...])`, `attrs.via = "SDK webhook registration"`). A URL on another host, a path that is not one of the app's routes, and a URL built in a helper the function only calls are not callbacks. `cg routes` shows `client: external callback (PaymentsClient::createPayment)` on the route. `cg link` lists it under "Routes called back by external parties" instead of "Backend routes without a client call", when the repo is linked as a backend or both.

**Drivers resolved at run time.** A route handler that calls `handleWebhook()` / `processWebhookRequest()` (also `processWebhook`, `handleWebhookRequest`, snake_case) on an object it does not name statically (`Gateway::driver($name)`, `$this->gateways[$row->gateway]`, a container-resolved interface) gets the implementations of that method: methods of that name in classes with an interface or base class that declares it, at most 12, plus what they call two levels down. Each gets a `heuristic` `ROUTES_TO` from the route (`via = driver`), and event comparisons inside them become `RECEIVED_BY` with `via = driver` (`heuristic`). A `constructEvent` or HMAC plus constant-time check inside an implementation marks the route verified with `how = "driver <Class>"` and `attrs.webhook.via = "driver"`; `attrs.webhook.drivers` lists the implementations. With no implementation in the repo the route is unchanged. One verifying driver marks the route verified even when another implementation does not check.

## Protocol index

| protocol | id | matcher | example | not covered |
|---|---|---|---|---|
| socketio | `endpoint:socketio:<ns>#<event>` | template | `sio.emit('order:created')` ↔ `@sio.on` / `socket.on` (Py, JS, Nest gateway, Dart, Kotlin, Swift, Rust) | `connect` / `disconnect`; `.svelte` / `.vue`; rooms as endpoints |
| ws | `route:WS <path>`, client `http:WS <path>` | path | `new WebSocketServer({path})` / Ktor `webSocket` ↔ `new WebSocket(url)` | message names inside the socket; Python and Rust clients; `noServer` path only when the upgrade handler names it |
| sse | HTTP route, `stream: sse` | path | `text/event-stream` or `@Sse()` ↔ `new EventSource(url)` | SSE `event:` names |
| webhook | `endpoint:webhook:<provider>:<event>` | exact; hmac/svix scheme is heuristic | `event.type === 'invoice.paid'` receives; svix `eventType` or a signed POST sends | a stored event name with no list in the repo (stays `{event}`), callback URLs built in a helper, drivers that share no interface or base class |
| worker | `<script>`, `<script>:out`, `service-worker` | exact | `new Worker('./w.ts')` ↔ `self.onmessage` | worker message types, `MessageChannel` |
| broadcastchannel | `<name>` | exact | `new BroadcastChannel(name).postMessage` | |
| postmessage | `<type>` or `*` | glob | `postMessage({type})` ↔ `event.data.type ===` | `targetOrigin` is a guard when the handler reads `event.origin` |
| extension | `<type>`, `port:<name>` | glob | `chrome.runtime.sendMessage` ↔ `onMessage` | `externally_connectable` |
| native-messaging | `<host>` | exact | `connectNative('com.app.host')` ↔ host manifest `path` | |
| unix | `<path>` | template | `UnixListener::bind(p)` ↔ `connect(p)` | abstract `\0` names, systemd socket activation |
| pipe | `<name>` | template | `CreateNamedPipe` / `mkfifo` ↔ writer `open` | |
| dbus | `<iface>.<Member>` | exact | zbus `#[interface]` ↔ `#[proxy]` | D-Bus service files |
| intent | `<component FQN>` | exact | `Intent(ctx, Foo::class.java)` ↔ `onCreate` / `onStartCommand` | extras, `content://`, Messenger |
| intent-action | `<action>` | exact, fan-out | `Intent("com.x.ACTION")` ↔ manifest `<action>` | `android.*` / SDK actions |
| aidl | `<pkg.IFace>.<method>` | exact | `Stub.asInterface(b).m()` ↔ `IFace.Stub` | |
| process | `<program>`, `<program>:out` | exact | in-repo `spawn` / `subprocess` / `artisan` ↔ `process.on('message')` | `cluster.fork`, `python -c` (a call, not a program) |
| isolate | `<file>#<entry>`, `:out` | exact | `Isolate.spawn` / `compute` ↔ `port.listen` | later messages on a returned `SendPort` |
| xpc | `<service>`, `<Protocol>.<method>` | exact | `NSXPCConnection(machServiceName:)` ↔ `shouldAcceptNewConnection` | `Info.plist` `NSXPCListener.service()`, dependency protocols |
| darwin-notification | `<name>` | exact, fan-out | `CFNotificationCenterPostNotification` ↔ `AddObserver` | |
| tcp, udp | `endpoint:tcp:<port>`, `udp:<port>` | same port; `env:KEY` is heuristic | `listen(6379)` ↔ `connect(6379)` across the languages in `cg_code_graph/sockets.py` | proof they speak; QUIC; WebRTC; port only in a config file; `env:` across `cg link` |
| mdns, osc, coap, ssdp | service type, OSC address, CoAP path, SSDP USN | exact / glob / path | zeroconf `ServiceInfo` ↔ `ServiceBrowser`; `dispatcher.map` ↔ `send_message` | |
| grpc | `endpoint:grpc:<pkg>.<Service>/<Method>` | contract method, case-insensitive | `.proto` `rpc` ↔ servicer class / stub call | Go; Nest `@GrpcMethod` not merged onto the contract node |
| thrift | `endpoint:thrift:<stem>.<Service>/<method>` | same, including `extends` | `.thrift` service ↔ `Svc.Client` / `Processor` | Java sources (heuristic plugin; Thrift receivers are not linked yet) |
| trpc | `endpoint:trpc:<dotted.path>` | exact path | `createTRPCRouter` procedure ↔ `x.post.create.useQuery` | `lazy()` routers |
| jsonrpc | `endpoint:jsonrpc:<method>` | exact | `addMethod("x")` / `@method` ↔ `.request("x")` | MCP (its own endpoints); LSP methods |
| graphql | `endpoint:graphql:<Root>.<field>` | exact | SDL or graphene / strawberry / ariadne / Nest ↔ Apollo `useQuery` | object-type fields; `.graphql` ops with no hook; Lighthouse, Rust, Spring, Apollo mobile |
| job | `endpoint:job:<fw>:<name>` | task name | Celery `send_task("billing.charge")` ↔ `@app.task(name=)`; also RQ, Dramatiq, Bull, Laravel, Messenger | Huey, arq, Spring JMS, Agenda; project wrappers |
| queue | `endpoint:queue:<fw>/<name>` | queue name | enqueue ↔ `celery -Q` / `queue:work` / `messenger:consume` | |
| kafka | `kafka:<topic>` | glob | `producer.send({topic})` ↔ `subscribe` | SQS, SNS, Pub/Sub, Service Bus, STOMP, ZeroMQ, JetStream |
| amqp | `amqp:<exchange>/<key>`, `amqp:queue:<name>` | amqp | `basic_publish` ↔ queue bound with `consume` | bindings only in Terraform / SAM |
| redis-pubsub | `redis-pubsub:<channel>` | glob for `PSUBSCRIBE` | `publish` ↔ `subscribe` | |
| redis-stream | `redis-stream:<key>` | exact | `xadd` ↔ `xreadgroup` | |
| mqtt | `mqtt:<topic>` | mqtt | `publish(topic)` ↔ `subscribe` | |
| nats | `nats:<subject>` | nats | `publish` / `request` ↔ `subscribe` | |

Broker and job names come from literals, constants, enums and templates (`{id}` is heuristic).
A repository wrapper that rewrites the topic is skipped. Kotlin (Spring listeners,
kafka-clients, RabbitMQ) and Rust (lapin, rdkafka, async-nats) use the same ids. `cg link` joins
them across repos.

## Not covered yet

- Guards on Bull processors are not copied onto the adapted `job` node, so `unguarded` is not
  checked there.
- `schema_mismatch` needs `schema` on both sides.
- Broker and host nodes are not attached to endpoints yet.
- Go has no plugin (gRPC, sockets, brokers). Java has a heuristic plugin; RPC shapes still add no
  edges.
- GraphQL: code-first schemas (TypeGraphQL, Pothos), `@deprecated` and field guards.
