"""AI harnesses (#66): LLM tools, MCP servers / clients and agents in the #31 endpoint model; `cg tools` / MCP."""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.protocols import matchers as M  # noqa: E402

FX = ROOT / "tests" / "ai_fixture"


def cli(*a):
    return subprocess.run([sys.executable, "-m", "cg_code_graph.cli", *a], cwd=ROOT, capture_output=True, text=True)


def edges(st, kind):
    return {(r["src"], r["dst"]) for r in st.q("SELECT src, dst FROM edges WHERE kind=?", (kind,))}


def test_mcp_matcher():
    assert M.mcp("*/refund", "orders/refund")["wild"] == 1
    assert M.mcp("orders/refund", "orders/refund")
    assert M.mcp("billing/refund", "orders/refund") is None
    assert M.mcp("*/orders://42", "orders/orders://{order_id}")
    assert M.mcp("*/orders://42/x", "orders/notes://{id}") is None


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    db = tmp_path_factory.mktemp("ai") / "shop.db"
    stats = index_project(FX, db, "shop")
    return {"db": db, "stats": stats}


def test_agent_loop_schema_dict_and_dynamic(shop):
    st = GraphStore(shop["db"])
    rb = edges(st, "RECEIVED_BY")
    assert ("endpoint:llm_tool:refund_order", "function:shop.agent.refund_order") in rb      # dict registry
    assert ("endpoint:llm_tool:lookup_order", "function:shop.agent.lookup_order") in rb
    sends = edges(st, "SENDS_TO")
    for t in ("refund_order", "lookup_order", "escalate"):                                    # tools=TOOLS offers them
        assert ("function:shop.agent.run_agent", f"endpoint:llm_tool:{t}") in sends
    n = st.node("function:shop.agent.run_plugin_tool")
    assert json.loads(n["attrs"])["llm_dynamic_dispatch"][0]["expr"].startswith("globals()[")
    calls = json.loads(st.node("function:shop.agent.run_agent")["attrs"])["llm_calls"]
    assert calls == [{"provider": "openai", "op": "chat", "model": "gpt-4o-mini", "line": 36}]
    assert st.node("endpoint:llm_tool:refund_order")["entry_kind"] == "llm_tool"


def test_mcp_server_client_path_and_checks(shop):
    st = GraphStore(shop["db"])
    rb = edges(st, "RECEIVED_BY")
    assert ("endpoint:mcp_tool:orders/refund", "function:shop.mcp_server.refund") in rb
    assert ("endpoint:mcp_resource:orders/orders://{order_id}", "function:shop.mcp_server.order_resource") in rb
    assert ("endpoint:mcp_prompt:orders/refund_prompt", "function:shop.mcp_server.refund_prompt") in rb
    assert ("endpoint:mcp_tool:*/refund", "endpoint:mcp_tool:orders/refund") in edges(st, "MATCHES_ENDPOINT")
    # the decorator reference was the only link before; the endpoint replaces it
    assert not [r for r in st.q("SELECT 1 FROM edges WHERE kind='REFERENCES_FN' AND dst='function:shop.mcp_server.refund'")]
    r = cli("path", "shop.mcp_client.refund_via_mcp", "table:refunds", "--db", str(shop["db"]))
    for hop in ("SENDS_TO", "endpoint:mcp_tool:*/refund", "MATCHES_ENDPOINT", "endpoint:mcp_tool:orders/refund",
                "RECEIVED_BY", "WRITES_TABLE", "table:refunds"):
        assert hop in r.stdout, r.stdout
    r = cli("impact", "shop.agent.refund_order", "--db", str(shop["db"]))
    assert "endpoint:llm_tool:refund_order" in r.stdout and "endpoint:mcp_tool:orders/refund" in r.stdout
    assert "shop.agent.run_agent" in r.stdout and "llm_tool" in r.stdout
    r = cli("tests", "shop.agent.refund_order", "--db", str(shop["db"]))
    assert "test_run_agent_refunds" in r.stdout
    from cg_code_graph.aitools import tools
    res = tools(st)
    by = {(e["protocol"], e["name"]): e for e in res["tools"]}
    assert by[("llm_tool", "escalate")]["checks"] == ["no_receiver"]
    assert by[("llm_tool", "refund_order")]["framework"] == "openai"
    assert by[("llm_tool", "refund_order")]["reaches_tables"] == ["refunds (write)"]
    assert [d["expr"] for d in res["dynamic_dispatch"]] == ["globals()[tool_call.function.name]"]
    out = cli("tools", "--db", str(shop["db"])).stdout
    assert "no_receiver" in out and "dynamic_dispatch" in out and "orders/refund" in out
    assert "escalate" in cli("tools", "--db", str(shop["db"]), "--unmatched").stdout
    j = json.loads(cli("tools", "--db", str(shop["db"]), "--framework", "mcp", "--json").stdout)
    assert j["tools"] and all(e["framework"] == "mcp" for e in j["tools"])


SDKS = {
    "app/agents_app.py": '''
        from agents import Agent, function_tool
        from langchain_core.tools import BaseTool, StructuredTool, tool


        @function_tool(name_override="get_weather")
        def weather(city: str) -> str:
            return city


        @tool("search_docs")
        def search(q: str) -> str:
            """Search the docs."""
            return q


        class Calc(BaseTool):
            name: str = "calculator"

            def _run(self, expr: str) -> str:
                return expr


        def add(a: int, b: int) -> int:
            return a + b


        adder = StructuredTool.from_function(func=add, name="adder")
        billing = Agent(name="Billing", tools=[weather, adder], model="gpt-4.1")
        triage = Agent(name="Triage", tools=[weather], handoffs=[billing])


        def chat(llm):
            return llm.bind_tools([search, Calc(), adder])
        ''',
    "app/lowlevel.py": '''
        from mcp.server import Server

        server = Server("lowlevel")


        def echo(arguments):
            return arguments


        def ping():
            return "pong"


        @server.call_tool()
        async def call_tool(name: str, arguments: dict):
            if name == "echo":
                return echo(arguments)
            match name:
                case "ping":
                    return ping()
        ''',
    "app/__init__.py": "",
}


def test_sdk_declarations_agents_and_lowlevel_mcp(tmp_path):
    for p, src in SDKS.items():
        (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / p).write_text(textwrap.dedent(src))
    db = tmp_path / "g.db"
    index_project(tmp_path, db, "sdk")
    st = GraphStore(db)
    rb = edges(st, "RECEIVED_BY")
    for ep, fn in (("llm_tool:get_weather", "function:app.agents_app.weather"),
                   ("llm_tool:search_docs", "function:app.agents_app.search"),
                   ("llm_tool:calculator", "method:app.agents_app.Calc._run"),
                   ("llm_tool:adder", "function:app.agents_app.add"),
                   ("mcp_tool:lowlevel/echo", "function:app.lowlevel.echo"),
                   ("mcp_tool:lowlevel/ping", "function:app.lowlevel.ping")):
        assert (f"endpoint:{ep}", fn) in rb, ep
    offers = edges(st, "OFFERS_TOOL")
    assert ("agent:Billing", "endpoint:llm_tool:get_weather") in offers and ("agent:Billing", "endpoint:llm_tool:adder") in offers
    assert ("agent:Triage", "agent:Billing") in edges(st, "HANDS_OFF_TO")
    assert json.loads(st.node("agent:Billing")["attrs"])["model"] == "gpt-4.1"
    sends = edges(st, "SENDS_TO")
    for t in ("search_docs", "calculator", "adder"):                                         # bind_tools offers
        assert ("function:app.agents_app.chat", f"endpoint:llm_tool:{t}") in sends
    r = cli("tools", "--db", str(db), "--agent", "Billing")
    assert "get_weather" in r.stdout and "adder" in r.stdout and "search_docs" not in r.stdout.split("agents:")[0]


def test_plain_python_untouched(tmp_path):
    (tmp_path / "m.py").write_text("TOOLS = {'a': len}\n\ndef run(name):\n    if name == 'x':\n        return run('y')\n")
    db = tmp_path / "g.db"
    index_project(tmp_path, db, "plain")
    st = GraphStore(db)
    assert not list(st.q("SELECT 1 FROM nodes WHERE kind IN ('endpoint', 'agent')"))
    assert "no LLM tools" in cli("tools", "--db", str(db)).stdout


def test_own_mcp_server_tools_are_endpoints(tmp_path):
    db = tmp_path / "self.db"
    index_project(ROOT / "cg_code_graph", db, "codegraph")
    st = GraphStore(db)
    eps = {r["id"] for r in st.q("SELECT id FROM nodes WHERE id LIKE 'endpoint:mcp_tool:code-graph/%'")}
    assert {"endpoint:mcp_tool:code-graph/impact", "endpoint:mcp_tool:code-graph/llm_tools",
            "endpoint:mcp_tool:code-graph/protocol_links"} <= eps
    assert not list(st.q("SELECT 1 FROM edges e JOIN edges r ON r.dst = e.dst WHERE e.kind='RECEIVED_BY' "
                         "AND e.src LIKE 'endpoint:mcp_tool:%' AND r.kind='REFERENCES_FN' AND r.attrs LIKE '%decorator%'"))


def test_mcp_llm_tools(shop, monkeypatch):
    from cg_code_graph import mcp_server
    monkeypatch.setattr(mcp_server, "_st", lambda: GraphStore(shop["db"]))
    out = mcp_server.llm_tools(unmatched=True)
    out = out if isinstance(out, str) else str(out)
    assert "escalate" in out and "no_receiver" in out


def test_mcp_tools_declared_inside_functions(tmp_path):
    """#76: a server built in a factory function and tools defined in a test body: the enclosing function receives."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "factory.py").write_text(textwrap.dedent('''
        from mcp.server.fastmcp import FastMCP


        def build():
            mcp = FastMCP("notes")

            @mcp.tool()
            def add_note(text: str) -> str:
                """Store a note."""
                return text

            @mcp.resource("notes://{id}")
            def note(id: str) -> str:
                return id

            @mcp.tool(name="drop")
            async def remove(id: str):
                return id
            return mcp


        def unrelated():
            def helper():
                return 1
            return helper
        '''))
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_srv.py").write_text(textwrap.dedent('''
        from mcp.server.fastmcp import FastMCP


        def test_tool_roundtrip():
            server = FastMCP("t")

            @server.tool()
            def echo(x: str) -> str:
                return x
            assert echo("a") == "a"
        '''))
    db = tmp_path / "g.db"
    index_project(tmp_path, db, "nested")
    st = GraphStore(db)
    rb = edges(st, "RECEIVED_BY")
    for ep in ("mcp_tool:notes/add_note", "mcp_resource:notes/notes://{id}", "mcp_tool:notes/drop"):
        assert (f"endpoint:{ep}", "function:app.factory.build") in rb, ep
    assert ("endpoint:mcp_tool:t/echo", "function:tests.test_srv.test_tool_roundtrip") in edges(st, "TEST_CALLS")
    eps = {r[0] for r in st.q("SELECT id FROM nodes WHERE kind='endpoint'")}
    assert eps == {"endpoint:mcp_tool:notes/add_note", "endpoint:mcp_resource:notes/notes://{id}",
                   "endpoint:mcp_tool:notes/drop", "endpoint:mcp_tool:t/echo"}           # no owner-named duplicates
    a = json.loads(st.node("endpoint:mcp_tool:notes/add_note")["attrs"])
    assert a["handler_name"] == "add_note" and a["nested_in"] == "build" and a["params"] == ["text"]
    assert a["description"] == "Store a note."
    assert not [e for e in rb if "unrelated" in e[1]]


def test_lowlevel_server_enum_branches(tmp_path):
    """#76: `match name: case GitTools.STATUS:` with `class GitTools(str, Enum)` (reference servers)."""
    (tmp_path / "srv").mkdir()
    (tmp_path / "srv" / "__init__.py").write_text("")
    (tmp_path / "srv" / "names.py").write_text(textwrap.dedent('''
        from enum import Enum


        class GitTools(str, Enum):
            STATUS = "git_status"
            DIFF = "git_diff"
        '''))
    (tmp_path / "srv" / "server.py").write_text(textwrap.dedent('''
        from enum import Enum
        from mcp.server import Server
        from srv.names import GitTools

        server = Server("mcp-git")


        class Local(Enum):
            LOG = "git_log"


        def git_status(repo):
            return repo


        def git_log(repo):
            return repo


        @server.call_tool()
        async def call_tool(name: str, arguments: dict):
            match name:
                case GitTools.STATUS:
                    return git_status(arguments)
                case Local.LOG:
                    return git_log(arguments)
            if name == GitTools.DIFF:
                return None
        '''))
    db = tmp_path / "g.db"
    index_project(tmp_path, db, "git")
    st = GraphStore(db)
    rb = edges(st, "RECEIVED_BY")
    assert ("endpoint:mcp_tool:mcp-git/git_status", "function:srv.server.git_status") in rb
    assert ("endpoint:mcp_tool:mcp-git/git_log", "function:srv.server.git_log") in rb
    assert ("endpoint:mcp_tool:mcp-git/git_diff", "function:srv.server.call_tool") in rb


def test_lowlevel_server_built_inside_function(tmp_path):
    """#76: mcp-server-git shape: `async def serve(): server = Server(...)` with a nested `@server.call_tool()`."""
    (tmp_path / "git_srv.py").write_text(textwrap.dedent('''
        from enum import Enum
        from mcp.server import Server


        class GitTools(str, Enum):
            STATUS = "git_status"


        def git_status(repo):
            return repo


        async def serve(repository):
            server = Server("mcp-git")

            @server.call_tool()
            async def call_tool(name: str, arguments: dict):
                match name:
                    case GitTools.STATUS:
                        return git_status(arguments)
            return server
        '''))
    db = tmp_path / "g.db"
    index_project(tmp_path, db, "git")
    assert ("endpoint:mcp_tool:mcp-git/git_status", "function:git_srv.git_status") in edges(GraphStore(db), "RECEIVED_BY")


def test_lowlevel_server_enum_value_and_single_tool(tmp_path):
    """#102: mcp-server-time (`case TimeTools.X.value:`) and mcp-server-fetch (one tool, no branch on the name)."""
    (tmp_path / "time_srv.py").write_text(textwrap.dedent('''
        from enum import Enum
        from mcp.server import Server
        from mcp.types import Tool


        class TimeTools(str, Enum):
            NOW = "get_current_time"


        class TimeServer:
            def now(self, tz):
                return tz


        async def serve():
            server = Server("mcp-time")
            time_server = TimeServer()

            @server.call_tool()
            async def call_tool(name: str, arguments: dict):
                match name:
                    case TimeTools.NOW.value:
                        return time_server.now(arguments)
        '''))
    (tmp_path / "fetch_srv.py").write_text(textwrap.dedent('''
        from mcp.server import Server
        from mcp.types import Tool


        async def serve():
            server = Server("mcp-fetch")

            @server.list_tools()
            async def list_tools():
                return [Tool(name="fetch", description="d", inputSchema={})]

            @server.call_tool()
            async def call_tool(name, arguments: dict):
                return arguments
        '''))
    db = tmp_path / "g.db"
    index_project(tmp_path, db, "srv")
    e = edges(GraphStore(db), "RECEIVED_BY")
    assert ("endpoint:mcp_tool:mcp-time/get_current_time", "method:time_srv.TimeServer.now") in e
    assert ("endpoint:mcp_tool:mcp-fetch/fetch", "function:fetch_srv.serve") in e


@pytest.mark.skipif(not (ROOT / "cg_code_graph" / "plugins" / "ts" / "extractor" / "node_modules").exists(),
                    reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")
def test_typescript_mcp_server_registrations(tmp_path):
    # #102: @modelcontextprotocol/sdk McpServer registrations -> endpoint:mcp_<kind>:<server>/<name> RECEIVED_BY the
    # handler (inline arrow = its own node, a function reference); a register helper taking `server: McpServer` uses
    # the one server of its package (resolved); a runtime uri (`registerResource(name, uri, ..)`) is skipped
    (tmp_path / "package.json").write_text('{"name": "srv", "dependencies": {"@modelcontextprotocol/sdk": "1.20.0"}}\n')
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {"strict": true}, "include": ["src"]}\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(textwrap.dedent("""\
        import { McpServer, ResourceTemplate } from "@modelcontextprotocol/sdk/server/mcp.js";
        import { registerEcho } from "./echo";

        const server = new McpServer({ name: "notes-server", version: "1.0.0" });

        const readNote = async (args: { id: string }) => ({ content: [{ type: "text" as const, text: args.id }] });

        server.registerTool("read_note", { description: "Read" }, readNote);
        server.registerTool("delete_note", { description: "Delete" }, async (args: { id: string }) => {
          return { content: [{ type: "text" as const, text: "deleted " + args.id }] };
        });
        server.registerResource("note", new ResourceTemplate("notes://{id}", { list: undefined }), {}, async (uri: URL) => ({
          contents: [{ uri: uri.href, text: "x" }],
        }));
        server.registerPrompt("summarize", { description: "Sum" }, () => ({ messages: [] }));
        registerEcho(server);
        """))
    (tmp_path / "src" / "echo.ts").write_text(textwrap.dedent("""\
        import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

        const name = "echo";
        export const registerEcho = (server: McpServer) => {
          server.registerTool(name, { description: "Echo" }, async (args: { message: string }) => ({
            content: [{ type: "text" as const, text: args.message }],
          }));
          const uri = "file://" + Math.random();
          server.registerResource("dyn", uri, {}, async () => ({ contents: [] }));
        };
        """))
    db = tmp_path / "g.db"
    index_project(tmp_path, db, "srv")
    st = GraphStore(str(db))
    got = {(r["src"], r["dst"], r["confidence"]) for r in st.q("SELECT src, dst, confidence FROM edges WHERE kind='RECEIVED_BY'")
           if r["src"].startswith("endpoint:mcp_")}
    assert got == {
        ("endpoint:mcp_tool:notes-server/read_note", "function:src/index.ts#readNote", "exact"),
        ("endpoint:mcp_tool:notes-server/delete_note", "function:src/index.ts#server.registerTool('delete_note')", "exact"),
        ("endpoint:mcp_resource:notes-server/notes://{id}", "function:src/index.ts#server.registerResource('note')", "exact"),
        ("endpoint:mcp_prompt:notes-server/summarize", "function:src/index.ts#server.registerPrompt('summarize')", "exact"),
        ("endpoint:mcp_tool:notes-server/echo", "function:src/echo.ts#registerEcho.server.registerTool(name)", "resolved"),
    }
