#!/usr/bin/env python3
"""Show a real MCP session with the code-graph server: the JSON-RPC messages an agent sends over stdio and the
results it gets back. No model is involved; every line printed is either a request this script sends or the
server's actual reply. Used by scripts/demo/agent.tape (docs/media/cg-agent-demo.mp4).

  .venv/bin/python scripts/demo/mcp-session.py tools
  .venv/bin/python scripts/demo/mcp-session.py call impact '{"method": "StockService::reserve"}'

The server is started exactly as an MCP host starts it from the config in docs/mcp.md
(`python -m cg_code_graph.mcp_server --db out/graph.db --gates ... --plans ...`, cwd = the checkout).
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = [sys.executable, "-m", "cg_code_graph.mcp_server", "--db", "out/graph.db",
          "--gates", "examples/bookstore.gates.json", "--plans", "examples/plans"]
DIM, CYAN, GREEN, RESET = "\033[2m", "\033[36m", "\033[32m", "\033[0m"


class Session:
    def __init__(self) -> None:
        self.p = subprocess.Popen(SERVER, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.next_id = 1

    def send(self, method: str, params: dict | None = None, notify: bool = False) -> dict | None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            msg["id"] = self.next_id
            self.next_id += 1
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        if notify:
            return None
        while True:
            reply = json.loads(self.p.stdout.readline())
            if reply.get("id") == msg["id"]:
                if "error" in reply:
                    raise SystemExit(f"server error: {reply['error']}")
                return reply["result"]

    def initialize(self) -> dict:
        res = self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "demo-client", "version": "1.0"}})
        self.send("notifications/initialized", notify=True)
        return res

    def close(self) -> None:
        self.p.stdin.close()
        self.p.wait(timeout=10)


def main(argv: list[str]) -> int:
    s = Session()
    init = s.initialize()
    try:
        if argv[:1] == ["tools"]:
            print(f"{DIM}-> initialize   <- server: {init['serverInfo']['name']}{RESET}")
            print(f"{CYAN}-> tools/list{RESET}")
            tools = s.send("tools/list", {})["tools"]
            print(f"{GREEN}<- {len(tools)} tools:{RESET}")
            names = "  ".join(t["name"] for t in tools)
            print(textwrap.fill(names, width=100, initial_indent="   ", subsequent_indent="   "))
        elif argv[:1] == ["call"] and len(argv) >= 2:
            name = argv[1]
            args = json.loads(argv[2]) if len(argv) > 2 else {}
            print(f"{CYAN}-> tools/call {name} {json.dumps(args)}{RESET}")
            res = s.send("tools/call", {"name": name, "arguments": args})
            text = "\n".join(c.get("text", "") for c in res.get("content", []))
            flag = " (error)" if res.get("isError") else ""
            print(f"{GREEN}<- result{flag}:{RESET}")
            for line in text.splitlines():
                print("   " + line)
        else:
            print(__doc__)
            return 2
    finally:
        s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
