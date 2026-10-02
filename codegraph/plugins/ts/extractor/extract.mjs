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
const I18N_FNS = new Set(['t', '$t', 'te', '$te', 'tm', 'rt'])
const SKIP_FILE = /(\.test|\.spec)\.(ts|js|mts)$|\/node_modules\/|\/\.nuxt\//
// framework plugins may exclude more (tests, build output) by a regex over the repo-relative path
const SKIP_REL = cfg.skip_re ? new RegExp(cfg.skip_re) : null
// the project's .cg.yaml exclude globs and skip_dirs.add names: never indexed, test code included
const EXCLUDE_REL = cfg.exclude_re ? new RegExp(cfg.exclude_re) : null
// generated / copied / vendored files the Python-side scan classified (codegraph/core/generated.py)
const EXCLUDE_FILES = new Set(cfg.exclude_files || [])
const excludedRel = r => !!(EXCLUDE_REL && EXCLUDE_REL.test(r)) || EXCLUDE_FILES.has(r)
const SRC_EXT = /\.(ts|tsx|mts|cts|js|jsx|mjs|cjs)$/
// test code (Vitest / Jest / Playwright / Cypress): indexed as test nodes unless cfg.index_tests === false; kept out of
// the application graph by the indexer (TEST_* edges)
const INDEX_TESTS = cfg.index_tests !== false
const TEST_FILE_RE = /\.(test|spec|e2e-spec|e2e|cy)\.(t|j)sx?$/
const TEST_DIR_RE = /(^|\/)(__tests__|e2e|tests?|cypress|playwright)\//
const TEST_ROOTS = ['e2e', 'test', 'tests', 'cypress', 'playwright', 'src/test', 'src/tests', 'src/e2e']
const TEST_WALK_SKIP = new Set(['node_modules', 'dist', 'build', 'coverage', 'playwright-report', 'test-results', 'fixtures', '__fixtures__', '__snapshots__'])
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
function walkDir(d, out) {
  let ents = []
  try { ents = fs.readdirSync(d, { withFileTypes: true }) } catch (err) { process.stderr.write(`codegraph: skipping ${d}: ${err.code || err}\n`); return out }
  for (const e of ents) {
    if (e.name === 'node_modules' || e.name.startsWith('.')) continue
    const p = path.join(d, e.name)
    if ((SKIP_REL && SKIP_REL.test(rel(p))) || excludedRel(rel(p)) || (e.isDirectory() && excludedRel(rel(p) + '/'))) continue
    if (e.isDirectory()) walkDir(p, out)
    else if (fileEntry(e, p)) out.push(p)
  }
  return out
}
let srcDirs = (cfg.src_dirs || ['app']).map(d => path.resolve(ROOT, d)).filter(fs.existsSync)
// none of the conventional dirs holds code the tsconfig includes: use the top dirs of the tsconfig's own files
{
  const inside = f => srcDirs.some(d => f.startsWith(d + path.sep))
  const own = (parsed.fileNames || []).filter(f => !f.includes('/node_modules/') && !f.endsWith('.d.ts'))
  if (own.length && !own.some(inside)) {
    const tops = new Set(own.map(f => { const r = path.relative(ROOT, f).split(path.sep); return r.length > 1 ? r.slice(0, Math.min(r.length - 1, 3)).join(path.sep) : '.' }))
    const dirs = [...tops].sort((a, b) => a.length - b.length).filter((d, i, all) => !all.slice(0, i).some(p => p === '.' || d.startsWith(p + path.sep)))
    srcDirs = dirs.map(d => path.resolve(ROOT, d))
    process.stderr.write(`codegraph: source dirs from tsconfig: ${dirs.join(', ')}\n`)
  }
}
const allFiles = srcDirs.flatMap(d => walkDir(d, []))
// test files: spec/test files anywhere in the source dirs, plus top-level test trees (e2e/, tests/, ...)
const testFiles = new Set()
function walkTests(d, all) {
  let ents = []
  try { ents = fs.readdirSync(d, { withFileTypes: true }) } catch { return }
  for (const e of ents) {
    if (e.name.startsWith('.') || TEST_WALK_SKIP.has(e.name)) continue
    const p = path.join(d, e.name)
    if (excludedRel(rel(p)) || (e.isDirectory() && excludedRel(rel(p) + '/'))) continue
    if (e.isDirectory()) walkTests(p, all)
    else if (SRC_EXT.test(e.name) && (e.isFile() || (e.isSymbolicLink() && !skippedLinks.includes(rel(p)) && fileEntry(e, p))) && !e.name.endsWith('.d.ts') && (all || isTestRel(rel(p)))) testFiles.add(p)
  }
}
if (INDEX_TESTS) {
  for (const d of srcDirs) walkTests(d, false)
  for (const d of TEST_ROOTS) { const p = path.resolve(ROOT, d); if (fs.existsSync(p) && fs.statSync(p).isDirectory()) walkTests(p, true) }
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
if (noConfig || cfg.walk_src) configFiles = [...new Set([...configFiles, ...allFiles.filter(f => SRC_EXT.test(f) && !f.endsWith('.d.ts'))])]
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
  v = testFiles.has(real) || (!fn.includes('/node_modules/') && !fn.includes('/.nuxt/') && srcDirs.some(d => real.startsWith(d + path.sep)) && !SKIP_FILE.test(real) && !real.endsWith('.d.ts') && !(SKIP_REL && SKIP_REL.test(rel(real))) && !excludedRel(rel(real)))
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
const isModuleExports = e => { e = unwrap(e); return !!e && ts.isPropertyAccessExpression(e) && e.getText() === 'module.exports' }
const nodes = []                  // {id, kind, name, file, line, end_line, doc, parent, attrs}
const declId = new Map()          // ts.Node (declaration) -> node id
const fileNode = new Map()        // abs real path -> node id
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
  }
  const fid = real.endsWith('.vue') ? `${fk}:${r}` : `module:${r}`
  usedIds.add(fid)
  fileNode.set(real, fid)
  nodes.push({ id: fid, kind: real.endsWith('.vue') ? fk : 'module', name: r, file: r, line: 1, end_line: sf.getLineAndCharacterOfPosition(sf.end).line + 1, attrs: { file_kind: fk, ...(isTestSf ? { test: true } : {}) } })
  if (real.endsWith('.vue')) continue // SFC: references are attributed to the component node
  const visit = (node, qual, parentId, inObj) => {
    let name = null, kind = null, body = null, wrapped = null
    if (ts.isFunctionDeclaration(node) && node.name) { name = node.name.text; kind = 'function'; body = node }
    else if (ts.isFunctionDeclaration(node) && !node.name && !qual && (ts.getCombinedModifierFlags(node) & ts.ModifierFlags.Default)) { name = 'default'; kind = 'function'; body = node }
    else if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
      const init = unwrap(node.initializer)
      if (isFn(init)) { name = node.name.text; kind = 'function'; body = init }
      else if (ts.isCallExpression(init) && ts.isIdentifier(init.expression) && init.expression.text === 'defineStore') {
        name = node.name.text; kind = 'store'; body = init
      } else if (!qual && (wrapped = wrappedFn(init))) { name = node.name.text; kind = 'function'; body = wrapped }
      else if (!qual && ts.isObjectLiteralExpression(init)) { ts.forEachChild(init, c => visit(c, node.name.text, parentId, true)); return }
    } else if (ts.isPropertyAssignment(node) && inObj && node.name && (ts.isIdentifier(node.name) || ts.isStringLiteral(node.name)) && ts.isObjectLiteralExpression(unwrap(node.initializer))) {
      ts.forEachChild(node, c => visit(c, qual ? `${qual}.${node.name.text}` : node.name.text, parentId, true)); return
    } else if ((ts.isMethodDeclaration(node) || ts.isPropertyAssignment(node)) && node.name && (ts.isIdentifier(node.name) || ts.isStringLiteral(node.name))) {
      const init = ts.isPropertyAssignment(node) ? unwrap(node.initializer) : node
      if (ts.isMethodDeclaration(node) || isFn(init)) { name = node.name.text; kind = ts.isMethodDeclaration(node) && ts.isClassLike(node.parent) ? 'method' : 'function'; body = init }
    } else if (ts.isPropertyDeclaration(node) && node.name && ts.isIdentifier(node.name) && node.initializer && ts.isClassLike(node.parent)) {
      const init = unwrap(node.initializer)
      const f = isFn(init) ? init : wrappedFn(init)
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

function edgeKindFor(targetId, isCall) {
  const k = targetId.split(':')[0]
  if (k === 'composable') return 'USES_COMPOSABLE'
  if (k === 'store') return 'USES_STORE'
  if (k === 'class') return 'INSTANTIATES'
  if (k === 'type') return 'REFERENCES_TYPE'
  return 'CALLS'
}

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
    // imports
    if (ts.isImportDeclaration(node) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      const ms = checker.getSymbolAtLocation(node.moduleSpecifier)
      const d = ms && (ms.declarations || [])[0]
      if (d && ts.isSourceFile(d) && projectSf(d)) addEdge(fid, fileNode.get(realFile(d)), 'IMPORTS', r, lineOf(node, sf), 'exact')
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
        && !ts.isBindingElement(node.parent) && !ts.isShorthandPropertyAssignment(node.parent) && !ts.isTypeReferenceNode(node.parent) && !ts.isQualifiedName(node.parent)) {
      const sym = checker.getSymbolAtLocation(node)
      if (sym) {
        const t = resolveSymbol(sym)
        if (t && t.id !== cur) {
          const k = t.id.split(':')[0]
          if (['function', 'composable', 'method', 'store', 'component', 'page', 'layout'].includes(k)) {
            const tplTag = isVue && isTemplateLine(node, sf) && /^__tplc_/.test(enclosingFnName(node))
            const jsxTag = node.parent && (ts.isJsxOpeningElement(node.parent) || ts.isJsxSelfClosingElement(node.parent)) && node.parent.tagName === node
            if (jsxTag) addEdge(cur, t.id, 'RENDERS', r, lineOf(node, sf), t.conf === 'exact' ? 'exact' : 'resolved', { via: ['jsx', ...t.via] })
            else if (tplTag) addEdge(cur, t.id, 'RENDERS', r, lineOf(node, sf), t.conf === 'exact' ? 'exact' : 'resolved', { via: ['template', ...t.via] })
            else if (!['component', 'page', 'layout'].includes(k)) addEdge(cur, t.id, edgeKindFor(t.id), r, lineOf(node, sf), t.conf, { ref: true, via: t.via.length ? t.via : undefined, template: isVue && isTemplateLine(node, sf) || undefined })
          }
        }
      }
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
      expr: http.urlExpr ? http.urlExpr.getText().slice(0, 160) : null, query: http.query, body: http.body }
    // URL built from a parameter of the enclosing function: expand at its call sites (1 level)
    const depParams = [...params].filter(p => encFn && encFn.parameters && encFn.parameters.some(x => ts.isIdentifier(x.name) && x.name.text === p))
    if (depParams.length && encFn) deferredParamCalls.push({ rec, encFn, urlExpr: http.urlExpr })
    apiCalls.push(rec)
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
  addEdge(cur, t.id, kind, r, line, t.conf, { via: t.via.length ? t.via : undefined, template: inTpl || undefined })
  callSites.push({ target: t.id, node, cur, r, line })
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
stats.test_visits = visits.length
stats.program_files = program.getSourceFiles().length
stats.seconds_program = (tProgram - t0) / 1000
stats.seconds_total = (Date.now() - t0) / 1000
const top = Object.entries(stats.unknown_tags).sort((a, b) => b[1] - a[1]).slice(0, 15)
stats.unknown_tags = Object.fromEntries(top)
stats.seconds_fw_facts = tFw / 1000
stats.fw_facts = fwFacts ? { classes: fwFacts.classes.length, calls: fwFacts.calls.length, member_calls: fwFacts.member_calls.length, env: fwFacts.env.length, budget_left: fwFacts.budget_left } : null
stats.config = { tsconfig: noConfig ? null : rel(tsconfigPath), root_files: rootNames.length }
fs.writeFileSync(cfg.out, JSON.stringify({ nodes, edges, api_calls: apiCalls, i18n: i18nUses, fallbacks, sfc_i18n: sfcI18n, page_meta: pageMeta, fw: fwFacts,
  subscriptions, visits, test_files: [...testFiles].map(rel).sort(), config_defaults: configDefaults,
  skipped_links: [...new Set(skippedLinks)].sort(), stats }))
console.log(JSON.stringify(stats))
