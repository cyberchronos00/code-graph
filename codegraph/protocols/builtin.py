"""Built-in protocols. Adapter protocols describe node kinds cg already emitted before #31 (ids unchanged, read by
protocols/view.py); `endpoint` protocols are filled by plugins through protocol_send / protocol_receive."""
from __future__ import annotations

from . import Protocol, register
from . import matchers as M

A = "adapter"
# ---- existing kinds, adapted (no new nodes or edges)
register(Protocol("http", "tcp", "HTTP request: client endpoint http:<METHOD> <path> -> route (MATCHES_ROUTE, cg link)",
                  matcher=M.path, ports=(80, 443), schemes=("http", "https"), source=A, kinds=("http", "route"), guards=True))
register(Protocol("ws", "tcp", "WebSocket route (Django Channels consumer, route:WS <path>)", matcher=M.path, ports=(80, 443),
                  schemes=("ws", "wss"), source=A, kinds=("route",), guards=True))
register(Protocol("graphql", "tcp", "GraphQL operation resolver (route:GRAPHQL Query.x)", source=A, kinds=("route",), guards=True))
register(Protocol("pusher", "tcp", "Pusher protocol broadcast channel (Laravel Broadcast::channel / broadcastOn -> Echo / "
                  "pusher-js subscription; MATCHES_CHANNEL; the `cg channels` view)", matcher=M.dotted, fanout=True,
                  source=A, kinds=("channel", "channel_sub")))
register(Protocol("nest-rpc", "tcp", "NestJS microservice request (ClientProxy.send -> @MessagePattern)", source=A,
                  kinds=("message",), guards=True))
register(Protocol("nest-event", "tcp", "NestJS microservice event (ClientProxy.emit -> @EventPattern)", fanout=True, source=A,
                  kinds=("message",), guards=True))
register(Protocol("nest-ws", "tcp", "NestJS WebSocket gateway message (@SubscribeMessage)", source=A, kinds=("message",),
                  guards=True))
register(Protocol("grpc", "tcp", "gRPC method (NestJS @GrpcMethod)", source=A, kinds=("message",), guards=True))
register(Protocol("bull", "tcp", "Bull / BullMQ job queue (Queue.add -> @Processor / @Process, WorkerHost.process)",
                  source=A, kinds=("job",)))
register(Protocol("laravel-queue", "tcp", "Laravel queued job (dispatch / Bus / schedule -> Job::handle)", source=A,
                  kinds=("job",)))
register(Protocol("celery", "tcp", "Celery / RQ / Dramatiq task (.delay / .apply_async / send_task -> task function)",
                  source=A, kinds=("job",)))
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
register(Protocol("amqp", "tcp", "AMQP 0-9-1 routing key on a topic exchange (`*` / `#`)", matcher=M.amqp_topic,
                  fanout=True, ports=(5672, 5671), schemes=("amqp", "amqps")))
register(Protocol("kafka", "tcp", "Kafka topic (produce -> consumer group subscription; regex subscriptions as globs)",
                  matcher=M.glob, fanout=True, ports=(9092,), schemes=("kafka",)))
register(Protocol("redis-pubsub", "tcp", "Redis PUBLISH -> SUBSCRIBE / PSUBSCRIBE (glob patterns)", matcher=M.glob,
                  fanout=True, ports=(6379,), schemes=("redis", "rediss")))
register(Protocol("socketio", "tcp", "Socket.IO event (<namespace>#<event>; emit -> on, both directions; python-socketio "
                  "server and client)", matcher=M.template, ports=(80, 443), schemes=("ws", "wss", "http", "https"), guards=True,
                  directional=True))

# ---- AI harnesses (#66, codegraph/plugins/python/aitools.py): tools the model or an MCP client calls by name
register(Protocol("llm_tool", "local", "LLM tool / function: schema offered to the model (OpenAI, Anthropic, LangChain, "
                  "Agents SDK, LlamaIndex) -> handler (decorated function, dict registry, agent-loop branch)",
                  entry_kind="llm_tool"))
for _n, _d in (("mcp_tool", "MCP tool"), ("mcp_resource", "MCP resource (URI template)"), ("mcp_prompt", "MCP prompt")):
    register(Protocol(_n, "tcp", f"{_d}: <server>/<name>; client call_tool / read_resource / get_prompt -> FastMCP / "
                      "MCPServer handler (`*/<name>` from a client that does not name the server)", matcher=M.mcp,
                      entry_kind="llm_tool", schemes=("stdio", "http", "https")))
