#!/usr/bin/env bash
# Build everything on the bundled sample apps (examples/bookstore-api + examples/bookstore-web) and run the tests.
# Static analysis only: nothing boots the sample apps or connects to a database. Outputs go to out/ (gitignored).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
C="$PY -m codegraph.cli"

# deps (once): php-cli >= 8.2 + composer, Node >= 20, Python 3.11+
[ -d .venv ] || python3 -m venv .venv
$PY -c 'import google.protobuf, pytest, mcp, yaml' 2>/dev/null || .venv/bin/pip install -q protobuf grpcio-tools pytest mcp pyyaml
[ -d codegraph/plugins/php/extractor/vendor ] || (cd codegraph/plugins/php/extractor && composer install -q)
[ -d codegraph/plugins/ts/extractor/node_modules/typescript ] || (cd codegraph/plugins/ts/extractor && npm ci --no-audit --no-fund)
mkdir -p out/viz

# index both sample apps, link them into one graph
$C index examples/bookstore-api --db out/api.db --name bookstore-api --gates examples/bookstore.gates.json > out/api_index_stats.json
$C index examples/bookstore-web --db out/web.db --name bookstore-web > out/web_index_stats.json
$C link --backend out/api.db --frontend out/web.db --db out/combined.db --backend-name bookstore-api --frontend-name bookstore-web \
   --report out/api_matches > /dev/null

# queries
{ $C reaches connection:warehouse table:warehouse_stock --db out/combined.db
  $C impact StockService::recordSale --db out/combined.db
  $C writers books --db out/combined.db
  $C path page:/reports/:id table:orders --db out/combined.db
  $C downstream page:/reports/:id --db out/combined.db
  $C api-calls page:/reports/:id --db out/combined.db; } > out/sample_queries.txt
$C resolutions timezone --db out/combined.db > out/sample_resolutions_timezone.txt

# planned-change layer: check the example plan, baseline, verify mode (before implementation: INCOMPLETE)
$C plan validate preorders --plans-dir examples/plans --db out/combined.db
$C plan check preorders --plans-dir examples/plans --db out/combined.db -o out/sample_plan_check.txt > /dev/null
$C plan baseline preorders --plans-dir examples/plans --db out/combined.db
$C plan check preorders --plans-dir examples/plans --db out/combined.db --verify --max-items 0 > out/sample_plan_verify.txt || true

# static HTML views (open from disk)
$C viz-export reaches connection:warehouse table:warehouse_stock --db out/combined.db -o out/viz/reaches_warehouse.html
$C viz-plan preorders --plans-dir examples/plans --db out/combined.db -o out/viz/plan_preorders.html

# optional screenshots (puppeteer-core + a local Chrome; no browser download)
if command -v google-chrome >/dev/null || [ -n "${CHROME:-}" ]; then
  [ -d codegraph/viz/tools/node_modules/puppeteer-core ] || (cd codegraph/viz/tools && PUPPETEER_SKIP_DOWNLOAD=1 npm ci --no-audit --no-fund --ignore-scripts)
  $C serve --db out/combined.db --port 8178 --plans-dir examples/plans > /dev/null 2>&1 & SRV=$!
  sleep 2
  (cd codegraph/viz/tools && node shoot.mjs http://127.0.0.1:8178/ ../../../out/screenshots) || true
  kill $SRV
fi

# tests (sample apps + fixtures + MCP e2e; regenerates docs/mcp/sample_outputs.md)
$PY -m pytest -q tests/
echo "done: out/sample_queries.txt, out/sample_resolutions_timezone.txt, out/sample_plan_check.txt, out/viz/"
