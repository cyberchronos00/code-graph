// Headless screenshots + layout checks of the code-graph view (issue #82 test plan).
// Usage: node shoot.mjs <base-url> <out-dir>   (the view must be served: codegraph.cli serve --db … --port …)
// Environment:
//   CHROME   browser binary (default /usr/bin/google-chrome)
//   WIDTHS   viewport widths, comma-separated (default 1280,1920; heights 1024→768, 1280→800, 1440→900, 1920→1080)
//   DPR      deviceScaleFactor (default 2)
//   SHOTS    JSON file with [{file, hash | url, maxTop?, actions?}] instead of the bundled sample-app list
//   PRESETS  1 = also shoot every query from /api/presets (starter presets of the served graph)
//   ONLY     substring filter on the file name
//   NO_ASSERT 1 = report failures but exit 0
//   THEME    light | dark: emulate prefers-color-scheme (default: the browser's)
//   BUDGET_FIRST  first-graph budget in ms (default 1500; tReady includes the API call)
// Shots: "landing" (hash ''), a hash on <base-url>, or an absolute url (e.g. file:///…/export.html from viz-export).
// actions: [{expandFirstCluster: true}, {tap: '<node id>'}, {key: 'Escape'}, {wait: ms}].
// Assertions per shot and width: no console error / warning / page error / HTTP >= 400; header.scrollWidth <=
// clientWidth with no clipped or wrapped control and height <= 56 px; every drawn label >= 11 px (font-size x zoom);
// <= 5 % of visible node label boxes overlap; no compound box around one node; top-level items <= maxTop.
// Writes <out-dir>/metrics.json and exits 1 when an assertion fails.
import puppeteer from 'puppeteer-core'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'

const base = (process.argv[2] || 'http://127.0.0.1:8177/').replace(/#.*$/, '')
const out = process.argv[3] || 'screenshots'
mkdirSync(out, { recursive: true })
const enc = (s) => encodeURIComponent(s)
const HEIGHT = { 1024: 768, 1280: 800, 1440: 900, 1920: 1080 }
const WIDTHS = (process.env.WIDTHS || '1280,1920').split(',').map(Number)
const DPR = Number(process.env.DPR || 2)
const MIN_PX = 11; const MAX_OVERLAP = 5; const MAX_HEADER = 56
// Default shots match the bundled sample apps (examples/); pass SHOTS=path/to/shots.json for your project.
const DEFAULT_SHOTS = [
  { file: 'sample_landing.png', hash: '' },
  { file: 'sample_reaches_warehouse.png', hash: `mode=reaches&spec=${enc('connection:warehouse')}&spec=${enc('table:warehouse_stock')}&select=${enc('connection:warehouse')}&hl=0` },
  { file: 'sample_report_page_downstream_tables.png', hash: `mode=downstream&spec=${enc('page:/reports/:id')}&sinks=${enc('table,column')}&select=${enc('page:app/pages/reports/[id].vue')}&hl=0` },
  { file: 'sample_report_page_path_orders.png', hash: `mode=path&spec=${enc('page:/reports/:id')}&spec=${enc('ReportController::top')}&spec=${enc('table:orders')}` +
    `&select=${enc('method:App\\Services\\SalesReportService::build')}` },
  { file: 'sample_plan_preorders_overview.png', hash: 'mode=plan&spec=preorders' },
  { file: 'sample_timezone_request_key_reaches.png', hash: `mode=reaches&spec=${enc('request_key:timezone')}` }
]
let SHOTS = process.env.SHOTS ? JSON.parse(readFileSync(process.env.SHOTS, 'utf8')) : DEFAULT_SHOTS
if (process.env.PRESETS === '1') {
  const presets = await (await fetch(new URL('api/presets', base))).json()
  SHOTS = SHOTS.concat(presets.map((p) => ({
    file: `preset_${p.id}.png`,
    hash: `mode=${p.mode}` + p.specs.map((s) => `&spec=${enc(s)}`).join('') + (p.sinks ? `&sinks=${enc(p.sinks.join(','))}` : '')
  })))
}
const only = process.env.ONLY

async function measure (page) {
  return page.evaluate((MIN_PX) => {
    const hd = document.querySelector('header')
    const r = { ready: document.body.dataset.ready, header: { scroll: hd.scrollWidth, client: hd.clientWidth, height: hd.offsetHeight } }
    const vis = [...hd.querySelectorAll('select,button,input,a')].filter((c) => c.offsetParent && getComputedStyle(c).visibility !== 'hidden')
    r.clipped = vis.filter((c) => c.getBoundingClientRect().right > hd.clientWidth + 1).map((c) => c.id || c.textContent.trim())
    r.wrapped = vis.filter((c) => c.tagName !== 'INPUT' && c.tagName !== 'SELECT' && c.getBoundingClientRect().height > 34).map((c) => c.id || c.textContent.trim())
    const land = document.getElementById('landing')
    if (land && !land.hidden) {
      r.landing = { stats: land.querySelectorAll('.stat').length, cards: land.querySelectorAll('.card').length }
      return r
    }
    const c = window.__cgCy
    if (!c) return r
    const z = c.zoom(); const ext = c.extent()
    const inView = (bb) => bb.x2 > ext.x1 && bb.x1 < ext.x2 && bb.y2 > ext.y1 && bb.y1 < ext.y2
    let minPx = Infinity; let drawn = 0; let hidden = 0; const boxes = []
    c.elements().forEach((e) => {
      if (!e.visible() || !e.style('label')) return
      const px = parseFloat(e.style('font-size')) * z
      if (px < (parseFloat(e.style('min-zoomed-font-size')) || 0)) { hidden++; return }
      const bb = e.boundingBox({ includeNodes: false, includeEdges: false, includeLabels: true, includeOverlays: false })
      if (!bb.w || !inView(bb)) return
      drawn++; minPx = Math.min(minPx, px)
      if (e.isNode()) boxes.push(bb)
    })
    let ov = 0
    boxes.forEach((a, i) => { if (boxes.some((b, j) => i !== j && a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2)) ov++ })
    const ltr = c.edges().filter((e) => e.source().position().x < e.target().position().x).length
    Object.assign(r, {
      zoom: +z.toFixed(3),
      labels: { drawn, hidden, minPx: drawn ? +minPx.toFixed(1) : null, overlapPct: boxes.length ? +(100 * ov / boxes.length).toFixed(1) : 0 },
      top: c.nodes().filter((n) => !n.isChild() && !n.hasClass('lane')).length,
      single: c.nodes(':parent').filter((p) => p.children().length === 1).length,
      clusters: c.nodes('.cluster').length,
      edges: c.edges().length,
      ltrPct: c.edges().length ? +(100 * ltr / c.edges().length).toFixed(1) : null,
      targetsVisible: c.nodes('.target, .entry').filter((n) => inView(n.boundingBox())).length + '/' + c.nodes('.target, .entry').length,
      fitAll: document.getElementById('fitall').hidden ? null : document.getElementById('fitall').textContent,
      status: document.getElementById('status').innerText.slice(0, 160)
    })
    return r
  }, MIN_PX)
}

// performance budgets (#82 item 15): first graph <= 1.5 s for <= 300 nodes, layout <= 1.5 s, <= 400 rendered elements
const BUDGET = { firstGraphMs: Number(process.env.BUDGET_FIRST || 1500), layoutMs: 1500, rendered: 400 }
function check (m, s) {
  const f = []
  if (m.perf && m.perf.nodes && m.perf.nodes <= 300 && m.tReady > BUDGET.firstGraphMs) f.push(`first graph ${m.tReady} ms > ${BUDGET.firstGraphMs}`)
  if (m.perf && m.perf.layoutMs > BUDGET.layoutMs) f.push(`layout ${m.perf.layoutMs} ms > ${BUDGET.layoutMs}`)
  if (m.perf && m.perf.rendered > BUDGET.rendered) f.push(`rendered elements ${m.perf.rendered} > ${BUDGET.rendered}`)
  if (m.logs.length) f.push(`console: ${m.logs.length} (${m.logs[0]})`)
  if (!['1', 'landing'].includes(m.ready)) f.push(`view reported ${m.ready}`)
  if (m.header.scroll > m.header.client) f.push(`header overflow ${m.header.scroll} > ${m.header.client}`)
  if (m.clipped.length) f.push('clipped: ' + m.clipped.join(', '))
  if (m.wrapped.length) f.push('wrapped: ' + m.wrapped.join(', '))
  if (m.width >= 1280 && m.header.height > MAX_HEADER) f.push(`header height ${m.header.height}`)
  if (m.labels) {
    if (m.labels.minPx !== null && m.labels.minPx < MIN_PX) f.push(`label ${m.labels.minPx} px < ${MIN_PX}`)
    if (m.labels.overlapPct > MAX_OVERLAP) f.push(`label overlap ${m.labels.overlapPct} % > ${MAX_OVERLAP}`)
    if (m.single) f.push(`${m.single} single-node compound(s)`)
    if (s.maxTop && m.top > s.maxTop) f.push(`top-level items ${m.top} > ${s.maxTop}`)
  }
  return f
}

const browser = await puppeteer.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless: true,
  args: ['--no-sandbox', '--disable-gpu'] })
const results = []
try {
  for (const s of SHOTS) {
    if (only && !s.file.includes(only)) continue
    for (const w of WIDTHS) {
      const page = await browser.newPage()
      const logs = []
      page.on('console', (m) => { if (['error', 'warn', 'warning'].includes(m.type())) logs.push(`console.${m.type()}: ${m.text()}`) })
      page.on('pageerror', (e) => logs.push('pageerror: ' + e.message))
      page.on('response', (r) => { if (r.status() >= 400) logs.push(`http ${r.status()}: ${r.url()}`) })
      await page.setViewport({ width: w, height: HEIGHT[w] || Math.round(w * 0.5625), deviceScaleFactor: DPR })
      if (process.env.THEME) await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: process.env.THEME }])
      // long tasks (main thread blocked > 50 ms) from the first byte on (#82 item 15)
      await page.evaluateOnNewDocument(() => {
        window.__cgLong = []
        try { new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__cgLong.push(e.duration) }).observe({ type: 'longtask', buffered: true }) } catch (e) { /* unsupported */ }
      })
      const t0 = Date.now()
      await page.goto(s.url || (base + (s.hash ? '#' + s.hash : '')), { waitUntil: 'networkidle0' })
      await page.waitForFunction(() => ['1', 'error', 'empty', 'landing'].includes(document.body.dataset.ready), { timeout: 60000 })
      const tReady = Date.now() - t0
      for (const a of s.actions || []) {
        if (a.expandFirstCluster) await page.evaluate(() => { const n = window.__cgCy.nodes('.cluster').not('.open')[0]; if (n) n.emit('tap') })
        if (a.tap) await page.evaluate((id) => window.__cgCy.getElementById(id).emit('tap'), a.tap)
        if (a.key) await page.keyboard.press(a.key)
        if (a.wait) await new Promise((r) => setTimeout(r, a.wait))
      }
      await new Promise((r) => setTimeout(r, 1000))  // last render + panel fetch
      const file = `${out}/${s.file.replace(/\.png$/, '')}_${w}.png`
      await page.screenshot({ path: file })
      const perf = await page.evaluate(() => ({ layoutMs: Math.round(window.__cgLayoutMs || 0), maxLongTaskMs: Math.round(Math.max(0, ...(window.__cgLong || []))),
        rendered: window.__cgCy ? window.__cgCy.elements(':visible').length : 0, nodes: window.__cgCy ? window.__cgCy.nodes().length : 0 }))
      const m = { file, width: w, tReady, perf, ...(await measure(page)), logs }
      m.failures = check(m, s)
      results.push(m)
      console.log(`${m.failures.length ? 'FAIL' : 'ok  '} ${file}` + (m.perf && m.perf.nodes ? `  ready ${m.tReady} ms layout ${m.perf.layoutMs} ms longtask ${m.perf.maxLongTaskMs} ms els ${m.perf.rendered}` : '') + (m.labels ? `  zoom ${m.zoom} label>=${m.labels.minPx}px overlap ${m.labels.overlapPct}% top ${m.top} ltr ${m.ltrPct}%` : m.landing ? `  landing stats ${m.landing.stats} cards ${m.landing.cards}` : '') +
        (m.failures.length ? '\n     ' + m.failures.join('\n     ') : ''))
      await page.close()
    }
  }
} finally { await browser.close() }
writeFileSync(`${out}/metrics.json`, JSON.stringify(results, null, 1))
const failed = results.filter((r) => r.failures.length).length
console.log(`${results.length} shots, ${failed} failed; metrics in ${out}/metrics.json`)
if (failed && process.env.NO_ASSERT !== '1') process.exit(1)
