# Comparison take for the live agent demo: the same agent (Cursor CLI), the same model (GPT-5.4 Mini at medium
# reasoning) and the same three tasks as scripts/demo/agent.tape, in a fresh copy of the sample apps with no MCP server
# configured: the agent works with its built-in search, file reading and editing only. One continuous live take; record
# it with
#   scripts/demo/record-agent.sh baseline
# which prepares /tmp/demo-base/bookstore-copy (agent-setup.sh with BASELINE=1), runs this tape and encodes
# docs/media/cg-agent-baseline.mp4. The prompts are the ones from agent.tape without the parts that name the MCP tools.

Output out/demo/agent-baseline-raw.mp4

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
Type `export HOME=/tmp/demo-base PATH=/tmp/demo-base/.local/bin:/tmp/demo-base/tools/bin:/usr/local/bin:/usr/bin:/bin PS1='$ ' PS2='> ' TZ=UTC LC_ALL=C.UTF-8 HISTFILE=/dev/null FORCE_COLOR=1 GIT_PAGER=cat AGENT_STREAM_LOGDIR=/tmp/demo-base/logs; set +m; cd /tmp/demo-base/bookstore-copy && clear`
Enter
Sleep 500ms
Show

Sleep 600ms
Type "# The same coding agent without code-graph (Cursor CLI, GPT-5.4 Mini at medium reasoning): built-in search and file reading."
Sleep 400ms
Enter
Type "agent mcp list"
Enter
Wait
Sleep 1500ms
Type `chat=$(agent create-chat); ask() { agent -p --trust --resume "$chat" --model gpt-5.4-mini-medium --output-format stream-json --stream-partial-output "$*" | agent-stream; }`
Enter
Sleep 2500ms

# 1. Security review
Ctrl+L
Sleep 300ms
Type "# 1. Security review: every write route without auth."
Enter
Type `ask "Which API routes write data without auth, and what does each one write?"`
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

# 2. Plan check
Ctrl+L
Sleep 300ms
Type "# 2. Plan check: what does the feature plan miss?"
Enter
Type `ask "Check plans/preorders.yaml before we implement it. What does the plan miss?"`
Sleep 500ms
Enter
Wait
Sleep 8s

# 3. Fix everything found in 1 and 2 (in this throwaway copy), then verify. Same chat, so the agent remembers 1 and 2.
Ctrl+L
Sleep 300ms
Type "# 3. Fix everything: close every gap and verify."
Enter
Type `ask "Fix all the gaps you found: guard every write route without auth like POST /v1/orders, and implement the preorders plan including what it missed. Then verify nothing is left."`
Sleep 500ms
Enter
Wait
Sleep 4s
Type "git status --short && git diff --stat"
Enter
Wait
Sleep 8s
