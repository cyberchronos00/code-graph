# Live agent demo: a real coding agent (Cursor CLI, a small low-cost model at medium reasoning) answers engineering
# questions on the bundled sample apps with cg connected over MCP. Every question is typed on camera and the agent's
# answer streams in as the model produces it. Rendered by charmbracelet/vhs; record it
#   scripts/demo/record-agent.sh
# which prepares the throwaway workspace (/tmp/demo/bookstore-copy, via agent-setup.sh), runs this tape and encodes
# docs/media/cg-agent-demo.mp4. The agent only ever edits that copy; examples/ keeps its planted bugs.
# `ask` (defined on camera) is the plain Cursor CLI in streaming print mode piped through agent-stream, which only
# reformats the CLI's own event stream (tool calls as one line each, text deltas as they arrive, usage at the end).

Output out/demo/agent-raw.mp4

Require agent
Require agent-stream
Require git

Set Shell "bash"
Set FontFamily "DejaVu Sans Mono"
Set FontSize 17
Set LineHeight 1.2
Set Width 1600
Set Height 900
Set Padding 30
Set Theme "Catppuccin Mocha"
Set Framerate 25
Set TypingSpeed 35ms
Set CursorBlink false
Set WaitTimeout 900s
Set WaitPattern /^\$ *$/

# Neutral setup: generic HOME, a plain '$ ' prompt, UTC, history off, so the screen shows only the demo.
Hide
Type `export HOME=/tmp/demo PATH=/tmp/demo/.local/bin:/tmp/demo/cg/bin:/usr/local/bin:/usr/bin:/bin PS1='$ ' PS2='> ' TZ=UTC LC_ALL=C.UTF-8 HISTFILE=/dev/null FORCE_COLOR=1 GIT_PAGER=cat AGENT_STREAM_LOGDIR=/tmp/demo/logs; set +m; cd /tmp/demo/bookstore-copy && clear`
Enter
Sleep 500ms
Show

Sleep 600ms
Type "# A coding agent using code-graph, live (Cursor CLI, GPT-5.4 Mini at medium reasoning)."
Sleep 400ms
Enter
Type "cat .cursor/mcp.json"
Enter
Wait
Type "agent mcp list"
Enter
Wait
Sleep 1500ms
Type `chat=$(agent create-chat); ask() { agent -p --trust --resume "$chat" --model gpt-5.4-mini-medium --output-format stream-json --stream-partial-output "$*" | agent-stream; }`
Enter
Sleep 2500ms

# 1. Security review: routes that write data with no auth in front of them.
Ctrl+L
Sleep 300ms
Type "# 1. Security review: every write route without auth, with file:line evidence."
Enter
Type `ask "Use code-graph: which API routes write data without auth, and what does each one write?"`
Sleep 500ms
Enter
Wait
Sleep 7s

# The plan, shown before the plan question (plans/preorders.yaml in the copy, header comment removed)
Ctrl+L
Sleep 300ms
Type "# The agreed plan for the pre-orders feature (plans/preorders.yaml: name, title, modify, forbid, require)"
Enter
Type "sed -n '2,3p;17,25p;30,38p' plans/preorders.yaml"
Enter
Wait
Sleep 9s

# 2. Feature: check the agreed plan against the real graph before anyone writes code.
Ctrl+L
Sleep 300ms
Type "# 2. Plan check: catch what a feature plan forgets before any code is written."
Enter
Type `ask "Use code-graph to check plans/preorders.yaml before we implement it. What does the plan miss?"`
Sleep 500ms
Enter
Wait
Sleep 8s

# 3. Fix everything found in 1 and 2 (in this throwaway copy), then verify. Same chat, so the agent remembers 1 and 2.
Ctrl+L
Sleep 300ms
Type "# 3. Fix everything: the agent closes every gap, re-indexes and re-checks with the graph."
Enter
Type `ask "Use code-graph: fix all the gaps you found: guard every write route without auth like POST /v1/orders, and implement the preorders plan including what it missed. Then re-index with code-graph and verify nothing is left."`
Sleep 500ms
Enter
Wait
Sleep 4s
Type "git status --short && git diff --stat"
Enter
Wait
Sleep 8s
