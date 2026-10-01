#!/usr/bin/env python3
"""Render the Cursor CLI's `--output-format stream-json --stream-partial-output` events as a readable live transcript.

    agent -p --output-format stream-json --stream-partial-output "<question>" | agent-stream.py [--log run.jsonl]

The agent's text streams in as it is generated; each tool call prints one line when it starts (cg MCP calls as
`cg <tool>(args)`) and a short result line when it finishes; the footer shows wall time, tool calls and token usage.
It only reformats the CLI's own event stream. --log (or AGENT_STREAM_LOGDIR) keeps the raw events for later review.
"""
import json, sys, time, textwrap, argparse, os, shutil

ap = argparse.ArgumentParser()
ap.add_argument("--log")
ap.add_argument("--width", type=int, default=0)
a = ap.parse_args()
if not a.log and os.environ.get("AGENT_STREAM_LOGDIR"):  # recording: keep each answer's raw events (01.jsonl, 02.jsonl…)
    d = os.environ["AGENT_STREAM_LOGDIR"]; os.makedirs(d, exist_ok=True)
    a.log = os.path.join(d, f"{len([f for f in os.listdir(d) if f.endswith('.jsonl')]) + 1:02d}.jsonl")
log = open(a.log, "w") if a.log else None
W = a.width or min(shutil.get_terminal_size((110, 30)).columns, 120)
tty = sys.stdout.isatty() or os.environ.get("FORCE_COLOR")
def c(code, s): return f"\x1b[{code}m{s}\x1b[0m" if tty else s
DIM, CYAN, GREEN, RED, YEL, BOLD = "2", "36", "32", "31", "33", "1"

t0 = time.time(); col = 0; calls = 0; mcp_calls = 0; in_text = False; thinking = False
def out(s):
    global col
    sys.stdout.write(s); sys.stdout.flush()
    nl = s.rfind("\n")
    col = len(s) - nl - 1 if nl >= 0 else col + len(s)
def newline():
    if col: out("\n")

def short(v, n=70):
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"

def fmt_args(d):
    if not d: return ""
    return ", ".join(f"{k}={short(v, 60)}" for k, v in d.items() if v not in (None, "", [], {}))

def rel(p):
    p = str(p or "")
    cwd = os.getcwd().rstrip("/") + "/"
    return p[len(cwd):] if p.startswith(cwd) else p

def describe(kind, body):
    args = body.get("args") or {}
    if kind == "mcpToolCall":
        return "cg", f"{args.get('toolName')}({fmt_args(args.get('args'))})"
    if kind == "getMcpToolsToolCall":
        return None, None  # schema lookup, not a real call
    if kind == "readToolCall":
        return "read", rel(args.get("path"))
    if kind in ("editToolCall", "writeToolCall", "strReplaceToolCall"):
        return "edit", rel(args.get("path"))
    if kind == "shellToolCall":
        return "shell", short(args.get("command"), 80)
    if kind in ("grepToolCall", "globToolCall", "lsToolCall"):
        return kind.replace("ToolCall", ""), short(args.get("pattern") or args.get("globPattern") or args.get("path"), 60)
    return kind.replace("ToolCall", ""), short(fmt_args({k: v for k, v in args.items() if k != "toolCallId"}), 70)

def result_line(kind, body):
    r = body.get("result") or {}
    if "rejected" in r: return c(RED, "rejected")
    if "error" in r: return c(RED, "error: " + short(r["error"], 80))
    ok = r.get("success") or {}
    if kind == "mcpToolCall":
        content = ok.get("content")
        if isinstance(content, list):
            content = " ".join(x.get("text", {}).get("text", "") if isinstance(x.get("text"), dict) else str(x.get("text", "")) for x in content)
        text = str(content or "")
        lines = [l for l in text.splitlines() if l.strip()]
        return c(DIM, f"{len(lines)} lines: " + short(lines[0] if lines else "(empty)", 80))
    if kind == "readToolCall":
        n = ok.get("totalLines") or (ok.get("content") or "").count("\n")
        return c(DIM, f"{n} lines")
    if kind in ("editToolCall", "writeToolCall", "strReplaceToolCall"):
        return c(GREEN, short(ok.get("message") or "edited", 60)) if ok else c(DIM, short(r, 60))
    return c(DIM, "done")

started = {}
for raw in sys.stdin:
    if log: log.write(raw); log.flush()
    try:
        e = json.loads(raw)
    except ValueError:
        continue
    t = e.get("type")
    if t == "thinking" and not thinking and not in_text:
        thinking = True
        newline(); out(c(DIM, "… thinking") + "\n")
    elif t == "assistant":
        if "model_call_id" in e or "timestamp_ms" not in e:  # aggregated repeat of the deltas already shown
            continue
        thinking = False
        for part in e["message"]["content"]:
            if part.get("type") == "text":
                if not in_text:
                    newline(); in_text = True
                out(part["text"])
    elif t == "tool_call":
        tc = e["tool_call"]
        kind = next((k for k in tc if k.endswith("ToolCall")), "tool")
        body = tc.get(kind, {})
        if e.get("subtype") == "started":
            label, what = describe(kind, body)
            if label is None:
                continue
            calls += 1; mcp_calls += kind == "mcpToolCall"
            started[e["call_id"]] = (label, what)
            newline(); in_text = False; thinking = False
            out(c(CYAN, f"  ▸ {label} ") + what + "\n")
        elif e.get("call_id") in started:
            out(c(DIM, "    └ ") + result_line(kind, body) + "\n")
    elif t == "result":
        newline()
        u = e.get("usage") or {}
        secs = (e.get("duration_ms") or (time.time() - t0) * 1000) / 1000
        tok = f"{u.get('inputTokens',0)+u.get('cacheReadTokens',0):,} in ({u.get('cacheReadTokens',0):,} cached) / {u.get('outputTokens',0):,} out"
        mcp = f" ({mcp_calls} cg)" if mcp_calls else ""
        out("\n" + c(DIM, f"── {secs:.0f}s · {calls} tool calls{mcp} · {tok}") + "\n")
if log: log.close()
