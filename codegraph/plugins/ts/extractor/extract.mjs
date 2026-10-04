// TypeScript / Vue SFC fact extractor for code-graph.
// Uses the TypeScript compiler API (exact symbol resolution with the project's tsconfig, including
// Nuxt's generated .nuxt types for auto-imports) and @vue/compiler-sfc for .vue files.
//
// .vue files become virtual "<file>.vue.ts" sources: script blocks are kept at their original
// offsets (everything else blanked, so line numbers stay 1:1), and template expressions /
// component tags are appended as stub functions with a line map back to the template.
//
// Usage: node extract.mjs --config cfg.json   (cfg: {root, tsconfig, components_dts, kinds, out})
import fs from 'node:fs'
import path from 'node:path'
import ts from 'typescript'
import { parse as parseSFC } from '@vue/compiler-sfc'
import { collectFrameworkFacts } from './fw.mjs'

const t0 = Date.now()
const cfg = JSON.parse(fs.readFileSync(process.argv[process.argv.indexOf('--config') + 1], 'utf8'))
const ROOT = path.resolve(cfg.root)
const rel = p => path.relative(ROOT, p).split(path.sep).join('/')
const HTTP_METHODS = new Set(['get', 'post', 'put', 'patch', 'delete', 'head', 'options'])
const FETCH_FNS = new Set(['$fetch', 'useFetch', 'useLazyFetch', 'ofetch', 'fetch'])
// realtime clients (#32): `new WebSocket(url)` -> http:WS, `new EventSource(url)` -> http:GET with stream sse
const WS_CTORS = new Map([['WebSocket', 'websocket'], ['ReconnectingWebSocket', 'reconnecting-websocket'], ['ReconnectingWebsocket', 'reconnecting-websocket'],
  ['Sockette', 'sockette'], ['EventSource', 'eventsource'], ['EventSourcePolyfill', 'eventsource'], ['NativeEventSource', 'eventsource'],
  ['ReconnectingEventSource', 'eventsource']])
const I18N_FNS = new Set(['t', '$t', 'te', '$te', 'tm', 'rt'])
const SKIP_FILE = /(\.test|\.spec)\.(ts|js|mts)$/
// directory rules from the presets and .cg.yaml (codegraph/core/paths.py PathRules.extractor_cfg): names the walks
// skip, names kept (hidden directories are skipped unless kept), `include` directories walked although a skip rule
// covers them, and the directories whose files the tsconfig pulls in as resolution input only (node_modules, .nuxt)
const SKIP_NAMES = new Set(cfg.skip_names || [])
const KEEP_NAMES = new Set(cfg.keep_names || [])
const TEST_SKIP_NAMES = new Set([...SKIP_NAMES, ...(cfg.test_skip_names || [])])
const INCLUDE = (cfg.include || []).map(i => i.replace(/^\/+|\/+$/g, '')).filter(Boolean)
const included = r => INCLUDE.some(i => r === i || r.startsWith(i + '/'))
const onIncludePath = r => included(r) || INCLUDE.some(i => i.startsWith(r + '/'))
const esc = x => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const SOURCE_SKIP_RE = (cfg.source_skip_names || []).length ? new RegExp('(^|/)(' + cfg.source_skip_names.map(esc).join('|') + ')/') : null
// a file below a directory the walks skip by name (dist/, coverage/, node_modules/, .nuxt/ ...) is not project
// source, also when the tsconfig lists it, unless it is inside an `include` directory
const belowSkipped = r => !included(r) && r.split('/').slice(0, -1).some(x => SKIP_NAMES.has(x))
const sourceSkipped = r => belowSkipped(r) || (!!(SOURCE_SKIP_RE && SOURCE_SKIP_RE.test(r)) && !included(r))
// a directory entry of a walk: skipped by name / as hidden, unless it is on the way to an `include` directory;
// `forced`: the parent is skipped by itself and only walked to reach an include directory
const skipDir = (name, r, names, forced) => !onIncludePath(r) && (forced || names.has(name) || (name.startsWith('.') && !KEEP_NAMES.has(name)))
const forcedBelow = (name, r, names, forced) => !included(r) && (forced || names.has(name) || (name.startsWith('.') && !KEEP_NAMES.has(name)))
// framework plugins may exclude more (tests, build output) by a regex over the repo-relative path
const SKIP_REL = cfg.skip_re ? new RegExp(cfg.skip_re) : null
// the project's .cg.yaml exclude globs and skip_dirs.add names: never indexed, test code included
const EXCLUDE_REL = cfg.exclude_re ? new RegExp(cfg.exclude_re) : null
// generated / copied / vendored files the Python-side scan classified (codegraph/core/generated.py)
const EXCLUDE_FILES = new Set(cfg.exclude_files || [])
// directories the generated-file classifier excludes as a whole (not inside an `include` directory)
const GENERATED_REL = cfg.generated_re ? new RegExp(cfg.generated_re) : null
const excludedRel = r => !!(EXCLUDE_REL && EXCLUDE_REL.test(r)) || (!included(r.replace(/\/$/, '')) && (!!(GENERATED_REL && GENERATED_REL.test(r)) || EXCLUDE_FILES.has(r)))
const SRC_EXT = /\.(ts|tsx|mts|cts|js|jsx|mjs|cjs)$/
// test code (Vitest / Jest / Playwright / Cypress): indexed as test nodes unless cfg.index_tests === false; kept out of
// the application graph by the indexer (TEST_* edges)
const INDEX_TESTS = cfg.index_tests !== false
const TEST_FILE_RE = /\.(test|spec|e2e-spec|e2e|cy)\.(t|j)sx?$/
const TEST_DIR_RE = /(^|\/)(__tests__|e2e|tests?|cypress|playwright)\//
const TEST_ROOTS = ['e2e', 'test', 'tests', 'cypress', 'playwright', 'src/test', 'src/tests', 'src/e2e']
const isTestRel = r => TEST_FILE_RE.test(r) || /(^|\/)__tests__\//.test(r) || TEST_ROOTS.some(d => r.startsWith(d + '/'))

// ---------- file kinds (from the framework plugin: [[prefix, kind], ...]) ----------
function fileKind(r) {
  for (const [prefix, kind] of cfg.kinds || []) if (r.startsWith(prefix)) return kind
  return r.endsWith('.vue') ? 'component' : 'module'
}

// ---------- global components (Nuxt .nuxt/types/components.d.ts) ----------
const globalComponents = {}
if (cfg.components_dts && fs.existsSync(cfg.components_dts)) {
  const txt = fs.readFileSync(cfg.components_dts, 'utf8')
  const re = /^\s*'?(\w+)'?: (?:LazyComponent<)?typeof import\("([^"]+)"\)/gm
  let m
  while ((m = re.exec(txt))) {
    if (m[1].startsWith('Lazy') && globalComponents[m[1].slice(4)]) { globalComponents[m[1]] = globalComponents[m[1].slice(4)]; continue }
    globalComponents[m[1]] = path.resolve(path.dirname(cfg.components_dts), m[2])
  }
}
const pascal = s => s.split('-').map(x => x ? x[0].toUpperCase() + x.slice(1) : '').join('')

// ---------- tsconfig ----------
// tsconfig.json, else jsconfig.json; a solution-style config (`files: []` + references) uses its referenced
// configs; with no config at all (plain JS projects) the source dirs are walked with allowJs defaults.
function parseConfig(p) {
  try { return ts.parseJsonConfigFileContent(ts.readConfigFile(p, ts.sys.readFile).config || {}, ts.sys, path.dirname(p), undefined, p) } catch { return null }
}
let tsconfigPath = path.resolve(ROOT, cfg.tsconfig || 'tsconfig.json')
if (!fs.existsSync(tsconfigPath) && fs.existsSync(path.join(ROOT, 'jsconfig.json'))) tsconfigPath = path.join(ROOT, 'jsconfig.json')
let parsed = fs.existsSync(tsconfigPath) ? parseConfig(tsconfigPath) : null
if (parsed && !parsed.fileNames.length && parsed.projectReferences && parsed.projectReferences.length) {
  for (const ref of parsed.projectReferences) {
    let rp = ref.path
    if (fs.existsSync(rp) && fs.statSync(rp).isDirectory()) rp = path.join(rp, 'tsconfig.json')
    const sub = fs.existsSync(rp) ? parseConfig(rp) : null
    if (sub && sub.fileNames.length) { parsed = { ...sub, fileNames: [...new Set([...parsed.fileNames, ...sub.fileNames])], options: { ...sub.options, ...parsed.options, paths: parsed.options.paths || sub.options.paths, baseUrl: parsed.options.baseUrl || sub.options.baseUrl, pathsBasePath: parsed.options.pathsBasePath || sub.options.pathsBasePath } } }
  }
}
// no root config, but per-package tsconfigs (a lerna / nx / workspaces monorepo; the Python side lists them): one
// program over all their files, each package's `paths` kept with absolute targets
const pkgDirs = []
if (!parsed && Array.isArray(cfg.package_tsconfigs) && cfg.package_tsconfigs.length) {
  const names = new Set(), paths = {}
  let base = null
  for (const r of cfg.package_tsconfigs) {
    const p = path.resolve(ROOT, r)
    const sub = fs.existsSync(p) ? parseConfig(p) : null
    if (!sub || !sub.fileNames.length) continue
    pkgDirs.push(path.dirname(p))
    for (const f of sub.fileNames) names.add(f)
    const pb = sub.options.pathsBasePath || sub.options.baseUrl || path.dirname(p)
    for (const [k, v] of Object.entries(sub.options.paths || {})) if (!paths[k]) paths[k] = v.map(x => path.resolve(pb, x))
    if (!base) base = sub
  }
  if (base) {
    const o = { ...base.options }
    for (const k of ['rootDir', 'outDir', 'declarationDir', 'composite', 'baseUrl', 'paths', 'pathsBasePath', 'tsBuildInfoFile']) delete o[k]
    if (Object.keys(paths).length) { o.paths = paths; o.pathsBasePath = ROOT }
    parsed = { options: o, fileNames: [...names], packageConfigs: cfg.package_tsconfigs.length }
    tsconfigPath = path.join(ROOT, 'tsconfig.json')
  }
}
const noConfig = !parsed
if (!parsed) parsed = { options: {}, fileNames: [] }
const options = { ...parsed.options, noEmit: true, skipLibCheck: true }
if (noConfig || cfg.allow_js) Object.assign(options, { allowJs: true, checkJs: false, maxNodeModuleJsDepth: 0 })
if (noConfig) Object.assign(options, { jsx: ts.JsxEmit.Preserve, module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler,
  target: ts.ScriptTarget.ESNext, resolveJsonModule: true, esModuleInterop: true, allowSyntheticDefaultImports: true })
if (options.moduleResolution === ts.ModuleResolutionKind.Classic) options.moduleResolution = ts.ModuleResolutionKind.Bundler
// React Native / Expo platform files (storage.ios.ts, storage.android.ts): `import './storage'` resolves the way Metro
// does (the project's own tsconfig moduleSuffixes win); the Python side links the sibling variants
if (cfg.platform_suffixes && !options.moduleSuffixes) options.moduleSuffixes = cfg.platform_suffixes
const pathsBase = options.pathsBasePath || options.baseUrl || path.dirname(tsconfigPath)
// local packages (package.json `"x": "file:./libraries/x"` / `link:` dependencies, workspace packages) with no installed
// node_modules link: imports of them resolve to the package source, as the package manager's symlink would
const localPkgs = []
{
  const readJson = f => { try { return JSON.parse(fs.readFileSync(f, 'utf8')) } catch { return null } }
  const pj = readJson(path.join(ROOT, 'package.json'))
  const dirs = []
  if (pj) {
    for (const [name, spec] of Object.entries({ ...(pj.dependencies || {}), ...(pj.devDependencies || {}) })) {
      const m = typeof spec === 'string' && spec.match(/^(?:file|link):(.+)$/)
      if (m) dirs.push([name, path.resolve(ROOT, m[1])])
    }
    const ws = Array.isArray(pj.workspaces) ? pj.workspaces : ((pj.workspaces || {}).packages || [])
    for (const w of ws) {
      if (typeof w !== 'string' || !/^[\w@./-]+(\/\*)?$/.test(w)) continue
      const base = path.resolve(ROOT, w.replace(/\/\*$/, ''))
      let subs = [base]
      if (w.endsWith('/*')) { try { subs = fs.readdirSync(base, { withFileTypes: true }).filter(e => e.isDirectory()).map(e => path.join(base, e.name)) } catch { subs = [] } }
      for (const d of subs) { const p = readJson(path.join(d, 'package.json')); if (p && p.name) dirs.push([p.name, d]) }
    }
  }
  for (const [name, dir] of dirs) {
    if (fs.existsSync(path.join(ROOT, 'node_modules', name)) || !dir.startsWith(ROOT + path.sep)) continue
    const p = readJson(path.join(dir, 'package.json'))
    if (!p) continue
    const cands = [p['react-native'], p.source, p.main, 'src/index', 'index'].filter(e => typeof e === 'string')
      .map(e => path.resolve(dir, e).replace(/\.(js|jsx|ts|tsx|mjs|cjs)$/, ''))
    const hit = cands.find(c => ['.ts', '.tsx', '.js', '.jsx'].some(x => fs.existsSync(c + x)))
    if (!hit) continue
    options.paths = { ...(options.paths || {}) }
    if (!options.baseUrl && !options.pathsBasePath) options.pathsBasePath = ROOT
    if (!options.paths[name]) options.paths[name] = [hit]
    if (!options.paths[name + '/*']) options.paths[name + '/*'] = [dir + '/*']
    localPkgs.push(name)
  }
}

// symlinks: a link to a file is indexed, a link to a directory is not followed, a dangling link (e.g. to a file
// outside the checkout) is skipped with a warning; an unreadable directory is skipped, never fatal
const skippedLinks = []
function fileEntry(e, p) {
  if (e.isFile()) return true
  if (!e.isSymbolicLink()) return false
  try { return fs.statSync(p).isFile() } catch (err) {
    skippedLinks.push(rel(p)); process.stderr.write(`codegraph: skipping ${rel(p)}: dangling symlink\n`); return false
  }
}
function walkDir(d, out, forced = false) {
  let ents = []
  try { ents = fs.readdirSync(d, { withFileTypes: true }) } catch (err) { process.stderr.write(`codegraph: skipping ${d}: ${err.code || err}\n`); return out }
  for (const e of ents) {
    const p = path.join(d, e.name), r = rel(p)
    if (e.isDirectory() ? skipDir(e.name, r, SKIP_NAMES, forced) : (forced && !included(r))) continue
    if ((SKIP_REL && SKIP_REL.test(r)) || excludedRel(r) || (e.isDirectory() && excludedRel(r + '/'))) continue
    if (e.isDirectory()) walkDir(p, out, forcedBelow(e.name, r, SKIP_NAMES, forced))
    else if (fileEntry(e, p)) out.push(p)
  }
  return out
}
let srcDirs = (cfg.src_dirs || ['app']).map(d => path.resolve(ROOT, d)).filter(fs.existsSync)
// none of the conventional dirs holds code the tsconfig includes: use the top dirs of the tsconfig's own files
{
  const inside = f => srcDirs.some(d => f.startsWith(d + path.sep))
  const own = (parsed.fileNames || []).filter(f => !sourceSkipped(path.relative(ROOT, f).split(path.sep).join('/')) && !f.endsWith('.d.ts'))
  if (parsed.packageConfigs && pkgDirs.length) {
    // each package directory is a source dir: files next to its tsconfig that `files` / `include` leave out (a
    // bridge script bundled by its own rollup config) are code of the package too
    srcDirs = pkgDirs
    process.stderr.write(`codegraph: source dirs from package tsconfigs: ${pkgDirs.map(rel).join(', ')}\n`)
  } else if (own.length && !own.some(inside)) {
    const tops = new Set(own.map(f => { const r = path.relative(ROOT, f).split(path.sep); return r.length > 1 ? r.slice(0, Math.min(r.length - 1, 3)).join(path.sep) : '.' }))
    const dirs = [...tops].sort((a, b) => a.length - b.length).filter((d, i, all) => !all.slice(0, i).some(p => p === '.' || d.startsWith(p + path.sep)))
    srcDirs = dirs.map(d => path.resolve(ROOT, d))
    process.stderr.write(`codegraph: source dirs from tsconfig: ${dirs.join(', ')}\n`)
  }
}
// package.json entry points: with no conventional source dir and no config files to take them from (a plain JS
// package: lib/ + bin/, no tsconfig), main / module / bin / exports and the `files` entries name the source (an entry
// in a sub directory makes that top directory a source dir, one at the root is a source file of its own); next to
// source dirs, a `bin` script outside them (bin/cli.js beside src/) is a source file, so tests that run it have a
// node to link to (#94)
const srcFiles = new Set()
let pkgSrc = false
{
  let pkg = null
  try { pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8')) } catch { pkg = null }
  if (!pkg || typeof pkg !== 'object') pkg = {}
  const strings = v => typeof v === 'string' ? [v] : Array.isArray(v) ? v.flatMap(strings) : v && typeof v === 'object' ? Object.values(v).flatMap(strings) : []
  const usable = e => {
    const r = rel(path.resolve(ROOT, e.split(/[*?{[]/)[0])).replace(/\/$/, '')
    return r && !r.startsWith('..') && !path.isAbsolute(r) && !sourceSkipped(r) && !excludedRel(r) && fs.existsSync(path.resolve(ROOT, r)) ? r : null
  }
  const isSrc = r => SRC_EXT.test(r) && !r.endsWith('.d.ts') && fs.statSync(path.resolve(ROOT, r)).isFile()
  if (!srcDirs.length) {
    const dirs = new Set()
    for (const e of strings([pkg.main, pkg.module, pkg.bin, pkg.exports, pkg.files])) {
      const r = usable(e)
      if (!r) continue
      const segs = r.split('/'), top = path.resolve(ROOT, segs[0])
      if (segs.length > 1 || fs.statSync(top).isDirectory()) {
        if (fs.statSync(top).isDirectory() && !skipDir(segs[0], segs[0], SKIP_NAMES, false)) dirs.add(top)
      } else if (isSrc(r)) srcFiles.add(path.resolve(ROOT, r))
    }
    srcDirs = [...dirs].sort()
    pkgSrc = srcDirs.length > 0
  } else {
    for (const e of strings(pkg.bin)) {
      const r = usable(e), p = r && path.resolve(ROOT, r)
      if (r && isSrc(r) && !srcDirs.some(d => p.startsWith(d + path.sep)) && !belowSkipped(r)) srcFiles.add(p)
    }
  }
  if (pkgSrc || srcFiles.size) {
    process.stderr.write(`codegraph: source from package.json: ${[...srcDirs, ...srcFiles].map(rel).join(', ')}\n`)
  }
}
// .cg.yaml include directories are source dirs too (generated sources a project wants in the graph)
for (const i of INCLUDE) {
  const p = path.resolve(ROOT, i)
  if (fs.existsSync(p) && fs.statSync(p).isDirectory() && !srcDirs.some(d => p === d || p.startsWith(d + path.sep))) srcDirs.push(p)
}
const allFiles = [...srcDirs.flatMap(d => walkDir(d, [])), ...srcFiles]
// test files: spec/test files anywhere in the source dirs, plus top-level test trees (e2e/, tests/, ...)
const testFiles = new Set()
function walkTests(d, all, forced = false) {
  let ents = []
  try { ents = fs.readdirSync(d, { withFileTypes: true }) } catch { return }
  for (const e of ents) {
    const p = path.join(d, e.name), r = rel(p)
    if (e.isDirectory() ? skipDir(e.name, r, TEST_SKIP_NAMES, forced) : (forced && !included(r))) continue
    if (excludedRel(r) || (e.isDirectory() && excludedRel(r + '/'))) continue
    if (e.isDirectory()) walkTests(p, all, forcedBelow(e.name, r, TEST_SKIP_NAMES, forced))
    else if (SRC_EXT.test(e.name) && (e.isFile() || (e.isSymbolicLink() && !skippedLinks.includes(rel(p)) && fileEntry(e, p))) && !e.name.endsWith('.d.ts') && (all || isTestRel(rel(p)))) testFiles.add(p)
  }
}
if (INDEX_TESTS) {
  for (const d of srcDirs) walkTests(d, false)
  for (const d of TEST_ROOTS) { const p = path.resolve(ROOT, d); if (fs.existsSync(p) && fs.statSync(p).isDirectory()) walkTests(p, true) }
}
// scripts the tests run in a subprocess (`spawnSync('node', [path.join(__dirname, '..', 'tools', 'gen.js')])`,
// `execSync('node tools/gen.js')`) outside every source dir are source files too, so the subprocess link
// (process_runs.py) has a module node to land on (#106). Literals in the call and in the last assignment of a name
// it passes are read, like process_runs does; scripts in test trees stay test code.
{
  const RUN_RX = /\b(?:spawn|spawnSync|execFile|execFileSync|fork|exec|execSync|execa|execaSync|execaNode|execaCommand|execaCommandSync)\s*\(/g
  const LIT_RX = /(['"`])((?:\\.|(?!\1)[^\\])*?)\1/gs
  const lits = t => [...t.matchAll(LIT_RX)].map(x => x[2])
  const LIT_AT = new RegExp(LIT_RX.source, 'ys')
  const argsText = (src, start) => {
    let depth = 0
    for (let i = start, n = Math.min(src.length, start + 600); i < n; i++) {
      const c = src[i]
      if ('([{'.includes(c)) depth++
      else if (')]}'.includes(c) && --depth === 0) return src.slice(start + 1, i)
      else if ('\'"`'.includes(c)) { LIT_AT.lastIndex = i; const m = LIT_AT.exec(src); if (m) i = m.index + m[0].length - 1 }
    }
    return src.slice(start + 1, start + 600)
  }
  const found = new Set()
  const consider = (tf, p) => {
    const r = rel(p)
    if (!r || r.startsWith('..') || path.isAbsolute(r) || !SRC_EXT.test(r) || r.endsWith('.d.ts') || testFiles.has(p)) return false
    if (srcFiles.has(p) || srcDirs.some(d => p.startsWith(d + path.sep)) || isTestRel(r) || sourceSkipped(r) || excludedRel(r)) return false
    try { if (!fs.statSync(p).isFile()) return false } catch { return false }
    found.add(p)
    return true
  }
  for (const tf of testFiles) {
    let src
    try { src = fs.readFileSync(tf, 'utf8') } catch { continue }
    if (!/child_process|execa/.test(src)) continue
    for (const m of src.matchAll(RUN_RX)) {
      const args = argsText(src, m.index + m[0].length - 1)
      let ls = lits(args)
      for (const id of new Set(args.replace(LIT_RX, '').match(/(?<![\w.$])[A-Za-z_]\w*\b(?!\s*[(.])/g) || [])) {
        const am = [...src.slice(0, m.index).matchAll(new RegExp(`(?<![\\w.$])${id}\\s*=(?!=)\\s*([^;]+);`, 'g'))].pop()
        if (am) ls = ls.concat(lits(am[1]))
      }
      ls = ls.flatMap(x => /\s/.test(x.trim()) ? x.trim().split(/\s+/) : [x])
      ls.forEach((x, i) => {
        if (!SRC_EXT.test(x) || x.startsWith('-')) return
        const segs = ls.slice(Math.max(0, i - 3), i).filter(y => /^[\w.\-/]+$/.test(y) && !y.startsWith('-'))
        for (let k = segs.length; k >= 0; k--) {
          const t = [...segs.slice(segs.length - k), x].join('/').replace(/\\/g, '/')
          const cands = t.startsWith('./') || t.startsWith('../') ? [path.resolve(path.dirname(tf), t)] : []
          cands.push(path.resolve(ROOT, t.replace(/^\/+/, '')))
          if (cands.some(c => consider(tf, c))) break
        }
      })
    }
  }
  for (const p of found) srcFiles.add(p)
  if (found.size) process.stderr.write(`codegraph: scripts the tests run: ${[...found].map(rel).sort().join(', ')}\n`)
}
const vueFiles = allFiles.filter(f => f.endsWith('.vue'))
const vueSet = new Set(vueFiles)

// ---------- virtual sources for .vue ----------
const virtual = new Map()       // virtual path -> text
const lineMaps = new Map()      // virtual path -> {virtualLine(1-based): origLine}
const sfcInfo = new Map()
const sfcI18n = {}             // rel vue path -> keys defined in <i18n> blocks       // vue abs path -> {templateTags:[...], pageMeta}
const stats = { vue_files: vueFiles.length, ts_files: 0, template_stubs: 0, template_component_tags: 0,
  sfc_errors: 0, components_global: 0, components_imported: 0, components_external: 0, components_unknown: 0, unknown_tags: {} }

function blank(src, keep) {
  // keep: [[start,end]] offsets to preserve; everything else -> spaces (newlines kept)
  const out = src.split('')
  let k = 0
  keep.sort((a, b) => a[0] - b[0])
  for (let i = 0; i < out.length; i++) {
    while (k < keep.length && i >= keep[k][1]) k++
    const inKeep = k < keep.length && i >= keep[k][0] && i < keep[k][1]
    if (!inKeep && out[i] !== '\n' && out[i] !== '\r') out[i] = ' '
  }
  return out.join('')
}

function buildVirtualVue(file) {
  let src
  try { src = fs.readFileSync(file, 'utf8') } catch (e) { process.stderr.write(`codegraph: skipping ${rel(file)}: ${e.code || e}\n`); return '' }
  let d
  try { d = parseSFC(src, { filename: file }).descriptor } catch (e) { stats.sfc_errors++; return '' }
  const keep = []
  for (const b of [d.script, d.scriptSetup]) if (b) keep.push([b.loc.start.offset, b.loc.end.offset])
  let text = blank(src, keep)
  const baseLines = text.split('\n').length
  const stubs = []         // [code, origLine]
  const tags = []          // {tag, line, stubIndex}
  let n = 0
  const T = d.template && d.template.ast
  const scopeStack = []
  const pushStub = (code, line) => { stubs.push([code, line]); stats.template_stubs++ }
  const params = () => scopeStack.flat().map(p => `${p}: any`).join(', ')
  function exprStub(exp, line, isHandler) {
    if (!exp || !exp.trim()) return
    const ps = params()
    if (isHandler) pushStub(`function __tpl_${n++}(${ps}${ps ? ', ' : ''}$event: any) { ${exp} }`, line)
    else pushStub(`function __tpl_${n++}(${ps}) { return (${exp}) }`, line)
  }
  function visit(node) {
    if (!node) return
    if (node.type === 5) exprStub(node.content && node.content.content, node.loc.start.line, false) // {{ }}
    if (node.type !== 1 && node.type !== 0) return
    let pushed = 0
    if (node.type === 1) {
      const isComp = node.tagType === 1 || (/[A-Z]/.test(node.tag) || node.tag.includes('-'))
      if (isComp && node.tagType !== 2 && node.tagType !== 3) tags.push({ tag: node.tag, line: node.loc.start.line })
      for (const p of node.props) {
        if (p.type !== 7) continue
        const line = (p.exp && p.exp.loc && p.exp.loc.start.line) || p.loc.start.line
        if (p.name === 'for') {
          const fr = p.forParseResult
          if (fr) {
            exprStub(fr.source && fr.source.content, line, false)
            const vars = [fr.value, fr.key, fr.index].filter(Boolean).map(x => x.content)
            scopeStack.push(vars); pushed++
          }
        } else if (p.name === 'slot') {
          if (p.exp && p.exp.content) { scopeStack.push([p.exp.content]); pushed++ }
        } else if (p.name === 'on') {
          exprStub(p.exp && p.exp.content, line, true)
        } else if (p.exp) {
          exprStub(p.exp.content, line, false)
        }
        if (p.arg && p.arg.type === 4 && !p.arg.isStatic) exprStub(p.arg.content, line, false)
      }
    }
    for (const c of node.children || []) visit(c)
    while (pushed--) scopeStack.pop()
  }
  if (T) visit(T)
  // component tags: emit a reference stub so the checker resolves explicit imports exactly
  for (const tg of tags) {
    const name = pascal(tg.tag)
    tg.name = name
    tg.stub = stubs.length
    pushStub(`function __tplc_${n++}() { return typeof ${/^[A-Za-z_$][\w$]*$/.test(name) ? name : 'undefined'} }`, tg.line)
    stats.template_component_tags++
  }
  const lm = {}
  let out = text
  stubs.forEach(([code, line], i) => { lm[baseLines + 1 + i] = line; out += '\n' + code.replace(/\n/g, ' ') })
  const i18nKeys = []
  for (const cb of d.customBlocks || []) {
    if (cb.type !== 'i18n') continue
    try {
      let data = JSON.parse(cb.content)
      const ks = Object.keys(data)
      if (ks.length && ks.every(k => /^[a-z]{2}(-[A-Z]{2})?$/.test(k))) data = Object.assign({}, ...ks.map(k => data[k]))
      const flat = (o, p) => { for (const [k, v] of Object.entries(o)) { const q = p ? p + '.' + k : k; if (v && typeof v === 'object') flat(v, q); else i18nKeys.push(q) } }
      flat(data, '')
    } catch { stats.sfc_i18n_unparsed = (stats.sfc_i18n_unparsed || 0) + 1 }
  }
  if (i18nKeys.length) sfcI18n[rel(file)] = [...new Set(i18nKeys)]
  sfcInfo.set(file, { tags, baseLines, hasSetup: !!d.scriptSetup })
  const vp = file + '.ts'
  lineMaps.set(vp, lm)
  return out
}
for (const f of vueFiles) virtual.set(f + '.ts', buildVirtualVue(f))

// ---------- compiler host ----------
const host = ts.createCompilerHost(options, true)
const origGetSourceFile = host.getSourceFile.bind(host)
const origFileExists = host.fileExists.bind(host)
const origReadFile = host.readFile.bind(host)
host.getSourceFile = (fn, lang, onErr, should) => virtual.has(fn)
  ? ts.createSourceFile(fn, virtual.get(fn), lang, true, ts.ScriptKind.TS)
  : origGetSourceFile(fn, lang, onErr, should)
host.fileExists = fn => virtual.has(fn) || origFileExists(fn)
host.readFile = fn => virtual.has(fn) ? virtual.get(fn) : origReadFile(fn)

function resolveVuePath(spec, containing) {
  let cands = []
  if (spec.startsWith('.')) cands.push(path.resolve(path.dirname(containing), spec))
  else for (const [pat, targets] of Object.entries(options.paths || {})) {
    const star = pat.indexOf('*')
    if (star < 0 ? spec === pat : spec.startsWith(pat.slice(0, star))) {
      const rest = star < 0 ? '' : spec.slice(star)
      for (const t of targets) cands.push(path.resolve(pathsBase, t.replace('*', rest)))
    }
  }
  return cands.find(c => vueSet.has(c)) || null
}
host.resolveModuleNameLiterals = (lits, containing, redirected, opts) => lits.map(l => {
  const spec = l.text
  const containingReal = containing.endsWith('.vue.ts') ? containing.slice(0, -3) : containing
  if (spec.endsWith('.vue')) {
    const p = resolveVuePath(spec, containingReal)
    if (p) return { resolvedModule: { resolvedFileName: p + '.ts', extension: ts.Extension.Ts, isExternalLibraryImport: false } }
  }
  return ts.resolveModuleName(spec, containingReal, opts, host)
})

const SKIP_ROOT = /(\.test|\.spec)\.(ts|tsx|js|jsx|mts)$/
let configFiles = parsed.fileNames
// files outside the config's include list but inside the source dirs (JS projects, partial includes)
if (noConfig || cfg.walk_src || pkgSrc) configFiles = [...new Set([...configFiles, ...allFiles.filter(f => SRC_EXT.test(f) && !f.endsWith('.d.ts'))])]
if (srcFiles.size) configFiles = [...new Set([...configFiles, ...srcFiles])]
// .cg.yaml include directories and kept hidden directories (skip_dirs.keep: [.storybook]): their files are roots
// even when the tsconfig leaves them out (node_modules, build/, and dot-directories its wildcards never match)
const keptHidden = r => r.split('/').slice(0, -1).some(x => x.startsWith('.') && KEEP_NAMES.has(x))
if (INCLUDE.length || KEEP_NAMES.size) configFiles = [...new Set([...configFiles, ...allFiles.filter(f => SRC_EXT.test(f) && !f.endsWith('.d.ts') && (included(rel(f)) || keptHidden(rel(f))))])]
configFiles = configFiles.filter(f => f.endsWith('.d.ts') || !belowSkipped(rel(f)))   // declarations stay resolution input
const extraFiles = (cfg.extra_files || []).map(f => path.resolve(ROOT, f)).filter(f => fs.existsSync(f))
if (extraFiles.some(f => /\.(c|m)?jsx?$/.test(f))) options.allowJs = true
const rootNames = [...new Set([...configFiles.filter(f => !SKIP_ROOT.test(f) && !(SKIP_REL && SKIP_REL.test(rel(f))) && !excludedRel(rel(f))), ...extraFiles, ...testFiles]), ...virtual.keys()]
if ([...testFiles].some(f => /\.(c|m)?jsx?$/.test(f))) options.allowJs = true
const program = ts.createProgram({ rootNames, options, host })
const checker = program.getTypeChecker()
const tProgram = Date.now()

// ---------- helpers ----------
const projectSfMemo = new Map()
const projectSf = sf => {
  const fn = sf.fileName
  let v = projectSfMemo.get(fn)
  if (v !== undefined) return v
  const real = fn.endsWith('.vue.ts') ? fn.slice(0, -3) : fn
  v = testFiles.has(real) || (!sourceSkipped(rel(real)) && (srcFiles.has(real) || srcDirs.some(d => real.startsWith(d + path.sep))) && !SKIP_FILE.test(real) && !real.endsWith('.d.ts') && !(SKIP_REL && SKIP_REL.test(rel(real))) && !excludedRel(rel(real)))
  projectSfMemo.set(fn, v)
  return v
}
const realFile = sf => sf.fileName.endsWith('.vue.ts') ? sf.fileName.slice(0, -3) : sf.fileName
function lineOf(node, sf = node.getSourceFile()) {
  const l = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1
  const lm = lineMaps.get(sf.fileName)
  return (lm && lm[l]) || l
}
const isTemplateLine = (node, sf = node.getSourceFile()) => {
  const lm = lineMaps.get(sf.fileName); if (!lm) return false
  return !!lm[sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1]
}
function unwrap(e) {
  while (e && (ts.isParenthesizedExpression(e) || ts.isAsExpression(e) || ts.isSatisfiesExpression?.(e) || ts.isNonNullExpression(e) || ts.isTypeAssertionExpression?.(e))) e = e.expression
  return e
}
const isFn = e => e && (ts.isArrowFunction(e) || ts.isFunctionExpression(e))
function docOf(node) {
  let n = node
  if (ts.isVariableDeclaration(n) && n.parent && n.parent.parent && ts.isVariableStatement(n.parent.parent)) n = n.parent.parent
  const sf = n.getSourceFile()
  const ranges = ts.getLeadingCommentRanges(sf.text, n.getFullStart()) || []
  const docs = ranges.filter(r => sf.text.slice(r.pos, r.pos + 3) === '/**').map(r => sf.text.slice(r.pos, r.end))
  if (!docs.length) return null
  return docs[docs.length - 1].replace(/^\/\*\*|\*\/$/g, '').split('\n').map(l => l.replace(/^\s*\* ?/, '')).join('\n').trim() || null
}

// ---------- pass 1: declarations -> nodes ----------
// router registrations with inline handlers: router.get('/x', mw, (req, res) => {...}) / app.route('/x').post(fn)
const ROUTE_VERBS = new Set(['get', 'post', 'put', 'patch', 'delete', 'del', 'head', 'options', 'all'])
const isStrArg = a => a && (ts.isStringLiteralLike(a) || ts.isTemplateExpression(a) || ts.isRegularExpressionLiteral(a) || (ts.isArrayLiteralExpression(a) && a.elements.length && a.elements.every(x => ts.isStringLiteralLike(x))))
function routeCallInfo(node) {
  if (!ts.isCallExpression(node)) return null
  const c = unwrap(node.expression)
  if (!ts.isPropertyAccessExpression(c) || !ROUTE_VERBS.has(c.name.text)) return null
  const a0 = unwrap(node.arguments[0])
  let pathText = null
  if (isStrArg(a0)) {
    if (c.name.text === 'get' && node.arguments.length === 2 && ts.isStringLiteralLike(a0) && !/^\/|^\*$|^:/.test(a0.text)) return null
    pathText = a0.getText().replace(/\s+/g, ' ').slice(0, 80)
  } else {
    const inner = unwrap(c.expression)
    const ic = inner && ts.isCallExpression(inner) && unwrap(inner.expression)
    if (!(ic && ts.isPropertyAccessExpression(ic) && (ic.name.text === 'route' || ROUTE_VERBS.has(ic.name.text)))) return null
    pathText = ic.name.text === 'route' && inner.arguments[0] ? inner.arguments[0].getText().slice(0, 80) : ''
  }
  let base = unwrap(c.expression)
  while (ts.isCallExpression(base) && ts.isPropertyAccessExpression(unwrap(base.expression))) base = unwrap(unwrap(base.expression).expression)
  return { label: `${base.getText().replace(/\s+/g, ' ').slice(0, 40)}.${c.name.text}(${pathText})` }
}
// MCP registrations with an inline handler (files importing @modelcontextprotocol/sdk): label `server.registerTool('echo')`
const MCP_REG_METHODS = new Set(['registerTool', 'tool', 'registerPrompt', 'prompt', 'registerResource', 'resource'])
const mcpFiles = new Map()
// tRPC procedures (#33): `name: publicProcedure.input(..).query(({ ctx }) => ..)` (also `.mutation` / `.subscription`,
// `t.procedure`): the inline resolver becomes a function node `<router var>.<path>.name`
const TRPC_TERMINAL = new Set(['query', 'mutation', 'subscription'])
function trpcResolver(e) {
  e = unwrap(e)
  if (!e || !ts.isCallExpression(e)) return null
  const c = unwrap(e.expression)
  if (!ts.isPropertyAccessExpression(c) || !TRPC_TERMINAL.has(c.name.text) || !e.arguments.length) return null
  const f = unwrap(e.arguments[e.arguments.length - 1])
  if (!isFn(f)) return null
  let b = unwrap(c.expression)
  for (let i = 0; i < 40 && b; i++) {
    if (ts.isCallExpression(b)) b = unwrap(b.expression)
    else if (ts.isPropertyAccessExpression(b)) { if (b.name.text === 'procedure') return { fn: f, kind: c.name.text }; b = unwrap(b.expression) }
    else break
  }
  return b && ts.isIdentifier(b) && /procedure$/i.test(b.text) ? { fn: f, kind: c.name.text } : null
}
function trpcLabel(prop) {
  const parts = []
  for (let n = prop; n; n = n.parent) {
    if (ts.isPropertyAssignment(n) && n.name && (ts.isIdentifier(n.name) || ts.isStringLiteral(n.name))) parts.unshift(n.name.text)
    else if (ts.isVariableDeclaration(n) && ts.isIdentifier(n.name)) { parts.unshift(n.name.text); break }
    else if (ts.isSourceFile(n) || ts.isFunctionLike(n) || ts.isClassLike(n)) break
  }
  return parts.join('.')
}
function mcpRegInfo(node, sf) {
  if (!ts.isCallExpression(node) || node.arguments.length < 2) return null
  const c = unwrap(node.expression)
  if (!ts.isPropertyAccessExpression(c) || !MCP_REG_METHODS.has(c.name.text)) return null
  if (!mcpFiles.has(sf)) mcpFiles.set(sf, sf.text.includes('@modelcontextprotocol/sdk'))
  if (!mcpFiles.get(sf)) return null
  const f = unwrap(node.arguments[node.arguments.length - 1])
  if (!isFn(f)) return null
  const a0 = unwrap(node.arguments[0])
  const lab = a0 && ts.isStringLiteralLike(a0) ? `'${a0.text}'` : (a0 ? a0.getText().replace(/\s+/g, ' ').slice(0, 40) : '')
  return { label: `${unwrap(c.expression).getText().replace(/\s+/g, ' ').slice(0, 40)}.${c.name.text}(${lab})` }
}
// Electron IPC / context bridge registrations whose inline handlers become function nodes:
//   ipcMain.handle('ch', fn) / handleOnce / on / once, ipcRenderer.on('ch', fn) / once  -> label `ipcMain.handle('ch')`
//   contextBridge.exposeInMainWorld('api', { ping: () => ... })                       -> one node per member `api.ping`
const IPC_RECV = new Map([['ipcMain', new Set(['handle', 'handleOnce', 'on', 'once'])], ['ipcRenderer', new Set(['on', 'once'])]])
function ipcCallInfo(node) {
  if (!ts.isCallExpression(node)) return null
  const c = unwrap(node.expression)
  if (!ts.isPropertyAccessExpression(c)) return null
  const obj = unwrap(c.expression)
  const on0 = obj && (ts.isIdentifier(obj) ? obj.text : ts.isPropertyAccessExpression(obj) ? obj.name.text : null)
  // ipcMain / ipcRenderer, or a project wrapper named after them (ipcMainManager.handle(IpcEvents.X, fn))
  const on = on0 && (IPC_RECV.has(on0) ? on0 : /^ipcMain[A-Z_]\w*$/.test(on0) ? 'ipcMain' : /^ipcRenderer[A-Z_]\w*$/.test(on0) ? 'ipcRenderer' : on0)
  if (!node.arguments[0]) return null
  const ch = ipcChan(node.arguments[0])        // a literal, a const, an enum member (IpcEvents.OPEN = 'open')
  if (on && IPC_RECV.has(on) && IPC_RECV.get(on).has(c.name.text) && node.arguments.length >= 2) {
    // a channel variable typed as a union of literals (`channel: IpcEvents` from a lookup table): every member
    const chans = ch != null ? [ch] : ipcChanUnion(node.arguments[0])
    if (!chans) return null
    const lab = ch != null ? `'${ch}'` : unwrap(node.arguments[0]).getText().slice(0, 40)
    return { kind: 'ipc', process: on === 'ipcMain' ? 'main' : 'renderer', channels: chans, label: `${on0}.${c.name.text}(${lab})`, wrapper: on !== on0, union: ch == null }
  }
  if (ch == null) return null
  if (on === 'contextBridge' && c.name.text === 'exposeInMainWorld' && node.arguments.length >= 2) return { kind: 'expose', key: ch }
  return null
}
function ipcChan(a) {
  a = unwrap(a)
  if (!a) return null
  if (ts.isStringLiteralLike(a)) return a.text
  if (!ts.isIdentifier(a) && !ts.isPropertyAccessExpression(a) && !ts.isElementAccessExpression(a)) return null
  try {
    if (ts.isPropertyAccessExpression(a) || ts.isElementAccessExpression(a)) {
      const v = checker.getConstantValue(a)
      if (typeof v === 'string') return v
    }
    const t = checker.getTypeAtLocation(a)
    if (t && t.isStringLiteral && t.isStringLiteral()) return t.value
  } catch { }
  return null
}
function ipcChanUnion(a) {
  a = unwrap(a)
  if (!a || !ts.isIdentifier(a)) return null
  try {
    const t = checker.getTypeAtLocation(a)
    const ts_ = t && t.isUnion && t.isUnion() ? t.types : null
    if (ts_ && ts_.length <= 500 && ts_.every(x => x.isStringLiteral && x.isStringLiteral())) return [...new Set(ts_.map(x => x.value))].sort()
  } catch { }
  return null
}
const exposedKeys = new Set()      // contextBridge.exposeInMainWorld keys (`window.api`)
// `wrap(async () => {...}, opts)` / `withA(withB(fn))`: the wrapped function literal (HOF wrappers: route handler
// builders, asyncHandler, withAuth, ...)
function wrappedFn(init, depth = 0) {
  init = unwrap(init)
  if (!init || depth > 3 || !ts.isCallExpression(init)) return null
  const c = unwrap(init.expression)
  if (ts.isIdentifier(c) && (c.text === 'require' || c.text === 'defineStore')) return null
  for (const a of init.arguments) {
    const u = unwrap(a)
    if (isFn(u)) return u
    const w = wrappedFn(u, depth + 1)
    if (w) return w
  }
  return null
}
// `const f = debounce(() => ..)` / `useCallback(..)` / `React.memo(..)`: the variable holds the wrapped function, so
// it is named after the variable. `const server = http.createServer((req, res) => ..)` passes a callback and holds
// an object (#138): a known non-callable type, or with no type a method call on an object (other than lodash /
// React / styled / test-mock namespaces and `Object.assign`), is not a wrapper.
const WRAP_NS = /^(?:_|lodash|React|R|Ramda|fp|util|utils|Vue|vi|vitest|jest|sinon|styled)$/   // `vi.fn(impl)` mocks impl
function wrapperOf(init, decl) {
  const f = wrappedFn(init)
  if (!f) return null
  let t = null
  try { t = checker.getTypeAtLocation(decl.name) } catch { t = null }
  const vague = x => !!(x.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown)) || (x.isUnionOrIntersection() && x.types.some(vague))
  if (t && !vague(t)) {
    const nn = checker.getNonNullableType(t)
    if (nn.getCallSignatures().length) return f
    if (nn.isUnion() && nn.types.some(x => x.getCallSignatures().length)) return f
    return null
  }
  const c = unwrap(unwrap(init).expression)
  if (ts.isIdentifier(c) || ts.isCallExpression(c)) return f          // `debounce(fn)`, curried `styled('img')(fn)`
  if (ts.isPropertyAccessExpression(c) && ts.isIdentifier(unwrap(c.expression)) && (WRAP_NS.test(unwrap(c.expression).text)
      || c.getText() === 'Object.assign')) return f                     // `Object.assign(Comp, { Item })`
  stats.callback_not_wrapper = (stats.callback_not_wrapper || 0) + 1
  return null
}
const REF_CALL_PROPS = new Set(['call', 'apply', 'bind', 'value'])
const isModuleExports = e => { e = unwrap(e); return !!e && ts.isPropertyAccessExpression(e) && e.getText() === 'module.exports' }
const nodes = []                  // {id, kind, name, file, line, end_line, doc, parent, attrs}
const declId = new Map()          // ts.Node (declaration) -> node id
const valueIds = new Map()        // enum member / constant declaration -> enum_case / constant node id (#84)
const valueNames = new Set()
// a constant's initializer: a literal, a literal array / object / template, `-1`, `as const`; not a call or `new`
function constInit(e) {
  e = unwrap(e)
  if (!e) return false
  if (ts.isStringLiteralLike(e) || ts.isNumericLiteral(e) || ts.isBigIntLiteral?.(e) || ts.isRegularExpressionLiteral(e)
      || e.kind === ts.SyntaxKind.TrueKeyword || e.kind === ts.SyntaxKind.FalseKeyword || e.kind === ts.SyntaxKind.NullKeyword) return true
  if (ts.isPrefixUnaryExpression(e)) return constInit(e.operand)
  if (ts.isBinaryExpression(e) && e.operatorToken.kind !== ts.SyntaxKind.EqualsToken) return constInit(e.left) && constInit(e.right)   // 1000 * 60
  if (ts.isTemplateExpression(e)) return true
  if (ts.isArrayLiteralExpression(e)) return true
  if (ts.isObjectLiteralExpression(e)) return !e.properties.some(p => ts.isMethodDeclaration(p) || ts.isGetAccessor(p) || ts.isSetAccessor(p) || (ts.isPropertyAssignment(p) && isFn(unwrap(p.initializer))))
  return false
}
// stored class fields (#88): `count = 0`, `private items: Item[]`, constructor parameter properties; accesses that the
// checker resolves to one are READS_PROP / WRITES_PROP
const fieldIds = new Map()        // declaration -> field node id
const fieldNames = new Set()
const TS_MUTATING = new Set(['push', 'pop', 'shift', 'unshift', 'splice', 'sort', 'reverse', 'fill', 'copyWithin',
  'set', 'delete', 'clear', 'add'])
function addField(cls, q, m, sf, r, parentId, how) {
  const nm = m.name.text
  const id = `field:${r}#${q}.${nm}`
  if (usedIds.has(id)) { if (how === 'this') fieldIds.set(m, id); return }
  usedIds.add(id)
  const fl = how === 'this' ? 0 : ts.getCombinedModifierFlags(m)
  nodes.push({ id, kind: 'field', name: `${q}.${nm}`, file: r, line: lineOf(m, sf), end_line: sf.getLineAndCharacterOfPosition(m.end).line + 1,
    doc: null, parent: parentId, attrs: { property: 'stored', declared: how, ...((fl & ts.ModifierFlags.Readonly) ? { readonly: true } : {}) } })
  fieldIds.set(m, id)
  fieldNames.add(nm)
  stats.stored_field_nodes = (stats.stored_field_nodes || 0) + 1
}
// component / composable state (#88): React `const [x, setX] = useState()` and Vue `const x = ref()` /
// `shallowRef()` / `reactive()` are field nodes (`property: state`, `hook`); a reference the checker resolves to the
// variable is READS_PROP, `setX(...)` / `x.value = ...` / `x.value++` / `state.p = ...` WRITES_PROP
const stateIds = new Map()        // declaration (VariableDeclaration / BindingElement) -> [field id, 'value' | 'setter' | 'reactive']
const stateNames = new Set()
const STATE_HOOKS = new Set(['useState', 'useReducer', 'ref', 'shallowRef', 'reactive', 'shallowReactive'])
// Pinia options stores (`defineStore('cart', { state: () => ({ items: [] }), actions: {...} })`) and the Vue Options
// API (`data() { return { count: 0 } }`): each state / data key is a `property: state` field (#88). `this.x` inside
// that options object, and `store.x` / `useCart().x` on a store, are its reads / writes
const optFields = new Map()       // options ObjectLiteralExpression -> Map(name -> field id)
const storeFields = new Map()     // store VariableDeclaration -> Map(name -> field id)
const optNames = new Set()
function returnedObject(f) {
  if (!f) return null
  f = unwrap(f)
  if (ts.isArrowFunction(f) && !ts.isBlock(f.body)) { const b = unwrap(f.body); return ts.isObjectLiteralExpression(b) ? b : null }
  const body = f.body
  if (!body || !ts.isBlock(body)) return null
  for (const st of body.statements) if (ts.isReturnStatement(st) && st.expression && ts.isObjectLiteralExpression(unwrap(st.expression))) return unwrap(st.expression)
  return null
}
function optionsFields(sf, r, fid) {
  const reg = (obj, owner, ownerName, keyProp, hook, storeDecl) => {
    const p = obj.properties.find(x => x.name && ts.isIdentifier(x.name) && x.name.text === keyProp)
    if (!p) return
    const ret = returnedObject(ts.isPropertyAssignment(p) ? p.initializer : (ts.isMethodDeclaration(p) ? p : null))
    if (!ret) return
    const m = new Map()
    for (const q of ret.properties) {
      if (!q.name || !(ts.isIdentifier(q.name) || ts.isStringLiteral(q.name))) continue
      const nm = q.name.text
      const id = `field:${r}#${ownerName ? ownerName + '.' : ''}${nm}`
      if (!usedIds.has(id)) {
        usedIds.add(id)
        nodes.push({ id, kind: 'field', name: id.split('#').pop(), file: r, line: lineOf(q, sf), end_line: lineOf(q, sf), doc: null,
          parent: owner, attrs: { property: 'state', hook } })
        stats.state_field_nodes = (stats.state_field_nodes || 0) + 1
      }
      m.set(nm, id); optNames.add(nm)
    }
    optFields.set(obj, m)
    if (storeDecl) storeFields.set(storeDecl, m)
  }
  const scan = (n) => {
    if (ts.isCallExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === 'defineStore') {
      const obj = n.arguments.map(unwrap).find(a => a && ts.isObjectLiteralExpression(a))
      const vd = ts.isVariableDeclaration(n.parent) ? n.parent : null
      const sid = vd && declId.get(n)
      if (obj && vd && ts.isIdentifier(vd.name)) reg(obj, sid || fid, vd.name.text, 'state', 'pinia', vd)
    } else if (ts.isObjectLiteralExpression(n) && (ts.isExportAssignment(n.parent)
        || (ts.isCallExpression(n.parent) && ts.isIdentifier(n.parent.expression) && n.parent.expression.text === 'defineComponent'))) {
      reg(n, fid, '', 'data', 'data', null)
    }
    ts.forEachChild(n, scan)
  }
  scan(sf)
}
function storeOfExpr(e) {
  e = unwrap(e)
  let call = null
  if (ts.isCallExpression(e)) call = e
  else if (ts.isIdentifier(e)) {
    let s = null
    try { s = checker.getSymbolAtLocation(e) } catch { }
    const vd = s && s.valueDeclaration
    if (vd && ts.isVariableDeclaration(vd) && vd.initializer && ts.isCallExpression(unwrap(vd.initializer))) call = unwrap(vd.initializer)
  }
  if (!call || !ts.isIdentifier(call.expression)) return null
  let s = null
  try { s = checker.getSymbolAtLocation(call.expression) } catch { }
  if (s && (s.flags & ts.SymbolFlags.Alias)) { try { s = checker.getAliasedSymbol(s) } catch { s = null } }
  return (s && s.valueDeclaration && storeFields.get(s.valueDeclaration)) || null
}
function writeOf(node) {
  const p = node.parent
  const isAssign = (e) => e.parent && ts.isBinaryExpression(e.parent) && e.parent.left === e && e.parent.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && e.parent.operatorToken.kind <= ts.SyntaxKind.LastAssignment
  const isIncr = (e) => e.parent && (ts.isPrefixUnaryExpression(e.parent) || ts.isPostfixUnaryExpression(e.parent)) && (e.parent.operator === ts.SyntaxKind.PlusPlusToken || e.parent.operator === ts.SyntaxKind.MinusMinusToken)
  if (isAssign(node) || isIncr(node) || (p && ts.isDeleteExpression(p))) return [true, undefined]
  if (p && ts.isElementAccessExpression(p) && p.expression === node && (isAssign(p) || (p.parent && ts.isDeleteExpression(p.parent)))) return [true, 'item']
  if (p && ts.isPropertyAccessExpression(p) && p.expression === node && TS_MUTATING.has(p.name.text) && p.parent && ts.isCallExpression(p.parent) && p.parent.expression === p) return [true, 'mutating']
  return [false, undefined]
}
function optionsRef(node, cur, sf, r) {
  let m = null
  if (node.expression.kind === ts.SyntaxKind.ThisKeyword) {
    for (let x = node.parent; x && !ts.isSourceFile(x); x = x.parent) if (optFields.has(x)) { m = optFields.get(x); break }
  } else m = storeOfExpr(node.expression)
  const fid = m && m.get(node.name.text)
  if (!fid || fid === cur) return
  const [write, via] = writeOf(node)
  const recv = node.expression.kind === ts.SyntaxKind.ThisKeyword ? 'this' : node.expression.getText(sf).slice(0, 40)
  addEdge(cur, fid, write ? 'WRITES_PROP' : 'READS_PROP', r, lineOf(node, sf), m === null ? 'resolved' : (recv === 'this' ? 'exact' : 'resolved'), { receiver: recv, ...(via ? { via } : {}) })
  stats[write ? 'state_field_writes' : 'state_field_reads'] = (stats[write ? 'state_field_writes' : 'state_field_reads'] || 0) + 1
}
function stateDecl(d, cur, sf, r) {
  const init = unwrap(d.initializer)
  if (!init || !ts.isCallExpression(init)) return
  const callee = unwrap(init.expression)
  const hook = ts.isIdentifier(callee) ? callee.text : (ts.isPropertyAccessExpression(callee) && ts.isIdentifier(callee.expression) && callee.expression.text === 'React' ? callee.name.text : null)
  if (!hook || !STATE_HOOKS.has(hook)) return
  const react = hook.startsWith('use')
  const owner = cur.split(':').slice(1).join(':')
  const add = (nameNode, decl, mode) => {
    const nm = nameNode.text
    const id = `field:${owner.includes('#') ? owner : owner + '#'}${owner.includes('#') ? '.' : ''}${nm}`
    if (!usedIds.has(id)) {
      usedIds.add(id)
      nodes.push({ id, kind: 'field', name: id.split('#').pop(), file: r, line: lineOf(decl, sf), end_line: lineOf(decl, sf), doc: null,
        parent: cur, attrs: { property: 'state', hook } })
      stats.state_field_nodes = (stats.state_field_nodes || 0) + 1
    }
    stateIds.set(decl, [id, mode])
    stateNames.add(nm)
  }
  if (react && ts.isArrayBindingPattern(d.name)) {
    const [v, set] = d.name.elements
    if (v && ts.isBindingElement(v) && ts.isIdentifier(v.name)) {
      add(v.name, v, 'read')
      if (set && ts.isBindingElement(set) && ts.isIdentifier(set.name)) {
        stateIds.set(set, [stateIds.get(v)[0], 'setter']); stateNames.add(set.name.text)
      }
    }
  } else if (!react && ts.isIdentifier(d.name)) add(d.name, d, hook.endsWith('eactive') ? 'reactive' : 'value')
}
function stateRef(node, cur, sf, r) {
  const p = node.parent
  if (p && (ts.isVariableDeclaration(p) || ts.isBindingElement(p)) && p.name === node) return
  let s = null
  try { s = checker.getSymbolAtLocation(node) } catch { }
  if (s && !s.valueDeclaration && (s.flags & ts.SymbolFlags.Alias)) { try { s = checker.getAliasedSymbol(s) } catch { s = null } }
  const hit = s && stateIds.get(s.valueDeclaration || (s.declarations || [])[0])
  if (!hit || hit[0] === cur) return
  const [fid, mode] = hit
  const isAssign = (e) => e.parent && ts.isBinaryExpression(e.parent) && e.parent.left === e && e.parent.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && e.parent.operatorToken.kind <= ts.SyntaxKind.LastAssignment
  const isIncr = (e) => e.parent && (ts.isPrefixUnaryExpression(e.parent) || ts.isPostfixUnaryExpression(e.parent)) && (e.parent.operator === ts.SyntaxKind.PlusPlusToken || e.parent.operator === ts.SyntaxKind.MinusMinusToken)
  let write = false, via
  if (mode === 'setter') {
    if (!(ts.isCallExpression(p) && p.expression === node)) return     // `onChange={setX}`: handed out, not a write here
    write = true; via = 'setter'
  } else if (p && ts.isPropertyAccessExpression(p) && p.expression === node && (isAssign(p) || isIncr(p) || ts.isDeleteExpression(p.parent))
      && (mode === 'reactive' || p.name.text === 'value')) { write = true; via = mode === 'reactive' ? 'property' : 'value' }
  else if (isAssign(node) || isIncr(node)) { write = true }
  else if (mode === 'reactive' && p && ts.isPropertyAccessExpression(p) && p.expression === node) {
    const q = p.parent      // `state.items.push(x)` / `state.items[0] = x`
    if (q && ts.isPropertyAccessExpression(q) && q.expression === p && TS_MUTATING.has(q.name.text) && q.parent && ts.isCallExpression(q.parent) && q.parent.expression === q) { write = true; via = 'mutating' }
  }
  addEdge(cur, fid, write ? 'WRITES_PROP' : 'READS_PROP', r, lineOf(node, sf), 'exact', via ? { via } : undefined)
  stats[write ? 'state_field_writes' : 'state_field_reads'] = (stats[write ? 'state_field_writes' : 'state_field_reads'] || 0) + 1
}
function addValue(kind, key, name, d, sf, r, parentId) {
  const id = `${kind}:${key}`
  if (usedIds.has(id)) return
  usedIds.add(id)
  nodes.push({ id, kind, name, file: r, line: lineOf(d, sf), end_line: sf.getLineAndCharacterOfPosition(d.end).line + 1, doc: null, parent: parentId, attrs: {} })
  valueIds.set(d, id)
  valueNames.add(d.name.text)
}
const fileNode = new Map()        // abs real path -> node id
const modNode = new Map()   // module node id -> node object (re-export attrs)
const usedIds = new Set()
function mkId(kind, key) { let id = `${kind}:${key}`, i = 2; while (usedIds.has(id)) id = `${kind}:${key}~${i++}`; usedIds.add(id); return id }

// describe / it / test (Vitest, Jest, Mocha, Playwright test, Cypress): {name, describe, framework}
const TEST_FNS = new Set(['it', 'test', 'describe', 'suite', 'context', 'specify'])
const TEST_MODS = new Set(['only', 'skip', 'todo', 'concurrent', 'serial', 'parallel', 'fixme', 'fail', 'slow', 'sequential', 'describe', 'each', 'skipIf', 'runIf'])
let testFramework = 'test'
function testCallInfo(node) {
  let c = unwrap(node.expression)
  if (ts.isCallExpression(c)) c = unwrap(c.expression)   // it.each([...])('name', fn) / test.skipIf(x)('name', fn)
  let base = c, mods = []
  while (ts.isPropertyAccessExpression(base)) { mods.unshift(base.name.text); base = unwrap(base.expression) }
  if (!ts.isIdentifier(base) || !TEST_FNS.has(base.text) || !mods.every(m => TEST_MODS.has(m))) return null
  const a0 = node.arguments[0] && unwrap(node.arguments[0])
  if (!a0 || !(ts.isStringLiteralLike(a0) || ts.isTemplateExpression(a0))) return null
  if (!node.arguments.some(a => isFn(unwrap(a)))) return null
  const describe = base.text === 'describe' || base.text === 'suite' || base.text === 'context' || mods.includes('describe')
  const name = ts.isStringLiteralLike(a0) ? a0.text : a0.getText().slice(1, -1)
  return { name: name.replace(/\s+/g, ' ').slice(0, 160), describe, framework: testFramework }
}
const sourceFiles = program.getSourceFiles().filter(projectSf)
// interfaces some project class `implements` (and the interfaces they extend): their function-typed properties
// (`fetch: (o: Opts) => Promise<R>`) become member nodes too; method signatures always do
const aliasTarget = sym => { for (let i = 0; sym && (sym.flags & ts.SymbolFlags.Alias) && i < 6; i++) { try { sym = checker.getAliasedSymbol(sym) } catch { return null } } return sym }
const ifaceDecls = sym => ((sym && sym.declarations) || []).filter(d => ts.isInterfaceDeclaration(d) || (ts.isTypeAliasDeclaration(d) && ts.isTypeLiteralNode(d.type)))
// project interfaces / object type aliases expected where `n` stands (a typed variable, a return value, an argument,
// `satisfies`): one type, `| undefined` / `| null` aside
const ctxType = n => {
  let ct = null
  try { ct = checker.getContextualType(n) } catch { }
  if (!ct) return null
  const ts0 = ct.isUnion() ? ct.types.filter(t => !(t.flags & (ts.TypeFlags.Undefined | ts.TypeFlags.Null))) : [ct]
  return ts0.length === 1 ? ts0[0] : null
}
const projIfaces = t => t ? ifaceDecls(t.aliasSymbol || t.getSymbol()).filter(d => projectSf(d.getSourceFile())) : []
const ctxIfaces = n => projIfaces(ctxType(n))
// a class passed as a value where a constructor of an interface is expected (`register(FollowingFeedAPI)` with
// `register(c: new () => FeedAPI)`): those interfaces
const ctorIfaces = n => {
  const t = ctxType(n)
  if (!t) return []
  const out = []
  for (const sig of t.getConstructSignatures()) { let rt = null; try { rt = checker.getReturnTypeOfSignature(sig) } catch { } for (const d of projIfaces(rt)) if (!out.includes(d)) out.push(d) }
  return out
}
const fnMembers = o => o.properties.filter(p => ts.isMethodDeclaration(p) || (ts.isPropertyAssignment(p) && isFn(unwrap(p.initializer))))
const VALUE_SLOT = n => n.parent && ((ts.isCallExpression(n.parent) || ts.isNewExpression(n.parent)) && (n.parent.arguments || []).includes(n)
  || ts.isArrayLiteralExpression(n.parent) || (ts.isPropertyAssignment(n.parent) && n.parent.initializer === n)
  || ts.isShorthandPropertyAssignment(n.parent) || (ts.isVariableDeclaration(n.parent) && n.parent.initializer === n)
  || ts.isReturnStatement(n.parent))
const classOfValue = n => {
  if (!ts.isIdentifier(n) || !VALUE_SLOT(n)) return null
  let sym = null
  try { sym = ts.isShorthandPropertyAssignment(n.parent) ? checker.getShorthandAssignmentValueSymbol(n.parent) : checker.getSymbolAtLocation(n) } catch { }
  sym = aliasTarget(sym)
  return ((sym && sym.declarations) || []).find(x => ts.isClassDeclaration(x) && projectSf(x.getSourceFile())) || null
}
// mixins: `class Client extends mix(Base).with(Users, Posts)` (or `Users(Posts(Base))`) where `const Users = (b) =>
// class extends b {..}`, merged with `interface Client extends Base, UsersMix, PostsMix`: the merged interfaces, and
// the mixin class expressions applied in the `extends` call
const mergedIfaces = cls => {
  let sym = null
  try { sym = cls.name && checker.getSymbolAtLocation(cls.name) } catch { }
  return ((sym && sym.declarations) || []).filter(d => ts.isInterfaceDeclaration(d) && projectSf(d.getSourceFile()))
}
const mixinClasses = cls => {
  const h = (cls.heritageClauses || []).find(h => h.token === ts.SyntaxKind.ExtendsKeyword)
  const e = h && h.types[0] && unwrap(h.types[0].expression)
  if (!e || !ts.isCallExpression(e)) return []
  const out = []
  const walk = n => {
    if (ts.isIdentifier(n) && n.parent && ts.isCallExpression(n.parent) && n.parent.arguments.includes(n)) {
      let sym = null
      try { sym = aliasTarget(checker.getSymbolAtLocation(n)) } catch { }
      for (const d of (sym && sym.declarations) || []) {
        const f = ts.isVariableDeclaration(d) && d.initializer ? unwrap(d.initializer) : ts.isFunctionDeclaration(d) ? d : null
        if (!f || !(isFn(f) || ts.isFunctionDeclaration(f)) || !f.body) continue
        let ce = ts.isBlock(f.body) ? null : unwrap(f.body)
        if (ts.isBlock(f.body)) for (const st of f.body.statements) if (ts.isReturnStatement(st) && st.expression) ce = unwrap(st.expression)
        if (ce && ts.isClassExpression(ce) && projectSf(ce.getSourceFile()) && !out.includes(ce)) out.push(ce)
      }
    }
    ts.forEachChild(n, walk)
  }
  walk(e)
  return out
}
const implementedIfaces = new Set()
{
  const addIface = (d, depth = 0) => {
    if (implementedIfaces.has(d) || depth > 8) return
    implementedIfaces.add(d)
    for (const h of d.heritageClauses || []) for (const t of h.types) {
      let sym = null
      try { sym = aliasTarget(checker.getSymbolAtLocation(t.expression)) } catch { }
      for (const x of ifaceDecls(sym)) addIface(x, depth + 1)
    }
  }
  const walk = n => {
    if ((ts.isClassDeclaration(n) || ts.isClassExpression(n)) && n.heritageClauses) {
      for (const h of n.heritageClauses) if (h.token === ts.SyntaxKind.ImplementsKeyword) for (const t of h.types) {
        let sym = null
        try { sym = aliasTarget(checker.getSymbolAtLocation(t.expression)) } catch { }
        for (const d of ifaceDecls(sym)) addIface(d)
      }
      if (ts.isClassDeclaration(n) && mixinClasses(n).length) for (const d of mergedIfaces(n)) addIface(d)
    } else if (ts.isObjectLiteralExpression(n) && fnMembers(n).length) for (const d of ctxIfaces(n)) addIface(d)   // `const api: FeedAPI = { fetch() {..} }`
    else if (classOfValue(n)) for (const d of ctorIfaces(n)) addIface(d)
    ts.forEachChild(n, walk)
  }
  for (const sf of sourceFiles) if (!realFile(sf).endsWith('.vue')) walk(sf)
}
const isFnType = t => t && (ts.isFunctionTypeNode(t) || (ts.isParenthesizedTypeNode(t) && isFnType(t.type)))
// members of an interface / object type alias: `method:<file>#Iface.member` (attrs.signature; no body). A call on an
// interface-typed value resolves to it; IMPLEMENTED_BY links it to the class members implementing it
function ifaceMembers(decl, q, parentId, r, sf) {
  const members = ts.isInterfaceDeclaration(decl) ? decl.members : decl.type.members
  const seen = new Map()
  for (const m of members || []) {
    if (!m.name || !(ts.isIdentifier(m.name) || ts.isStringLiteralLike(m.name))) continue
    const isM = ts.isMethodSignature(m), isP = ts.isPropertySignature(m) && isFnType(m.type) && implementedIfaces.has(decl)
    if (!isM && !isP) continue
    const mq = `${q}.${m.name.text}`
    let id = seen.get(mq)          // overloads share one node
    if (!id) {
      id = mkId('method', `${r}#${mq}`)
      seen.set(mq, id)
      nodes.push({ id, kind: 'method', name: mq, file: r, line: lineOf(m, sf), end_line: sf.getLineAndCharacterOfPosition(m.end).line + 1,
        doc: docOf(m), parent: parentId, attrs: { signature: true, interface_member: true } })
      stats.interface_members = (stats.interface_members || 0) + 1
    }
    declId.set(m, id)
  }
}
for (const sf of sourceFiles) {
  const real = realFile(sf), r = rel(real), fk = fileKind(r)
  const isTestSf = testFiles.has(real)
  if (isTestSf) {
    const txt = sf.text
    testFramework = /@playwright\/test/.test(txt) ? 'playwright' : /from ['"]vitest['"]/.test(txt) ? 'vitest' : /\bcy\./.test(txt) ? 'cypress' : /@jest\/globals|jest\./.test(txt) ? 'jest' : 'test'
  }
  if (!real.endsWith('.vue')) stats.ts_files++
  if (sf.parseDiagnostics && sf.parseDiagnostics.length) {   // the parser recovers; count files that needed recovery
    stats.syntax_error_files = (stats.syntax_error_files || 0) + 1
    if ((stats.syntax_error_samples ||= []).length < 5) stats.syntax_error_samples.push(r)
    // error lines per file for `cg coverage` (#73); in a .vue file only those of its <script> blocks (the template
    // stubs are appended after the file's own lines)
    let maxLine = Infinity
    if (real.endsWith('.vue')) { try { maxLine = fs.readFileSync(real, 'utf8').split('\n').length } catch (e) { maxLine = 0 } }
    const lines = [...new Set(sf.parseDiagnostics.map(d => sf.getLineAndCharacterOfPosition(d.start || 0).line + 1))]
      .filter(l => l <= maxLine).slice(0, 20)
    const se = (stats.syntax_errors ||= {})
    if (lines.length && Object.keys(se).length < 500) se[r] = lines.map(l => [l, l])
  }
  const fid = real.endsWith('.vue') ? `${fk}:${r}` : `module:${r}`
  usedIds.add(fid)
  fileNode.set(real, fid)
  nodes.push({ id: fid, kind: real.endsWith('.vue') ? fk : 'module', name: r, file: r, line: 1, end_line: sf.getLineAndCharacterOfPosition(sf.end).line + 1, attrs: { file_kind: fk, ...(isTestSf ? { test: true } : {}) } })
  modNode.set(fid, nodes[nodes.length - 1])
  if (real.endsWith('.vue')) continue // SFC: references are attributed to the component node
  const visit = (node, qual, parentId, inObj) => {
    let name = null, kind = null, body = null, wrapped = null
    if (!qual && !inObj && ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer
        && ts.isVariableDeclarationList(node.parent) && (node.parent.flags & ts.NodeFlags.Const)
        && ts.isVariableStatement(node.parent.parent) && node.parent.parent.parent === sf && constInit(node.initializer))
      addValue('constant', `${r}#${node.name.text}`, node.name.text, node, sf, r, fid)     // `export const MAX = 3` (#84)
    const tp = ((ts.isPropertyAssignment(node) && node.name && (ts.isIdentifier(node.name) || ts.isStringLiteral(node.name)))
      || (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer)) && trpcResolver(node.initializer)
    if (tp) {                       // a tRPC procedure's inline resolver: its own node, the rest of the chain stays outside
      const q = ts.isVariableDeclaration(node) ? (qual ? `${qual}.${node.name.text}` : node.name.text) : trpcLabel(node)
      const id = mkId('function', `${r}#${q}`)
      const f = tp.fn
      nodes.push({ id, kind: 'function', name: q, file: r, line: lineOf(f, sf), end_line: sf.getLineAndCharacterOfPosition(f.end).line + 1,
        doc: null, parent: parentId, attrs: { inline_handler: true, trpc: tp.kind } })
      declId.set(f, id)
      ts.forEachChild(f, c => visit(c, q, id))
      ts.forEachChild(unwrap(node.initializer), c => { if (c !== f && unwrap(c) !== f) visit(c, qual, parentId) })
      return
    }
    if (ts.isFunctionDeclaration(node) && node.name) { name = node.name.text; kind = 'function'; body = node }
    else if (ts.isFunctionDeclaration(node) && !node.name && !qual && (ts.getCombinedModifierFlags(node) & ts.ModifierFlags.Default)) { name = 'default'; kind = 'function'; body = node }
    else if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
      const init = unwrap(node.initializer)
      if (isFn(init)) { name = node.name.text; kind = 'function'; body = init }
      else if (ts.isCallExpression(init) && ts.isIdentifier(init.expression) && init.expression.text === 'defineStore') {
        name = node.name.text; kind = 'store'; body = init
      } else if (!qual && (wrapped = wrapperOf(init, node))) { name = node.name.text; kind = 'function'; body = wrapped }
      else if (!qual && ts.isObjectLiteralExpression(init)) { ts.forEachChild(init, c => visit(c, node.name.text, parentId, true)); return }
    } else if (ts.isPropertyAssignment(node) && inObj && node.name && (ts.isIdentifier(node.name) || ts.isStringLiteral(node.name)) && ts.isObjectLiteralExpression(unwrap(node.initializer))) {
      ts.forEachChild(node, c => visit(c, qual ? `${qual}.${node.name.text}` : node.name.text, parentId, true)); return
    } else if ((ts.isMethodDeclaration(node) || ts.isPropertyAssignment(node)) && node.name && (ts.isIdentifier(node.name) || ts.isStringLiteral(node.name))) {
      const init = ts.isPropertyAssignment(node) ? unwrap(node.initializer) : node
      if (ts.isMethodDeclaration(node) || isFn(init)) { name = node.name.text; kind = ts.isMethodDeclaration(node) && ts.isClassLike(node.parent) ? 'method' : 'function'; body = init }
    } else if (ts.isPropertyDeclaration(node) && node.name && ts.isIdentifier(node.name) && node.initializer && ts.isClassLike(node.parent)) {
      const init = unwrap(node.initializer)
      const f = isFn(init) ? init : wrapperOf(init, node)
      if (f) { name = node.name.text; kind = 'method'; body = f }
    } else if (ts.isExportAssignment(node) && !qual) {
      const ex = unwrap(node.expression)
      const f = isFn(ex) ? ex : wrappedFn(ex)
      if (f) { name = 'default'; kind = 'function'; body = f }
      else if (ts.isObjectLiteralExpression(ex)) { ts.forEachChild(ex, c => visit(c, '', parentId, true)); return }
    } else if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsToken && !qual && ts.isExpressionStatement(node.parent)) {
      // CommonJS: module.exports = fn | {..}; exports.x = fn; module.exports.x = fn
      const l = unwrap(node.left), rgt = unwrap(node.right)
      const f = isFn(rgt) ? rgt : wrappedFn(rgt)
      if (isModuleExports(l)) {
        if (f) { name = f.name ? f.name.text : 'default'; kind = 'function'; body = f }
        else if (ts.isObjectLiteralExpression(rgt)) { ts.forEachChild(rgt, c => visit(c, '', parentId, true)); return }
      } else if (ts.isPropertyAccessExpression(l) && (unwrap(l.expression).getText() === 'exports' || isModuleExports(l.expression)) && f) {
        name = l.name.text; kind = 'function'; body = f
      }
    } else if (ts.isCallExpression(node) && isTestSf && testCallInfo(node)) {
      const tc = testCallInfo(node)
      const cb = [...node.arguments].reverse().map(unwrap).find(isFn)
      if (tc.describe) {
        if (cb) ts.forEachChild(cb, c => visit(c, qual ? `${qual} > ${tc.name}` : tc.name, parentId))
        for (const a of node.arguments) if (unwrap(a) !== cb) visit(a, qual, parentId)
        return
      }
      const q = qual ? `${qual} > ${tc.name}` : tc.name
      const id = mkId('test', `${r}#${q}`)
      nodes.push({ id, kind: 'test', name: q, file: r, line: lineOf(node, sf), end_line: sf.getLineAndCharacterOfPosition(node.end).line + 1,
        doc: null, parent: parentId, attrs: { framework: tc.framework, test: true } })
      stats.test_cases = (stats.test_cases || 0) + 1
      if (cb) { declId.set(cb, id); declId.set(node, id); ts.forEachChild(cb, c => visit(c, q, id)) }
      return
    } else if (ts.isCallExpression(node)) {
      const ic = ipcCallInfo(node)
      if (ic) {
        const fns = []
        if (ic.kind === 'ipc') {
          const h = unwrap(node.arguments[1])
          if (isFn(h)) fns.push([h, ic.label])
        } else {
          exposedKeys.add(ic.key)
          const o = unwrap(node.arguments[1])
          if (o && ts.isObjectLiteralExpression(o)) for (const p of o.properties) {
            const pn = p.name && (ts.isIdentifier(p.name) || ts.isStringLiteralLike(p.name)) ? p.name.text : null
            if (!pn) continue
            if (ts.isMethodDeclaration(p)) fns.push([p, `${ic.key}.${pn}`])
            else if (ts.isPropertyAssignment(p) && isFn(unwrap(p.initializer))) fns.push([unwrap(p.initializer), `${ic.key}.${pn}`])
          }
        }
        if (fns.length) {
          const handled = new Set()
          for (const [f, lab] of fns) {
            const q = qual ? `${qual}.${lab}` : lab
            const id = mkId('function', `${r}#${q}`)
            nodes.push({ id, kind: 'function', name: q, file: r, line: lineOf(f, sf), end_line: sf.getLineAndCharacterOfPosition(f.end).line + 1,
              doc: null, parent: parentId, attrs: { inline_handler: true, ipc: ic.kind === 'ipc' && !ic.union ? ic.channels[0] : undefined, exposed: ic.kind === 'expose' ? ic.key : undefined } })
            declId.set(f, id)
            ts.forEachChild(f, c => visit(c, q, id))
            handled.add(f)
          }
          ts.forEachChild(node, c => { if (!handled.has(c) && !handled.has(unwrap(c))) visit(c, qual, parentId) })
          return
        }
      }
      const mc = mcpRegInfo(node, sf)
      if (mc) {                     // MCP server.registerTool('name', cfg, async (args) => ..): the inline handler is a node
        const f = unwrap(node.arguments[node.arguments.length - 1])
        const q = qual ? `${qual}.${mc.label}` : mc.label
        const id = mkId('function', `${r}#${q}`)
        nodes.push({ id, kind: 'function', name: q, file: r, line: lineOf(f, sf), end_line: sf.getLineAndCharacterOfPosition(f.end).line + 1,
          doc: null, parent: parentId, attrs: { inline_handler: true } })
        declId.set(f, id)
        ts.forEachChild(f, c => visit(c, q, id))
        ts.forEachChild(node, c => { if (c !== f && unwrap(c) !== f) visit(c, qual, parentId) })
        return
      }
      const rc = routeCallInfo(node)
      if (rc) {
        const handled = new Set()
        for (const a of node.arguments) {
          const u = unwrap(a)
          const fns = isFn(u) ? [u] : ts.isArrayLiteralExpression(u) ? u.elements.map(unwrap).filter(isFn) : [wrappedFn(u)].filter(Boolean)
          for (const f of fns) {
            const q = qual ? `${qual}.${rc.label}` : rc.label
            const id = mkId('function', `${r}#${q}`)
            nodes.push({ id, kind: 'function', name: q, file: r, line: lineOf(f, sf), end_line: sf.getLineAndCharacterOfPosition(f.end).line + 1,
              doc: null, parent: parentId, attrs: { inline_handler: true } })
            declId.set(f, id)
            ts.forEachChild(f, c => visit(c, q, id))
            handled.add(f)
          }
        }
        const v2 = c => { if (handled.has(c)) return; if (handled.has(unwrap(c))) return; visit(c, qual, parentId) }
        ts.forEachChild(node, v2)
        return
      }
    } else if (ts.isClassDeclaration(node) && node.name) { name = node.name.text; kind = 'class'; body = node }
    else if ((ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node) || ts.isEnumDeclaration(node)) && node.name) {
      name = node.name.text; kind = 'type'
    }
    if (name) {
      const q = qual ? `${qual}.${name}` : name
      if (kind === 'function' && !qual && fk === 'composable' && /^use[A-Z0-9]/.test(name)) kind = 'composable'
      const attrs = {}
      if (wrapped || (body && body !== node && !isFn(unwrap(node.initializer || node)) && !ts.isFunctionDeclaration(node) && !ts.isMethodDeclaration(node) && kind !== 'store' && kind !== 'class')) {
        const w = node.initializer || node.expression || node.right
        if (w && ts.isCallExpression(unwrap(w))) attrs.wrapped_by = unwrap(unwrap(w).expression).getText().slice(0, 60)
      }
      if (kind === 'store') { const a0 = body.arguments[0]; if (a0 && ts.isStringLiteralLike(a0)) attrs.store_id = a0.text }
      let isExported = false
      try { isExported = !qual && (name === 'default' || ts.isExportAssignment(node) || ts.isBinaryExpression(node) || (ts.getCombinedModifierFlags(node) & ts.ModifierFlags.Export) !== 0) } catch { }
      if (isExported) attrs.exported = true
      const id = mkId(kind, `${r}#${q}`)
      nodes.push({ id, kind, name: q, file: r, line: lineOf(node, sf), end_line: sf.getLineAndCharacterOfPosition(node.end).line + 1,
        doc: docOf(node), parent: parentId, attrs })
      declId.set(node, id)
      if (body && body !== node) declId.set(body, id)
      if (kind === 'type' && (ts.isInterfaceDeclaration(node) || (ts.isTypeAliasDeclaration(node) && ts.isTypeLiteralNode(node.type)))) ifaceMembers(node, q, id, r, sf)
      if (ts.isEnumDeclaration(node)) for (const m of node.members) {
        if (ts.isIdentifier(m.name) || ts.isStringLiteral(m.name)) addValue('enum_case', `${r}#${q}.${m.name.text}`, `${q}.${m.name.text}`, m, sf, r, id)
      }
      if (ts.isClassDeclaration(node)) for (const m of node.members) {    // `static readonly MAX = 3`
        const fl = ts.getCombinedModifierFlags(m)
        if (ts.isPropertyDeclaration(m) && ts.isIdentifier(m.name) && (fl & ts.ModifierFlags.Static) && (fl & ts.ModifierFlags.Readonly) && constInit(m.initializer))
          addValue('constant', `${r}#${q}.${m.name.text}`, `${q}.${m.name.text}`, m, sf, r, id)
        else if (ts.isPropertyDeclaration(m) && m.name && (ts.isIdentifier(m.name) || ts.isPrivateIdentifier(m.name)) && !(fl & ts.ModifierFlags.Static)
            && !(fl & ts.ModifierFlags.Abstract) && !(m.initializer && (isFn(unwrap(m.initializer)) || wrappedFn(unwrap(m.initializer)))))
          addField(node, q, m, sf, r, id, 'class')
        else if (ts.isConstructorDeclaration(m)) for (const p of m.parameters) {     // constructor(private api: Api)
          if (ts.isIdentifier(p.name) && (ts.getCombinedModifierFlags(p) & (ts.ModifierFlags.ParameterPropertyModifier)))
            addField(node, q, p, sf, r, id, 'constructor')
        }
      }
      // plain JavaScript: `this.x = ...` in the constructor or a method declares the field (#88); in a .ts file the
      // checker only accepts declared properties, so this only adds fields a JS class never declares
      if (ts.isClassDeclaration(node) && /\.(c|m)?jsx?$/.test(r)) {
        const declared = new Set(node.members.filter(m => m.name && (ts.isIdentifier(m.name) || ts.isPrivateIdentifier(m.name))).map(m => m.name.text))
        const scan = (n) => {
          if (ts.isBinaryExpression(n) && n.operatorToken.kind === ts.SyntaxKind.EqualsToken && ts.isPropertyAccessExpression(n.left)
              && n.left.expression.kind === ts.SyntaxKind.ThisKeyword && !declared.has(n.left.name.text)) {
            addField(node, q, n.left, sf, r, id, 'this'); fieldIds.set(n, fieldIds.get(n.left))
          }
          if (!ts.isClassLike(n) && !(ts.isFunctionLike(n) && !ts.isArrowFunction(n) && !ts.isMethodDeclaration(n) && !ts.isConstructorDeclaration(n) && !ts.isAccessor(n))) ts.forEachChild(n, scan)
        }
        for (const m of node.members) if ((ts.isConstructorDeclaration(m) || ts.isMethodDeclaration(m) || ts.isAccessor(m)) && m.body) ts.forEachChild(m.body, scan)
      }
      if (kind === 'type') return
      ts.forEachChild(node, c => visit(c, q, id))
      return
    }
    ts.forEachChild(node, c => visit(c, qual, parentId))
  }
  ts.forEachChild(sf, c => visit(c, '', fid))
}
for (const n of nodes) if (testFiles.has(path.resolve(ROOT, n.file)) && n.kind !== 'test') n.attrs = { ...(n.attrs || {}), test: true }
stats.test_files = testFiles.size
const nodeById = new Map(nodes.map(n => [n.id, n]))

// ---------- symbol -> target node ----------
const projectDecl = d => d && projectSf(d.getSourceFile())
// `new WebSocket(..)` on a class / function the project declares itself (a wrapper named like the browser API)
function ownClass(id) {
  let sym = null
  try { sym = checker.getSymbolAtLocation(id) } catch { return false }
  if (sym && sym.flags & ts.SymbolFlags.Alias) { try { sym = checker.getAliasedSymbol(sym) } catch { } }
  return !!(sym && (sym.declarations || []).some(d => projectDecl(d) && !ts.isImportSpecifier(d) && !ts.isImportClause(d) && !ts.isNamespaceImport(d)))
}
function declToNode(d) {
  if (!d) return null
  if (declId.has(d)) return declId.get(d)
  if ((ts.isArrowFunction(d) || ts.isFunctionExpression(d)) && declId.has(d.parent)) return declId.get(d.parent)
  // CommonJS `exports.x = fn`: the symbol's declaration is the left-hand property access
  if (ts.isPropertyAccessExpression(d) && d.parent && ts.isBinaryExpression(d.parent) && d.parent.left === d && declId.has(d.parent)) return declId.get(d.parent)
  if (ts.isSourceFile(d)) return fileNode.get(realFile(d)) || null
  return null
}
function importTypeTarget(d) {
  // .nuxt/types/imports.d.ts:  const useApi: typeof import('../../app/composables/useApi').useApi
  const tn = d.type
  if (!tn || !ts.isImportTypeNode(tn) || !tn.qualifier) return null
  const spec = tn.argument && tn.argument.literal && tn.argument.literal.text
  if (!spec) return null
  const res = host.resolveModuleNameLiterals([{ text: spec }], d.getSourceFile().fileName, undefined, options)[0]
  const fn = res && res.resolvedModule && res.resolvedModule.resolvedFileName
  const sf = fn && program.getSourceFile(fn)
  if (!sf) return null
  const msym = sf.symbol && checker.getMergedSymbol(sf.symbol)
  if (!msym) return null
  const name = ts.isIdentifier(tn.qualifier) ? tn.qualifier.text : tn.qualifier.right.text
  return checker.getExportsOfModule(msym).find(s => s.name === name) || null
}
// a Nuxt auto-import global (`const x: typeof import('…').x`), if that is all the symbol is
function autoImportDecl(sym) {
  const ds = sym.declarations || []
  return ds.length === 1 && ts.isVariableDeclaration(ds[0]) && ds[0].type && ts.isImportTypeNode(ds[0].type) ? ds[0] : null
}
// returns {id, conf, via} or null
function resolveSymbol(sym, conf = 'exact', via = []) {
  for (let i = 0; sym && i < 10; i++) {
    if (sym.flags & ts.SymbolFlags.Alias) {
      const ad = (sym.declarations || [])[0]
      const imp = ad && (ts.isImportClause(ad) ? ad.parent : (ts.isImportSpecifier(ad) ? ad.parent.parent.parent : null))
      if (imp && ts.isImportDeclaration(imp) && imp.moduleSpecifier.text.endsWith('.vue')) {
        const ms = checker.getSymbolAtLocation(imp.moduleSpecifier); const sd = ms && (ms.declarations || [])[0]
        if (sd && ts.isSourceFile(sd) && fileNode.has(realFile(sd))) return { id: fileNode.get(realFile(sd)), conf, via: [...via, 'import'] }
      }
      try { sym = checker.getAliasedSymbol(sym) } catch { return null } via.push('import'); continue
    }
    const decls = sym.declarations || []
    const d = decls.find(projectDecl) || decls[0]
    if (!d) return null
    const id = projectDecl(d) ? declToNode(d) : null
    if (id) return { id, conf, via }
    if (ts.isShorthandPropertyAssignment(d)) { sym = checker.getShorthandAssignmentValueSymbol(d); conf = 'resolved'; via.push('shorthand'); continue }
    if (ts.isPropertyAssignment(d) && d.initializer) { sym = checker.getSymbolAtLocation(unwrap(d.initializer)); conf = 'resolved'; via.push('property'); continue }
    if (ts.isBindingElement(d) && ts.isObjectBindingPattern(d.parent)) {
      const pname = (d.propertyName || d.name).getText()
      const t = checker.getTypeAtLocation(d.parent)
      const p = t && t.getProperty(pname)
      if (!p || p === sym) return null
      sym = p; conf = 'resolved'; via.push('destructure'); continue
    }
    if (ts.isVariableDeclaration(d)) {
      if (d.type && ts.isImportTypeNode(d.type)) { sym = importTypeTarget(d); conf = 'resolved'; via.push('nuxt-auto-import'); continue }
      const init = d.initializer && unwrap(d.initializer)
      if (init && (ts.isIdentifier(init) || ts.isPropertyAccessExpression(init)) && projectDecl(d)) { sym = checker.getSymbolAtLocation(init); conf = 'resolved'; via.push('alias'); continue }
    }
    if (ts.isExportSpecifier(d)) { try { sym = checker.getExportSpecifierLocalTargetSymbol(d) || null } catch { return null } continue }
    if (ts.isExportAssignment(d)) { const ex = unwrap(d.expression); const id = declToNode(ex) || (ts.isIdentifier(ex) ? null : null); if (id) return { id, conf, via }; sym = ts.isIdentifier(ex) ? checker.getSymbolAtLocation(ex) : null; continue }
    return null
  }
  return null
}

// ---------- string evaluation (URL templates) ----------
const PH = (n) => `\u0001${n}\u0002`
const MAXV = 16
function cross(a, b) { const o = []; for (const x of a) for (const y of b) { o.push(x + y); if (o.length >= MAXV) return o } return o }
function literalUnion(expr) {
  try {
    const t = checker.getTypeAtLocation(expr)
    const parts = t.isUnion() ? t.types : [t]
    if (parts.length > 8) return null
    const vals = []
    for (const p of parts) { if (p.isStringLiteral() || p.isNumberLiteral()) vals.push(String(p.value)); else return null }
    return vals.length ? vals : null
  } catch { return null }
}
function returnExprs(fn) {
  if (!fn) return []
  if (ts.isArrowFunction(fn) && !ts.isBlock(fn.body)) return [fn.body]
  const out = []
  const v = n => { if (ts.isReturnStatement(n) && n.expression) out.push(n.expression); if (!isFn(n) && !ts.isFunctionDeclaration(n)) ts.forEachChild(n, v) }
  if (fn.body) ts.forEachChild(fn.body, v)
  return out
}
function fnDeclOf(sym) {
  for (let i = 0; sym && i < 8; i++) {
    if (sym.flags & ts.SymbolFlags.Alias) { sym = checker.getAliasedSymbol(sym); continue }
    const d = (sym.declarations || [])[0]
    if (!d) return null
    if (ts.isFunctionDeclaration(d) || ts.isMethodDeclaration(d)) return d
    if (ts.isVariableDeclaration(d)) {
      if (d.type && ts.isImportTypeNode(d.type)) { sym = importTypeTarget(d); continue }
      const init = d.initializer && unwrap(d.initializer)
      if (isFn(init)) return init
      if (init && ts.isIdentifier(init)) { sym = checker.getSymbolAtLocation(init); continue }
      return null
    }
    if (ts.isPropertyAssignment(d) && isFn(unwrap(d.initializer))) return unwrap(d.initializer)
    if (ts.isShorthandPropertyAssignment(d)) { sym = checker.getShorthandAssignmentValueSymbol(d); continue }
    if (ts.isBindingElement(d) && ts.isObjectBindingPattern(d.parent)) {
      const t = checker.getTypeAtLocation(d.parent); const p = t && t.getProperty((d.propertyName || d.name).getText())
      if (!p || p === sym) return null; sym = p; continue
    }
    return null
  }
  return null
}
// configured values: runtime config (useRuntimeConfig().public.X / config.public.X) and env (process.env.X,
// import.meta.env.X) become {runtimeConfig.X} / {env.X} placeholders; the Python side folds in their values
// (nuxt.config runtimeConfig defaults, .env files). `X || 'default'` records the in-code default.
const CONF_PH = /^\u0001(runtimeConfig|env)\.[^\u0002]*\u0002$/
const configDefaults = {}
function envKey(e) {
  e = unwrap(e)
  if (!(ts.isPropertyAccessExpression(e) || ts.isElementAccessExpression(e))) return null
  const name = ts.isPropertyAccessExpression(e) ? e.name.text : (ts.isStringLiteralLike(unwrap(e.argumentExpression)) ? unwrap(e.argumentExpression).text : null)
  if (!name) return null
  const o = unwrap(e.expression).getText().replace(/\s+/g, '')
  if (o === 'process.env' || o === 'import.meta.env' || o === 'Bun.env' || o === 'Deno.env') return 'env.' + name
  if (/(^|\.)public$|useRuntimeConfig\(\)$|^\$config$|runtimeConfig$/.test(o)) return 'runtimeConfig.' + name
  return null
}
// destructured config: const { apiBase } = useRuntimeConfig().public / const { public: { apiBase } } = useRuntimeConfig()
function bindingConfigKey(d) {
  if (!ts.isBindingElement(d)) return null
  const name = (d.propertyName || d.name).getText()
  let pat = d.parent, outer = []
  while (pat && ts.isBindingElement(pat.parent)) { outer.unshift((pat.parent.propertyName || pat.parent.name).getText()); pat = pat.parent.parent }
  const decl = pat && pat.parent
  if (!decl || !ts.isVariableDeclaration(decl) || !decl.initializer) return null
  const init = unwrap(decl.initializer).getText().replace(/\s+/g, '')
  const full = [init, ...outer].join('.')
  if (/^(process\.env|import\.meta\.env)$/.test(full)) return 'env.' + name
  if (/(useRuntimeConfig\(\)|\$config|runtimeConfig)(\.public)?$/.test(full) || /(^|\.)public$/.test(full)) {
    if (/(useRuntimeConfig\(\)|\$config|runtimeConfig)$/.test(full) && name === 'public') return null
    return 'runtimeConfig.' + name
  }
  if (/^(config|runtimeConfig)$/.test(init) && outer[0] === 'public') return 'runtimeConfig.' + name
  return null
}
// ref / computed unwrapping: x.value where x = computed(() => expr) | ref(expr) | useX() returning one of them
function refValue(e, depth, ctx) {
  e = unwrap(e)
  if (!e || depth > 8) return null
  if (ts.isIdentifier(e)) {
    const sym = checker.getSymbolAtLocation(e)
    const d = sym && (sym.declarations || [])[0]
    if (d && ts.isVariableDeclaration(d) && d.initializer) return refValue(d.initializer, depth + 1, ctx)
    return null
  }
  if (!ts.isCallExpression(e)) return null
  const callee = unwrap(e.expression)
  const cname = ts.isIdentifier(callee) ? callee.text : (ts.isPropertyAccessExpression(callee) ? callee.name.text : '')
  // computed(() => expr) only: a ref's initial value (ref(null), ref('')) is not what it holds at request time
  if (cname === 'computed') {
    const a = unwrap(e.arguments[0])
    if (!a) return null
    if (isFn(a)) {
      const rs = returnExprs(a)
      if (!rs.length || rs.length > 4) return null
      let vals = []
      for (const r of rs) vals.push(...evalStr(r, depth + 1, ctx).vals)
      return { vals: [...new Set(vals)].slice(0, MAXV), conf: 'resolved' }
    }
    return null
  }
  const fn = fnDeclOf(checker.getSymbolAtLocation(ts.isPropertyAccessExpression(callee) ? callee.name : callee))
  if (fn && projectDecl(fn)) {
    const rs = returnExprs(fn)
    if (!rs.length || rs.length > 4) return null
    let vals = []
    for (const r of rs) { const v = refValue(r, depth + 1, { fn, args: e.arguments, outer: ctx }); if (v) vals.push(...v.vals) }
    return vals.length ? { vals: [...new Set(vals)].slice(0, MAXV), conf: 'resolved' } : null
  }
  return null
}
// evalStr -> {vals:[...], conf:'exact'|'resolved', params:Set}
function evalStr(e, depth = 0, ctx = {}) {
  e = unwrap(e)
  const R = (vals, conf = 'exact') => ({ vals, conf })
  if (!e || depth > 12) return R([PH('?')], 'resolved')
  if (ts.isStringLiteralLike(e)) return R([e.text])
  if (ts.isNumericLiteral(e)) return R([e.text])
  if (ts.isTemplateExpression(e)) {
    let vals = [e.head.text], conf = 'exact'
    for (const sp of e.templateSpans) {
      const v = evalStr(sp.expression, depth + 1, ctx)
      if (v.conf !== 'exact') conf = 'resolved'
      vals = cross(cross(vals, v.vals), [sp.literal.text])
    }
    return R(vals, conf)
  }
  if (ts.isBinaryExpression(e) && e.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    const a = evalStr(e.left, depth + 1, ctx), b = evalStr(e.right, depth + 1, ctx)
    return R(cross(a.vals, b.vals), a.conf === 'exact' && b.conf === 'exact' ? 'exact' : 'resolved')
  }
  if (ts.isConditionalExpression(e)) {
    const a = evalStr(e.whenTrue, depth + 1, ctx), b = evalStr(e.whenFalse, depth + 1, ctx)
    return R([...new Set([...a.vals, ...b.vals])].slice(0, MAXV), 'resolved')
  }
  if (ts.isIdentifier(e)) {
    const sym = e.parent && ts.isShorthandPropertyAssignment(e.parent) && e.parent.name === e
      ? checker.getShorthandAssignmentValueSymbol(e.parent) : checker.getSymbolAtLocation(e)
    const d = sym && (sym.declarations || [])[0]
    if (d && ts.isParameter(d)) {
      if (ctx.args && ctx.fn && d.parent === ctx.fn) {
        const idx = ctx.fn.parameters.indexOf(d)
        const a = ctx.args[idx]
        if (a && ts.isNumericLiteral(unwrap(a))) return R([PH(e.text)], 'resolved')  // ids stay parameters
        if (a) { const v = evalStr(a, depth + 1, ctx.outer || {}); return R(v.vals, 'resolved') }
      }
      const lu = literalUnion(e); if (lu) return R(lu, 'resolved')
      ctx.params && ctx.params.add(e.text)
      return R([PH(e.text)])
    }
    if (d && ts.isBindingElement(d)) { const k = bindingConfigKey(d); if (k) return R([PH(k)]) }
    if (d && ts.isVariableDeclaration(d) && d.initializer && (ts.getCombinedNodeFlags(d) & ts.NodeFlags.Const)) {
      const v = evalStr(d.initializer, depth + 1, ctx)
      if (v.vals.length === 1 && CONF_PH.test(v.vals[0])) return R(v.vals, v.conf)
      if (v.vals.length === 1 && /^\u0001[^\u0002]*\u0002$/.test(v.vals[0])) return R([PH(e.text)], v.conf)
      return R(v.vals, v.conf === 'exact' && !(d.getSourceFile() !== e.getSourceFile()) ? 'exact' : 'resolved')
    }
    if (d && ts.isVariableDeclaration(d) && d.initializer && (ts.getCombinedNodeFlags(d) & ts.NodeFlags.Let)) {
      const v = evalStr(d.initializer, depth + 1, ctx)
      if (v.vals.length === 1 && /^\u0001[^\u0002]*\u0002$/.test(v.vals[0])) return R(v.vals, 'resolved')
    }
    const lu = literalUnion(e); if (lu) return R(lu, 'resolved')
    return R([PH(e.text)])
  }
  const ek = envKey(e)
  if (ek) return R([PH(ek)])
  if (ts.isPropertyAccessExpression(e) && e.name.text === 'value') {
    const rv = refValue(e.expression, depth + 1, ctx)
    if (rv && rv.vals.length && !rv.vals.some(v => v.includes(PH('?')))) return R(rv.vals, 'resolved')
  }
  if (ts.isElementAccessExpression(e) || ts.isPropertyAccessExpression(e)) {
    const objSym = checker.getSymbolAtLocation(unwrap(e.expression))
    const od = objSym && (objSym.declarations || [])[0]
    const obj = od && ts.isVariableDeclaration(od) && od.initializer && unwrap(od.initializer)
    if (obj && ts.isObjectLiteralExpression(obj)) {
      const key = ts.isPropertyAccessExpression(e) ? e.name.text : (ts.isStringLiteralLike(unwrap(e.argumentExpression)) ? unwrap(e.argumentExpression).text : null)
      const props = obj.properties.filter(p => ts.isPropertyAssignment(p) && (key === null || p.name.getText().replace(/['"]/g, '') === key))
      if (props.length) {
        let vals = []
        for (const p of props) vals.push(...evalStr(p.initializer, depth + 1, ctx).vals)
        return R([...new Set(vals)].slice(0, MAXV), 'resolved')
      }
    }
    const lu = literalUnion(e); if (lu) return R(lu, 'resolved')
    return R([PH(ts.isPropertyAccessExpression(e) ? e.name.text : '?')])
  }
  if (ts.isCallExpression(e)) {
    const callee = unwrap(e.expression)
    const cname = ts.isIdentifier(callee) ? callee.text : (ts.isPropertyAccessExpression(callee) ? callee.name.text : '')
    // value-preserving wrappers do not count against the depth budget
    if (['encodeURIComponent', 'encodeURI', 'String', 'Number'].includes(cname) && e.arguments[0]) {
      const v = evalStr(e.arguments[0], depth, ctx)
      return R(v.vals, 'resolved')
    }
    if (['toString', 'trim', 'replace', 'replaceAll', 'toLowerCase', 'toUpperCase'].includes(cname) && ts.isPropertyAccessExpression(callee)) {
      const v = evalStr(callee.expression, depth, ctx)
      return R(v.vals, cname === 'toString' ? v.conf : 'resolved')
    }
    const fn = fnDeclOf(checker.getSymbolAtLocation(ts.isPropertyAccessExpression(callee) ? callee.name : callee))
    if (fn && projectDecl(fn)) {
      const rs = returnExprs(fn)
      if (rs.length && rs.length <= 4) {
        let vals = []
        for (const r of rs) vals.push(...evalStr(r, depth + 1, { fn, args: e.arguments, outer: ctx }).vals)
        return R([...new Set(vals)].slice(0, MAXV), 'resolved')
      }
    }
    const lu = literalUnion(e); if (lu) return R(lu, 'resolved')
    return R([PH(cname ? cname + '()' : '?')])
  }
  if (ts.isBinaryExpression(e) && (e.operatorToken.kind === ts.SyntaxKind.BarBarToken || e.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken)) {
    const l = evalStr(e.left, depth + 1, ctx)
    if (l.vals.length === 1 && CONF_PH.test(l.vals[0])) {
      const r = evalStr(e.right, depth + 1, ctx)
      const lits = r.vals.filter(v => v && !v.includes('\u0001'))
      if (lits.length) configDefaults[l.vals[0].slice(1, -1)] = lits[0]
      return R(l.vals, 'resolved')
    }
    if (l.vals.length === 1 && /^\u0001[^\u0002]*\u0002$/.test(l.vals[0])) return R(l.vals, 'resolved')
    return R([PH(e.left.getText().split('.').pop().replace(/\W/g, '') || '?')])
  }
  const lu = literalUnion(e); if (lu) return R(lu, 'resolved')
  return R([PH('?')])
}
const render = s => s.replace(/\u0001([^\u0002]*)\u0002/g, (_, n) => `{${n}}`)
function shapeDedupe(vals) {
  const seen = new Map()
  for (const v of vals) {
    const shape = v.replace(/\u0001[^\u0002]*\u0002/g, '\u0001\u0002')
    const prev = seen.get(shape)
    if (prev === undefined || (/\u0001\?\u0002/.test(prev) && !/\u0001\?\u0002/.test(v))) seen.set(shape, v)
  }
  return [...seen.values()]
}

// axios instance origin: follow the receiver back to an axios.create({...}) call, return its config object
function axiosCreateOf(expr, depth = 0) {
  expr = unwrap(expr)
  if (!expr || depth > 8) return null
  if (ts.isCallExpression(expr)) {
    const c = unwrap(expr.expression)
    if (ts.isPropertyAccessExpression(c) && c.name.text === 'create' && isAxiosType(c.expression)) return { cfg: expr.arguments[0], via: ['axios.create'] }
    if (ts.isPropertyAccessExpression(c) && c.name.text === 'create' && FW.importSource && FW.importSource(c.expression) === 'axios') return { cfg: expr.arguments[0], via: ['axios.create'] }
    // call returning an instance
    const fn = fnDeclOf(checker.getSymbolAtLocation(ts.isPropertyAccessExpression(c) ? c.name : c))
    for (const r of returnExprs(fn)) { const o = axiosCreateOf(r, depth + 1); if (o) return o }
    return null
  }
  let sym = ts.isPropertyAccessExpression(expr) ? checker.getSymbolAtLocation(expr.name) : checker.getSymbolAtLocation(expr)
  for (let i = 0; sym && i < 10; i++) {
    if (sym.flags & ts.SymbolFlags.Alias) { sym = checker.getAliasedSymbol(sym); continue }
    const ai = autoImportDecl(sym)
    if (ai) { sym = importTypeTarget(ai); continue }
    const d = (sym.declarations || []).find(projectDecl)
    if (!d) return null
    if ((ts.isVariableDeclaration(d) || ts.isPropertyDeclaration(d)) && d.initializer) { const o = axiosCreateOf(d.initializer, depth + 1); return o && { cfg: o.cfg, via: [...o.via, 'var'] } }
    if (ts.isShorthandPropertyAssignment(d)) { sym = checker.getShorthandAssignmentValueSymbol(d); continue }
    if (ts.isPropertyAssignment(d)) return axiosCreateOf(d.initializer, depth + 1)
    if (ts.isBindingElement(d) && ts.isObjectBindingPattern(d.parent)) {
      const t = checker.getTypeAtLocation(d.parent); const p = t && t.getProperty((d.propertyName || d.name).getText())
      if (!p || p === sym) return null; sym = p; continue
    }
    return null
  }
  return null
}
function isAxiosType(expr) {
  try {
    const t = checker.getTypeAtLocation(expr)
    const s = checker.typeToString(t)
    if (/\bAxios(Instance|Static)\b/.test(s) || (t.symbol && /^Axios(Instance|Static)$/.test(t.symbol.name))) return true
    // axios types not installed: the receiver is the axios import itself
    return (s === 'any' || /^typeof /.test(s) || s === 'error') && FW.importSource && FW.importSource(expr) === 'axios' && (ts.isIdentifier(unwrap(expr)))
  } catch { return false }
}
const FW = {}   // filled with fw.mjs helpers (importSource) once the program is built
// instance created by <lib>.create/extend({...}) (ky, ofetch, $fetch): {lib, cfg}
function clientCreateOf(expr, depth = 0) {
  expr = unwrap(expr)
  if (!expr || depth > 6) return null
  if (ts.isCallExpression(expr)) {
    const c = unwrap(expr.expression)
    if (ts.isPropertyAccessExpression(c) && ['create', 'extend'].includes(c.name.text)) {
      const lib = FW.importSource ? FW.importSource(c.expression) : null
      const base = ts.isIdentifier(unwrap(c.expression)) ? unwrap(c.expression).text : ''
      if (lib === 'ky' || lib === 'ofetch' || base === '$fetch' || base === 'ofetch') return { lib: lib || base, cfg: expr.arguments[0] }
      const inner = clientCreateOf(c.expression, depth + 1)   // api.extend({...}) on an instance
      if (inner) return { lib: inner.lib, cfg: expr.arguments[0] || inner.cfg, parent: inner }
    }
    // factory: useApiClient() returning $fetch.create({...}) / ky.create({...})
    const fn = fnDeclOf(checker.getSymbolAtLocation(ts.isPropertyAccessExpression(c) ? c.name : c))
    if (fn && projectDecl(fn)) {
      for (const r of returnExprs(fn)) { const o = clientCreateOf(r, depth + 1); if (o) return o }
    }
    return null
  }
  let sym = ts.isPropertyAccessExpression(expr) ? checker.getSymbolAtLocation(expr.name) : checker.getSymbolAtLocation(expr)
  for (let i = 0; sym && i < 8; i++) {
    if (sym.flags & ts.SymbolFlags.Alias) { try { sym = checker.getAliasedSymbol(sym) } catch { return null } continue }
    const ai = autoImportDecl(sym)
    if (ai) { sym = importTypeTarget(ai); continue }
    const d = (sym.declarations || []).find(projectDecl)
    if (!d) return null
    if (ts.isVariableDeclaration(d) && d.initializer) return clientCreateOf(d.initializer, depth + 1)
    if (ts.isPropertyDeclaration(d) && d.initializer) return clientCreateOf(d.initializer, depth + 1)
    return null
  }
  return null
}
function objProp(obj, name) {
  obj = unwrap(obj)
  if (!obj || !ts.isObjectLiteralExpression(obj)) return null
  for (const p of obj.properties) {
    if (ts.isPropertyAssignment(p) && p.name.getText().replace(/['"]/g, '') === name) return p.initializer
    if (ts.isShorthandPropertyAssignment(p) && p.name.text === name) return p.name
  }
  return null
}

// ---------- request keys (query params / body) ----------
function requestKeys(e, depth = 0) {
  e = unwrap(e)
  const res = { keys: new Set(), conditional: new Set(), opaque: false }
  if (!e || depth > 4) { res.opaque = true; return res }
  const merge = (o, cond) => { for (const k of o.keys) (cond ? res.conditional : res.keys).add(k); for (const k of o.conditional) res.conditional.add(k); if (o.opaque) res.opaque = true; if (o.forwarded) { res.forwarded = o.forwarded; res.forwarded_index = o.forwarded_index } }
  if (ts.isObjectLiteralExpression(e)) {
    for (const p of e.properties) {
      if (ts.isSpreadAssignment(p)) merge(requestKeys(p.expression, depth + 1), false)
      else if (p.name) res.keys.add(p.name.getText().replace(/['"]/g, ''))
    }
    return res
  }
  if (ts.isIdentifier(e)) {
    const sym = e.parent && ts.isShorthandPropertyAssignment(e.parent) && e.parent.name === e
      ? checker.getShorthandAssignmentValueSymbol(e.parent) : checker.getSymbolAtLocation(e)
    const d = sym && (sym.declarations || [])[0]
    if (d && ts.isVariableDeclaration(d) && d.initializer) {
      merge(requestKeys(d.initializer, depth + 1), false)
      // later `x.key = ...` assignments in the same function; conditional when nested in an if
      const scope = d.parent && d.parent.parent && d.parent.parent.parent
      const v = n => {
        if (ts.isBinaryExpression(n) && n.operatorToken.kind === ts.SyntaxKind.EqualsToken && ts.isPropertyAccessExpression(n.left)
            && ts.isIdentifier(n.left.expression) && n.left.expression.text === e.text) {
          let c = n.parent, cond = false
          while (c && c !== scope) { if (ts.isIfStatement(c) || ts.isConditionalExpression(c)) cond = true; c = c.parent }
          (cond ? res.conditional : res.keys).add(n.left.name.text)
        }
        ts.forEachChild(n, v)
      }
      if (scope) v(scope)
      return res
    }
    if (d && ts.isParameter(d)) {
      // forwarded parameter: keys come from its declared type (optional props -> conditional); the call
      // sites' object keys (CALLS.arg_keys) say what is actually passed
      const ty = checker.getNonNullableType(checker.getTypeAtLocation(d))
      // only plain object shapes carry request keys: not primitives / literal unions (String's methods), arrays,
      // functions, URLSearchParams…
      const plainObj = t => t.isUnion() ? t.types.every(plainObj) : t.isIntersection() ? t.types.some(plainObj)
        : !!(t.flags & ts.TypeFlags.Object) && !checker.isArrayLikeType(t) && !t.getCallSignatures().length &&
          !(t.getSymbol() && /^(URLSearchParams|FormData|Map|Set|Headers|Date|Blob|File)$/.test(t.getSymbol().name))
      const props = ty && plainObj(ty) ? checker.getPropertiesOfType(ty).filter(p => !p.name.startsWith('__@')) : []
      if (props.length && props.length <= 80) {
        for (const p of props) ((p.flags & ts.SymbolFlags.Optional) ? res.conditional : res.keys).add(p.name)
        res.forwarded = d.name && ts.isIdentifier(d.name) ? d.name.text : '?'
        res.forwarded_index = d.parent && d.parent.parameters ? d.parent.parameters.indexOf(d) : undefined
      } else res.opaque = true
      return res
    }
  }
  if (ts.isCallExpression(e)) {
    const c = unwrap(e.expression)
    const fn = fnDeclOf(checker.getSymbolAtLocation(ts.isPropertyAccessExpression(c) ? c.name : c))
    if (fn && projectDecl(fn)) { for (const r of returnExprs(fn)) merge(requestKeys(r, depth + 1), false); return res }
  }
  res.opaque = true
  return res
}
const keysOut = r => ({ keys: [...r.keys].sort(), conditional: [...r.conditional].filter(k => !r.keys.has(k)).sort(), opaque: r.opaque || undefined, forwarded: r.forwarded || undefined, forwarded_index: r.forwarded ? r.forwarded_index : undefined })

// ---------- framework facts helpers (import sources etc.) ----------
// test code (spec files, test trees) stays in the graph as tests, but its routers, controllers and handlers are test
// setups, never application routes: framework facts come from application files only
const appSourceFiles = sourceFiles.filter(sf => !testFiles.has(realFile(sf)))
const FWX = { ts, checker, program, sourceFiles: appSourceFiles, rel, realFile, lineOf, declId, declToNode, resolveSymbol, evalStr, render, unwrap, projectSf, fileNode }
let fwFacts = null
const tFw0 = Date.now()
fwFacts = collectFrameworkFacts(FWX)
FW.importSource = FWX.importSource
const tFw = Date.now() - tFw0

// ---------- pass 2: references ----------
const edges = []
const apiCalls = []
const i18nUses = []
const fallbacks = []
const FALLBACK_OPS = new Set([ts.SyntaxKind.QuestionQuestionToken, ts.SyntaxKind.BarBarToken])
const pageMeta = {}
const edgeSeen = new Set()
function addEdge(src, dst, kind, file, line, conf, attrs) {
  if (!src || !dst || (src === dst && kind === 'CALLS')) return
  const k = `${src}|${dst}|${kind}|${line}`
  if (edgeSeen.has(k)) return
  edgeSeen.add(k)
  edges.push({ src, dst, kind, file, line, confidence: conf, attrs: attrs || undefined })
}
const callSites = []

// ---------- class hierarchy: EXTENDS / IMPLEMENTS between project classes, OVERRIDDEN_BY base method -> override ----------
// (a call on a base-typed value resolves to the base declaration; the override edge lets impact / tests reach the
// callers of the base from an override and the callers of the overrides from the base)
{
  const memberNode = (cls, name) => {
    for (const m of cls.members || []) {
      if (m.name && (ts.isIdentifier(m.name) || ts.isPrivateIdentifier(m.name) || ts.isStringLiteralLike(m.name)) && m.name.text === name
          && (ts.isMethodDeclaration(m) || ts.isGetAccessor(m) || ts.isSetAccessor(m) || ts.isPropertyDeclaration(m)) && declId.has(m)) return declId.get(m)
    }
    return null
  }
  const baseClassOf = cls => {
    for (const h of cls.heritageClauses || []) {
      if (h.token !== ts.SyntaxKind.ExtendsKeyword || !h.types.length) continue
      let sym = null
      try { sym = checker.getSymbolAtLocation(h.types[0].expression) } catch { }
      for (let i = 0; sym && (sym.flags & ts.SymbolFlags.Alias) && i < 6; i++) { try { sym = checker.getAliasedSymbol(sym) } catch { sym = null } }
      const d = sym && (sym.declarations || []).find(x => ts.isClassDeclaration(x) || ts.isClassExpression(x))
      return d && projectDecl(d) ? d : null
    }
    return null
  }
  const implementedClasses = cls => {
    const out = []
    for (const h of cls.heritageClauses || []) {
      if (h.token !== ts.SyntaxKind.ImplementsKeyword) continue
      for (const t of h.types) {
        let sym = null
        try { sym = checker.getSymbolAtLocation(t.expression) } catch { }
        for (let i = 0; sym && (sym.flags & ts.SymbolFlags.Alias) && i < 6; i++) { try { sym = checker.getAliasedSymbol(sym) } catch { sym = null } }
        const d = sym && (sym.declarations || []).find(x => ts.isClassDeclaration(x))
        if (d && projectDecl(d)) out.push(d)
      }
    }
    return out
  }
  // interface members: `class X implements I` (also through X's base classes and I's own `extends`)
  const ifaceMember = (d, name, depth = 0) => {
    const members = ts.isInterfaceDeclaration(d) ? d.members : (d.type && d.type.members) || []
    for (const m of members) if (m.name && (ts.isIdentifier(m.name) || ts.isStringLiteralLike(m.name)) && m.name.text === name && declId.has(m)) return declId.get(m)
    if (depth < 8) for (const h of d.heritageClauses || []) for (const t of h.types) {
      let sym = null
      try { sym = aliasTarget(checker.getSymbolAtLocation(t.expression)) } catch { }
      for (const x of ifaceDecls(sym)) { const hit = ifaceMember(x, name, depth + 1); if (hit) return hit }
    }
    return null
  }
  const ownIfaces = cls => {
    const out = []
    for (const h of cls.heritageClauses || []) {
      if (h.token !== ts.SyntaxKind.ImplementsKeyword) continue
      for (const t of h.types) {
        let sym = null
        try { sym = aliasTarget(checker.getSymbolAtLocation(t.expression)) } catch { }
        for (const d of ifaceDecls(sym)) if (projectDecl(d)) out.push(d)
      }
    }
    return out
  }
  const memberName = m => m.name && (ts.isIdentifier(m.name) || ts.isStringLiteralLike(m.name)) ? m.name.text : null
  const implementsIfaces = (decl, r, sf, ifaces, conf, attrs, inherited) => {
    let n = 0
    for (const m of decl.members || []) {
      if (!(ts.isMethodDeclaration(m) || ts.isGetAccessor(m) || ts.isSetAccessor(m) || ts.isPropertyDeclaration(m)) || !declId.has(m)) continue
      if (!declId.get(m).startsWith('method:') || (m.modifiers && m.modifiers.some(x => x.kind === ts.SyntaxKind.StaticKeyword))) continue
      const nm = memberName(m)
      if (!nm) continue
      for (const [d, inh] of ifaces) {
        if (inh && inherited(nm)) continue      // the base class's own member implements it
        const im = ifaceMember(d, nm)
        if (im && im !== declId.get(m)) { addEdge(im, declId.get(m), 'IMPLEMENTED_BY', r, lineOf(m, sf), conf, attrs); n++ }
      }
    }
    return n
  }
  let nh = 0, no = 0
  for (const [decl, id] of declId) {
    if (!ts.isClassDeclaration(decl) || !id.startsWith('class:')) continue
    const sf = decl.getSourceFile()
    if (!projectSf(sf)) continue
    const r = rel(realFile(sf))
    for (const h of decl.heritageClauses || []) {
      const kind = h.token === ts.SyntaxKind.ExtendsKeyword ? 'EXTENDS' : 'IMPLEMENTS'
      for (const t of h.types) {
        let sym = null
        try { sym = checker.getSymbolAtLocation(t.expression) } catch { }
        const tgt = sym && resolveSymbol(sym)
        if (tgt && /^(class|type):/.test(tgt.id) && tgt.id !== id) { addEdge(id, tgt.id, kind, r, lineOf(t, sf), tgt.conf); nh++ }
      }
    }
    // each method overrides the nearest base class declaring a member of that name
    for (const m of decl.members || []) {
      if (!(ts.isMethodDeclaration(m) || ts.isGetAccessor(m) || ts.isSetAccessor(m)) || !declId.has(m) || !m.name || !ts.isIdentifier(m.name)) continue
      if (m.modifiers && m.modifiers.some(x => x.kind === ts.SyntaxKind.StaticKeyword)) continue
      let b = baseClassOf(decl), hit = null
      for (let i = 0; b && !hit && i < 12; i++) { hit = memberNode(b, m.name.text); if (!hit) b = baseClassOf(b) }
      if (hit && hit !== declId.get(m)) { addEdge(hit, declId.get(m), 'OVERRIDDEN_BY', r, lineOf(m, sf), 'exact'); no++ }
      // `implements AbstractRepo` (a class used as an interface): its declared methods are implemented here
      for (const ic of implementedClasses(decl)) {
        const im = memberNode(ic, m.name.text)
        if (im && im !== declId.get(m) && im !== hit) { addEdge(im, declId.get(m), 'IMPLEMENTED_BY', r, lineOf(m, sf), 'exact'); no++ }
      }
    }
    // interfaces this class or a base class implements -> their members
    const ifaces = ownIfaces(decl).map(d => [d, false])
    for (let b = baseClassOf(decl), i = 0; b && i < 12; b = baseClassOf(b), i++) for (const d of ownIfaces(b)) ifaces.push([d, true])
    if (ifaces.length) {
      const inherited = nm => { for (let b = baseClassOf(decl), i = 0; b && i < 12; b = baseClassOf(b), i++) if (memberNode(b, nm)) return true; return false }
      no += implementsIfaces(decl, r, sf, ifaces, 'exact', undefined, inherited)
    }
  }
  // structural: `new X()` where an interface type is expected (`const api: FeedAPI = new X()`, an argument, a return
  // value) and X does not declare `implements` it
  for (const sf of sourceFiles) {
    if (realFile(sf).endsWith('.vue') || !sf.text.includes('new ')) continue
    const r = rel(realFile(sf))
    const done = new Set()
    const walk = n => {
      if (ts.isNewExpression(n)) {
        let ct = null, sym = null
        try { ct = checker.getContextualType(n) } catch { }
        const ts0 = ct && (ct.aliasSymbol || ct.getSymbol())
        const ids = ts0 ? ifaceDecls(ts0).filter(projectDecl) : []
        if (ids.length) {
          try { sym = aliasTarget(checker.getSymbolAtLocation(n.expression)) } catch { }
          const cd = sym && (sym.declarations || []).find(x => ts.isClassDeclaration(x) && projectDecl(x) && declId.has(x))
          if (cd) for (const d of ids) {
            const key = `${declId.get(cd)}|${declId.get(d)}`
            if (done.has(key) || ownIfaces(cd).includes(d)) continue
            done.add(key)
            const csf = cd.getSourceFile()
            no += implementsIfaces(cd, rel(realFile(csf)), csf, [[d, false]], 'resolved', { via: ['structural'], at: `${r}:${lineOf(n, sf)}` }, () => false)
          }
        }
      }
      ts.forEachChild(n, walk)
    }
    walk(sf)
  }
  // object literals where an interface is expected (`const api: FeedAPI = { fetch() {..} }`, a factory returning one,
  // an argument, `satisfies`): their function members implement the interface's; a class passed as a value where a
  // constructor of an interface is expected (`register(FollowingFeedAPI)`): structurally, as for `new X()`
  let nol = 0
  for (const sf of sourceFiles) {
    if (realFile(sf).endsWith('.vue')) continue
    const r = rel(realFile(sf))
    const done = new Set()
    const walk = n => {
      if (ts.isObjectLiteralExpression(n)) {
        const fm = fnMembers(n).filter(p => declId.has(p))
        if (fm.length) for (const d of ctxIfaces(n)) for (const p of fm) {
          const nm = memberName(p), im = nm && ifaceMember(d, nm)
          if (im && im !== declId.get(p)) { addEdge(im, declId.get(p), 'IMPLEMENTED_BY', r, lineOf(p, sf), 'exact', { via: ['object_literal'] }); nol++ }
        }
      } else {
        const cd = classOfValue(n)
        if (cd && declId.has(cd)) for (const d of ctorIfaces(n)) {
          const key = `${declId.get(cd)}|${declId.get(d)}`
          if (done.has(key) || ownIfaces(cd).includes(d)) continue
          done.add(key)
          const csf = cd.getSourceFile()
          no += implementsIfaces(cd, rel(realFile(csf)), csf, [[d, false]], 'resolved', { via: ['structural'], at: `${r}:${lineOf(n, sf)}` }, () => false)
        }
      }
      ts.forEachChild(n, walk)
    }
    walk(sf)
  }
  // mixin members implement the merged interface's members (`interface Client extends UsersMix` + `class Client
  // extends mix(Base).with(Users)`)
  let nmx = 0
  for (const [decl, id] of declId) {
    if (!ts.isClassDeclaration(decl) || !id.startsWith('class:') || !projectSf(decl.getSourceFile())) continue
    const mix = mixinClasses(decl)
    if (!mix.length) continue
    const ifs = mergedIfaces(decl)
    if (!ifs.length) continue
    const at = `${rel(realFile(decl.getSourceFile()))}:${lineOf(decl, decl.getSourceFile())}`
    for (const ce of mix) {
      const csf = ce.getSourceFile(), cr = rel(realFile(csf))
      for (const m of ce.members || []) {
        if (!declId.has(m) || !declId.get(m).startsWith('method:') || (m.modifiers && m.modifiers.some(x => x.kind === ts.SyntaxKind.StaticKeyword))) continue
        const nm = memberName(m)
        if (!nm) continue
        for (const d of ifs) {
          const im = ifaceMember(d, nm)
          if (im && im !== declId.get(m)) { addEdge(im, declId.get(m), 'IMPLEMENTED_BY', cr, lineOf(m, csf), 'resolved', { via: ['mixin'], at }); nmx++; break }
        }
      }
    }
  }
  if (nmx) { stats.mixin_impl_edges = nmx; no += nmx }
  if (nol) { stats.object_literal_impl_edges = nol; no += nol }
  if (nh) stats.class_heritage_edges = nh
  if (no) stats.override_edges = no
}
const deferredParamCalls = []   // api calls whose URL depends on an enclosing-function parameter
const subscriptions = []        // realtime channel subscriptions (laravel-echo, pusher-js, useEcho hooks)
const visits = []               // browser tests opening a page: page.goto('/x'), cy.visit('/x')

// ---------- realtime: laravel-echo / pusher-js ----------
const ECHO_SUB = { private: 'private', channel: 'public', join: 'presence', encryptedPrivate: 'private-encrypted' }
const ECHO_HOOKS = { useEcho: 'private', useEchoPublic: 'public', useEchoPresence: 'presence', useEchoNotification: 'private', useEchoModel: 'private' }
function declOfExpr(e) {
  try {
    const sym = checker.getSymbolAtLocation(ts.isPropertyAccessExpression(e) ? e.name : e)
    return sym && (sym.declarations || [])[0]
  } catch { return null }
}
// how sure we are that `expr` is a laravel-echo (lib='echo') / pusher-js (lib='pusher') client: 'exact' | 'resolved' | 'heuristic' | null
function clientKind(expr, lib) {
  const e = unwrap(expr)
  if (!e) return null
  const tre = lib === 'echo' ? /\bEcho\b/ : /\bPusher\b/
  const pkg = lib === 'echo' ? 'laravel-echo' : 'pusher-js'
  try { const s = checker.typeToString(checker.getNonNullableType(checker.getTypeAtLocation(e))); if (tre.test(s)) return 'exact' } catch { }
  if (FW.importSource && FW.importSource(e) === pkg) return 'exact'
  const d = declOfExpr(e)
  if (d && (ts.isVariableDeclaration(d) || ts.isPropertyDeclaration(d) || ts.isParameter(d) || ts.isPropertySignature(d))) {
    if (d.type && tre.test(d.type.getText())) return 'exact'
    if (d.initializer && new RegExp('new\\s+' + (lib === 'echo' ? 'Echo' : 'Pusher') + '\\b').test(d.initializer.getText())) return 'exact'
  }
  const txt = e.getText()
  if (lib === 'echo' && /^(window\.|globalThis\.)?Echo$/.test(txt)) return 'resolved'
  if (new RegExp('(^|\\.)\\$?' + lib + '(Instance|Client)?$', 'i').test(txt)) return 'heuristic'
  return null
}
function strVals(e) {
  if (!e) return []
  const v = evalStr(e)
  return shapeDedupe(v.vals).map(render)
}
// .listen('X') / .listenToAll / .notification / .listenForWhisper chained on (or called on a const holding) a subscription
function listenedEvents(call) {
  const ev = new Set()
  const add = (m, c) => {
    if (m === 'listen' && c.arguments[0]) strVals(c.arguments[0]).forEach(x => ev.add(x))
    else if (m === 'listenToAll') ev.add('*')
    else if (m === 'notification') ev.add('(notification)')
    else if (m === 'listenForWhisper' && c.arguments[0]) strVals(c.arguments[0]).forEach(x => ev.add('whisper:' + x))
  }
  let n = call
  for (let i = 0; i < 20; i++) {
    const pa = n.parent
    if (!(pa && ts.isPropertyAccessExpression(pa) && pa.expression === n && pa.parent && ts.isCallExpression(pa.parent))) break
    add(pa.name.text, pa.parent); n = pa.parent
  }
  let holder = n.parent
  while (holder && (ts.isAwaitExpression(holder) || ts.isParenthesizedExpression(holder) || ts.isAsExpression(holder))) holder = holder.parent
  if (holder && ts.isVariableDeclaration(holder) && ts.isIdentifier(holder.name)) {
    const sym = checker.getSymbolAtLocation(holder.name)
    let scope = holder
    while (scope && !ts.isFunctionLike(scope) && !ts.isSourceFile(scope)) scope = scope.parent
    const v = x => {
      if (ts.isCallExpression(x) && ts.isPropertyAccessExpression(x.expression) && ts.isIdentifier(unwrap(x.expression.expression))
          && checker.getSymbolAtLocation(unwrap(x.expression.expression)) === sym) add(x.expression.name.text, x)
      ts.forEachChild(x, v)
    }
    if (scope && sym) v(scope)
  }
  // this.channel = Echo.private(...) / ctx.channel = Echo.join(...): listeners chained on `<any>.channel` in the same
  // file (matched by property name)
  if (holder && ts.isBinaryExpression(holder) && holder.operatorToken.kind === ts.SyntaxKind.EqualsToken && holder.right === n
      && ts.isPropertyAccessExpression(holder.left)) {
    const prop = holder.left.name.text
    const v = x => {
      if (ts.isPropertyAccessExpression(x) && x.name.text === prop && x !== holder.left) {
        let c = x
        for (let i = 0; i < 20; i++) {
          const pa = c.parent
          if (!(pa && ts.isPropertyAccessExpression(pa) && pa.expression === c && pa.parent && ts.isCallExpression(pa.parent))) break
          add(pa.name.text, pa.parent); c = pa.parent
        }
      }
      ts.forEachChild(x, v)
    }
    v(holder.getSourceFile())
  }
  return [...ev]
}
function realtimeSub(node, callee) {
  if (!ts.isCallExpression(node)) return null
  if (ts.isIdentifier(callee) && ECHO_HOOKS[callee.text] && node.arguments[0]) {
    let names
    if (callee.text === 'useEchoModel') {
      const m = strVals(node.arguments[0]), id = node.arguments[1] ? strVals(node.arguments[1]) : ['{id}']
      names = m.flatMap(a => id.map(b => `${a}.${b}`))
    } else names = strVals(node.arguments[0])
    const evs = callee.text === 'useEchoNotification' ? ['(notification)'] : (node.arguments[1] && callee.text !== 'useEchoModel' ? strVals(node.arguments[1]) : [])
    const lib = FW.importSource ? FW.importSource(callee) : null
    return { client: callee.text, names, visibility: ECHO_HOOKS[callee.text], events: evs, conf: lib && /echo/.test(lib) ? 'exact' : 'resolved' }
  }
  if (!ts.isPropertyAccessExpression(callee) || !node.arguments[0]) return null
  const m = callee.name.text
  if (ECHO_SUB[m]) {
    const k = clientKind(callee.expression, 'echo')
    if (!k) return null
    return { client: 'echo', names: strVals(node.arguments[0]), visibility: ECHO_SUB[m], events: listenedEvents(node), conf: k }
  }
  if (m === 'subscribe') {
    const k = clientKind(callee.expression, 'pusher')
    if (!k) return null
    const out = { client: 'pusher', names: [], visibility: 'public', events: [], conf: k }
    for (const n of strVals(node.arguments[0])) {
      const pm = n.match(/^(private-encrypted-|private-|presence-)(.*)$/)
      if (pm) { out.visibility = pm[1] === 'presence-' ? 'presence' : pm[1] === 'private-' ? 'private' : 'private-encrypted'; out.names.push(pm[2]) } else out.names.push(n)
    }
    // channel.bind('event', cb) on the returned channel
    const evs = new Set()
    let holder = node.parent
    if (holder && ts.isPropertyAccessExpression(holder) && holder.name.text === 'bind' && holder.parent && ts.isCallExpression(holder.parent) && holder.parent.arguments[0]) strVals(holder.parent.arguments[0]).forEach(x => evs.add(x))
    if (holder && ts.isVariableDeclaration(holder) && ts.isIdentifier(holder.name)) {
      const sym = checker.getSymbolAtLocation(holder.name)
      let scope = holder; while (scope && !ts.isFunctionLike(scope) && !ts.isSourceFile(scope)) scope = scope.parent
      const v = x => { if (ts.isCallExpression(x) && ts.isPropertyAccessExpression(x.expression) && x.expression.name.text === 'bind' && ts.isIdentifier(unwrap(x.expression.expression)) && checker.getSymbolAtLocation(unwrap(x.expression.expression)) === sym && x.arguments[0]) strVals(x.arguments[0]).forEach(e => evs.add(e)); ts.forEachChild(x, v) }
      if (scope && sym) v(scope)
    }
    out.events = [...evs]
    return out
  }
  return null
}

// ---------- web / native bridges: Capacitor plugins, React Native native modules, Expo modules ----------
// A call `X.m()` whose receiver X evaluates to a native module handle (registerPlugin('Name'), Plugins.Name,
// NativeModules.Name, TurboModuleRegistry.get('Name'), requireNativeModule('Name'), an @capacitor/* plugin export) is
// a bridge send: {protocol, module, method}. codegraph/bridges.py links it to the Kotlin / Java / Swift / ObjC side.
const bridges = []
const bridgeReceivers = []
const ELECTRON = ['electron']
const TAURI_PKGS = ['@tauri-apps/api']
function handlerNode(a) {
  const u = unwrap(a)
  if (!u) return null
  if (declId.has(u)) return { id: declId.get(u), conf: 'exact' }
  if (ts.isIdentifier(u) || ts.isPropertyAccessExpression(u)) {
    let sym = null
    try { sym = checker.getSymbolAtLocation(ts.isPropertyAccessExpression(u) ? u.name : u) } catch { }
    const t = sym && resolveSymbol(sym)
    if (t) return { id: t.id, conf: t.conf }
  }
  return null
}
function ipcFacts(node, callee, cur, r, line) {
  const testSf = testFiles.has(realFile(node.getSourceFile())) || undefined
  if (ts.isPropertyAccessExpression(callee)) {
    const m = callee.name.text
    const obj = unwrap(callee.expression)
    // renderer / preload -> main: ipcRenderer.invoke('ch') / send / sendSync / postMessage
    // project wrappers named after the Electron objects: ipcRendererManager.invoke / ipcMainManager.send(ch, ...)
    const wn = ts.isIdentifier(obj) ? obj.text : ts.isPropertyAccessExpression(obj) ? obj.name.text : ''
    if (['invoke', 'send', 'sendSync', 'postMessage'].includes(m) && /^ipc(Main|Renderer)[A-Z_]\w*$/.test(wn)) {
      const ch = ipcChan(node.arguments[0])
      if (ch) { bridges.push({ src: cur, file: r, line, protocol: 'electron-ipc', module: ch, method: null, conf: 'heuristic', via: [`${wn}.${m}`], process: wn.startsWith('ipcMain') ? 'main' : 'renderer', test: testSf }); return }
    }
    if (['invoke', 'send', 'sendSync', 'postMessage'].includes(m)) {
      const k = bridgeLib(obj, ['ipcRenderer'], ELECTRON)
      const ch = k && (strArg(node) ?? ipcChan(node.arguments[0]))
      if (ch) { bridges.push({ src: cur, file: r, line, protocol: 'electron-ipc', module: ch, method: null, conf: k, via: [`ipcRenderer.${m}`], process: 'renderer', test: testSf }); return }
      // main -> renderer: win.webContents.send('ch') / webContents.send
      // main -> renderer: win.webContents.send('ch'), event.sender.send (reply), a WebFrameMain (frame.send)
      if (m === 'send' || m === 'postMessage') {
        let tn = null
        try { const t = checker.getTypeAtLocation(obj); tn = t && t.symbol && t.symbol.name } catch { }
        const typed = tn === 'WebContents' || tn === 'WebFrameMain'
        const named = ['webContents', 'sender', 'mainFrame', 'frame'].includes(wn)
        const c2 = (typed || named) && (strArg(node) ?? ipcChan(node.arguments[0]))
        if (c2) { bridges.push({ src: cur, file: r, line, protocol: 'electron-ipc', module: c2, method: null, conf: typed || wn === 'webContents' ? 'resolved' : 'heuristic', via: [`${tn || wn}.${m}`], process: 'main', test: testSf }); return }
      }
    }
    // receivers: ipcMain.handle('ch', fn) / on / once, ipcRenderer.on('ch', fn)
    const info = ipcCallInfo(node)
    if (info && info.kind === 'ipc') {
      const k = info.wrapper ? 'heuristic' : bridgeLib(obj, [info.process === 'main' ? 'ipcMain' : 'ipcRenderer'], ELECTRON)
      if (testSf) return            // a test registering a handler is not the app's receiver
      // a handler outside the project (`ipcMain.on('quit', app.quit)`): the function registering it receives
      const h = k && (handlerNode(node.arguments[1]) || (cur && !cur.startsWith('module:') ? { id: cur, conf: 'heuristic' } : null))
      if (h) for (const ch of info.channels)
        bridgeReceivers.push({ protocol: 'electron-ipc', module: ch, method: null, handler: h.id, file: r, line, conf: info.union ? 'heuristic' : k === 'exact' ? h.conf : k, via: info.label.replace(/\(.*/, ''), process: info.process, union: info.union || undefined })
      return
    }
    if (info && info.kind === 'expose') {
      if (testSf) return
      const o = unwrap(node.arguments[1])
      if (o && ts.isObjectLiteralExpression(o)) for (const p of o.properties) {
        const pn = p.name && (ts.isIdentifier(p.name) || ts.isStringLiteralLike(p.name)) ? p.name.text : null
        const h = pn && handlerNode(ts.isPropertyAssignment(p) ? p.initializer : ts.isShorthandPropertyAssignment(p) ? p.name : p)
        if (h) bridgeReceivers.push({ protocol: 'electron-preload', module: info.key, method: pn, handler: h.id, file: r, line: lineOf(p), conf: h.conf, via: 'contextBridge.exposeInMainWorld', process: 'preload' })
      }
      return
    }
    // renderer -> preload: window.api.ping() / globalThis.api.ping() for an exposed key
    let ob = obj
    if (ts.isIdentifier(obj) && exposedKeys.size) {     // const api = window.api; api.ping()
      try {
        const d = (checker.getSymbolAtLocation(obj) || {}).valueDeclaration
        const init = d && ts.isVariableDeclaration(d) && unwrap(d.initializer)
        if (init && ts.isPropertyAccessExpression(init)) ob = init
      } catch { }
    }
    if (ts.isPropertyAccessExpression(ob) && exposedKeys.has(ob.name.text)) {
      const obj = ob
      const root = unwrap(obj.expression)
      if (root && ts.isIdentifier(root) && ['window', 'globalThis', 'self'].includes(root.text)) {
        bridges.push({ src: cur, file: r, line, protocol: 'electron-preload', module: obj.name.text, method: m, conf: 'resolved', via: [`${root.text}.${obj.name.text}`], process: 'renderer', test: testSf })
        return
      }
    }
  }
  // Tauri: invoke('cmd', args) from @tauri-apps/api/core (v2) or /tauri (v1), window.__TAURI__.core.invoke
  const isInvoke = (ts.isIdentifier(callee) && callee.text === 'invoke') || (ts.isPropertyAccessExpression(callee) && callee.name.text === 'invoke')
  if (isInvoke) {
    let k = null
    if (ts.isIdentifier(callee)) {
      const lib = FW.importSource ? FW.importSource(callee) : null
      if (lib && TAURI_PKGS.some(p => lib === p || lib.startsWith(p + '/'))) k = 'exact'
    } else if (/__TAURI__|__TAURI_INTERNALS__/.test(callee.expression.getText())) k = 'resolved'
    else {
      const o = unwrap(callee.expression)
      const lib = o && ts.isIdentifier(o) && FW.importSource ? FW.importSource(o) : null
      if (lib && TAURI_PKGS.some(p => lib === p || lib.startsWith(p + '/'))) k = 'exact'
    }
    const cmd = k && strArg(node)
    if (cmd) {
      const plugin = cmd.match(/^plugin:([^|]+)\|/)
      bridges.push({ src: cur, file: r, line, protocol: 'tauri', module: cmd, method: null, conf: k, via: ['invoke'], process: 'webview', test: testSf,
        external: plugin ? `tauri-plugin-${plugin[1]}` : undefined })
    }
  }
}
const BRIDGE_SKIP = new Set(['then', 'catch', 'finally', 'bind', 'call', 'apply', 'toString', 'hasOwnProperty'])
// bridge calls whose module / method / event name is not a literal cg can evaluate (cg bridges lists them, #61)
const bridgeDynamic = []
// events React Native itself emits through DeviceEventEmitter / NativeEventEmitter (no project native sender)
const RN_CORE_EVENTS = new Set(['keyboardWillShow', 'keyboardDidShow', 'keyboardWillHide', 'keyboardDidHide', 'keyboardWillChangeFrame',
  'keyboardDidChangeFrame', 'hardwareBackPress', 'appStateDidChange', 'memoryWarning', 'change', 'url', 'didUpdateDimensions',
  'accessibilityServiceChanged', 'screenReaderChanged', 'reduceMotionChanged', 'appearanceChanged', 'websocketMessage', 'websocketOpen',
  'websocketClosed', 'websocketFailed', 'didReceiveNetworkResponse', 'didReceiveNetworkData', 'didCompleteNetworkResponse',
  'remoteNotificationReceived', 'localNotificationReceived', 'remoteNotificationsRegistered', 'remoteNotificationRegistrationError'])
const EXPO_EMITTER_PKGS = ['expo-modules-core', 'expo']
// a React Native / Expo event emitter: new NativeEventEmitter(Module), DeviceEventEmitter, NativeAppEventEmitter,
// new EventEmitter(ExpoModule) (expo-modules-core), an Expo native module handle itself -> {module, conf, via}
function rnEmitterOf(e, depth) {
  e = unwrap(e)
  if (!e || depth > 4) return null
  const k = bridgeLib(e, ['DeviceEventEmitter', 'NativeAppEventEmitter'], RN_PKGS)
  if (k) return { module: null, conf: k, via: e.text }
  if (ts.isNewExpression(e) && ts.isIdentifier(e.expression)) {
    const nm = e.expression.text
    const lib = FW.importSource ? FW.importSource(e.expression) : null
    if ((nm === 'NativeEventEmitter' && (!lib || RN_PKGS.includes(lib))) || (nm === 'EventEmitter' && lib && EXPO_EMITTER_PKGS.includes(lib))) {
      const bm = e.arguments && e.arguments[0] ? bridgeModuleOf(e.arguments[0], 0) : null
      return { module: bm ? bm.module : null, conf: lib ? 'exact' : 'resolved', via: `new ${nm}` }
    }
    return null
  }
  if (ts.isIdentifier(e) || ts.isPropertyAccessExpression(e)) {
    const bm = bridgeModuleOf(e, 0)
    if (bm && bm.protocol === 'react-native' && bm.api === 'expo-modules') return { module: bm.module, conf: bm.conf, via: 'expo module' }
    let sym
    try { sym = checker.getSymbolAtLocation(ts.isPropertyAccessExpression(e) ? e.name : e) } catch { return null }
    for (const d of (sym && sym.declarations) || []) {
      const init = (ts.isVariableDeclaration(d) || ts.isPropertyDeclaration(d) || ts.isPropertyAssignment(d)) ? d.initializer : null
      const r = init && rnEmitterOf(init, depth + 1)
      if (r) return r
    }
  }
  return null
}
// JS side of native -> app events: Capacitor Plugin.addListener('evt', cb), RN emitter.addListener('evt', cb)
// events the JS side emits itself (DeviceEventEmitter.emit('evt') as an in-app event bus): not a native bridge
const jsEmitted = new Set()
function listenerFacts(node, callee, cur, r, line, testSf) {
  if (ts.isPropertyAccessExpression(callee) && callee.name.text === 'emit') {
    const evt = strArg(node)
    if (evt && rnEmitterOf(callee.expression, 0)) jsEmitted.add(evt)
    return
  }
  if (testSf || !ts.isPropertyAccessExpression(callee) || !['addListener', 'addEventListener'].includes(callee.name.text)) return
  const h = handlerNode(node.arguments[1]) || (cur && !cur.startsWith('module:') ? { id: cur, conf: 'heuristic' } : null)
  const bm = bridgeModuleOf(callee.expression, 0)
  const evt = strArg(node)
  if (bm && bm.protocol === 'capacitor') {
    if (!evt) { bridgeDynamic.push({ file: r, line, protocol: 'capacitor-event', what: `${bm.module}.addListener(<dynamic>)` }); return }
    if (h) bridgeReceivers.push({ protocol: 'capacitor-event', module: bm.module, method: evt, handler: h.id, file: r, line, conf: bm.conf === 'exact' ? h.conf : bm.conf,
      via: `${bm.module}.addListener`, external: bm.external })
    return
  }
  const em = rnEmitterOf(callee.expression, 0)
  if (!em) return
  if (!evt) { bridgeDynamic.push({ file: r, line, protocol: 'react-native-event', what: `${em.via}.addListener(<dynamic>)` }); return }
  if (!em.module && RN_CORE_EVENTS.has(evt)) return
  if (h) bridgeReceivers.push({ protocol: 'react-native-event', module: evt, method: null, handler: h.id, file: r, line, conf: em.conf === 'exact' ? h.conf : em.conf,
    via: `${em.via}.addListener`, emitter_module: em.module || undefined })
}
// Cordova: cordova.exec(ok, fail, 'Service', 'action', args) / exec(...) from 'cordova/exec'
function cordovaFacts(node, callee, cur, r, line, testSf) {
  let ok = false
  if (ts.isPropertyAccessExpression(callee) && callee.name.text === 'exec' && /(^|\.)cordova$/.test(unwrap(callee.expression).getText())) ok = true
  else if (ts.isIdentifier(callee) && callee.text === 'exec') {
    const lib = FW.importSource ? FW.importSource(callee) : null
    let req = false
    try {
      const d = ((checker.getSymbolAtLocation(callee) || {}).declarations || [])[0]
      const init = d && ts.isVariableDeclaration(d) && unwrap(d.initializer)
      req = !!(init && ts.isCallExpression(init) && ts.isIdentifier(init.expression) && init.expression.text === 'require' && init.arguments[0]
        && ts.isStringLiteralLike(init.arguments[0]) && init.arguments[0].text === 'cordova/exec')
    } catch { }
    ok = lib === 'cordova/exec' || req
  }
  if (!ok || node.arguments.length < 4) return
  const sv = a => { const u = unwrap(a); if (u && ts.isStringLiteralLike(u)) return u.text; const v = u ? strVals(u) : []; return v.length === 1 && !/[\u0001{]/.test(v[0]) ? v[0] : null }
  const svc = sv(node.arguments[2]), act = sv(node.arguments[3])
  if (!svc || !act) { bridgeDynamic.push({ file: r, line, protocol: 'cordova', what: `cordova.exec(${svc || '<dynamic>'}, ${act || '<dynamic>'})` }); return }
  bridges.push({ src: cur, file: r, line, protocol: 'cordova', module: svc, method: act, conf: 'resolved', via: ['cordova.exec'], test: testSf || undefined })
}
const CAP_BUILTIN = new Set(['addListener', 'removeAllListeners', 'removeListener', 'notifyListeners'])   // listener plumbing of the bridge
const CAP_CORE_EXPORTS = new Set(['Capacitor', 'registerPlugin', 'Plugins', 'WebPlugin', 'CapacitorHttp', 'CapacitorCookies', 'WebView', 'SplashScreen'])
const bridgeCache = new Map()
const atOf = e => { const sf = e.getSourceFile(); return rel(realFile(sf)) + ':' + lineOf(e, sf) }
function bridgeLib(e, names, pkgs) {
  const id = unwrap(e)
  if (!id || !ts.isIdentifier(id) || !names.includes(id.text)) return null
  const lib = FW.importSource ? FW.importSource(id) : null
  if (lib && pkgs.some(p => lib === p || lib.startsWith(p + '/'))) return 'exact'
  if (lib) return null      // a local NativeModules / Plugins of another library
  try { const s = checker.getSymbolAtLocation(id); if (s && (s.declarations || []).some(d => projectSf(d.getSourceFile()))) return null } catch { }
  return 'resolved'
}
const RN_PKGS = ['react-native', 'react-native-web', 'react-native-windows', 'react-native-macos']
const CAP_PKGS = ['@capacitor/core']
const EXPO_PKGS = ['expo-modules-core', 'expo']
function strArg(c) {
  const a = c.arguments && unwrap(c.arguments[0])
  if (!a) return null
  if (ts.isStringLiteralLike(a)) return a.text
  const v = strVals(a)
  return v.length === 1 && !/[\u0001{]/.test(v[0]) ? v[0] : null
}
function moduleFile(spec, fromSf) {
  if (!spec.startsWith('.')) {
    let f = null
    try { f = (ts.resolveModuleName(spec, fromSf.fileName, options, host).resolvedModule || {}).resolvedFileName } catch { }
    return f ? program.getSourceFile(f) || null : null
  }
  const base = path.resolve(path.dirname(fromSf.fileName), spec)
  for (const ext of ['', '.ts', '.tsx', '.js', '.jsx', '/index.ts', '/index.tsx', '/index.js']) {
    const sf = program.getSourceFile(base + ext)
    if (sf) return sf
  }
  return null
}
function defaultExportOf(sf) {
  for (const st of sf.statements) if (ts.isExportAssignment(st) && !st.isExportEquals) return st.expression
  return null
}
// {protocol, module, conf, via, api?, external?} for a native module handle expression, else null
function bridgeModuleOf(e, depth) {
  e = unwrap(e)
  if (!e || depth > 8) return null
  if (ts.isPropertyAccessExpression(e) || ts.isElementAccessExpression(e)) {
    const name = ts.isPropertyAccessExpression(e) ? e.name.text : (e.argumentExpression && ts.isStringLiteralLike(e.argumentExpression) ? e.argumentExpression.text : null)
    if (name) {
      let k = bridgeLib(e.expression, ['NativeModules'], RN_PKGS)
      if (k) return { protocol: 'react-native', module: name, conf: k, via: ['NativeModules'], at: atOf(e) }
      k = bridgeLib(e.expression, ['Plugins'], CAP_PKGS)
      if (k) return { protocol: 'capacitor', module: name, conf: k, via: ['Plugins'], at: atOf(e) }
      // Capacitor.Plugins.Name / window.Capacitor.Plugins.Name
      const pe = unwrap(e.expression)
      if (ts.isPropertyAccessExpression(pe) && pe.name.text === 'Plugins' && /(^|\.)(Capacitor|cap)$/.test(unwrap(pe.expression).getText()))
        return { protocol: 'capacitor', module: name, conf: 'resolved', via: ['Capacitor.Plugins'], at: atOf(e) }
    }
    // require('./NativeFoo').default
    if (ts.isPropertyAccessExpression(e) && e.name.text === 'default') {
      const c = unwrap(e.expression)
      if (c && ts.isCallExpression(c) && ts.isIdentifier(c.expression) && c.expression.text === 'require' && c.arguments[0] && ts.isStringLiteralLike(c.arguments[0])) {
        const sf = moduleFile(c.arguments[0].text, e.getSourceFile())
        const x = sf && defaultExportOf(sf)
        const r = x && bridgeModuleOf(x, depth + 1)
        if (r) return { ...r, via: [...r.via, 'require'] }
        return null
      }
    }
    if (!ts.isPropertyAccessExpression(e)) return null
  }
  if (ts.isCallExpression(e)) {
    const c = unwrap(e.expression)
    // registerPlugin('Name') (imported from @capacitor/core, or a local re-export of it) / Capacitor.registerPlugin('Name')
    if ((ts.isIdentifier(c) && c.text === 'registerPlugin') || (ts.isPropertyAccessExpression(c) && c.name.text === 'registerPlugin'
        && /(^|\.)(Capacitor|cap)$/.test(unwrap(c.expression).getText()))) {
      const lib = ts.isIdentifier(c) && FW.importSource ? FW.importSource(c) : null
      const n = strArg(e)
      if (n && (!lib || lib === '@capacitor/core' || lib.startsWith('.'))) return { protocol: 'capacitor', module: n, conf: lib === '@capacitor/core' ? 'exact' : 'resolved', via: ['registerPlugin'], at: atOf(e) }
    }
    if (ts.isPropertyAccessExpression(c) && ['get', 'getEnforcing'].includes(c.name.text)) {
      const k = bridgeLib(c.expression, ['TurboModuleRegistry'], RN_PKGS)
      const n = k && strArg(e)
      if (n) return { protocol: 'react-native', module: n, conf: k, via: ['TurboModuleRegistry'], at: atOf(e) }
    }
    // Object.assign(NativeModule, { helpers }) is still the native module
    // (the members the other arguments add are JS helpers, not native methods)
    if (ts.isPropertyAccessExpression(c) && c.name.text === 'assign' && ts.isIdentifier(c.expression) && c.expression.text === 'Object' && e.arguments.length) {
      const r = bridgeModuleOf(e.arguments[0], depth + 1)
      if (!r) return null
      const js = [...(r.jsOnly || [])]
      for (const a of e.arguments.slice(1)) {
        const o = unwrap(a)
        if (o && ts.isObjectLiteralExpression(o)) for (const p of o.properties) if (p.name && (ts.isIdentifier(p.name) || ts.isStringLiteralLike(p.name))) js.push(p.name.text)
      }
      return js.length ? { ...r, jsOnly: js } : r
    }
    if (ts.isIdentifier(c) && ['requireNativeModule', 'requireOptionalNativeModule'].includes(c.text)) {
      const lib = FW.importSource ? FW.importSource(c) : null
      const n = strArg(e)
      if (n && (!lib || EXPO_PKGS.includes(lib))) return { protocol: 'react-native', module: n, conf: lib ? 'exact' : 'resolved', via: [c.text], api: 'expo-modules', at: atOf(e) }
    }
    return null
  }
  if (ts.isConditionalExpression(e)) return bridgeModuleOf(e.whenFalse, depth + 1) || bridgeModuleOf(e.whenTrue, depth + 1)
  if (ts.isBinaryExpression(e) && [ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(e.operatorToken.kind))
    return bridgeModuleOf(e.left, depth + 1) || bridgeModuleOf(e.right, depth + 1)
  if (!ts.isIdentifier(e) && !ts.isPropertyAccessExpression(e)) return null
  let sym
  try { sym = checker.getSymbolAtLocation(ts.isPropertyAccessExpression(e) ? e.name : e) } catch { return null }
  if (!sym) return null
  if (bridgeCache.has(sym)) return bridgeCache.get(sym)
  bridgeCache.set(sym, null)
  let out = null
  const first = (sym.declarations || [])[0]
  // an import from an @capacitor/* plugin package (node_modules is not indexed): the export name is the plugin name
  if (first && ts.isImportSpecifier(first)) {
    let p = first; while (p && !ts.isImportDeclaration(p)) p = p.parent
    const lib = p && ts.isStringLiteral(p.moduleSpecifier) ? p.moduleSpecifier.text : ''
    const nm = (first.propertyName || first.name).text
    if (/^@capacitor(-community)?\/|^@capawesome(-team)?\//.test(lib) && lib !== '@capacitor/core' && /^[A-Z]/.test(nm) && !CAP_CORE_EXPORTS.has(nm)) {
      let target = null
      try { target = checker.getAliasedSymbol(sym) } catch { }
      const td = target && (target.declarations || [])[0]
      if (!td || !projectSf(td.getSourceFile())) out = { protocol: 'capacitor', module: nm, conf: 'heuristic', via: ['package-export'], external: lib }
    }
  }
  if (!out) {
    let s = sym
    for (let i = 0; i < 6 && s && (s.flags & ts.SymbolFlags.Alias); i++) { try { s = checker.getAliasedSymbol(s) } catch { s = null } }
    for (const d of (s && s.declarations) || []) {
      if (ts.isVariableDeclaration(d) && d.initializer) out = bridgeModuleOf(d.initializer, depth + 1)
      else if ((ts.isPropertyDeclaration(d) || ts.isPropertyAssignment(d)) && d.initializer) out = bridgeModuleOf(d.initializer, depth + 1)
      else if (ts.isExportAssignment(d)) out = bridgeModuleOf(d.expression, depth + 1)
      else if (ts.isBindingElement(d) && ts.isObjectBindingPattern(d.parent) && ts.isVariableDeclaration(d.parent.parent) && d.parent.parent.initializer) {
        const nm = d.propertyName && ts.isIdentifier(d.propertyName) ? d.propertyName.text : (ts.isIdentifier(d.name) ? d.name.text : null)
        const init = d.parent.parent.initializer
        if (nm) {
          let k = bridgeLib(init, ['NativeModules'], RN_PKGS)
          if (k) out = { protocol: 'react-native', module: nm, conf: k, via: ['NativeModules'], at: atOf(d) }
          else if ((k = bridgeLib(init, ['Plugins'], CAP_PKGS))) out = { protocol: 'capacitor', module: nm, conf: k, via: ['Plugins'], at: atOf(d) }
        }
      }
      if (out) break
    }
  }
  bridgeCache.set(sym, out)
  return out
}

// the innermost if / else / ternary / `&&` / switch case around a construction or a JSX element (#88): attrs.branch
// (`if (editing)`, `else of open ?`, `case 'settings'`, `user &&`) and attrs.branch_line, so the parent that swaps a
// component or object under a condition shows on its INSTANTIATES / RENDERS edge
function branchOf(node, sf) {
  let prev = node, x = node.parent
  const lab = (t) => t.replace(/\s+/g, ' ').slice(0, 80)
  const at = (n) => sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1
  while (x && !ts.isFunctionLike(x) && !ts.isSourceFile(x) && !ts.isClassLike(x)) {
    if (ts.isIfStatement(x) && prev !== x.expression) {
      const c = `if (${x.expression.getText(sf)})`
      return { branch: lab(prev === x.elseStatement ? `else of ${c}` : c), branch_line: at(x) }
    }
    if (ts.isConditionalExpression(x) && prev !== x.condition) {
      const c = `${x.condition.getText(sf)} ?`
      return { branch: lab(prev === x.whenFalse ? `else of ${c}` : c), branch_line: at(x) }
    }
    if (ts.isBinaryExpression(x) && prev === x.right && [ts.SyntaxKind.AmpersandAmpersandToken, ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(x.operatorToken.kind))
      return { branch: lab(`${x.left.getText(sf)} ${x.operatorToken.getText(sf)}`), branch_line: at(x) }
    if (ts.isCaseClause(x) && prev !== x.expression) return { branch: lab(`case ${x.expression.getText(sf)}`), branch_line: at(x) }
    if (ts.isDefaultClause(x)) return { branch: 'default', branch_line: at(x) }
    prev = x; x = x.parent
  }
  return null
}
function edgeKindFor(targetId, isCall) {
  const k = targetId.split(':')[0]
  if (k === 'composable') return 'USES_COMPOSABLE'
  if (k === 'store') return 'USES_STORE'
  if (k === 'class') return 'INSTANTIATES'
  if (k === 'type') return 'REFERENCES_TYPE'
  return 'CALLS'
}

for (const sf of sourceFiles) { const real = realFile(sf); optionsFields(sf, rel(real), fileNode.get(real)) }
for (const sf of sourceFiles) {
  const real = realFile(sf), r = rel(real), fid = fileNode.get(real)
  const isVue = real.endsWith('.vue')
  const stack = [fid]
  const fnStack = []
  const visit = node => {
    const own = !isVue && declId.get(node)
    if (own) stack.push(own)
    const isFnNode = ts.isFunctionLike(node)
    if (isFnNode) fnStack.push(node)
    const cur = stack[stack.length - 1]
    if (ts.isVariableDeclaration(node) && node.initializer && cur) stateDecl(node, cur, sf, r)
    if (ts.isIdentifier(node) && stateNames.has(node.text) && cur) stateRef(node, cur, sf, r)
    if (ts.isPropertyAccessExpression(node) && optNames.has(node.name.text) && cur) optionsRef(node, cur, sf, r)
    // imports
    if (ts.isImportDeclaration(node) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      const ms = checker.getSymbolAtLocation(node.moduleSpecifier)
      const d = ms && (ms.declarations || [])[0]
      if (d && ts.isSourceFile(d) && projectSf(d)) addEdge(fid, fileNode.get(realFile(d)), 'IMPORTS', r, lineOf(node, sf), 'exact')
    }
    // re-exports (`export {a as b} from './m'`, `export {x}`, `export * from './m'`): the module defines those names
    // too (attrs.reexports: exported name -> node id; reexports_all: modules re-exported whole), and depends on './m'
    if (!isVue && ts.isExportDeclaration(node)) {
      let target = null
      if (node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
        const ms = checker.getSymbolAtLocation(node.moduleSpecifier)
        const d = ms && (ms.declarations || [])[0]
        if (d && ts.isSourceFile(d) && projectSf(d)) {
          target = fileNode.get(realFile(d))
          addEdge(fid, target, 'IMPORTS', r, lineOf(node, sf), 'exact', { reexport: true })
        }
      }
      const mo = modNode.get(fid)
      if (mo && node.exportClause && ts.isNamedExports(node.exportClause)) {
        for (const el of node.exportClause.elements) {
          let s = null
          try { s = node.moduleSpecifier ? checker.getSymbolAtLocation(el.propertyName || el.name) : checker.getExportSpecifierLocalTargetSymbol(el) } catch { }
          const res = s && resolveSymbol(s)
          if (res && res.id && res.id !== fid) (mo.attrs.reexports ||= {})[el.name.text] = res.id
          else if (!res && !(node.isTypeOnly || el.isTypeOnly)) (mo.attrs.reexports_external ||= []).push(el.name.text)   // from a package
        }
      } else if (mo && target && !node.exportClause) (mo.attrs.reexports_all ||= []).push(target)
    }
    // `export const X = Imported` / `= ns.member`: an alias the module defines (re-export of a project or package symbol)
    if (!isVue && ts.isVariableStatement(node) && node.parent === sf && (node.modifiers || []).some(m => m.kind === ts.SyntaxKind.ExportKeyword)) {
      const mo = modNode.get(fid)
      for (const d of node.declarationList.declarations) {
        const init = d.initializer && unwrap(d.initializer)
        if (!mo || !ts.isIdentifier(d.name) || !init || !(ts.isIdentifier(init) || ts.isPropertyAccessExpression(init)) || declId.get(d)) continue
        let s = null
        try { s = checker.getSymbolAtLocation(init) } catch { }
        const res = s && resolveSymbol(s)
        if (res && res.id && res.id !== fid) (mo.attrs.reexports ||= {})[d.name.text] = res.id
        else if (!res) (mo.attrs.reexports_external ||= []).push(d.name.text)
      }
    }
    if (ts.isCallExpression(node) || ts.isNewExpression(node)) handleCall(node, cur, sf, r, fnStack[fnStack.length - 1])
    // literal fallbacks: `a ?? b ?? 'X'` / `a || 'X'` (outermost operator only)
    if (ts.isBinaryExpression(node) && FALLBACK_OPS.has(node.operatorToken.kind)
        && !(ts.isBinaryExpression(node.parent) && node.parent.left === node && node.parent.operatorToken.kind === node.operatorToken.kind)) {
      const ops = []
      let x = node
      while (ts.isBinaryExpression(x) && x.operatorToken.kind === node.operatorToken.kind) { ops.unshift(x.right); x = x.left }
      ops.unshift(x)
      const last = unwrap(ops[ops.length - 1])
      if (ts.isStringLiteralLike(last) || ts.isNumericLiteral(last)) {
        fallbacks.push({ owner: cur, file: r, line: lineOf(node, sf), op: node.operatorToken.getText(sf),
          chain: ops.map(o => o.getText(sf).replace(/\s+/g, ' ').slice(0, 120)), literal: ts.isNumericLiteral(last) ? Number(last.text) : last.text,
          template: isVue && isTemplateLine(node, sf) || undefined })
      }
    }
    // identifier references to project functions/stores/composables that are not direct callees (callbacks, template handlers)
    if (ts.isIdentifier(node) && !isDeclName(node) && !(node.parent && (ts.isCallExpression(node.parent) || ts.isNewExpression(node.parent)) && node.parent.expression === node)
        && !(ts.isPropertyAccessExpression(node.parent) && node.parent.name === node && ts.isCallExpression(node.parent.parent) && node.parent.parent.expression === node.parent)
        && !ts.isImportSpecifier(node.parent) && !ts.isImportClause(node.parent) && !ts.isExportSpecifier(node.parent)
        && !ts.isBindingElement(node.parent) && !ts.isShorthandPropertyAssignment(node.parent) && !ts.isTypeReferenceNode(node.parent) && !ts.isQualifiedName(node.parent)
        // #138: `server.url` / `fn['x']` read a property of the function object, `return l` hands the function back and
        // `request = request.defaults(..)` assigns the variable; none of them calls it. `fn.call(..)` / `.apply` /
        // `.bind`, `.value` of a Vue `computed` getter and an expando method call (`request.get(..)` with
        // `request.get = verbFunc('get')`) still count
        && !(ts.isPropertyAccessExpression(node.parent) && node.parent.expression === node && !REF_CALL_PROPS.has(node.parent.name.text)
             && !(ts.isCallExpression(node.parent.parent) && node.parent.parent.expression === node.parent)
             && !(ts.isJsxOpeningLikeElement(node.parent.parent) || ts.isJsxClosingElement(node.parent.parent)))   // <Menu.Item>
        && !(ts.isElementAccessExpression(node.parent) && node.parent.expression === node)
        && !(ts.isReturnStatement(node.parent) && node.parent.expression === node)
        && !(ts.isBinaryExpression(node.parent) && node.parent.left === node && node.parent.operatorToken.kind === ts.SyntaxKind.EqualsToken)) {
      const sym = checker.getSymbolAtLocation(node)
      if (sym) {
        const t = resolveSymbol(sym)
        if (t && t.id !== cur) {
          const k = t.id.split(':')[0]
          if (['function', 'composable', 'method', 'store', 'component', 'page', 'layout'].includes(k)) {
            const tplTag = isVue && isTemplateLine(node, sf) && /^__tplc_/.test(enclosingFnName(node))
            const jsxTag = node.parent && (ts.isJsxOpeningElement(node.parent) || ts.isJsxSelfClosingElement(node.parent)) && node.parent.tagName === node
            if (jsxTag) addEdge(cur, t.id, 'RENDERS', r, lineOf(node, sf), t.conf === 'exact' ? 'exact' : 'resolved', { via: ['jsx', ...t.via], ...(branchOf(node.parent, sf) || {}) })
            else if (tplTag) addEdge(cur, t.id, 'RENDERS', r, lineOf(node, sf), t.conf === 'exact' ? 'exact' : 'resolved', { via: ['template', ...t.via] })
            else if (!['component', 'page', 'layout'].includes(k)) addEdge(cur, t.id, edgeKindFor(t.id), r, lineOf(node, sf), t.conf, { ref: true, via: t.via.length ? t.via : undefined, template: isVue && isTemplateLine(node, sf) || undefined })
          }
        }
      }
    }
    // stored class fields (#88): `this.count`, `cart.items`, `this.#secret`, resolved by the checker
    if (ts.isPropertyAccessExpression(node) && fieldNames.has(node.name.text)) {
      let s = null
      try { s = checker.getSymbolAtLocation(node.name) } catch { }
      const fid = s && fieldIds.get(s.valueDeclaration || (s.declarations || [])[0])
      if (fid && fid !== cur && cur) {
        const p = node.parent; let via
        let write = (ts.isBinaryExpression(p) && p.left === node && p.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && p.operatorToken.kind <= ts.SyntaxKind.LastAssignment)
          || ((ts.isPrefixUnaryExpression(p) || ts.isPostfixUnaryExpression(p)) && (p.operator === ts.SyntaxKind.PlusPlusToken || p.operator === ts.SyntaxKind.MinusMinusToken))
          || ts.isDeleteExpression(p)
        if (!write && ts.isElementAccessExpression(p) && p.expression === node && p.parent && ((ts.isBinaryExpression(p.parent) && p.parent.left === p
            && p.parent.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && p.parent.operatorToken.kind <= ts.SyntaxKind.LastAssignment) || ts.isDeleteExpression(p.parent))) { write = true; via = 'item' }
        if (!write && ts.isPropertyAccessExpression(p) && p.expression === node && TS_MUTATING.has(p.name.text) && p.parent && ts.isCallExpression(p.parent) && p.parent.expression === p) { write = true; via = 'mutating' }
        const recv = node.expression.kind === ts.SyntaxKind.ThisKeyword ? 'this' : node.expression.getText(sf).slice(0, 40)
        addEdge(cur, fid, write ? 'WRITES_PROP' : 'READS_PROP', r, lineOf(node, sf), 'exact', { receiver: recv, ...(via ? { via } : {}) })
        stats[write ? 'stored_field_writes' : 'stored_field_reads'] = (stats[write ? 'stored_field_writes' : 'stored_field_reads'] || 0) + 1
      }
    }
    // enum members and constants (#84): `Color.Red`, `MAX`, an imported `MAX`, `ns.MAX`; resolved by the checker
    if (ts.isImportSpecifier(node) && node.propertyName && valueNames.has(node.propertyName.text)) valueNames.add(node.name.text)
    if (ts.isIdentifier(node) && valueNames.has(node.text) && !(node.parent && node.parent.name === node
        && (ts.isVariableDeclaration(node.parent) || ts.isEnumMember(node.parent) || ts.isPropertyDeclaration(node.parent)))
        && !ts.isImportSpecifier(node.parent) && !ts.isImportClause(node.parent) && !ts.isExportSpecifier(node.parent)
        && !ts.isQualifiedName(node.parent)) {
      let s = null
      try { s = checker.getSymbolAtLocation(node) } catch { }
      if (s && (s.flags & ts.SymbolFlags.Alias)) { try { s = checker.getAliasedSymbol(s) } catch { s = null } }
      const vd = s && (s.valueDeclaration || (s.declarations || [])[0])
      const vid = vd && valueIds.get(vd)
      if (vid && vid !== cur) addEdge(cur, vid, 'USES_VALUE', r, lineOf(node, sf), 'exact', { how: 'checker' })
    }
    if (ts.isTypeReferenceNode(node)) {
      const sym = checker.getSymbolAtLocation(ts.isQualifiedName(node.typeName) ? node.typeName.right : node.typeName)
      const t = sym && resolveSymbol(sym)
      if (t && t.id.startsWith('type:') || t && t.id.startsWith('class:')) addEdge(cur, t.id, 'REFERENCES_TYPE', r, lineOf(node, sf), t.conf)
    }
    ts.forEachChild(node, visit)
    if (own) stack.pop()
    if (isFnNode) fnStack.pop()
  }
  ts.forEachChild(sf, visit)
}
function enclosingFnName(n) { while (n && !ts.isFunctionDeclaration(n)) n = n.parent; return n && n.name ? n.name.text : '' }
function isDeclName(n) {
  const p = n.parent
  return p && (p.name === n) && (ts.isVariableDeclaration(p) || ts.isFunctionDeclaration(p) || ts.isParameter(p) || ts.isPropertyAssignment(p)
    || ts.isMethodDeclaration(p) || ts.isPropertyDeclaration(p) || ts.isClassDeclaration(p) || ts.isInterfaceDeclaration(p) || ts.isTypeAliasDeclaration(p)
    || ts.isPropertySignature(p) || ts.isEnumDeclaration(p) || ts.isPropertyAccessExpression(p) || ts.isEnumMember(p))
}

function handleCall(node, cur, sf, r, encFn) {
  const callee = unwrap(node.expression)
  const line = lineOf(node, sf)
  const inTpl = isTemplateLine(node, sf)
  // i18n keys
  const cname = ts.isIdentifier(callee) ? callee.text : (ts.isPropertyAccessExpression(callee) ? callee.name.text : '')
  if (I18N_FNS.has(cname) && node.arguments && node.arguments[0] && ts.isStringLiteralLike(node.arguments[0])) {
    i18nUses.push({ src: cur, key: node.arguments[0].text, file: r, line })
  }
  if (cname === 'definePageMeta' && node.arguments[0]) {
    const o = unwrap(node.arguments[0]); const m = {}
    const lay = objProp(o, 'layout'); if (lay && ts.isStringLiteralLike(unwrap(lay))) m.layout = unwrap(lay).text
    const mw = objProp(o, 'middleware')
    if (mw) { const u = unwrap(mw); m.middleware = ts.isArrayLiteralExpression(u) ? u.elements.filter(ts.isStringLiteralLike).map(x => x.text) : (ts.isStringLiteralLike(u) ? [u.text] : []) }
    pageMeta[r] = m
  }
  // HTTP
  let http = null
  let axiosInst = null
  if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(callee) && (HTTP_METHODS.has(callee.name.text) || callee.name.text === 'request')
      && (isAxiosType(callee.expression) || (FW.importSource && (axiosInst = axiosCreateOf(callee.expression))))) {
    const recvType = checker.typeToString(checker.getTypeAtLocation(callee.expression))
    const isInstance = /AxiosInstance/.test(recvType) || !!axiosInst
    let method = callee.name.text.toUpperCase(), urlExpr = node.arguments[0], cfgObj = null
    if (callee.name.text === 'request') { cfgObj = node.arguments[0]; urlExpr = objProp(cfgObj, 'url'); const m = objProp(cfgObj, 'method'); method = m && ts.isStringLiteralLike(unwrap(m)) ? unwrap(m).text.toUpperCase() : 'GET' }
    let base = null, baseConf = 'exact', baseVia = []
    if (isInstance) {
      const o = axiosCreateOf(callee.expression)
      if (o) { const b = objProp(o.cfg, 'baseURL'); if (b) { const v = evalStr(b); base = v.vals; baseConf = v.conf; baseVia = o.via } }
      else { baseConf = 'heuristic'; baseVia = ['instance-origin-unknown'] }
    }
    const lower = callee.name.text
    const cfgArg = ['get', 'delete', 'head', 'options'].includes(lower) ? node.arguments[1] : node.arguments[2]
    const qp = objProp(cfgArg, 'params')
    const body = ['post', 'put', 'patch'].includes(lower) ? node.arguments[1] : null
    http = { client: isInstance ? 'axios-instance' : 'axios', method, urlExpr, base, baseConf, baseVia,
      query: qp ? keysOut(requestKeys(qp)) : undefined, body: body ? keysOut(requestKeys(body)) : undefined }
  } else if (ts.isCallExpression(node) && (http = generatedClientCall(node, callee))) {
    // OpenAPI-generated clients: this.request({ path, method }) / __request(OpenAPI, { method, url })
  } else if (ts.isCallExpression(node) && (http = kyOrInstanceCall(node, callee))) {
    // ky / ofetch.create / $fetch.create instances
  } else if (ts.isCallExpression(node) && ts.isIdentifier(callee) && /^useSWR(Immutable|Infinite)?$|^useQuery$/.test(callee.text) && callee.text.startsWith('useSWR') && node.arguments[0]) {
    // SWR: the key is the URL fetched by the (global or passed) fetcher; `cond ? url : null` keys keep the URL branch
    let k = unwrap(node.arguments[0])
    if (ts.isConditionalExpression(k)) k = [k.whenTrue, k.whenFalse].map(unwrap).find(x => x.kind !== ts.SyntaxKind.NullKeyword && !(ts.isIdentifier(x) && x.text === 'undefined')) || k
    if (ts.isBinaryExpression(k) && k.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken) k = unwrap(k.right)
    if (ts.isArrayLiteralExpression(k)) k = unwrap(k.elements[0])
    if (k && (ts.isStringLiteralLike(k) || ts.isTemplateExpression(k) || ts.isBinaryExpression(k) || ts.isIdentifier(k) || ts.isCallExpression(k)))
      http = { client: 'swr', method: 'GET', urlExpr: k, base: null, baseConf: 'exact', baseVia: [] }
  } else if (ts.isNewExpression(node) && ts.isIdentifier(callee) && WS_CTORS.has(callee.text) && node.arguments && node.arguments[0] && !ownClass(callee)) {
    const c = WS_CTORS.get(callee.text), sse = c === 'eventsource'
    http = { client: c, method: sse ? 'GET' : 'WS', urlExpr: node.arguments[0], base: null, baseConf: 'exact', baseVia: [], stream: sse ? 'sse' : undefined }
  } else if (ts.isCallExpression(node) && ts.isIdentifier(callee) && callee.text === 'fetchEventSource' && node.arguments[0] && !ownClass(callee)) {
    // @microsoft/fetch-event-source: fetchEventSource(url, { method, body, onmessage })
    const m = objProp(node.arguments[1], 'method')
    http = { client: 'fetch-event-source', method: m && ts.isStringLiteralLike(unwrap(m)) ? unwrap(m).text.toUpperCase() : 'GET', urlExpr: node.arguments[0],
      base: null, baseConf: 'exact', baseVia: [], stream: 'sse' }
  } else if (ts.isCallExpression(node) && ts.isIdentifier(callee) && FETCH_FNS.has(callee.text)) {
    const o = node.arguments[1]; const m = objProp(o, 'method')
    const method = m && ts.isStringLiteralLike(unwrap(m)) ? unwrap(m).text.toUpperCase() : 'GET'
    const b = objProp(o, 'baseURL')
    const bv = b ? evalStr(b) : null
    const fq = objProp(o, 'query') || objProp(o, 'params'), fb = objProp(o, 'body')
    http = { client: callee.text, method, urlExpr: node.arguments[0], base: bv ? bv.vals : null, baseConf: bv ? bv.conf : 'exact', baseVia: [],
      query: fq ? keysOut(requestKeys(fq)) : undefined, body: fb ? keysOut(requestKeys(fb)) : undefined }
  }
  // browser / API tests: page.goto('/x'), cy.visit('/x'); Playwright request.get('/api/x'), cy.request('/api/x')
  const testSf = testFiles.has(realFile(sf))
  if (testSf && !http && ts.isCallExpression(node) && node.arguments[0]) {
    const recvTxt = ts.isPropertyAccessExpression(callee) ? unwrap(callee.expression).getText() : ''
    if (ts.isPropertyAccessExpression(callee) && ((callee.name.text === 'goto' && /(^|\.)page$|Page$/i.test(recvTxt)) || (callee.name.text === 'visit' && recvTxt === 'cy'))) {
      for (const u of strVals(node.arguments[0])) visits.push({ src: cur, file: r, line, url: u, via: `${recvTxt}.${callee.name.text}` })
    } else if (ts.isPropertyAccessExpression(callee) && (HTTP_METHODS.has(callee.name.text) || callee.name.text === 'fetch') && /(^|\.)request$|apiContext|requestContext/i.test(recvTxt)) {
      const o = node.arguments[1]; const mo = objProp(o, 'method')
      const method = callee.name.text === 'fetch' ? (mo && ts.isStringLiteralLike(unwrap(mo)) ? unwrap(mo).text.toUpperCase() : 'GET') : callee.name.text.toUpperCase()
      http = { client: 'playwright-request', method, urlExpr: node.arguments[0], base: null, baseConf: 'exact', baseVia: [] }
    } else if (ts.isPropertyAccessExpression(callee) && callee.name.text === 'request' && recvTxt === 'cy') {
      const a0 = unwrap(node.arguments[0])
      if (ts.isObjectLiteralExpression(a0)) { const mo = objProp(a0, 'method'), uo = objProp(a0, 'url'); if (uo) http = { client: 'cypress', method: mo && ts.isStringLiteralLike(unwrap(mo)) ? unwrap(mo).text.toUpperCase() : 'GET', urlExpr: uo, base: null, baseConf: 'exact', baseVia: [] } }
      else if (node.arguments.length >= 2 && ts.isStringLiteralLike(a0) && /^[A-Z]+$/.test(a0.text)) http = { client: 'cypress', method: a0.text, urlExpr: node.arguments[1], base: null, baseConf: 'exact', baseVia: [] }
      else http = { client: 'cypress', method: 'GET', urlExpr: node.arguments[0], base: null, baseConf: 'exact', baseVia: [] }
    }
  }
  const sub = realtimeSub(node, callee)
  if (sub && sub.names.length) subscriptions.push({ src: cur, file: r, line, ...sub, test: testSf || undefined })
  if (http) {
    const params = new Set()
    const v = http.urlExpr ? evalStr(http.urlExpr, 0, { params }) : { vals: [PH('?')], conf: 'resolved' }
    const rec = { src: cur, file: r, line, client: http.client, method: http.method, template: inTpl || undefined, test: testSf || undefined,
      urls: shapeDedupe(v.vals).map(render), url_conf: v.conf, base: http.base && shapeDedupe(http.base).map(render), base_conf: http.baseConf, base_via: http.baseVia,
      expr: http.urlExpr ? http.urlExpr.getText().slice(0, 160) : null, query: http.query, body: http.body, stream: http.stream }
    // URL built from a parameter of the enclosing function: expand at its call sites (1 level)
    const depParams = [...params].filter(p => encFn && encFn.parameters && encFn.parameters.some(x => ts.isIdentifier(x.name) && x.name.text === p))
    if (depParams.length && encFn) deferredParamCalls.push({ rec, encFn, urlExpr: http.urlExpr })
    apiCalls.push(rec)
  }
  // Electron IPC / context bridge / Tauri commands -> endpoint:<protocol>:<name> (codegraph/bridges.py)
  if (ts.isCallExpression(node)) ipcFacts(node, callee, cur, r, line)
  if (ts.isCallExpression(node)) { listenerFacts(node, callee, cur, r, line, testSf); cordovaFacts(node, callee, cur, r, line, testSf) }
  // web / native bridge sends: Capacitor.nativePromise('Plugin', 'method', ...) / nativeCallback (the low-level bridge)
  if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(callee) && ['nativePromise', 'nativeCallback'].includes(callee.name.text)
      && node.arguments.length >= 2 && /(^|\.)(Capacitor|cap)$/.test(unwrap(callee.expression).getText())) {
    const pn = strArg(node), mn = ts.isStringLiteralLike(unwrap(node.arguments[1])) ? unwrap(node.arguments[1]).text : null
    if (pn && mn) bridges.push({ src: cur, file: r, line, method: mn, protocol: 'capacitor', module: pn, conf: 'exact', via: [callee.name.text], test: testSf || undefined })
  } else if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(callee) && !BRIDGE_SKIP.has(callee.name.text)) {
    const ce = unwrap(callee.expression)
    if (ce && ts.isElementAccessExpression(ce) && !(ce.argumentExpression && ts.isStringLiteralLike(ce.argumentExpression))) {
      if (bridgeLib(ce.expression, ['NativeModules'], RN_PKGS))
        bridgeDynamic.push({ file: r, line, protocol: 'react-native', what: `NativeModules[${ce.argumentExpression ? ce.argumentExpression.getText().slice(0, 40) : '?'}].${callee.name.text}` })
      else if (bridgeLib(ce.expression, ['Plugins'], CAP_PKGS))
        bridgeDynamic.push({ file: r, line, protocol: 'capacitor', what: `Plugins[${ce.argumentExpression ? ce.argumentExpression.getText().slice(0, 40) : '?'}].${callee.name.text}` })
    }
    const bm = bridgeModuleOf(callee.expression, 0)
    if (bm && !(bm.protocol === 'capacitor' && CAP_BUILTIN.has(callee.name.text)) && !(bm.jsOnly && bm.jsOnly.includes(callee.name.text))) {
      const { jsOnly, ...fact } = bm
      bridges.push({ src: cur, file: r, line, method: callee.name.text, ...fact, test: testSf || undefined })
    }
  }
  // call edges
  let sym = null
  if (ts.isIdentifier(callee)) sym = checker.getSymbolAtLocation(callee)
  else if (ts.isPropertyAccessExpression(callee)) sym = checker.getSymbolAtLocation(callee.name)
  if (!sym) return
  const t = resolveSymbol(sym, ts.isIdentifier(callee) ? 'exact' : 'resolved')
  if (!t) return
  let kind = edgeKindFor(t.id, true)
  if (kind === 'REFERENCES_TYPE') return
  const recv = kind === 'CALLS' && ts.isPropertyAccessExpression(callee) ? receiverClasses(callee.expression, t.id) : null
  const br = kind === 'INSTANTIATES' && !inTpl ? branchOf(node, sf) : null
  addEdge(cur, t.id, kind, r, line, t.conf, { via: t.via.length ? t.via : undefined, template: inTpl || undefined, recv: recv || undefined, ...(br || {}) })
  callSites.push({ target: t.id, node, cur, r, line })
}

// `b.run()` landing on `Base.run` (B inherits it): the receiver's project classes (attrs.recv), so `impact B.run` can
// keep only the calls whose receiver can be a B. Only for methods of classes / interfaces something extends or implements
var baseTypeIds = null      // var: handleCall runs before this line is reached
function receiverClasses(expr, targetId) {
  if (!targetId.startsWith('method:')) return null
  if (!baseTypeIds) baseTypeIds = new Set(edges.filter(e => e.kind === 'EXTENDS' || e.kind === 'IMPLEMENTS').map(e => e.dst))
  const tn = nodeById.get(targetId)
  if (!tn || !tn.parent || !baseTypeIds.has(tn.parent)) return null
  let ty = null
  try { ty = checker.getTypeAtLocation(expr) } catch { return null }
  if (!ty) return null
  const out = []
  for (const m of ty.isUnion() ? ty.types : [ty]) {
    const sym = m.getSymbol && m.getSymbol()
    const ds = (sym && sym.declarations) || []      // a merged `interface X` + `class X`: the class
    const d = ds.find(x => ts.isClassDeclaration(x) && declId.has(x)) || ds.find(x => ts.isInterfaceDeclaration(x) && declId.has(x))
    if (!d) return null          // a receiver type outside the project classes: unknown
    const id = declId.get(d)
    if (!out.includes(id)) out.push(id)
  }
  return out.length && !(out.length === 1 && out[0] === tn.parent) ? out : null
}

function generatedClientCall(node, callee) {
  // swagger-typescript-api: this.request({ path: `/users/${id}`, method: 'GET', query, body })
  // openapi-typescript-codegen: __request(OpenAPI, { method: 'GET', url: '/users/{id}', path: { id }, query, body })
  let o = null
  if (ts.isPropertyAccessExpression(callee) && callee.name.text === 'request' && node.arguments.length === 1) o = unwrap(node.arguments[0])
  else if (ts.isIdentifier(callee) && /^_*request$/.test(callee.text) && node.arguments.length === 2) o = unwrap(node.arguments[1])
  if (!o || !ts.isObjectLiteralExpression(o)) return null
  const m = objProp(o, 'method')
  if (!m || !ts.isStringLiteralLike(unwrap(m))) return null
  const pathProp = objProp(o, 'path'), urlProp = objProp(o, 'url')
  const strish = e => !!e && (ts.isStringLiteralLike(e) || ts.isTemplateExpression(e) || ts.isBinaryExpression(e))
  const urlExpr = pathProp && strish(unwrap(pathProp)) ? pathProp : urlProp && strish(unwrap(urlProp)) ? urlProp : null
  if (!urlExpr) return null
  const q = objProp(o, 'query'), b = objProp(o, 'body')
  // the base URL is client configuration (new Api({ baseUrl }) / OpenAPI.BASE): origin unknown
  return { client: 'openapi-client', method: unwrap(m).text.toUpperCase(), urlExpr, base: [PH('baseUrl')], baseConf: 'resolved', baseVia: ['generated-client'],
    query: q ? keysOut(requestKeys(q)) : undefined, body: b ? keysOut(requestKeys(b)) : undefined }
}
function kyOrInstanceCall(node, callee) {
  if (!FW.importSource) return null
  let recv = null, method = null, optsArg = null
  if (ts.isIdentifier(callee) || ts.isCallExpression(callee)) { recv = callee; optsArg = node.arguments[1] }   // api(...) / useApi()(...)
  else if (ts.isPropertyAccessExpression(callee) && HTTP_METHODS.has(callee.name.text)) { recv = callee.expression; method = callee.name.text.toUpperCase(); optsArg = node.arguments[1] }
  else return null
  let lib = FW.importSource(recv), inst = null
  if (lib === 'ky' && ts.isIdentifier(unwrap(recv)) && unwrap(recv).text !== 'ky' && !ts.isIdentifier(callee)) lib = 'ky'
  if (lib !== 'ky') { inst = clientCreateOf(recv); if (!inst) return null; lib = inst.lib }
  if (lib === 'ofetch' || lib === '$fetch') { if (method) return null }   // ofetch instances are called directly
  const m = objProp(optsArg, 'method')
  method = method || (m && ts.isStringLiteralLike(unwrap(m)) ? unwrap(m).text.toUpperCase() : 'GET')
  let base = null, baseConf = 'exact'
  for (let i = inst; i; i = i.parent) {
    const b = objProp(i.cfg, 'prefixUrl') || objProp(i.cfg, 'prefix') || objProp(i.cfg, 'baseURL') || objProp(i.cfg, 'baseUrl')
    if (b) { const v = evalStr(b); base = v.vals; baseConf = v.conf; break }
  }
  const q = objProp(optsArg, 'searchParams') || objProp(optsArg, 'query') || objProp(optsArg, 'params')
  const b = objProp(optsArg, 'json') || objProp(optsArg, 'body')
  return { client: lib === 'ky' ? (inst ? 'ky-instance' : 'ky') : 'ofetch-instance', method, urlExpr: node.arguments[0], base, baseConf, baseVia: inst ? [`${lib}.create`] : [],
    query: q ? keysOut(requestKeys(q)) : undefined, body: b ? keysOut(requestKeys(b)) : undefined }
}

// 1-level expansion of parameter-dependent URLs at the enclosing function's call sites
let expanded = 0
for (const d of deferredParamCalls) {
  const fid = declToNode(d.encFn)
  if (!fid) continue
  const sites = callSites.filter(c => c.target === fid)
  for (const s of sites) {
    if (!ts.isCallExpression(s.node)) continue
    const v = evalStr(d.urlExpr, 0, { fn: d.encFn, args: s.node.arguments })
    if (v.vals.every(x => /^\u0001[^\u0002]*\u0002$/.test(x))) continue
    apiCalls.push({ ...d.rec, src: s.cur, file: s.r, line: s.line, urls: shapeDedupe(v.vals).map(render), url_conf: 'resolved',
      via_helper: { fn: fid, at: `${d.rec.file}:${d.rec.line}` } })
    expanded++
  }
}

const httpFns = new Set(apiCalls.map(a => a.src))
for (const c of callSites) {
  if (!httpFns.has(c.target) || !ts.isCallExpression(c.node)) continue
  // object keys passed per argument: literals, variables, and builder calls (requestKeys follows initialisers
  // and project-function returns, up to 4 levels); conditional keys (set under an if) are kept apart
  const rks = c.node.arguments.map(a => {
    const r = keysOut(requestKeys(a))
    return (r.keys.length || r.conditional.length) && !(r.opaque && !r.keys.length) ? r : null
  })
  if (!rks.some(Boolean)) continue
  const e = edges.find(x => x.src === c.cur && x.dst === c.target && x.line === c.line)
  if (e) e.attrs = { ...(e.attrs || {}), arg_keys: rks.map(r => r ? r.keys : null),
    arg_keys_conditional: rks.some(r => r && r.conditional.length) ? rks.map(r => r ? r.conditional : null) : undefined,
    arg_keys_partial: rks.some(r => r && (r.opaque || r.forwarded)) ? rks.map(r => !!(r && (r.opaque || r.forwarded))) : undefined }
}

// ---------- component tags not resolved by imports: Nuxt global components ----------
for (const [file, info] of sfcInfo) {
  const src = fileNode.get(file); const r = rel(file)
  if (!src) continue
  const resolvedTags = new Set(edges.filter(e => e.src === src && e.kind === 'RENDERS').map(e => e.line + ':' + e.dst))
  for (const tg of info.tags) {
    const already = edges.some(e => e.src === src && e.kind === 'RENDERS' && e.line === tg.line)
    if (already) { stats.components_imported++; continue }
    const target = globalComponents[tg.name]
    if (target && fileNode.has(target)) { addEdge(src, fileNode.get(target), 'RENDERS', r, tg.line, 'resolved', { via: ['nuxt-global-component'], tag: tg.tag }); stats.components_global++ }
    else if (target) stats.components_external++
    else { stats.components_unknown++; stats.unknown_tags[tg.name] = (stats.unknown_tags[tg.name] || 0) + 1 }
  }
}

stats.nodes = nodes.length
stats.edges = edges.length
stats.api_calls = apiCalls.length
stats.api_calls_param_expanded = expanded
stats.subscriptions = subscriptions.length
stats.bridge_sends = bridges.length
stats.local_packages = localPkgs.length ? localPkgs : undefined
stats.test_visits = visits.length
stats.program_files = program.getSourceFiles().length
stats.seconds_program = (tProgram - t0) / 1000
stats.seconds_total = (Date.now() - t0) / 1000
const top = Object.entries(stats.unknown_tags).sort((a, b) => b[1] - a[1]).slice(0, 15)
stats.unknown_tags = Object.fromEntries(top)
stats.seconds_fw_facts = tFw / 1000
stats.fw_facts = fwFacts ? { classes: fwFacts.classes.length, calls: fwFacts.calls.length, member_calls: fwFacts.member_calls.length, env: fwFacts.env.length, budget_left: fwFacts.budget_left } : null
stats.config = { tsconfig: noConfig ? null : (parsed.packageConfigs ? null : rel(tsconfigPath)), root_files: rootNames.length,
  ...(parsed.packageConfigs ? { package_tsconfigs: parsed.packageConfigs } : {}) }
fs.writeFileSync(cfg.out, JSON.stringify({ nodes, edges, api_calls: apiCalls, i18n: i18nUses, fallbacks, sfc_i18n: sfcI18n, page_meta: pageMeta, fw: fwFacts,
  subscriptions, bridges, bridge_receivers: bridgeReceivers.filter(b => !(b.protocol === 'react-native-event' && jsEmitted.has(b.module))), bridge_dynamic: bridgeDynamic, visits, test_files: [...testFiles].map(rel).sort(), config_defaults: configDefaults,
  skipped_links: [...new Set(skippedLinks)].sort(),
  // for `cg coverage` (#106): the files analysed, and the roots they come from (source dirs, source files, test trees);
  // a discovered file outside every root was never read, so it is not indexed rather than exact
  seen_files: [...new Set(sourceFiles.map(sf => rel(realFile(sf))))].sort(),
  roots: [...srcDirs.map(d => rel(d) + '/'), ...[...srcFiles].map(rel), ...TEST_ROOTS.map(d => d + '/')].filter(r => r && r !== '/' && !r.startsWith('..')),
  stats }))
console.log(JSON.stringify(stats))
