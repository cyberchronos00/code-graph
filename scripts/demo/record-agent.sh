#!/usr/bin/env bash
# Record the live agent demo: docs/media/cg-agent-demo.mp4 (H.264, 1600x900).
# A real Cursor CLI agent answers the questions typed in scripts/demo/agent.tape, with cg's MCP server connected;
# the answers stream live from the model (each recording differs a little). Costs a few model calls on your Cursor
# account. Needs: the Cursor CLI (curl https://cursor.com/install -fsS | bash) with CURSOR_API_KEY in the environment,
# vhs (+ ttyd, Chrome/Chromium), ffmpeg, git, and the README quickstart deps (.venv with mcp).
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -n "${CURSOR_API_KEY:-}" ] || { echo "CURSOR_API_KEY is not set" >&2; exit 1; }
mkdir -p out/demo docs/media
if [ "${1:-}" = baseline ]; then   # comparison take without code-graph (scripts/demo/agent-baseline.tape)
  BASELINE=1 scripts/demo/agent-setup.sh   # fresh /tmp/demo-base/bookstore-copy, no graph, no MCP config
  rm -rf /tmp/demo-base/logs
  PATH="/tmp/demo-base/.local/bin:/tmp/demo-base/tools/bin:$PATH" vhs scripts/demo/agent-baseline.tape
  raw=out/demo/agent-baseline-raw.mp4 f=docs/media/cg-agent-baseline.mp4
  mkdir -p out/demo/baseline-logs && cp /tmp/demo-base/logs/*.jsonl out/demo/baseline-logs/
else
  scripts/demo/agent-setup.sh            # fresh /tmp/demo/bookstore-copy (+ graph, mcp.json, rules), every run
  rm -rf /tmp/demo/logs
  PATH="/tmp/demo/.local/bin:/tmp/demo/cg/bin:$PATH" vhs scripts/demo/agent.tape
  raw=out/demo/agent-raw.mp4 f=docs/media/cg-agent-demo.mp4
  mkdir -p out/demo/cg-logs && cp /tmp/demo/logs/*.jsonl out/demo/cg-logs/
fi

ffmpeg -y -loglevel error -i "$raw" \
  -c:v libx264 -preset veryslow -crf 26 -tune stillimage -pix_fmt yuv420p -r 25 -movflags +faststart -an "$f"
printf '%s  %s bytes  %ss\n' "$f" "$(stat -c %s "$f")" "$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")"
