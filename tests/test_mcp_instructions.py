"""cg-mcp tells agents to use it before grep / file reads: short initialize instructions, the full reference as the
cg://guide resource, and core tool descriptions that open with when to use them."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.store import GraphStore  # noqa: E402


def test_initialize_instructions_and_guide(tmp_path):
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    db = tmp_path / "g.db"
    GraphStore.create(db).db.close()

    async def go():
        params = StdioServerParameters(command=sys.executable, cwd=str(tmp_path),
                                       args=["-m", "cg_code_graph.mcp_server", "--db", str(db), "--tools", "core"])
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                init = await s.initialize()
                tools = {t.name: t.description or "" for t in (await s.list_tools()).tools}
                guide = await s.read_resource("cg://guide")
                return init.instructions, tools, "".join(getattr(c, "text", "") for c in guide.contents)

    instructions, tools, guide = asyncio.run(go())
    assert instructions and len(instructions) <= 600
    for word in ("before grep", "`search`", "`impact`", "`snippet`", "`graphs`", "not covered", "cg://guide"):
        assert word in instructions, word
    assert "Specs:" in guide and "COVERAGE" in guide
    assert len(tools) == 13
    for name, desc in tools.items():
        assert desc.startswith(("Use ", "Start here")), (name, desc[:60])
