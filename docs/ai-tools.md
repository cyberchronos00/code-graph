# AI harnesses: LLM tools, MCP servers and agents

A tool is registered under a name and a harness dispatches to the handler. There is no call edge
in the code, so cg models the tool as a [protocol endpoint](protocols.md):

```
agent / MCP client -SENDS_TO-> endpoint:llm_tool:<name> | endpoint:mcp_tool:<server>/<name> -RECEIVED_BY-> handler
agent:<name> -OFFERS_TOOL-> tool;  agent -HANDS_OFF_TO-> agent
```

A receiving tool is an entry point of kind `llm_tool`. Only modules that import an AI SDK are
read (Python: mcp, fastmcp, openai, agents, anthropic, langchain, langgraph, llama_index;
TypeScript: `@modelcontextprotocol/sdk`).

| framework | receiver | sender |
|---|---|---|
| FastMCP / `MCPServer` | `@mcp.tool` / `.resource` / `.prompt`, `add_tool(fn)`. A server built inside a function: the enclosing function is the receiver | `mcp.run()` |
| MCP `Server` | `@server.call_tool()` branching on `name == "x"` or an enum member. One `Tool(name=)` and no branch maps that tool to the whole handler | |
| TS `@modelcontextprotocol/sdk` | `registerTool` / `tool` / `registerPrompt` / `registerResource` on `new McpServer({name})` | |
| MCP clients | | `session.call_tool` / `read_resource` / `get_prompt` (MATCHES_ENDPOINT, including `cg link`) |
| OpenAI / Anthropic | | a `tools=` schema literal or a module constant |
| OpenAI Agents | `@function_tool` | `Agent(name=, tools=, handoffs=)` → `agent:<name>` |
| LangChain | `@tool`, `StructuredTool.from_function`, `BaseTool.name` → `_run` | `bind_tools`, `create_react_agent`, `AgentExecutor`, `ToolNode` |
| LlamaIndex | `FunctionTool.from_defaults` | constructors with `tools=` |
| Hand-written loops | `TOOLS = {"x": fn}`, `if name == "x"` in a function that handles tool calls | the schema list |

Endpoint attrs: `framework`, `description` (first docstring line), `params`, `schema_source`
(decorator, constructor, class attrs, dict registry, agent loop, schema literal, low-level
server), `declared_in`, `toolset`, `server`. An unnamed local server is named after the
function that builds it (`nested_in`, `handler_name`). A TS handler is the inline arrow (a
function node `server.registerTool('x')`) or the function passed. A `server: McpServer`
parameter of a register helper uses the package's one named server.

A loop that picks the handler with `globals()[name]`, `getattr` or `eval` is recorded as
`attrs.llm_dynamic_dispatch` and listed by `cg tools`. It is never linked. Literal `model=` /
`base_url=` of `chat.completions.create`, `responses.create`, `messages.create`,
`embeddings.create` and of constructors (`ChatOpenAI`, `OpenAI(base_url=)`, `init_chat_model`) are `attrs.llm_calls` (`provider`, `op`, `model`, `base_url`, `line`) on the calling
function. Those facts feed the external nodes in [External systems](external.md). Confidence is
`exact` for a literal name, `resolved` through a wrapper or a module constant, `heuristic` when
a `BaseTool` subclass has no literal `name` (the class name is used).

cg's own server (`cg_code_graph/mcp_server.py`) indexes as `endpoint:mcp_tool:code-graph/<tool>`.
The decorator's reference edge is replaced by that endpoint.

```bash
cg tools --db graph.db
cg tools refund --framework mcp --unmatched --db graph.db
cg tools --agent Billing --db graph.db
```

Checks are the [protocol checks](protocols.md#checks) plus `name_collision` (two handlers, one
name, one module). `tables` lists tables the handler reads or writes within three calls. MCP:
`llm_tools(pattern?, framework?, unmatched?, agent?)`.

`cg tools refund` prints one block: handler, who offers or calls it, matches, agents, and the
tables it reaches. `cg protocols --protocol llm_tool|mcp_tool|mcp_resource|mcp_prompt` shows the
same endpoints. `impact`, `tests`, `reaches`, `path` and `downstream` follow SENDS_TO /
RECEIVED_BY / MATCHES_ENDPOINT / OFFERS_TOOL with no extra flags.

## Not covered yet

- TypeScript MCP clients, low-level `setRequestHandler`, Vercel AI SDK `tool()`, OpenAI /
  Anthropic Node schema literals.
- LangGraph edges, LlamaIndex `QueryEngineTool` and `@step`, `Runner.run`, `agent.as_tool()`,
  MCP servers attached to agents.
- `@function_tool` on a nested function (no handler node). `USES_MODEL` edges to `external:llm:`
  nodes.

Public harnesses:
[AI harnesses](validation-log.md#ai-harnesses-llm-tools-mcp-servers-and-agents-66).
