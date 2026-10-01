// Headless screenshots of the code-graph view. Usage: node shoot.mjs <base-url> <out-dir>
// Uses the system Chrome (CHROME env, default /usr/bin/google-chrome); the view must be served (codegraph.cli serve).
import puppeteer from 'puppeteer-core'
import { mkdirSync, readFileSync } from 'node:fs'

const base = process.argv[2] || 'http://127.0.0.1:8177/'
const out = process.argv[3] || 'screenshots'
mkdirSync(out, { recursive: true })
const enc = (s) => encodeURIComponent(s)
// Default shots match the bundled sample apps (examples/); pass SHOTS=path/to/shots.json ([{file, hash}]) for your project.
const DEFAULT_SHOTS = [
  { file: 'sample_reaches_warehouse.png', hash: `mode=reaches&spec=${enc('connection:warehouse')}&spec=${enc('table:warehouse_stock')}&select=${enc('connection:warehouse')}&hl=0` },
  { file: 'sample_report_page_downstream_tables.png', hash: `mode=downstream&spec=${enc('page:/reports/:id')}&sinks=${enc('table,column')}&select=${enc('page:app/pages/reports/[id].vue')}&hl=0` },
  { file: 'sample_report_page_path_orders.png', hash: `mode=path&spec=${enc('page:/reports/:id')}&spec=${enc('ReportController::top')}&spec=${enc('table:orders')}&layout=breadthfirst` +
    `&select=${enc('method:App\\Services\\SalesReportService::build')}` },
  { file: 'sample_plan_preorders_overview.png', hash: `mode=plan&spec=preorders` },
  { file: 'sample_timezone_request_key_reaches.png', hash: `mode=reaches&spec=${enc('request_key:timezone')}` }
]
const SHOTS = process.env.SHOTS ? JSON.parse(readFileSync(process.env.SHOTS, 'utf8')) : DEFAULT_SHOTS
const only = process.env.ONLY
const browser = await puppeteer.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--window-size=1920,1080'] })
try {
  for (const s of SHOTS) {
    if (only && !s.file.includes(only)) continue
    const page = await browser.newPage()
    await page.setViewport({ width: 1680, height: 1050, deviceScaleFactor: 1.5 })
    await page.goto(base + '#' + s.hash, { waitUntil: 'networkidle0' })
    await page.waitForFunction(() => ['1', 'error', 'empty'].includes(document.body.dataset.ready), { timeout: 60000 })
    const st = await page.evaluate(() => document.body.dataset.ready)
    if (st !== '1') throw new Error(`${s.file}: view reported ${st}`)
    await new Promise((r) => setTimeout(r, 1200))  // last render + panel fetch
    await page.screenshot({ path: `${out}/${s.file}` })
    console.log(`${out}/${s.file}`)
    await page.close()
  }
} finally { await browser.close() }
