# AI harnesses: LLM tools, MCP servers and agents

LLM applications route control through the model: a function is registered as a tool under a name, the model returns
that name and a harness dispatches to the handler. There is no call from the dispatcher to the handler in the code,
so without this layer tool handlers look dead, `impact` stops at the tool function and `reaches` cannot say "this
table is reachable from the `refund_order` tool". cg models every tool as a [protocol endpoint](protocols.md):

```
offering code / agent / MCP client -SENDS_TO-> endpoint:llm_tool:<name> | endpoint:mcp_tool:<server>/<name> -RECEIVED_BY-> handler
agent:<name> -OFFERS_TOOL-> tool endpoint;  agent -HANDS_OFF_TO-> agent
```

A receiving tool endpoint is an entry point of kind `llm_tool` (runtime group), so `reaches` / `impact` report
"reachable from tool X". Python, plus TypeScript MCP servers; only modules importing an AI SDK (Python: mcp,
fastmcp, openai, agents, anthropic, langchain*, langgraph, llama_index; TypeScript: `@modelcontextprotocol/sdk`) are
read, other code is unchanged.

## What is extracted

| Framework | Receiver (tool → handler) | Sender (offer / invoke) | Confidence |
|---|---|---|---|
| MCP servers (FastMCP, `MCPServer`, standalone fastmcp) | `@mcp.tool()` / `@mcp.tool(name=)`, `@mcp.resource("notes://{id}")`, `@mcp.prompt()`, `mcp.tool()(fn)`, `add_tool(fn)`, local decorators that register their argument; servers built inside a function (`def build(): mcp = MCPServer(...)` with nested `@mcp.tool()`, tools in test bodies): the enclosing function is the receiver (`nested_in`, `handler_name`; an unnamed local server is named after that function) | `mcp.run()` is the external entry | exact (resolved through a wrapper) |
| MCP low-level `Server` | `@server.call_tool()` handlers branching on `if name == "x"` / `match name: case "x"`, or on enum members (`case GitTools.STATUS:` with `class GitTools(str, Enum): STATUS = "git_status"`, also
`TimeTools.X.value`); a server with one `Tool(name=)` in its `@server.list_tools()` and no branch maps that tool to the
whole handler (mcp-server-fetch); also when the server and handler are built inside a function (`async def serve(): server = Server("mcp-git")`) → the function the branch calls | | resolved |
| MCP servers in TypeScript (`@modelcontextprotocol/sdk`) | `server.registerTool("x", config, handler)`, `server.tool("x", ...)`, `registerPrompt` / `prompt`, `registerResource(name, "uri" \| new ResourceTemplate("notes://{id}"), ...)`; names from string literals or consts; the handler is the inline arrow (a function node `server.registerTool('x')`) or the function passed. The server is the `new McpServer({ name })` the receiver holds, or, for a `server: McpServer` parameter of a register helper, the package's one named server | | exact / resolved (server from the package) |
| MCP clients | | `session.call_tool("x")`, `read_resource("uri")`, `get_prompt("x")` → `*/<name>` (MATCHES_ENDPOINT pairs it with the server's tool, also across repos through `cg link`) | exact |
| OpenAI / Anthropic SDK schema literals | | `{"type": "function", "function": {"name"}}`, Responses `{"type": "function", "name"}`, Anthropic `{"name", "input_schema"}` in a function, or a module constant passed as `tools=` | exact / resolved |
| OpenAI Agents SDK | `@function_tool` (`name_override=`) | `Agent(name=, tools=[...], handoffs=[...])` → `agent:<name>` | exact |
| LangChain | `@tool` / `@tool("name")`, `StructuredTool.from_function(func=, name=)`, `Tool(name=, func=)`, `BaseTool` subclasses (literal `name` → `_run` / `_arun`; class name otherwise, heuristic) | `bind_tools([...])`, `create_react_agent(model, tools)`, `AgentExecutor(tools=)`, `ToolNode([...])` | exact |
| LlamaIndex | `FunctionTool.from_defaults(fn=, name=)` | agent constructors with `tools=[...]` | exact |
| Hand-written agent loops | module dict registries `TOOLS = {"x": fn}` / `{f.__name__: f for f in [a, b]}` (name contains tool / function / handler / dispatch / action), `if name == "x":` / `match name:` branches in a function that handles tool calls | the schema list | resolved |

Endpoint attrs: `framework`, `description` (first docstring line), `params`, `schema_source` (decorator | constructor |
class attrs | dict registry | agent loop | schema literal | low-level server), `declared_in`, `toolset`, `server`.

**Findings instead of guesses.** An agent loop that picks the handler by a runtime name (`globals()[name]`,
`getattr(obj, name)`, `eval`) is recorded as `attrs.llm_dynamic_dispatch` on the function and listed by `cg tools`;
it is never linked.

**Model / provider config** (facts for the external-system nodes of #40): literal `model=` / `base_url=` of
`chat.completions.create`, `responses.create`, `messages.create`, `embeddings.create` and of model constructors
(`ChatOpenAI`, `ChatAnthropic`, `OpenAI(base_url=)`, `init_chat_model` ...) are stored as `attrs.llm_calls`
(`provider`, `op`, `model`, `base_url`, `line`) on the calling function.

**cg's own MCP server** (`codegraph/mcp_server.py`, a local `@tool` decorator that calls `server.tool()(served)`)
indexes with its tools as `endpoint:mcp_tool:code-graph/<tool>`; the registering decorator's REFERENCES_FN to each
handler, which showed up as `tool (ref: decorator)` in every impact report, is replaced by the endpoint.

## `cg tools`

```
cg tools --db graph.db                         # summary per protocol / framework, agents, dynamic dispatch, model calls
cg tools refund --db graph.db                  # one block per tool: handler, offered / called by, matches, agents, tables
cg tools --db graph.db --framework mcp --unmatched
cg tools --db graph.db --agent Billing         # the tools an agent offers
```

Checks per tool: the [#31 checks](protocols.md#checks) (`no_receiver`: offered to the model or called by a client, no
handler; `no_sender`: a handler nothing offers or calls; `test_sender_only`; `ambiguous`) plus `name_collision` (two
handlers for one name in one module). `tables` lists the tables the handler writes or reads within three calls.
`cg protocols --protocol llm_tool|mcp_tool|mcp_resource|mcp_prompt` shows the same endpoints, and `impact`, `tests`,
`reaches`, `path`, `downstream` follow SENDS_TO / RECEIVED_BY / MATCHES_ENDPOINT / OFFERS_TOOL without new arguments.
MCP tool: `llm_tools(pattern?, framework?, unmatched?, agent?)`.

## Not covered yet

- TypeScript: MCP clients (`client.callTool`, `readResource`, `getPrompt`), low-level `Server` with
  `setRequestHandler(CallToolRequestSchema, ...)` and a `switch (name)`, resource URIs computed at runtime, Vercel AI
  SDK `tool({...})` objects, OpenAI / Anthropic Node SDK schema literals.
- LangGraph `StateGraph` nodes / edges / conditional path maps (`GRAPH_EDGE`), LlamaIndex `QueryEngineTool` and
  workflow `@step`s.
- Agent runners (`Runner.run(agent)`), `agent.as_tool()`, MCP servers attached to agents (`mcp_servers=[...]`),
  `langchain_mcp_adapters` tool loading.
- Non-MCP tools declared inside a function body (`@function_tool` / `@tool` on a nested def, common in Agents SDK
  tests): nested defs collapse into their owner, so they have no handler node.
- `USES_MODEL` edges to `external:llm:<provider>` nodes (#40); inference-serving entry points (vLLM, Ray Serve,
  BentoML, Triton).
