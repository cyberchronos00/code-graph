// Visual-view demo of code-graph, recorded with Playwright (1920x1080 viewport + video).
// Usage: node scripts/demo/record-view.mjs <base-url> <video-dir>     (normally run via scripts/demo/record-view.sh)
// Uses the system Chrome (CHROME env, default /usr/bin/google-chrome); playwright-core records video with its own
// ffmpeg (`npx playwright-core install ffmpeg` once). Only reads the read-only `cg serve` view.
import { chromium } from 'playwright-core'

const base = process.argv[2] || 'http://127.0.0.1:8199/'
const videoDir = process.argv[3] || 'out/demo/view-video'
const W = 1920; const H = 1080
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// Injected into the page before any app script: a seeded Math.random (the clustered layout is randomized, so this
// makes every recording draw the same picture), a visible cursor that follows the real mouse events, a click ripple,
// and a small caption chip. These are demo-only overlays; the app itself is unchanged.
const INIT = () => {
  let seed = 20240917
  Math.random = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296 }
  const css = `
    #demo-cursor { position: fixed; left: 0; top: 0; width: 30px; height: 30px; z-index: 2147483647; pointer-events: none;
      transform: translate(-100px, -100px); filter: drop-shadow(0 2px 3px rgba(0,0,0,.35)); }
    #demo-cursor svg { position: absolute; left: 0; top: 0; transition: transform .12s ease-out; transform-origin: 3px 3px; }
    #demo-cursor.down svg { transform: scale(.85); }
    .demo-ripple { position: fixed; z-index: 2147483646; pointer-events: none; width: 44px; height: 44px; margin: -22px 0 0 -22px;
      border-radius: 50%; border: 3px solid #3d6be0; background: rgba(61,107,224,.18); animation: demo-ripple .6s ease-out forwards; }
    @keyframes demo-ripple { from { transform: scale(.3); opacity: 1 } to { transform: scale(1.5); opacity: 0 } }
    #demo-caption { position: fixed; left: 24px; bottom: 74px; z-index: 2147483645; pointer-events: none; max-width: 900px;
      background: rgba(29,35,48,.92); color: #fff; font: 600 22px/1.3 -apple-system, "Segoe UI", Roboto, "Noto Sans", sans-serif;
      padding: 12px 18px; border-radius: 10px; box-shadow: 0 6px 20px rgba(0,0,0,.25); opacity: 0; transition: opacity .35s; }
    #demo-caption.on { opacity: 1; }
    #demo-caption small { display: block; font-weight: 400; font-size: 16px; color: #c9d3e6; margin-top: 2px; }`
  const add = () => {
    if (document.getElementById('demo-cursor')) return
    const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st)
    const c = document.createElement('div'); c.id = 'demo-cursor'
    c.innerHTML = '<svg width="30" height="30" viewBox="0 0 30 30"><path d="M3 2 L3 24 L9 18.5 L13 27 L17 25.2 L13 16.8 L21 16.8 Z" ' +
      'fill="#fff" stroke="#111" stroke-width="1.8" stroke-linejoin="round"/></svg>'
    document.body.appendChild(c)
    const cap = document.createElement('div'); cap.id = 'demo-caption'; document.body.appendChild(cap)
    const mv = (e) => { c.style.transform = `translate(${e.clientX}px, ${e.clientY}px)` }
    window.addEventListener('mousemove', mv, true)
    window.addEventListener('mousedown', (e) => {
      mv(e); c.classList.add('down')
      const r = document.createElement('div'); r.className = 'demo-ripple'; r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px'
      document.body.appendChild(r); setTimeout(() => r.remove(), 700)
    }, true)
    window.addEventListener('mouseup', () => c.classList.remove('down'), true)
    window.__demoCaption = (title, sub) => {
      cap.innerHTML = title ? title + (sub ? `<small>${sub}</small>` : '') : ''
      cap.classList.toggle('on', !!title)
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add()
}

const browser = await chromium.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--font-render-hinting=none'] })
const context = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1, locale: 'en-US',
  timezoneId: 'UTC', recordVideo: { dir: videoDir, size: { width: W, height: H } } })
await context.addInitScript(INIT)
const page = await context.newPage()

let mx = W / 2; let my = H / 2
// Smooth, eased cursor glide (Playwright's own steps are dispatched instantly, so pace them here).
async function glide (x, y, ms = 800) {
  const sx = mx; const sy = my; const n = Math.max(12, Math.round(ms / 16))
  for (let i = 1; i <= n; i++) {
    const t = i / n; const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2
    await page.mouse.move(sx + (x - sx) * e, sy + (y - sy) * e)
    await sleep(ms / n)
  }
  mx = x; my = y
}
async function click (x, y, ms) { await glide(x, y, ms); await sleep(250); await page.mouse.down(); await sleep(90); await page.mouse.up() }
async function center (sel) { const b = await page.locator(sel).boundingBox(); return [b.x + b.width / 2, b.y + b.height / 2] }
const caption = (t, s) => page.evaluate(([a, b]) => window.__demoCaption(a, b), [t, s || ''])
const ready = () => page.waitForFunction(() => document.body.dataset.ready === '1', null, { timeout: 30000 })
// Screen position of a graph node (read-only access to the Cytoscape instance that owns the canvas).
async function nodeXY (id) {
  return page.evaluate((nid) => {
    const box = document.getElementById('cy'); const cy = box._cyreg.cy; const n = cy.getElementById(nid)
    if (!n.length) throw new Error('node not in view: ' + nid)
    const p = n.renderedPosition(); const r = box.getBoundingClientRect(); return [r.left + p.x, r.top + p.y]
  }, id)
}

await page.goto(base, { waitUntil: 'networkidle' })
await page.mouse.move(mx, my)
await sleep(700)

// 1. Search: type a spec into the search box (reaches mode is the default) and run it.
await caption('Search', 'who depends on the warehouse connection and its table?')
const [sx, sy] = await center('#spec')
await click(sx - 300, sy, 900)
await sleep(300)
await page.locator('#spec').pressSequentially('connection:warehouse, table:warehouse_stock', { delay: 85 })
await sleep(600)
const [gx, gy] = await center('#go')
await click(gx, gy, 700)
await ready()
await sleep(2600)

// 2. Select a node: its docblock, file:line and source snippet open on the right, and its evidence paths light up.
await caption('Select a node', 'its evidence paths are highlighted; source and file:line evidence on the right')
const [nx, ny] = await nodeXY('method:App\\Services\\StockService::reserve')
await click(nx, ny, 1100)
await sleep(2700)
const [ox, oy] = await nodeXY('method:App\\Console\\Commands\\SyncWarehouseCommand::handle')
await click(ox, oy, 1000)
await sleep(2200)

// 3. Planned-change overlay: switch the query to the 'preorders' plan.
await caption('Planned-change overlay', 'plan "preorders" drawn on the real graph: green = planned, magenta = missing from plan, red = forbidden path')
const [mdx, mdy] = await center('#mode')
await click(mdx, mdy, 900)
await page.locator('#mode').selectOption('plan')
await sleep(500)
await click(sx - 300, sy, 700)
await page.locator('#spec').fill('')
await page.locator('#spec').pressSequentially('preorders', { delay: 95 })
await sleep(400)
await page.keyboard.press('Enter')
await ready()
await sleep(2600)
const [fx, fy] = await nodeXY('property:App\\Models\\Book::$fillable')
await click(fx, fy, 1100)
await sleep(3000)
const [ux, uy] = await nodeXY('method:App\\Http\\Controllers\\Admin\\BookController::update')
await click(ux, uy, 900)
await sleep(3000)
await caption('')
await sleep(700)

const video = page.video()
await context.close()
await browser.close()
console.log(await video.path())
