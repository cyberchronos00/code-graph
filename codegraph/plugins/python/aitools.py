"""LLM tools, MCP servers / clients and agents (#66) -> the #31 endpoint model (codegraph/protocols).

Only modules importing an AI SDK take part (mcp, fastmcp, openai, agents, anthropic, langchain*, langgraph,
llama_index); other code is untouched.

  endpoint:mcp_tool:<server>/<name>, mcp_resource:<server>/<uri>, mcp_prompt:<server>/<name>
      receive  FastMCP / MCPServer `@mcp.tool()` / `@mcp.resource("notes://{id}")` / `@mcp.prompt()`, `mcp.tool()(fn)`,
               `add_tool(fn)` and local decorators that register their argument (the registrations refs.py records);
               low-level `@server.call_tool()` handlers branching on `if name == "x"` (resolved)
      send     clients: `session.call_tool("x")`, `read_resource("uri")`, `get_prompt("x")` -> `*/<name>` (any server;
               MATCHES_ENDPOINT pairs it with every server tool of that name)
  endpoint:llm_tool:<name>
      receive  LangChain `@tool` / `@tool("name")`, `StructuredTool.from_function` / `Tool(name=, func=)`, BaseTool
               subclasses (`name` class attribute -> `_run` / `_arun`), Agents SDK `@function_tool` (name_override),
               Anthropic `@beta_tool`, LlamaIndex `FunctionTool.from_defaults(fn=, name=)` (exact); module dict
               registries `TOOLS = {"x": fn}` / `{f.__name__: f for f in [a, b]}` and `if name == "x":` / `match name:`
               branches in an agent loop (resolved)
      send     schema literals offered to the model (role offer): OpenAI `{"type": "function", "function": {"name"}}`,
               Responses `{"type": "function", "name"}`, Anthropic `{"name", "input_schema"}`; from the function holding
               them, or from each function passing the module constant as `tools=`; `bind_tools([...])`,
               `create_react_agent(model, tools=[...])`, `AgentExecutor(tools=[...])`, `ToolNode([...])`
  agent:<name>   Agents SDK `Agent(name=..., tools=[...], handoffs=[...])` and agent constructors assigned to a module
                 name; OFFERS_TOOL -> its tools, HANDS_OFF_TO -> its handoffs; attrs.model
  findings       an agent loop dispatching by a runtime name (`globals()[name]`, `getattr(obj, name)`, `eval`) records
                 attrs.llm_dynamic_dispatch on the function instead of a guessed link
  model config   literal `model=` / `base_url=` of chat / responses / messages calls and model constructors ->
                 attrs.llm_calls on the calling function (facts for the external-system nodes of #40)
"""
from __future__ import annotations

import ast
import re

from ...core.model import EXACT, HEURISTIC, RESOLVED
from ...protocols import protocol_receive, protocol_send

LIBS = ("mcp", "fastmcp", "openai", "agents", "anthropic", "langchain", "langchain_core", "langchain_community",
        "langchain_openai", "langchain_anthropic", "langgraph", "llama_index", "langchain_mcp_adapters")
MCP_SERVERS = ("FastMCP", "MCPServer", "Server")
TOOL_DECOS = {"tool": None, "function_tool": "openai-agents", "beta_tool": "anthropic", "beta_async_tool": "anthropic"}
TOOL_CTORS = {"from_function", "from_defaults", "Tool", "StructuredTool", "FunctionTool"}
OFFER_CALLS = {"Agent", "bind_tools", "create_react_agent", "create_agent", "AgentExecutor", "ToolNode", "create_tool_calling_agent",
               "create_openai_tools_agent", "ReActAgent", "FunctionAgent", "OpenAIAgent", "AgentWorkflow"}
AGENT_CTORS = {"Agent", "create_react_agent", "create_agent", "AgentExecutor", "ReActAgent", "FunctionAgent", "OpenAIAgent"}
BASE_TOOLS = ("langchain_core.tools.BaseTool", "langchain_core.tools.base.BaseTool", "langchain.tools.BaseTool",
              "langchain.tools.base.BaseTool", "langchain_community.tools.BaseTool")
CLIENT_CALLS = {"call_tool": "mcp_tool", "read_resource": "mcp_resource", "get_prompt": "mcp_prompt"}
NAME_VARS = {"name", "tool_name", "function_name", "fn_name", "func_name", "tool"}
LOOP_MARKS = {"tool_calls", "tool_call", "tool_use", "function_call", "tool_name", "tool_uses"}
REGISTRY_NAME = re.compile(r"tool|function|handler|dispatch|action", re.I)
MODEL_CALLS = {("completions", "create"): ("openai", "chat"), ("responses", "create"): ("openai", "responses"),
               ("messages", "create"): ("anthropic", "messages"), ("messages", "stream"): ("anthropic", "messages"),
               ("embeddings", "create"): ("openai", "embeddings")}
MODEL_CTORS = {"ChatOpenAI": "openai", "AzureChatOpenAI": "azure-openai", "ChatAnthropic": "anthropic", "OpenAI": "openai",
               "AsyncOpenAI": "openai", "AzureOpenAI": "azure-openai", "Anthropic": "anthropic", "AsyncAnthropic": "anthropic",
               "init_chat_model": None, "ChatOllama": "ollama", "OpenAIChatCompletionsModel": "openai"}


def _s(e):
    return e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else None


def _kw(call, *names):
    for k in call.keywords:
        if k.arg in names:
            return k.value
    return None


def _last(e):
    if isinstance(e, ast.Subscript):          # Agent[Context](...)
        e = e.value
    return e.attr if isinstance(e, ast.Attribute) else e.id if isinstance(e, ast.Name) else None


def _head(m, e):
    """Top-level package a name / attribute chain is imported from."""
    while isinstance(e, (ast.Attribute, ast.Subscript)):
        e = e.value
    if isinstance(e, ast.Call):
        return _head(m, e.func)
    if not isinstance(e, ast.Name):
        return None
    imp = m.imports.get(e.id)
    if not imp or not isinstance(imp, tuple) or len(imp) < 2 or not isinstance(imp[1], str):
        return None
    return imp[1].split(".")[0]


def _framework(head):
    return {"agents": "openai-agents", "llama_index": "llamaindex", "langgraph": "langgraph", "openai": "openai",
            "anthropic": "anthropic", "mcp": "mcp", "fastmcp": "mcp"}.get(head, "langchain" if head and head.startswith("langchain") else head)


def _first_line(doc):
    return (doc or "").strip().splitlines()[0][:160] if (doc or "").strip() else None


def _params(fnode):
    a = fnode.args
    return [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs if x.arg not in ("self", "cls", "ctx", "context")]


def _is_test(path):
    p = path.replace("\\", "/")
    return any(s.startswith("test") for s in p.split("/")[:-1]) or p.rsplit("/", 1)[-1].startswith("test_") or p.endswith("_test.py")


def index(prog, b, walk_body, Ctx) -> dict:
    mods = [m for m in prog.modules.values() if any(
        isinstance(v, tuple) and len(v) > 1 and isinstance(v[1], str) and v[1].split(".")[0] in LIBS for v in m.imports.values())]
    ai_mods = {m.name for m in mods}
    st = {"mcp": {"tool": 0, "resource": 0, "prompt": 0, "client_calls": 0}, "llm_tools": 0, "offers": 0, "agents": 0,
          "dynamic_dispatch": 0, "model_calls": 0}
    regd = [n for n in b.nodes.values() if n.kind in ("function", "method") and any(
        r.get("framework") == "mcp" for r in ((n.attrs or {}).get("registrations") or []))]
    if not mods and not regd:
        return {}
    by_file = {m.file: m for m in prog.modules.values()}
    fn_by_id = {f.id: f for f in prog.funcs.values()}

    # ---- MCP server objects: (module, var) -> server name
    servers: dict = {}
    for m in prog.modules.values():
        for name, vals in m.vars.items():
            for v, _line, _a in vals:
                if isinstance(v, ast.Call) and _last(v.func) in MCP_SERVERS and _head(m, v.func) in ("mcp", "fastmcp"):
                    sn = _s(v.args[0]) if v.args else None
                    servers[(m.name, name)] = sn or _s(_kw(v, "name")) or m.name

    def server_of(m, var):
        if (m.name, var) in servers:
            return servers[(m.name, var)]
        imp = m.imports.get(var)
        if imp and imp[0] == "sym" and (imp[1], imp[2]) in servers:
            return servers[(imp[1], imp[2])]
        return None

    tool_of_func: dict = {}      # FuncInfo.id -> endpoint id
    tool_of_var: dict = {}       # (module, var) -> endpoint id
    tool_of_class: dict = {}     # ClassInfo.id -> endpoint id
    handlers: set = set()

    def recv(protocol, name, f, file, line, conf, framework, **kw):
        if hasattr(f, "id") and f.id not in b.nodes:     # a nested def collapses into its owner: no handler node
            return None
        nid = protocol_receive(b, protocol, name, f.id if hasattr(f, "id") else f, file, line, conf, framework=framework,
                               node_attrs={"framework": framework, "declared_in": file, **kw})
        if hasattr(f, "id"):
            handlers.add(f.id)
        return nid

    # ---- 1. MCP server registrations (refs.py) -> endpoints
    for n in regd:
        f = fn_by_id.get(n.id)
        if f is None:
            continue
        for r in n.attrs["registrations"]:
            if r.get("framework") != "mcp":
                continue
            kind = r.get("registration") or "tool"
            txt = r.get("decorator") or r.get("call") or ""
            try:
                e = ast.parse(txt, mode="eval").body
            except SyntaxError:
                e = None
            call = e if isinstance(e, ast.Call) else None
            if call is not None and isinstance(call.func, ast.Call):          # mcp.tool()(fn)
                call = call.func
            var = None
            if call is not None and isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
                var = call.func.value.id
            elif isinstance(e, ast.Attribute) and isinstance(e.value, ast.Name):   # @mcp.tool without call
                var, call = e.value.id, None
            at_file = (r.get("at") or "").rsplit(":", 1)[0]
            m = by_file.get(at_file) or f.module
            srv = (server_of(m, var) if var else None) or m.name
            pos = [a for a in (call.args if call is not None else [])]
            if kind == "resource":
                nm = (_s(pos[0]) if pos else None) or _s(_kw(call, "uri")) if call is not None else None
            else:
                nm = (_s(_kw(call, "name")) if call is not None else None) or (_s(pos[0]) if pos and kind != "resource" else None)
            if kind == "resource" and not nm:
                continue
            nm = nm or f.name
            line = f.line if r.get("registered_by") else int((r.get("at") or ":0").rsplit(":", 1)[-1] or 0) or f.line
            ok = recv(f"mcp_{kind}", f"{srv}/{nm}", f, f.file if r.get("registered_by") else at_file or f.file, line, EXACT if not r.get("registered_by") else RESOLVED,
                 "mcp", server=srv, description=_first_line(ast.get_docstring(f.node)), params=_params(f.node),
                 schema_source="decorator", toolset=srv)
            st["mcp"][kind if kind in st["mcp"] else "tool"] += bool(ok)
    # decorators on a server object the registration pass could not type (the SDK's own examples: MCPServer is a
    # local class there)
    for f in prog.funcs.values():
        if f.id in handlers:
            continue
        for d in f.decorators:
            de = d.func if isinstance(d, ast.Call) else d
            if not (isinstance(de, ast.Attribute) and de.attr in ("tool", "resource", "prompt") and isinstance(de.value, ast.Name)):
                continue
            srv = server_of(f.module, de.value.id)
            if srv is None:
                continue
            call = d if isinstance(d, ast.Call) else None
            pos = call.args if call is not None else []
            if de.attr == "resource":
                nm = ((_s(pos[0]) if pos else None) or _s(_kw(call, "uri"))) if call is not None else None
                if not nm:
                    continue
            else:
                nm = ((_s(_kw(call, "name")) or (_s(pos[0]) if pos else None)) if call is not None else None) or f.name
            ok = recv(f"mcp_{de.attr}", f"{srv}/{nm}", f, f.file, d.lineno, EXACT, "mcp", server=srv, toolset=srv,
                 description=_first_line(ast.get_docstring(f.node)), params=_params(f.node), schema_source="decorator")
            st["mcp"][de.attr] += bool(ok)
    # the registering decorator's reference to each handler was the only link before: the endpoint replaces it
    drop = [k for k, e in b.edges.items() if e.kind == "REFERENCES_FN" and e.dst in handlers
            and (e.attrs or {}).get("how") == "decorator"]
    for k in drop:
        del b.edges[k]
    if not mods:
        return st

    def scopes():
        for f in prog.funcs.values():
            if f.module.name in ai_mods:
                yield f.module, f, walk_body(f.node)
        for m in mods:
            yield m, None, (s for st_ in m.tree.body if not isinstance(st_, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                            for s in ast.walk(st_))

    # ---- 2. declared tools (decorators, constructors, BaseTool classes)
    for f in prog.funcs.values():
        m = f.module
        if m.name not in ai_mods:
            continue
        for d in f.decorators:
            de = d.func if isinstance(d, ast.Call) else d
            last, head = _last(de), _head(m, de)
            if last not in TOOL_DECOS or head not in LIBS or head in ("mcp", "fastmcp"):
                continue
            fw = TOOL_DECOS[last] or _framework(head)
            nm = None
            if isinstance(d, ast.Call):
                nm = (_s(d.args[0]) if d.args else None) or _s(_kw(d, "name_override", "name", "name_or_callable"))
            nid = recv("llm_tool", nm or f.name, f, f.file, d.lineno, EXACT, fw, description=_first_line(ast.get_docstring(f.node)),
                       params=_params(f.node), schema_source="decorator")
            if nid:
                tool_of_func[f.id] = nid
                st["llm_tools"] += 1
    for m, f, nodes in scopes():
        ctx = Ctx(m, f, f.cls if f else None)
        for sub in nodes:
            if isinstance(sub, (ast.Assign, ast.AnnAssign)) and isinstance(sub.value, ast.Call):
                c = sub.value
                if _last(c.func) in TOOL_CTORS and _head(m, c.func) in LIBS and _head(m, c.func) not in ("mcp", "fastmcp"):
                    fnx = _kw(c, "func", "fn", "coroutine", "async_fn") or (c.args[0] if c.args and _last(c.func) in ("from_function", "from_defaults") else None)
                    t = prog.infer(fnx, ctx) if fnx is not None else None
                    h = t[1] if t and t[0] in ("func", "bound") else None
                    nm = _s(_kw(c, "name")) or (h.name if h else None)
                    if not nm:
                        continue
                    nid = recv("llm_tool", nm, h or (f.id if f else m.id), m.file, c.lineno, EXACT if h else RESOLVED,
                               _framework(_head(m, c.func)), description=_s(_kw(c, "description")),
                               schema_source="constructor")
                    if not nid:
                        continue
                    if h:
                        tool_of_func.setdefault(h.id, nid)
                    tgts = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                    for tg in tgts:
                        if isinstance(tg, ast.Name) and f is None:
                            tool_of_var[(m.name, tg.id)] = nid
                    st["llm_tools"] += 1
    for c in prog.classes.values():
        if c.module.name not in ai_mods or not prog.subclass_of(c, *BASE_TOOLS):
            continue
        nm, conf = None, EXACT
        for s in c.node.body:
            if isinstance(s, (ast.Assign, ast.AnnAssign)):
                tg = s.targets[0] if isinstance(s, ast.Assign) else s.target
                if isinstance(tg, ast.Name) and tg.id == "name":
                    nm = _s(s.value)
        if not nm:
            nm, conf = c.name, HEURISTIC
        for mn in ("_run", "_arun"):
            f = c.methods.get(mn)
            if f is not None:
                nid = recv("llm_tool", nm, f, f.file, f.line, conf, "langchain", schema_source="class attrs",
                                           params=_params(f.node))
                if nid:
                    tool_of_class[c.id] = nid
                    st["llm_tools"] += 1

    # ---- 3. dict registries and agent-loop branches
    registries: dict = {}            # (module, var) -> [endpoint ids]
    for m in mods:
        for name, vals in m.vars.items():
            if not REGISTRY_NAME.search(name):
                continue
            for v, line, _a in vals:
                pairs = []
                if isinstance(v, ast.Dict) and v.keys and all(_s(k) for k in v.keys):
                    pairs = [(_s(k), x) for k, x in zip(v.keys, v.values)]
                elif isinstance(v, ast.DictComp) and isinstance(v.key, ast.Attribute) and v.key.attr == "__name__" \
                        and v.generators and isinstance(v.generators[0].iter, (ast.List, ast.Tuple)):
                    pairs = [(None, x) for x in v.generators[0].iter.elts]
                ids = []
                for k, x in pairs:
                    t = prog.infer(x, Ctx(m, None, None))
                    h = t[1] if t and t[0] in ("func", "bound") else None
                    if h is None:
                        continue
                    nid = recv("llm_tool", k or h.name, h, m.file, line, RESOLVED, "custom", via="dict registry",
                               schema_source="dict registry", toolset=f"{m.name}.{name}", params=_params(h.node))
                    if not nid:
                        continue
                    ids.append(nid)
                    tool_of_func.setdefault(h.id, nid)
                    st["llm_tools"] += 1
                if ids:
                    registries[(m.name, name)] = ids

    def loop_like(f):
        for d in f.decorators:
            de = d.func if isinstance(d, ast.Call) else d
            if _last(de) == "call_tool":
                return "mcp"
        for sub in walk_body(f.node):
            if isinstance(sub, ast.Attribute) and sub.attr in LOOP_MARKS:
                return "llm"
            if isinstance(sub, ast.Constant) and sub.value in ("tool_use", "function_call", "tool_calls"):
                return "llm"
        if LOOP_MARKS & set(_params(f.node)) or ("tool" in f.name.lower() and NAME_VARS & set(_params(f.node))):
            return "llm"
        return None

    def name_expr(e):
        return (isinstance(e, ast.Name) and e.id in NAME_VARS) or (isinstance(e, ast.Attribute) and e.attr == "name")

    def lowlevel_server(f):
        for d in f.decorators:
            de = d.func if isinstance(d, ast.Call) else d
            if _last(de) == "call_tool" and isinstance(de, ast.Attribute) and isinstance(de.value, ast.Name):
                return server_of(f.module, de.value.id) or f.module.name
        return None

    for f in list(prog.funcs.values()):
        if f.module.name not in ai_mods:
            continue
        kind = loop_like(f)
        if not kind:
            continue
        ctx = Ctx(f.module, f, f.cls)
        srv = lowlevel_server(f) if kind == "mcp" else None
        branches = []
        for sub in walk_body(f.node):
            if isinstance(sub, ast.If) and isinstance(sub.test, ast.Compare) and len(sub.test.ops) == 1 \
                    and isinstance(sub.test.ops[0], ast.Eq):
                l, r = sub.test.left, sub.test.comparators[0]
                lit = _s(r) if name_expr(l) else _s(l) if name_expr(r) else None
                if lit:
                    branches.append((lit, sub.body, sub.lineno, "if"))
            elif isinstance(sub, ast.Match) and name_expr(sub.subject):
                for case in sub.cases:
                    p = case.pattern
                    if isinstance(p, ast.MatchValue) and _s(p.value):
                        branches.append((_s(p.value), case.body, p.lineno, "match"))
            elif isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Call) and _last(sub.value.func) in ("globals", "locals"):
                _dynamic(b, f, sub, st)
            elif isinstance(sub, ast.Call) and _last(sub.func) == "getattr" and len(sub.args) >= 2 and _dyn_name(sub.args[1]):
                _dynamic(b, f, sub, st)
            elif isinstance(sub, ast.Call) and _last(sub.func) == "eval":
                _dynamic(b, f, sub, st)
        for lit, body, line, how in branches:
            h = None
            for s in body:
                for c in ast.walk(s):
                    if isinstance(c, ast.Call):
                        for tgt, _conf, _via in prog.resolve_call(c, ctx):
                            if hasattr(tgt, "node") and getattr(tgt, "kind", None) in ("function", "method"):
                                h = tgt
                                break
                    if h:
                        break
                if h:
                    break
            if srv:
                recv("mcp_tool", f"{srv}/{lit}", h or f, f.file, line, RESOLVED, "mcp", server=srv, via=f"{how} name",
                     schema_source="low-level server", toolset=srv)
                st["mcp"]["tool"] += 1
            else:
                nid = recv("llm_tool", lit, h or f, f.file, line, RESOLVED, "custom", via=f"{how} name",
                           schema_source="agent loop")
                if h and nid:
                    tool_of_func.setdefault(h.id, nid)
                st["llm_tools"] += bool(nid)

    # ---- 4. senders: schema literals, offers, MCP client calls; agents; model config
    schema_vars: dict = {}           # (module, var) -> [(name, framework, line)]

    def schema(d):
        if not isinstance(d, ast.Dict):
            return None
        kv = {_s(k): v for k, v in zip(d.keys, d.values) if _s(k)}
        if _s(kv.get("type")) == "function" and isinstance(kv.get("function"), ast.Dict):
            inner = {_s(k): v for k, v in zip(kv["function"].keys, kv["function"].values) if _s(k)}
            return (_s(inner.get("name")), "openai") if _s(inner.get("name")) else None
        if _s(kv.get("type")) == "function" and _s(kv.get("name")) and ("parameters" in kv or "description" in kv):
            return _s(kv["name"]), "openai"
        if _s(kv.get("name")) and "input_schema" in kv:
            return _s(kv["name"]), "anthropic"
        return None

    for m in mods:
        for name, vals in m.vars.items():
            for v, line, _a in vals:
                for sub in ast.walk(v):
                    sc = schema(sub)
                    if sc:
                        schema_vars.setdefault((m.name, name), []).append((*sc, sub.lineno))

    def tools_in(e, ctx, depth=0):
        """Endpoint ids of the tools a `tools=[...]` value lists."""
        out = []
        if e is None or depth > 3:
            return out
        if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
            for x in e.elts:
                out += tools_in(x, ctx, depth + 1)
            return out
        if isinstance(e, ast.Starred):
            return tools_in(e.value, ctx, depth + 1)
        if isinstance(e, ast.Call) and _last(e.func) not in ("list",):
            t = prog.infer(e.func, ctx)
            if t and t[0] == "type" and t[1].id in tool_of_class:
                return [tool_of_class[t[1].id]]
            return out
        t = prog.infer(e, ctx)
        if t and t[0] in ("func", "bound") and t[1].id in tool_of_func:
            return [tool_of_func[t[1].id]]
        if isinstance(e, ast.Name):
            r = prog.resolve_name(ctx.mod, e.id)
            if ctx.func is not None and e.id in prog.local_vars(ctx):
                for lv in _local_values(ctx.func.node, e.id):
                    out += tools_in(lv, ctx, depth + 1)
                return out
            if r and r[0] == "var":
                key = (r[1].name, r[2])
                if key in tool_of_var:
                    return [tool_of_var[key]]
                for v, _l, _a in r[1].vars.get(r[2], []):
                    out += tools_in(v, Ctx(r[1], None, None), depth + 1)
        return out

    def offer(m, f, ids, file, line, via, fw):
        for nid in ids:
            src = f.id if f else m.id
            b.add_edge(src, nid, "SENDS_TO", file, line, RESOLVED, role="offer", via=via, framework=fw)
            st["offers"] += 1

    agent_vars: dict = {}
    for m, f, nodes in scopes():
        ctx = Ctx(m, f, f.cls if f else None)
        test = _is_test(m.file)
        assigned = {}
        for sub in nodes:
            if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Call) and len(sub.targets) == 1 \
                    and isinstance(sub.targets[0], ast.Name):
                assigned[id(sub.value)] = sub.targets[0].id
        for sub in (nodes if isinstance(nodes, list) else list(_rewalk(m, f, walk_body))):
            if f is not None:
                sc = schema(sub)
                if sc:
                    protocol_send(b, "llm_tool", sc[0], f.id, m.file, sub.lineno, EXACT, test=test, role="offer",
                                  framework=sc[1], schema_source="schema literal",
                                  node_attrs={"framework": sc[1], "schema_source": "schema literal"})
                    st["offers"] += 1
            if not isinstance(sub, ast.Call):
                continue
            last = _last(sub.func)
            head = _head(m, sub.func)
            tv = _kw(sub, "tools")
            if f is not None and isinstance(tv, ast.Name):
                r = prog.resolve_name(m, tv.id) if tv.id not in prog.local_vars(ctx) else None
                if r and r[0] == "var" and (r[1].name, r[2]) in schema_vars:
                    for nm, fw, _l in schema_vars[(r[1].name, r[2])]:
                        protocol_send(b, "llm_tool", nm, f.id, m.file, sub.lineno, RESOLVED, test=test, role="offer",
                                      framework=fw, schema_source="schema literal", via=f"tools={tv.id}",
                                      node_attrs={"framework": fw, "schema_source": "schema literal"})
                        st["offers"] += 1
            if last in CLIENT_CALLS and head is None and m.name in ai_mods:
                nm = (_s(sub.args[0]) if sub.args else None) or _s(_kw(sub, "name", "uri"))
                if nm and f is not None:
                    protocol_send(b, CLIENT_CALLS[last], f"*/{nm}", f.id, m.file, sub.lineno, EXACT, test=test, role="invoke",
                                  framework="mcp")
                    st["mcp"]["client_calls"] += 1
                continue
            if last in OFFER_CALLS and (head in LIBS or (last == "bind_tools")):
                lst = tv if tv is not None else (sub.args[0] if last in ("bind_tools", "ToolNode") and sub.args else
                                                sub.args[1] if last in ("create_react_agent", "create_agent", "create_tool_calling_agent")
                                                and len(sub.args) > 1 else None)
                ids = tools_in(lst, ctx)
                fw = _framework(head) or "langchain"
                var = assigned.get(id(sub))
                if last in AGENT_CTORS and (_s(_kw(sub, "name")) or (var and f is None)):
                    aname = _s(_kw(sub, "name")) or f"{m.name}.{var}"
                    aid = b.add_node("agent", aname, aname.rsplit(".", 1)[-1], fqn=aname, file=m.file, line=sub.lineno,
                                     lang="python", attrs={"framework": fw, "model": _s(_kw(sub, "model")),
                                                           "tools": ids, "instructions_source": "literal" if _s(_kw(sub, "instructions")) else None})
                    for nid in ids:
                        b.add_edge(aid, nid, "OFFERS_TOOL", m.file, sub.lineno, RESOLVED)
                    offer(m, f, ids, m.file, sub.lineno, last, fw)       # constructing the agent hands the tools to the model
                    b.add_edge(f.id if f else m.id, aid, "CONTAINS" if f is None else "INSTANTIATES", m.file, sub.lineno, EXACT)
                    if var and f is None:
                        agent_vars[(m.name, var)] = (aid, sub, ctx)
                    st["agents"] += 1
                else:
                    offer(m, f, ids, m.file, sub.lineno, last, fw)
            if last in MODEL_CTORS and head in LIBS or (isinstance(sub.func, ast.Attribute) and isinstance(sub.func.value, ast.Attribute)
                                                          and (sub.func.value.attr, sub.func.attr) in MODEL_CALLS):
                mdl, base = _s(_kw(sub, "model", "model_name")), _s(_kw(sub, "base_url", "openai_api_base", "azure_endpoint"))
                if mdl or base:
                    prov, op = MODEL_CALLS.get((sub.func.value.attr, sub.func.attr), (None, None)) if isinstance(sub.func, ast.Attribute) \
                        and isinstance(sub.func.value, ast.Attribute) else (MODEL_CTORS.get(last), "client")
                    owner = b.nodes.get(f.id if f else m.id)
                    if owner is not None:
                        if owner.attrs is None:
                            owner.attrs = {}
                        rec = {k: v for k, v in {"provider": prov or _framework(head), "op": op, "model": mdl, "base_url": base,
                                                 "line": sub.lineno}.items() if v}
                        if rec not in owner.attrs.setdefault("llm_calls", []):
                            owner.attrs["llm_calls"].append(rec)
                            st["model_calls"] += 1
    for (mn, var), (aid, call, ctx) in agent_vars.items():
        for h in (_kw(call, "handoffs").elts if isinstance(_kw(call, "handoffs"), (ast.List, ast.Tuple)) else []):
            if isinstance(h, ast.Name):
                r = prog.resolve_name(ctx.mod, h.id)
                if r and r[0] == "var" and (r[1].name, r[2]) in agent_vars:
                    b.add_edge(aid, agent_vars[(r[1].name, r[2])][0], "HANDS_OFF_TO", ctx.mod.file, call.lineno, RESOLVED)
    # module-level schema constants nobody passes as tools= are offered by their module
    offered = {e.dst for e in b.edges.values() if e.kind in ("SENDS_TO", "TEST_CALLS") and e.dst.startswith("endpoint:llm_tool:")}
    for (mn, var), lst in schema_vars.items():
        m = prog.modules.get(mn)
        for nm, fw, line in lst:
            if f"endpoint:llm_tool:{nm}" not in offered and m is not None:
                protocol_send(b, "llm_tool", nm, m.id, m.file, line, EXACT, test=_is_test(m.file), role="offer", framework=fw,
                              schema_source="schema literal", node_attrs={"framework": fw})
                st["offers"] += 1
    return {k: v for k, v in st.items() if v} if any(st["mcp"].values()) or any(v for k, v in st.items() if k != "mcp") else {}


def _dyn_name(e) -> bool:
    """A runtime tool name: `name` / `tool_name` / ... or `<tool call>.name` (`tool_call.function.name`, `block.name`)."""
    if isinstance(e, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) and _dyn_name(v.value) for v in e.values)
    if isinstance(e, ast.Name):
        return e.id in NAME_VARS - {"tool"}
    if isinstance(e, ast.Attribute) and e.attr == "name":
        try:
            base = ast.unparse(e.value).lower()
        except Exception:  # noqa: BLE001
            return False
        return any(w in base for w in ("tool", "function", "call", "block", "item"))
    return False


def _rewalk(m, f, walk_body):
    if f is not None:
        return walk_body(f.node)
    return (s for st_ in m.tree.body if not isinstance(st_, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for s in ast.walk(st_))


def _local_values(fnode, name):
    for sub in ast.walk(fnode):
        if isinstance(sub, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in sub.targets):
            yield sub.value


def _dynamic(b, f, sub, st):
    n = b.nodes.get(f.id)
    if n is None:
        return
    if n.attrs is None:
        n.attrs = {}
    try:
        expr = ast.unparse(sub)[:100]
    except Exception:  # noqa: BLE001
        expr = "?"
    rec = {"line": sub.lineno, "expr": expr}
    if rec not in n.attrs.setdefault("llm_dynamic_dispatch", []):
        n.attrs["llm_dynamic_dispatch"].append(rec)
        st["dynamic_dispatch"] += 1
