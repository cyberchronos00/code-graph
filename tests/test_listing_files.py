"""The MCP Registry / plugin listing files agree with each other and with the package."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _json(rel):
    return json.loads((ROOT / rel).read_text())


def test_server_json_matches_readme_marker():
    name = _json("server.json")["name"]
    assert name == "io.github.cyberchronos00/cg-code-graph"
    assert f"<!-- mcp-name: {name} -->" in (ROOT / "README.md").read_text()


def test_server_json_package_runs_the_mcp_server():
    pkg = _json("server.json")["packages"][0]
    assert (pkg["registryType"], pkg["identifier"], pkg["transport"]["type"]) == ("pypi", "cg-code-graph", "stdio")
    # uvx runs the executable named like the package: it must start the MCP server
    py = (ROOT / "pyproject.toml").read_text()
    assert re.search(r'^cg-code-graph = "cg_code_graph\.mcp_server:main"', py, re.M)
    assert {a["name"]: a.get("default") for a in pkg["packageArguments"]} == {"--db": "out/graph.db", "--tools": "core"}


def test_mcp_configs_and_plugin_manifests_agree():
    assert _json(".mcp.json") == _json("mcp.json")
    args = _json(".mcp.json")["mcpServers"]["code-graph"]["args"]
    assert args[:3] == ["--from", "cg-code-graph", "cg-mcp"]
    cursor, grok = _json(".cursor-plugin/plugin.json"), _json(".grok-plugin/plugin.json")
    assert cursor == grok
    assert re.fullmatch(r"[a-z0-9][a-z0-9.-]*[a-z0-9]", cursor["name"])
    for rel in (cursor["logo"], cursor["mcpServers"], "assets/logo.png"):
        assert not rel.startswith(("/", "..")) and (ROOT / rel).is_file()
    png = (ROOT / "assets/logo.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and png[16:24] == (512).to_bytes(4, "big") * 2


def test_listing_versions_match_the_package():
    from cg_code_graph import __version__
    server = _json("server.json")
    assert server["version"] == server["packages"][0]["version"] == __version__
    assert _json(".cursor-plugin/plugin.json")["version"] == __version__
