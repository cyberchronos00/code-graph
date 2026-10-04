"""Built-in protocols. Adapter protocols describe node kinds cg already emitted before #31 (ids unchanged, read by
protocols/view.py); `endpoint` protocols are filled by plugins through protocol_send / protocol_receive."""
from __future__ import annotations

from . import Protocol, register
from . import matchers as M

A = "adapter"
# ---- existing kinds, adapted (no new nodes or edges)
register(Protocol("http", "tcp", "HTTP request: client endpoint http:<METHOD> <path> -> route (MATCHES_ROUTE, cg link)",
                  matcher=M.path, ports=(80, 443), schemes=("http", "https"), source=A, kinds=("http", "route"), guards=True))
register(Protocol("ws", "tcp", "WebSocket connection: client endpoint http:WS <path> (browser WebSocket, ws, Dart "
                  "web_socket_channel) -> route:WS <path> (ws servers, express-ws, @fastify/websocket, Hono, FastAPI / "
                  "Starlette, Django Channels consumers, python websockets)", matcher=M.path, ports=(80, 443),
                  schemes=("ws", "wss"), source=A, kinds=("http", "route"), guards=True))
register(Protocol("sse", "tcp", "Server-Sent Events stream: EventSource / fetchEventSource client endpoint (http:GET with "
                  "stream sse) -> a route answering text/event-stream (Nest @Sse, Hono streamSSE, EventSourceResponse)",
                  matcher=M.path, ports=(80, 443), schemes=("http", "https"), source=A, kinds=("http", "route"), guards=True))
register(Protocol("graphql", "tcp", "GraphQL root field <Query|Mutation|Subscription>.<field> (codegraph/graphql.py): "
                  "operations (useQuery / client.query / codegen hooks / gql documents) -> schema resolvers (resolver maps, "
                  "graphene, strawberry, ariadne, Nest route:GRAPHQL)", kinds=("endpoint", "route"), guards=True))
register(Protocol("pusher", "tcp", "Pusher protocol broadcast channel (Laravel Broadcast::channel / broadcastOn -> Echo / "
                  "pusher-js subscription; MATCHES_CHANNEL; the `cg channels` view)", matcher=M.dotted, fanout=True,
                  source=A, kinds=("channel", "channel_sub")))
register(Protocol("nest-rpc", "tcp", "NestJS microservice request (ClientProxy.send -> @MessagePattern)", source=A,
                  kinds=("message",), guards=True))
register(Protocol("nest-event", "tcp", "NestJS microservice event (ClientProxy.emit -> @EventPattern)", fanout=True, source=A,
                  kinds=("message",), guards=True))
register(Protocol("nest-ws", "tcp", "NestJS WebSocket gateway message (@SubscribeMessage)", source=A, kinds=("message",),
                  guards=True))
register(Protocol("grpc", "tcp", "gRPC method <package>.<Service>/<Method> from .proto files (codegraph/rpc.py): stub "
                  "call -> generated base class implementation; NestJS @GrpcMethod messages are read as grpc too",
                  kinds=("endpoint", "message"), guards=True, schemes=("grpc", "grpcs")))
register(Protocol("thrift", "tcp", "Apache Thrift method <module>.<Service>/<method> from .thrift files (codegraph/rpc.py): "
                  "client call -> handler implementing the generated Iface / If", guards=True))
register(Protocol("trpc", "tcp", "tRPC procedure <path> (codegraph/rpc.py): api.<path>.useQuery / .query / .mutate / "
                  "queryOptions and server-side callers -> the router's procedure resolver", guards=True))
register(Protocol("jsonrpc", "tcp", "JSON-RPC 2.0 method by name (codegraph/rpc.py): client.request('x') / payloads "
                  "{jsonrpc: '2.0', method: 'x'} -> jayson / json-rpc-2.0 / vscode-jsonrpc / jsonrpcserver / jsonrpsee handlers",
                  guards=True))
register(Protocol("bull", "tcp", "Bull / BullMQ job queue (Queue.add -> @Processor / @Process, WorkerHost.process)",
                  source=A, kinds=("job",)))
register(Protocol("laravel-queue", "tcp", "Laravel queued job (dispatch / Bus / schedule -> Job::handle)", source=A,
                  kinds=("job",)))
register(Protocol("celery", "tcp", "Celery / RQ / Dramatiq task (.delay / .apply_async / send_task -> task function)",
                  source=A, kinds=("job",)))
register(Protocol("job", "tcp", "Background job / task by name, endpoint:job:<framework>:<name> (codegraph/jobs.py): "
                  "enqueue (Celery .delay / send_task, RQ enqueue, Dramatiq .send, Bull add, Messenger dispatch) -> the "
                  "task function; the Django / Nest / Laravel plugins' job nodes are shown with their twin's senders", matcher=M.template))
register(Protocol("queue", "tcp", "Named job queue, endpoint:queue:<framework>/<queue> (codegraph/jobs.py): enqueue sites "
                  "naming the queue -> the tasks routed to it; attrs.consumers: worker processes (Procfile, compose, systemd, "
                  "supervisor, scripts); no_consumer: produced, workers known, none consumes it", fanout=True, entry=False))
register(Protocol("laravel-event", "local", "Laravel application event (event() / dispatch -> listener handle())",
                  fanout=True, entry=False, source=A, kinds=("event",)))
register(Protocol("nest-event-emitter", "local", "NestJS EventEmitter2 event (emit -> @OnEvent)", fanout=True, entry=False,
                  source=A, kinds=("event",)))
register(Protocol("django-signal", "local", "Django signal (send -> @receiver)", fanout=True, entry=False, source=A,
                  kinds=("event",), framework_senders=("django.*",)))

# ---- web / native bridges and desktop process boundaries (codegraph/bridges.py: endpoint nodes with their own checks)
for _n, _t, _d in (("capacitor", "local", "Capacitor plugin method"), ("react-native", "local", "React Native / Expo native module method"),
                   ("flutter", "local", "Flutter MethodChannel method"), ("flutter-event", "local", "Flutter EventChannel stream"),
                   ("pigeon", "local", "Pigeon API method"), ("electron-ipc", "ipc", "Electron IPC channel"),
                   ("electron-preload", "local", "Electron context bridge member"), ("tauri", "ipc", "Tauri command")):
    register(Protocol(_n, _t, _d + " (`cg bridges`)", entry=False, source="bridges"))

# ---- endpoint protocols (protocol_send / protocol_receive; matchers run at index and link time)
register(Protocol("mqtt", "tcp", "MQTT topic (publish -> subscribe, `+` / `#` wildcards)", matcher=M.mqtt, fanout=True,
                  ports=(1883, 8883), schemes=("mqtt", "mqtts")))
register(Protocol("nats", "tcp", "NATS subject (publish / request -> subscribe, `*` / `>` wildcards)", matcher=M.nats,
                  fanout=True, ports=(4222,), schemes=("nats", "tls")))
register(Protocol("amqp", "tcp", "AMQP 0-9-1 `<exchange>/<routing key>` -> bound queues (topic `*` / `#`; fanout "
                  "`<exchange>/#`) and `queue:<name>` (default exchange)", matcher=M.amqp,
                  fanout=True, ports=(5672, 5671), schemes=("amqp", "amqps")))
register(Protocol("kafka", "tcp", "Kafka topic (produce -> consumer group subscription; regex subscriptions as globs)",
                  matcher=M.glob, fanout=True, ports=(9092,), schemes=("kafka",)))
register(Protocol("redis-pubsub", "tcp", "Redis PUBLISH -> SUBSCRIBE / PSUBSCRIBE (glob patterns)", matcher=M.glob,
                  fanout=True, ports=(6379,), schemes=("redis", "rediss")))
register(Protocol("redis-stream", "tcp", "Redis stream key: XADD -> XREAD / XREADGROUP (consumer group)", fanout=True,
                  ports=(6379,), schemes=("redis", "rediss")))
register(Protocol("socketio", "tcp", "Socket.IO event (<namespace>#<event>; emit -> on, both directions; python-socketio "
                  "and JS / TS socket.io servers, socket.io-client and Nest gateways)", matcher=M.template, ports=(80, 443), schemes=("ws", "wss", "http", "https"), guards=True,
                  directional=True))
register(Protocol("webhook", "tcp", "Webhook event <provider>:<event> (codegraph/webhooks.py): a provider (Stripe, GitHub, "
                  "svix, Twilio, ...) posts it to a receiver route; receivers record their signature check as a guard",
                  matcher=M.webhook, fanout=True, guards=True, ports=(443,), schemes=("https",), framework_senders=("*",)))

# ---- local IPC between execution contexts of a JS / TS app (#38 part 1, codegraph/local_ipc.py)
register(Protocol("worker", "local", "Web Worker / SharedWorker / service worker messages: endpoint:worker:<script> "
                  "(postMessage, comlink wrap -> the script's onmessage / expose) and <script>:out back to the page",
                  entry=False))
register(Protocol("broadcastchannel", "local", "BroadcastChannel by name: postMessage -> onmessage / message listeners "
                  "of any channel of that name", fanout=True, entry=False))
register(Protocol("postmessage", "local", "window.postMessage between windows / iframes by message type (`*`: any "
                  "type; listeners record an origin check as a guard)", matcher=M.glob, fanout=True, guards=True))
register(Protocol("extension", "local", "Browser-extension runtime messaging by message type (runtime / tabs "
                  "sendMessage -> runtime.onMessage) and `port:<name>` (runtime.connect -> onConnect)",
                  matcher=M.extension, fanout=True, guards=True))
register(Protocol("native-messaging", "ipc", "Browser native messaging host: connectNative / sendNativeMessage -> the "
                  "in-repo program its host manifest names"))

# ---- raw sockets (#39, codegraph/sockets.py): endpoint:tcp:<port> / endpoint:udp:<port> (env:<KEY> without a value)
register(Protocol("tcp", "tcp", "Raw TCP socket: connect -> listener on the same port (listen / bind vs connect; "
                  "bind address and exposure on the listener)"))
register(Protocol("udp", "udp", "Raw UDP socket: send_to / connect -> socket bound on the same port", fanout=True))

# UDP application protocols with a shared name (#39, codegraph/udp_apps.py)
register(Protocol("mdns", "udp", "mDNS / DNS-SD service type: browse -> advertise (zeroconf, bonjour, NWBrowser / "
                  "NetService, NsdManager, JmDNS, bonsoir)", fanout=True, ports=(5353,)))
register(Protocol("osc", "udp", "Open Sound Control address: send_message -> dispatcher map (address patterns as globs)",
                  matcher=M.glob, fanout=True))
register(Protocol("coap", "udp", "CoAP resource path: request -> resource (aiocoap, Californium, node-coap)", matcher=M.path,
                  ports=(5683, 5684), schemes=("coap", "coaps")))
register(Protocol("ssdp", "udp", "SSDP / UPnP search target: M-SEARCH -> advertised USN / device type", fanout=True,
                  ports=(1900,)))

# ---- AI harnesses (#66, codegraph/plugins/python/aitools.py): tools the model or an MCP client calls by name
register(Protocol("llm_tool", "local", "LLM tool / function: schema offered to the model (OpenAI, Anthropic, LangChain, "
                  "Agents SDK, LlamaIndex) -> handler (decorated function, dict registry, agent-loop branch)",
                  entry_kind="llm_tool"))
for _n, _d in (("mcp_tool", "MCP tool"), ("mcp_resource", "MCP resource (URI template)"), ("mcp_prompt", "MCP prompt")):
    register(Protocol(_n, "tcp", f"{_d}: <server>/<name>; client call_tool / read_resource / get_prompt -> FastMCP / "
                      "MCPServer handler (`*/<name>` from a client that does not name the server)", matcher=M.mcp,
                      entry_kind="llm_tool", schemes=("stdio", "http", "https")))
