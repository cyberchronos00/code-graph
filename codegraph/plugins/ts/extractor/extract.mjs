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

const t0 = Date.now()
const cfg = JSON.parse(fs.readFileSync(process.argv[process.argv.indexOf('--config') + 1], 'utf8'))
const ROOT = path.resolve(cfg.root)
const rel = p => path.relative(ROOT, p).split(path.sep).join('/')
const HTTP_METHODS = new Set(['get', 'post', 'put', 'patch', 'delete', 'head', 'options'])
const FETCH_FNS = new Set(['$fetch', 'useFetch', 'useLazyFetch', 'ofetch', 'fetch'])
const I18N_FNS = new Set(['t', '$t', 'te', '$te', 'tm', 'rt'])
const SKIP_FILE = /(\.test|\.spec)\.(ts|js|mts)$|\/node_modules\/|\/\.nuxt\//

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
const tsconfigPath = path.resolve(ROOT, cfg.tsconfig)
const parsed = ts.parseJsonConfigFileContent(ts.readConfigFile(tsconfigPath, ts.sys.readFile).config, ts.sys, path.dirname(tsconfigPath))
const options = { ...parsed.options, noEmit: true, skipLibCheck: true }
const pathsBase = options.pathsBasePath || options.baseUrl || path.dirname(tsconfigPath)

function walkDir(d, out) {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    if (e.name === 'node_modules' || e.name.startsWith('.')) continue
    const p = path.join(d, e.name)
    if (e.isDirectory()) walkDir(p, out)
    else out.push(p)
  }
  return out
}
const srcDirs = (cfg.src_dirs || ['app']).map(d => path.join(ROOT, d)).filter(fs.existsSync)
const allFiles = srcDirs.flatMap(d => walkDir(d, []))
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
  const src = fs.readFileSync(file, 'utf8')
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

const SKIP_ROOT = /(\.test|\.spec)\.(ts|js|mts)$/
const rootNames = [...parsed.fileNames.filter(f => !SKIP_ROOT.test(f)), ...virtual.keys()]
const program = ts.createProgram({ rootNames, options, host })
const checker = program.getTypeChecker()
const tProgram = Date.now()

// ---------- helpers ----------
const projectSf = sf => {
  const fn = sf.fileName
  if (fn.includes('/node_modules/') || fn.includes('/.nuxt/')) return false
  const real = fn.endsWith('.vue.ts') ? fn.slice(0, -3) : fn
  return srcDirs.some(d => real.startsWith(d + path.sep)) && !SKIP_FILE.test(real) && !real.endsWith('.d.ts')
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
const nodes = []                  // {id, kind, name, file, line, end_line, doc, parent, attrs}
const declId = new Map()          // ts.Node (declaration) -> node id
const fileNode = new Map()        // abs real path -> node id
const usedIds = new Set()
function mkId(kind, key) { let id = `${kind}:${key}`, i = 2; while (usedIds.has(id)) id = `${kind}:${key}~${i++}`; usedIds.add(id); return id }

const sourceFiles = program.getSourceFiles().filter(projectSf)
for (const sf of sourceFiles) {
  const real = realFile(sf), r = rel(real), fk = fileKind(r)
  if (!real.endsWith('.vue')) stats.ts_files++
  const fid = real.endsWith('.vue') ? `${fk}:${r}` : `module:${r}`
  usedIds.add(fid)
  fileNode.set(real, fid)
  nodes.push({ id: fid, kind: real.endsWith('.vue') ? fk : 'module', name: r, file: r, line: 1, end_line: sf.getLineAndCharacterOfPosition(sf.end).line + 1, attrs: { file_kind: fk } })
  if (real.endsWith('.vue')) continue // SFC: references are attributed to the component node
  const visit = (node, qual, parentId) => {
    let name = null, kind = null, body = null
    if (ts.isFunctionDeclaration(node) && node.name) { name = node.name.text; kind = 'function'; body = node }
    else if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
      const init = unwrap(node.initializer)
      if (isFn(init)) { name = node.name.text; kind = 'function'; body = init }
      else if (ts.isCallExpression(init) && ts.isIdentifier(init.expression) && init.expression.text === 'defineStore') {
        name = node.name.text; kind = 'store'; body = init
      }
    } else if ((ts.isMethodDeclaration(node) || ts.isPropertyAssignment(node)) && node.name && (ts.isIdentifier(node.name) || ts.isStringLiteral(node.name))) {
      const init = ts.isPropertyAssignment(node) ? unwrap(node.initializer) : node
      if (ts.isMethodDeclaration(node) || isFn(init)) { name = node.name.text; kind = ts.isMethodDeclaration(node) && ts.isClassLike(node.parent) ? 'method' : 'function'; body = init }
    } else if (ts.isClassDeclaration(node) && node.name) { name = node.name.text; kind = 'class'; body = node }
    else if ((ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node) || ts.isEnumDeclaration(node)) && node.name) {
      name = node.name.text; kind = 'type'
    }
    if (name) {
      const q = qual ? `${qual}.${name}` : name
      if (kind === 'function' && !qual && fk === 'composable' && /^use[A-Z0-9]/.test(name)) kind = 'composable'
      const attrs = {}
      if (kind === 'store') { const a0 = body.arguments[0]; if (a0 && ts.isStringLiteralLike(a0)) attrs.store_id = a0.text }
      const isExported = !qual && (ts.getCombinedModifierFlags(ts.isVariableDeclaration(node) ? node : node) & ts.ModifierFlags.Export) !== 0
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
const nodeById = new Map(nodes.map(n => [n.id, n]))

// ---------- symbol -> target node ----------
const projectDecl = d => d && projectSf(d.getSourceFile())
function declToNode(d) {
  if (!d) return null
  if (declId.has(d)) return declId.get(d)
  if ((ts.isArrowFunction(d) || ts.isFunctionExpression(d)) && declId.has(d.parent)) return declId.get(d.parent)
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
// evalStr -> {vals:[...], conf:'exact'|'resolved', params:Set}
function evalStr(e, depth = 0, ctx = {}) {
  e = unwrap(e)
  const R = (vals, conf = 'exact') => ({ vals, conf })
  if (!e || depth > 8) return R([PH('?')], 'resolved')
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
    if (d && ts.isVariableDeclaration(d) && d.initializer && (ts.getCombinedNodeFlags(d) & ts.NodeFlags.Const)) {
      const v = evalStr(d.initializer, depth + 1, ctx)
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
  if (ts.isPropertyAccessExpression(e) && /(^|\.)public$|useRuntimeConfig\(\)$/.test(unwrap(e.expression).getText())) {
    return R([PH('runtimeConfig.' + e.name.text)])
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
    if (['encodeURIComponent', 'encodeURI', 'String', 'Number'].includes(cname) && e.arguments[0]) {
      const v = evalStr(e.arguments[0], depth + 1, ctx)
      return R(v.vals, 'resolved')
    }
    if (['toString', 'trim', 'replace', 'replaceAll', 'toLowerCase', 'toUpperCase'].includes(cname) && ts.isPropertyAccessExpression(callee)) {
      const v = evalStr(callee.expression, depth + 1, ctx)
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
    // call returning an instance
    const fn = fnDeclOf(checker.getSymbolAtLocation(ts.isPropertyAccessExpression(c) ? c.name : c))
    for (const r of returnExprs(fn)) { const o = axiosCreateOf(r, depth + 1); if (o) return o }
    return null
  }
  let sym = ts.isPropertyAccessExpression(expr) ? checker.getSymbolAtLocation(expr.name) : checker.getSymbolAtLocation(expr)
  for (let i = 0; sym && i < 10; i++) {
    if (sym.flags & ts.SymbolFlags.Alias) { sym = checker.getAliasedSymbol(sym); continue }
    const d = (sym.declarations || []).find(projectDecl)
    if (!d) return null
    if (ts.isVariableDeclaration(d) && d.initializer) { const o = axiosCreateOf(d.initializer, depth + 1); return o && { cfg: o.cfg, via: [...o.via, 'var'] } }
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
    return /\bAxios(Instance|Static)\b/.test(s) || (t.symbol && /^Axios(Instance|Static)$/.test(t.symbol.name))
  } catch { return false }
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
            if (tplTag) addEdge(cur, t.id, 'RENDERS', r, lineOf(node, sf), t.conf === 'exact' ? 'exact' : 'resolved', { via: ['template', ...t.via] })
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
  if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(callee) && (HTTP_METHODS.has(callee.name.text) || callee.name.text === 'request') && isAxiosType(callee.expression)) {
    const recvType = checker.typeToString(checker.getTypeAtLocation(callee.expression))
    const isInstance = /AxiosInstance/.test(recvType)
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
  } else if (ts.isCallExpression(node) && ts.isIdentifier(callee) && FETCH_FNS.has(callee.text)) {
    const o = node.arguments[1]; const m = objProp(o, 'method')
    const method = m && ts.isStringLiteralLike(unwrap(m)) ? unwrap(m).text.toUpperCase() : 'GET'
    const b = objProp(o, 'baseURL')
    const bv = b ? evalStr(b) : null
    const fq = objProp(o, 'query') || objProp(o, 'params'), fb = objProp(o, 'body')
    http = { client: callee.text, method, urlExpr: node.arguments[0], base: bv ? bv.vals : null, baseConf: bv ? bv.conf : 'exact', baseVia: [],
      query: fq ? keysOut(requestKeys(fq)) : undefined, body: fb ? keysOut(requestKeys(fb)) : undefined }
  }
  if (http) {
    const params = new Set()
    const v = http.urlExpr ? evalStr(http.urlExpr, 0, { params }) : { vals: [PH('?')], conf: 'resolved' }
    const rec = { src: cur, file: r, line, client: http.client, method: http.method, template: inTpl || undefined,
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
stats.program_files = program.getSourceFiles().length
stats.seconds_program = (tProgram - t0) / 1000
stats.seconds_total = (Date.now() - t0) / 1000
const top = Object.entries(stats.unknown_tags).sort((a, b) => b[1] - a[1]).slice(0, 15)
stats.unknown_tags = Object.fromEntries(top)
fs.writeFileSync(cfg.out, JSON.stringify({ nodes, edges, api_calls: apiCalls, i18n: i18nUses, fallbacks, sfc_i18n: sfcI18n, page_meta: pageMeta, stats }))
console.log(JSON.stringify(stats))
