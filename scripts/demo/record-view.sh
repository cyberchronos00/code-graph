#!/usr/bin/env bash
# Re-record the visual-view demo: docs/media/cg-view-demo.mp4 (H.264, 1920x1080).
# Needs: the README quickstart deps, Node 20+, a local Chrome (CHROME=/path/to/chrome to override), ffmpeg.
# Builds out/graph.db from the bundled sample apps, serves it read-only on 127.0.0.1, and drives it with Playwright.
set -euo pipefail
cd "$(dirname "$0")/../.."
PORT=${PORT:-8199}
cg() { .venv/bin/python -m codegraph.cli "$@"; }
mkdir -p out/demo docs/media
cg index examples/bookstore-api --gates examples/bookstore.gates.json --db out/api.db > /dev/null
cg index examples/bookstore-web --db out/web.db > /dev/null
cg link --backend out/api.db --frontend out/web.db --db out/graph.db \
  --backend-name bookstore-api --frontend-name bookstore-web > /dev/null

[ -d scripts/demo/node_modules/playwright-core ] || (cd scripts/demo && npm ci --no-audit --no-fund)
(cd scripts/demo && npx playwright-core install ffmpeg > /dev/null)

cg serve --db out/graph.db --plans-dir examples/plans --host 127.0.0.1 --port "$PORT" > out/demo/serve.log 2>&1 & SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
for _ in $(seq 50); do curl -sf "http://127.0.0.1:$PORT/" > /dev/null && break; sleep 0.2; done

rm -rf out/demo/view-video
WEBM=$(node scripts/demo/record-view.mjs "http://127.0.0.1:$PORT/" out/demo/view-video | tail -n 1)

# H.264 MP4 (the webm from Playwright starts with a short blank frame before the page paints; trim it).
ffmpeg -y -loglevel error -ss 0.4 -i "$WEBM" \
  -c:v libx264 -preset veryslow -crf 24 -pix_fmt yuv420p -r 30 -movflags +faststart -an \
  docs/media/cg-view-demo.mp4
f=docs/media/cg-view-demo.mp4
printf '%s  %s bytes  %ss\n' "$f" "$(stat -c %s "$f")" "$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")"
