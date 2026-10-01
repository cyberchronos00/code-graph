#!/usr/bin/env bash
# Re-record the terminal demo: docs/media/cg-terminal-demo.mp4 (H.264) + docs/media/cg-terminal-demo.gif (README preview).
# Needs: vhs (+ ttyd, Chrome/Chromium; https://github.com/charmbracelet/vhs), ffmpeg, jq, and the project deps from the
# README quickstart (.venv, PHP extractor vendor/, TS extractor node_modules/). The font is DejaVu Sans Mono.
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p out/demo docs/media
vhs scripts/demo/terminal.tape

# MP4: H.264, yuv420p, faststart, small.
ffmpeg -y -loglevel error -i out/demo/terminal-raw.mp4 \
  -c:v libx264 -preset veryslow -crf 26 -tune stillimage -pix_fmt yuv420p -r 25 -movflags +faststart -an \
  docs/media/cg-terminal-demo.mp4

# GIF preview: 1200 px wide, 10 fps, single global palette.
ffmpeg -y -loglevel error -i out/demo/terminal-raw.mp4 \
  -vf "fps=10,scale=1200:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" \
  docs/media/cg-terminal-demo.gif

for f in docs/media/cg-terminal-demo.mp4 docs/media/cg-terminal-demo.gif; do
  printf '%s  %s bytes  %ss\n' "$f" "$(stat -c %s "$f")" \
    "$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")"
done
