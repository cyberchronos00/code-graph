"""Graph data model: node/edge kinds, confidence levels, stable ids."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Confidence of an edge, strongest first.
EXACT = "exact"          # syntactically certain (static call, `new X`, $this->m(), literal key)
RESOLVED = "resolved"    # needed type/name resolution (typed property/param, inferred var type, model->table)
HEURISTIC = "heuristic"  # best effort (unique-method-name fallback, unique column-name literal)
CONFIDENCE_RANK = {EXACT: 3, RESOLVED: 2, HEURISTIC: 1}

# Edge kinds. propagates=True means "src depends on dst": reverse traversal from a
# target follows these edges to find everything that depends on it.
EDGE_KINDS: dict[str, tuple[bool, str]] = {
    "CALLS": (True, "function/method calls function/method"),
    "IMPLEMENTED_BY": (True, "interface/abstract method may dispatch to implementation"),
    "OVERRIDDEN_BY": (True, "parent method may dispatch to child override"),
    "BOUND_TO": (True, "container binding abstract -> concrete"),
    "ROUTES_TO": (True, "HTTP route -> controller action"),
    "USES_MIDDLEWARE": (True, "route -> middleware handle()"),
    "HANDLED_BY": (True, "artisan command name -> handler method"),
    "SCHEDULES": (True, "scheduler entry -> command/job"),
    "DISPATCHES": (True, "code dispatches job/event"),
    "LISTENED_BY": (True, "event -> listener handler"),
    "READS_COLUMN": (True, "code reads column"),
    "WRITES_COLUMN": (True, "code writes column"),
    "MENTIONS_COLUMN": (True, "string literal equal to a distinctive column name"),
    "READS_TABLE": (True, "code reads table"),
    "WRITES_TABLE": (True, "code writes table"),
    "USES_CONNECTION": (True, "code/model uses DB connection"),
    "REGISTERS_CONNECTION": (True, "code registers a (dynamic) DB connection"),
    "READS_CONFIG": (True, "code reads config key"),
    "WRITES_CONFIG": (True, "code sets config key at runtime"),
    "READS_ENV": (True, "code/config reads env key"),
    "REFERS_TO": (True, "config value names a connection/other entity"),
    "CONFIGURED_BY": (True, "connection or external system defined by a config / env key"),
    "CONFIG_CONTAINS": (True, "config parent key contains child key"),
    "MAPS_TO_TABLE": (False, "model class -> table"),
    "HAS_RELATION": (False, "model -> related model (hasMany, belongsTo, ...)"),
    "CONTAINS": (False, "class -> member, table -> column"),
    "EXTENDS": (False, "class extends class"),
    "IMPLEMENTS": (False, "class implements interface"),
    "USES_TRAIT": (False, "class uses trait"),
    "INSTANTIATES": (False, "code instantiates class (constructor call is a separate CALLS edge)"),
    "INJECTS": (False, "constructor-injected dependency type"),
    "REFERENCES": (False, "X::class reference"),
    "OBSERVED_BY": (False, "model observer registration"),
    "DEFINES": (False, "file/migration defines table/column"),
    "BINDS": (False, "service provider registers binding"),
    # TypeScript / Vue / Nuxt
    "IMPORTS": (False, "module/component imports module/component"),
    "RENDERS": (True, "component template renders child component"),
    "USES_COMPOSABLE": (True, "code calls a composable (useX())"),
    "USES_STORE": (True, "code calls a Pinia store (useXStore())"),
    "HTTP_CALLS": (True, "client code calls an HTTP endpoint (method + path template)"),
    "MATCHES_ROUTE": (True, "client endpoint matched to a backend route (cross-repo link)"),
    "USES_LAYOUT": (False, "page uses layout"),
    "USES_I18N": (False, "code/template uses i18n key"),
    "REFERENCES_TYPE": (False, "code references a type/interface"),
    # value facts
    "READS_SETTING": (True, "code reads a JSON settings key (getSetting('a.b', default))"),
    "WRITES_SETTING": (True, "code writes a JSON settings key (setSetting)"),
    "READS_INPUT": (True, "code reads an HTTP request key ($request->input('k'), $validated['k'], $filters['k'] via arg flow)"),
    "VALIDATES": (True, "FormRequest::rules() declares a request key"),
    "VALIDATED_BY": (True, "controller action is validated by a FormRequest (rules())"),
    "HAS_RESOLUTION": (True, "code resolves a value through a fallback chain (resolution node)"),
    "FALLS_BACK_TO": (True, "resolution chain step -> source node (setting, request key, column, config, env); attrs.order"),
    # native code (Rust, C, C++)
    "USES_TYPE": (True, "code/type refers to a type (struct, enum, union, trait, class, typedef)"),
    "ACCESSES_FIELD": (True, "code reads/writes a struct/class field or enum variant"),
    "USES_VALUE": (True, "code refers to a constant, static/global variable or object-like macro"),
    "REFERENCES_FN": (True, "code/data takes a function as a value (callback, dispatch table, handler registration)"),
    "USES_UNSAFE": (True, "code contains an unsafe block or is an unsafe fn (sink node unsafe:<crate>)"),
    "GATED_BY": (True, "code compiled only under a condition: Cargo feature, cfg predicate or preprocessor macro"),
    "INCLUDES": (True, "source/header file #includes a header"),
    # Python / Django
    "USES_SCHEMA": (False, "handler/view uses a wire schema (ninja/pydantic Schema, DRF serializer) for request/response; attrs.role"),
    # Dart / Flutter
    "EMITS_STATE": (False, "bloc/cubit code emits a state class"),
    "HANDLES_STATE": (False, "UI code checks for a state class (is / switch pattern / BlocListener)"),
    "NAVIGATES_TO": (False, "UI code navigates to a page/route (Navigator.push, context.go, named route)"),
    "PARSES_JSON": (False, "code parses an HTTP response into a model (X.fromJson) or serialises a request body (toJson)"),
    # realtime broadcasting (Laravel channels, Echo / pusher-js subscriptions)
    "AUTHORIZES_CHANNEL": (True, "broadcasting auth route -> channel whose authorization callback it runs"),
    "BROADCASTS_ON": (False, "broadcast event -> channel it publishes on (broadcastOn(); attrs.name = evaluated channel name)"),
    "SUBSCRIBES_CHANNEL": (True, "client code subscribes to a channel (Echo.private / channel / join, pusher.subscribe, useEcho)"),
    "MATCHES_CHANNEL": (True, "client channel subscription matched to a backend channel pattern (cross-repo link)"),
    "LISTENS_FOR": (False, "client channel subscription listens for a backend broadcast event (.listen('Name'))"),
    # protocol endpoints (#31 model, codegraph/protocols/; web / native bridges in codegraph/bridges.py):
    # code -> endpoint:<protocol>:<name> -> handler
    "SENDS_TO": (True, "code sends to a protocol endpoint (publish, emit, request, invoke, enqueue; bridge call: Capacitor "
                       "plugin / React Native module method, Flutter MethodChannel.invokeMethod); attrs.role, via"),
    "RECEIVED_BY": (True, "protocol endpoint -> the handler receiving it (subscriber, event handler; native @PluginMethod / "
                          "@ReactMethod / channel handler); attrs.platform"),
    "MATCHES_ENDPOINT": (True, "send-side endpoint -> receive-side endpoint of the same protocol whose name matches by the "
                               "protocol's rules (wildcards, {param} templates); attrs.pattern, segments"),
    # external systems (#40, codegraph/external.py): external:<protocol>:<target>
    "CONNECTS_TO": (True, "code or a logical connection -> external system it connects to (database, cache, broker, mail relay, "
                          "directory, file-transfer host, object store); attrs.op, via"),
    "CREDENTIAL_FROM": (False, "external system -> env / config node holding its password / token (never the value); attrs.kind"),
    # AI harnesses (#66): agents and the tools they expose (tools themselves are endpoint:llm_tool / mcp_tool nodes)
    "OFFERS_TOOL": (True, "agent -> a tool endpoint it gives the model (Agents SDK Agent(tools=), create_react_agent)"),
    "HANDS_OFF_TO": (True, "agent -> agent it can hand the conversation to (Agents SDK handoffs=[...])"),
    # generated / copied files (codegraph/core/generated.py), only with --include-generated
    "COPY_OF": (False, "copied file (Capacitor / Cordova web assets in a native project) -> the source file it is copied from"),
    # test code (tests/, *.spec.ts, ...). Never propagating: tests do not change blast radius, caller counts or entry
    # tagging; the `tests` query walks these on purpose
    "TEST_CALLS": (False, "test code calls / dispatches to code (attrs.orig = the original edge kind)"),
    "TEST_USES": (False, "test code touches a table, config key, class... (attrs.orig = the original edge kind)"),
    "TEST_HTTP": (False, "test sends an HTTP request to a route ($this->getJson('/x'), Pest get(), Playwright request)"),
    "TEST_VISITS": (False, "browser test opens a frontend page (Playwright / Cypress page.goto('/x'))"),
}
TEST_EDGE_KINDS = ("TEST_CALLS", "TEST_USES", "TEST_HTTP", "TEST_VISITS")
PROPAGATING = sorted(k for k, (p, _) in EDGE_KINDS.items() if p)

ENTRY_KINDS = ("http_route", "websocket", "artisan_command", "management_command", "scheduled", "queue_job", "listener",
               "admin_panel", "observer", "ui_page", "ui_global",
               "main", "ffi_export", "public_api", "test", "bench", "example", "build_script")
UI_ENTRY_KINDS = ("ui_page", "ui_global")
# websocket = Channels consumer routes; management_command = Django `manage.py <name>` (operator, like artisan)
RUNTIME_ENTRY_KINDS = ("http_route", "websocket", "scheduled", "queue_job", "listener", "main", "ffi_export")
OPERATOR_ENTRY_KINDS = ("artisan_command", "management_command", "admin_panel")
# native code: library API surface (pub items of a lib crate, exported C/C++ API) and dev/build-time entries
LIBRARY_ENTRY_KINDS = ("public_api",)
DEV_ENTRY_KINDS = ("test", "bench", "example", "build_script")
# TS server frameworks: microservice / WebSocket message handlers run at runtime; CLI commands (nest-commander) are
# operator-only, like artisan commands.
ENTRY_KINDS += ("message_handler", "cli_command")
RUNTIME_ENTRY_KINDS += ("message_handler",)
# realtime: a channel authorization callback (Laravel Broadcast::channel) runs on every private/presence subscription
ENTRY_KINDS += ("channel_auth",)
RUNTIME_ENTRY_KINDS += ("channel_auth",)
OPERATOR_ENTRY_KINDS += ("cli_command",)
# AI harnesses: a tool endpoint the model (agent runner) or an MCP client calls by name
ENTRY_KINDS += ("llm_tool",)
RUNTIME_ENTRY_KINDS += ("llm_tool",)


@dataclass
class Node:
    id: str
    kind: str
    name: str
    fqn: str | None = None
    file: str | None = None
    line: int | None = None
    end_line: int | None = None
    module: str | None = None
    doc: str | None = None
    lang: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    entry_kind: str | None = None  # set when the node itself is an entry point


@dataclass
class Edge:
    src: str
    dst: str
    kind: str
    file: str | None = None
    line: int | None = None
    confidence: str = EXACT
    attrs: dict[str, Any] = field(default_factory=dict)
    # gate scenario under which this reference is dead (e.g. "new_inventory"), None = live.
    # Set deterministically by the guard evaluator (plugins/php/gating.py); evidence in attrs["guard"].
    gate: str | None = None


def node_id(kind: str, key: str) -> str:
    """Stable id = kind + ':' + canonical key (FQN, table.column, route signature...)."""
    return f"{kind}:{key}"
