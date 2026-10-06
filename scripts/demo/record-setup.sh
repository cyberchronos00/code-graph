#!/usr/bin/env bash
# Re-record the setup demo: docs/media/cg-setup-demo.mp4 (H.264, 1600x900).
# Needs: vhs (+ ttyd, Chrome/Chromium; https://github.com/charmbracelet/vhs), ffmpeg, git, python3, composer, npm, jq,
# and network access to GitHub. The tape clones the public repo into a throwaway HOME (/tmp/demo) and runs the README
# quickstart there. Package caches point at the recorder's own caches so downloads are quick; the install is real.
# DEMO_CLONE_FROM=/path/to/repo.git records a release that is not pushed yet: the on-screen clone of the public URL is
# served from that repo (git url.insteadOf in the throwaway HOME), so the video matches the code it ships with.
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p out/demo docs/media
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$HOME/.cache/pip}"
export npm_config_cache="${npm_config_cache:-$HOME/.npm}"
export COMPOSER_CACHE_DIR="${COMPOSER_CACHE_DIR:-$HOME/.cache/composer}"
vhs scripts/demo/setup.tape

ffmpeg -y -loglevel error -i out/demo/setup-raw.mp4 \
  -c:v libx264 -preset veryslow -crf 26 -tune stillimage -pix_fmt yuv420p -r 25 -movflags +faststart -an \
  docs/media/cg-setup-demo.mp4
f=docs/media/cg-setup-demo.mp4
printf '%s  %s bytes  %ss\n' "$f" "$(stat -c %s "$f")" "$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")"
