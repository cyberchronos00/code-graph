// React Router v6 / v7 and Remix facts for the TypeScript extractor.
//
// Code routers (createBrowserRouter / createHashRouter / createMemoryRouter / useRoutes /
// createRoutesFromElements and <Routes>/<Route>) become page facts. Framework mode
// (app/routes.ts helpers and flatRoutes file conventions) becomes file-route facts.
// Navigation (<Link to>, <NavLink>, <Navigate>, navigate(), redirect(), Next.js <Link href>)
// and forms (<Form>, useFetcher, useSubmit) are recorded for the Python pass.
import fs from 'node:fs'
import nodePath from 'node:path'

const LIB = /^(react-router|react-router-dom|@remix-run\/react)(\/|$)/
const CONFIG_SRC = /@react-router\/dev\/routes|@remix-run\/dev\/routes|@react-router\/fs-routes/
const ROUTERS = new Set(['createBrowserRouter', 'createHashRouter', 'createMemoryRouter', 'useRoutes'])
const NAV_TAGS = new Set(['Link', 'NavLink', 'Navigate'])

export function collectReactRouter(X) {
  const { ts, checker, sourceFiles, rel, realFile, lineOf, declId, resolveSymbol, unwrap, objProp,
    routeLocs, isFn, varInit, returnExprs, fileNode, testFiles, cfg } = X
  const routes = []
  const nav = []
  const forms = []
  const appDir = (cfg.react_router && cfg.react_router.app_dir) || 'app'
  const framework = !!(cfg.react_router && cfg.react_router.framework)
  const sfByRel = new Map()
  for (const sf of sourceFiles) sfByRel.set(rel(realFile(sf)), sf)

  function isTest(sf) { return testFiles && testFiles.has(realFile(sf)) }
  function binding(id) {
    try {
      const sym = checker.getSymbolAtLocation(id)
      const d = sym && (sym.declarations || [])[0]
      if (!d) return null
      if (ts.isImportSpecifier(d) || ts.isImportClause(d) || ts.isNamespaceImport(d)) {
        let imp = d
        while (imp && !ts.isImportDeclaration(imp)) imp = imp.parent
        const src = imp && ts.isStringLiteral(imp.moduleSpecifier) ? imp.moduleSpecifier.text : null
        if (ts.isImportSpecifier(d)) return { src, exp: (d.propertyName || d.name).text }
        if (ts.isNamespaceImport(d)) return { src, exp: '*' }
        if (d.name) return { src, exp: 'default' }
      }
    } catch { /* unresolved import still has a declaration */ }
    return null
  }
  function jsxBinding(tag) {
    if (!tag) return null
    if (ts.isIdentifier(tag)) return binding(tag)
    if (ts.isPropertyAccessExpression(tag) && ts.isIdentifier(tag.expression) && ts.isIdentifier(tag.name)) {
      const ns = binding(tag.expression)
      if (ns && ns.exp === '*') return { src: ns.src, exp: tag.name.text }
      return null
    }
    return null
  }
  function calleeInfo(expr) {
    expr = unwrap(expr)
    if (!expr) return null
    if (ts.isIdentifier(expr)) {
      const b = binding(expr)
      return b ? { ...b, local: expr.text } : { src: null, exp: expr.text, local: expr.text }
    }
    if (ts.isPropertyAccessExpression(expr))
      return { src: null, exp: expr.name.text, recv: unwrap(expr.expression) }
    return null
  }
  function nodeId(expr) {
    expr = unwrap(expr)
    if (!expr) return null
    if (declId.has(expr)) return declId.get(expr)
    if (ts.isIdentifier(expr) || ts.isPropertyAccessExpression(expr)) {
      try {
        const sym = checker.getSymbolAtLocation(ts.isPropertyAccessExpression(expr) ? expr.name : expr)
        const res = sym && resolveSymbol(sym)
        if (res && res.id) return res.id
      } catch { /* symbol not in the program */ }
    }
    return null
  }
  function declFile(expr) {
    expr = unwrap(expr)
    if (!expr) return null
    try {
      if (ts.isIdentifier(expr)) {
        const sym = checker.getSymbolAtLocation(expr)
        const d = sym && (sym.declarations || [])[0]
        if (d) return rel(realFile(d.getSourceFile()))
      }
    } catch { /* ignore */ }
    if (expr.getSourceFile) return rel(realFile(expr.getSourceFile()))
    return null
  }
  function resolveRel(spec, fromReal) {
    if (!spec || !fromReal) return null
    const base = spec.startsWith('.') ? nodePath.resolve(nodePath.dirname(fromReal), spec) : nodePath.resolve(XROOT(fromReal), spec)
    for (const ext of ['', '.tsx', '.ts', '.jsx', '.js', '/index.tsx', '/index.ts', '/index.jsx', '/index.js']) {
      const abs = base + ext
      if (fs.existsSync(abs) && fs.statSync(abs).isFile()) return rel(abs)
    }
    return null
  }
  function XROOT() { return cfg.root ? nodePath.resolve(cfg.root) : nodePath.resolve('.') }
  function resolveApp(spec) {
    if (!spec) return null
    const root = XROOT()
    const base = nodePath.resolve(root, appDir, spec)
    for (const ext of ['', '.tsx', '.ts', '.jsx', '.js']) {
      if (fs.existsSync(base + ext) && fs.statSync(base + ext).isFile()) return rel(base + ext)
    }
    return rel(base)
  }
  function strLit(e) {
    e = unwrap(e)
    return e && ts.isStringLiteralLike(e) ? e.text : null
  }
  function joinRoute(prefix, child) {
    if (child == null || child === '' || child === '/') return prefix || '/'
    if (String(child).startsWith('/')) return child
    if (!prefix || prefix === '/') return '/' + child
    return String(prefix).replace(/\/$/, '') + '/' + child
  }
  function importSpec(e, fromReal) {
    e = unwrap(e)
    if (!e) return null
    if (isFn(e)) {
      if (ts.isBlock(e.body)) {
        const rs = returnExprs(e)
        return rs.length ? importSpec(rs[0], fromReal) : null
      }
      return importSpec(e.body, fromReal)
    }
    if (ts.isCallExpression(e) && e.expression.kind === ts.SyntaxKind.ImportKeyword) {
      const a = e.arguments[0]
      return a && ts.isStringLiteralLike(a) ? resolveRel(a.text, fromReal) : null
    }
    if (ts.isIdentifier(e)) {
      const init = varInit(e)
      return init ? importSpec(init, fromReal) : null
    }
    return null
  }
  function defaultExport(sf) {
    if (!sf) return null
    for (const st of sf.statements) {
      if (ts.isFunctionDeclaration(st) && st.name) {
        const fl = ts.getCombinedModifierFlags(st)
        if ((fl & ts.ModifierFlags.Export) && (fl & ts.ModifierFlags.Default)) return declId.get(st) || null
      }
      if (ts.isExportAssignment(st)) {
        const ex = unwrap(st.expression)
        return nodeId(ex) || (ex && declId.get(ex)) || null
      }
      if (ts.isVariableStatement(st)) {
        const fl = ts.getCombinedModifierFlags(st)
        if (!((fl & ts.ModifierFlags.Export) && (fl & ts.ModifierFlags.Default))) continue
        for (const d of st.declarationList.declarations) return declId.get(d) || nodeId(d.initializer)
      }
    }
    return null
  }
  function namedFn(sf, name) {
    if (!sf) return null
    for (const st of sf.statements) {
      if (ts.isFunctionDeclaration(st) && st.name && st.name.text === name) return declId.get(st) || null
      if (ts.isVariableStatement(st)) {
        for (const d of st.declarationList.declarations)
          if (ts.isIdentifier(d.name) && d.name.text === name) return declId.get(d) || nodeId(d.initializer)
      }
    }
    return null
  }
  function fillModule(rec) {
    const sf = rec.module ? sfByRel.get(rec.module) : null
    if (!sf) return rec
    if (!rec.component) rec.component = defaultExport(sf)
    if (!rec.loader) rec.loader = namedFn(sf, 'loader')
    if (!rec.action) rec.action = namedFn(sf, 'action')
    if (!rec.client_loader) rec.client_loader = namedFn(sf, 'clientLoader')
    if (!rec.client_action) rec.client_action = namedFn(sf, 'clientAction')
    return rec
  }
  function pushRoute(rec) {
    if (!rec || rec.path == null) return
    if (!rec.path.startsWith('/')) rec.path = '/' + rec.path
    routes.push(fillModule(rec))
  }
  function compOf(obj, fromReal) {
    const el = objProp(obj, 'element')
    const Comp = objProp(obj, 'Component')
    const lazy = objProp(obj, 'lazy')
    const lazyFile = lazy ? importSpec(lazy, fromReal) : null
    const target = Comp || el
    let id = null, file = null
    const t = unwrap(target)
    if (t && (ts.isJsxElement(t) || ts.isJsxSelfClosingElement(t))) {
      const tag = (ts.isJsxElement(t) ? t.openingElement : t).tagName
      if (ts.isIdentifier(tag)) { id = nodeId(tag); file = declFile(tag) }
    } else if (t && ts.isIdentifier(t)) {
      id = nodeId(t); file = declFile(t)
    } else if (t && isFn(t)) {
      id = declId.get(t) || null
      file = rel(realFile(t.getSourceFile()))
    }
    if (lazyFile) {
      file = lazyFile
      const sf = sfByRel.get(lazyFile)
      if (sf && !id) id = defaultExport(sf)
    }
    return { id, file }
  }
  function fnProp(obj, name) {
    const p = objProp(obj, name)
    if (!p) return null
    const u = unwrap(p)
    if (ts.isIdentifier(u)) return nodeId(u)
    if (isFn(u)) return declId.get(u) || null
    return null
  }
  function hasIndexChild(expr) {
    expr = unwrap(expr)
    if (ts.isIdentifier(expr)) return hasIndexChild(varInit(expr))
    if (!expr || !ts.isArrayLiteralExpression(expr)) return false
    for (const el of expr.elements) {
      const o = unwrap(el)
      if (!o || !ts.isObjectLiteralExpression(o)) continue
      const ip = objProp(o, 'index')
      const u = unwrap(ip)
      if (u && u.kind === ts.SyntaxKind.TrueKeyword) return true
    }
    return false
  }
  function collectArray(expr, prefix, layoutKeys, fromReal, depth, mode) {
    expr = unwrap(expr)
    if (!expr || depth > 10) return
    if (ts.isIdentifier(expr)) { collectArray(varInit(expr), prefix, layoutKeys, fromReal, depth + 1, mode); return }
    if (ts.isCallExpression(expr)) {
      const info = calleeInfo(expr.expression)
      if (info && info.exp === 'createRoutesFromElements' && LIB.test(info.src || ''))
        walkJsx(expr.arguments[0], prefix, layoutKeys, depth + 1)
      return
    }
    if (!ts.isArrayLiteralExpression(expr)) return
    for (const el of expr.elements) {
      if (ts.isSpreadElement(el)) { collectArray(el.expression, prefix, layoutKeys, fromReal, depth + 1, mode); continue }
      const o = unwrap(el)
      if (o && ts.isObjectLiteralExpression(o)) collectObject(o, prefix, layoutKeys, fromReal, depth + 1, mode)
    }
  }
  function collectObject(obj, prefix, layoutKeys, fromReal, depth, mode) {
    if (!obj || depth > 10) return
    const pathProp = objProp(obj, 'path')
    const pathLit = strLit(pathProp)
    const ip = objProp(obj, 'index')
    const isIndex = !!(ip && unwrap(ip) && unwrap(ip).kind === ts.SyntaxKind.TrueKeyword)
    const children = objProp(obj, 'children')
    const hasChildren = !!children
    const full = isIndex || pathLit == null ? (prefix || '/') : joinRoute(prefix, pathLit)
    const comp = compOf(obj, fromReal)
    const indexChild = hasChildren && hasIndexChild(children)
    const sf = obj.getSourceFile()
    const base = { mode, path: full, file: rel(realFile(sf)), line: lineOf(obj, sf), module: comp.file,
      component: comp.id, loader: fnProp(obj, 'loader'), action: fnProp(obj, 'action'),
      client_loader: fnProp(obj, 'clientLoader'), client_action: fnProp(obj, 'clientAction'),
      layouts: layoutKeys.slice() }
    if (isIndex || !hasChildren) pushRoute({ ...base, role: 'page' })
    else if (indexChild) pushRoute({ ...base, role: 'layout' })
    else pushRoute({ ...base, role: 'both' })
    if (hasChildren) {
      const key = 'react-router:' + full
      collectArray(children, full, layoutKeys.concat(key), fromReal, depth + 1, mode)
    }
  }
  const seenJsx = new Set()
  function walkJsx(expr, prefix, layoutKeys, depth) {
    expr = unwrap(expr)
    if (!expr || depth > 12) return
    if (ts.isIdentifier(expr)) { walkJsx(varInit(expr), prefix, layoutKeys, depth + 1); return }
    if (ts.isJsxFragment(expr)) { for (const c of expr.children) walkJsx(c, prefix, layoutKeys, depth + 1); return }
    if (ts.isArrayLiteralExpression(expr)) { for (const el of expr.elements) walkJsx(el, prefix, layoutKeys, depth + 1); return }
    const open = ts.isJsxElement(expr) ? expr.openingElement : ts.isJsxSelfClosingElement(expr) ? expr : null
    if (!open) return
    const b = jsxBinding(open.tagName)
    if (b && b.exp === 'Routes' && LIB.test(b.src || '')) {
      if (seenJsx.has(expr)) return
      seenJsx.add(expr)
      if (ts.isJsxElement(expr)) for (const c of expr.children) walkJsx(c, prefix, layoutKeys, depth + 1)
      return
    }
    if (!b || b.exp !== 'Route' || !LIB.test(b.src || '')) return
    const pathLit = jsxStr(open, 'path')
    const isIndex = jsxBool(open, 'index')
    const full = isIndex || pathLit == null ? (prefix || '/') : joinRoute(prefix, pathLit)
    const comp = jsxElementComp(open)
    const sf = expr.getSourceFile()
    const kids = ts.isJsxElement(expr) ? expr.children : []
    const hasKids = kids.some(c => {
      const o = c && (ts.isJsxElement(c) || ts.isJsxSelfClosingElement(c)) ? (ts.isJsxElement(c) ? c.openingElement : c) : null
      const jb = o && jsxBinding(o.tagName)
      return jb && jb.exp === 'Route'
    })
    const indexChild = kids.some(c => {
      const o = ts.isJsxElement(c) ? c.openingElement : ts.isJsxSelfClosingElement(c) ? c : null
      return o && jsxBool(o, 'index')
    })
    const rec = { mode: 'code', path: full, file: rel(realFile(sf)), line: lineOf(open, sf), module: comp.file,
      component: comp.id, layouts: layoutKeys.slice(), loader: null, action: null, client_loader: null, client_action: null }
    if (isIndex || !hasKids) pushRoute({ ...rec, role: 'page' })
    else if (indexChild) pushRoute({ ...rec, role: 'layout' })
    else pushRoute({ ...rec, role: 'both' })
    if (hasKids) {
      const key = 'react-router:' + full
      for (const c of kids) walkJsx(c, full, layoutKeys.concat(key), depth + 1)
    }
  }
  function jsxStr(open, name) {
    for (const p of open.attributes.properties) {
      if (!ts.isJsxAttribute(p) || !p.name || p.name.text !== name || !p.initializer) continue
      const v = p.initializer
      if (ts.isStringLiteral(v)) return v.text
      if (ts.isJsxExpression(v)) return strLit(v.expression)
    }
    return null
  }
  function jsxBool(open, name) {
    for (const p of open.attributes.properties) {
      if (!ts.isJsxAttribute(p) || !p.name || p.name.text !== name) continue
      if (!p.initializer) return true
      const v = p.initializer
      if (ts.isJsxExpression(v) && unwrap(v.expression) && unwrap(v.expression).kind === ts.SyntaxKind.TrueKeyword) return true
    }
    return false
  }
  function jsxElementComp(open) {
    for (const p of open.attributes.properties) {
      if (!ts.isJsxAttribute(p) || !p.name || (p.name.text !== 'element' && p.name.text !== 'Component') || !p.initializer) continue
      if (!ts.isJsxExpression(p.initializer)) continue
      const ex = unwrap(p.initializer.expression)
      if (!ex) continue
      if (ts.isJsxElement(ex) || ts.isJsxSelfClosingElement(ex)) {
        const tag = (ts.isJsxElement(ex) ? ex.openingElement : ex).tagName
        if (ts.isIdentifier(tag)) return { id: nodeId(tag), file: declFile(tag) }
      }
      if (ts.isIdentifier(ex)) return { id: nodeId(ex), file: declFile(ex) }
    }
    return { id: null, file: null }
  }
  function configHasIndex(expr) {
    expr = unwrap(expr)
    if (!expr || !ts.isArrayLiteralExpression(expr)) return false
    for (const el of expr.elements) {
      const c = unwrap(ts.isSpreadElement(el) ? el.expression : el)
      if (!c || !ts.isCallExpression(c)) continue
      const info = calleeInfo(c.expression)
      if (info && info.exp === 'index' && CONFIG_SRC.test(info.src || '')) return true
    }
    return false
  }
  function collectConfig(expr, prefix, layoutFiles, depth) {
    expr = unwrap(expr)
    if (!expr || depth > 12) return
    if (ts.isIdentifier(expr)) { collectConfig(varInit(expr), prefix, layoutFiles, depth + 1); return }
    if (ts.isArrayLiteralExpression(expr)) {
      for (const el of expr.elements) {
        if (ts.isSpreadElement(el)) collectConfig(el.expression, prefix, layoutFiles, depth + 1)
        else collectConfig(el, prefix, layoutFiles, depth + 1)
      }
      return
    }
    if (!ts.isCallExpression(expr)) return
    const info = calleeInfo(expr.expression)
    if (!info || !CONFIG_SRC.test(info.src || '')) {
      if (info && info.exp === 'flatRoutes' && /react-router|remix/.test(info.src || '')) scanFlat(expr)
      return
    }
    if (info.exp === 'flatRoutes') { scanFlat(expr); return }
    const sf = expr.getSourceFile()
    const at = { file: rel(realFile(sf)), line: lineOf(expr, sf), mode: 'config' }
    if (info.exp === 'prefix') {
      const p = strLit(expr.arguments[0])
      collectConfig(expr.arguments[1], joinRoute(prefix, p || ''), layoutFiles, depth + 1)
      return
    }
    if (info.exp === 'layout') {
      const mod = resolveApp(strLit(expr.arguments[0]))
      pushRoute({ ...at, role: 'layout', path: prefix || '/', module: mod, layouts: layoutFiles.slice(), component: null })
      collectConfig(expr.arguments[1], prefix, mod ? layoutFiles.concat(mod) : layoutFiles, depth + 1)
      return
    }
    if (info.exp === 'index') {
      const mod = resolveApp(strLit(expr.arguments[0]))
      pushRoute({ ...at, role: 'page', path: prefix || '/', module: mod, layouts: layoutFiles.slice(), component: null, index: true })
      return
    }
    if (info.exp === 'route') {
      const p = strLit(expr.arguments[0])
      const mod = resolveApp(strLit(expr.arguments[1]))
      const full = joinRoute(prefix, p || '')
      const third = expr.arguments[2] && unwrap(expr.arguments[2])
      const kids = third && ts.isArrayLiteralExpression(third) ? third : null
      const role = kids ? (configHasIndex(kids) ? 'layout' : 'both') : 'page'
      pushRoute({ ...at, role, path: full, module: mod, layouts: layoutFiles.slice(), component: null })
      if (kids) collectConfig(kids, full, mod ? layoutFiles.concat(mod) : layoutFiles, depth + 1)
    }
  }
  function splitId(id) {
    const parts = []
    let cur = ''
    for (let i = 0; i < id.length; i++) {
      if (id.startsWith('[.]', i)) { cur += '.'; i += 2; continue }
      if (id[i] === '.') { if (cur) parts.push(cur); cur = ''; continue }
      cur += id[i]
    }
    if (cur) parts.push(cur)
    return parts
  }
  function segPath(s) {
    const opt = /^\((\$[A-Za-z_][A-Za-z0-9_]*)\)$/.exec(s)
    if (opt) return ':' + opt[1].slice(1) + '?'
    if (s === '$' || s === '$*') return '*'
    if (s.startsWith('$')) return ':' + s.slice(1)
    return s
  }
  function urlOf(segs) {
    const parts = []
    for (const s of segs) {
      if (s === '_index') continue
      if (s.startsWith('_')) continue
      parts.push(segPath(s))
    }
    return '/' + parts.join('/')
  }
  function scanFlat(call) {
    let dir = nodePath.resolve(XROOT(), appDir, 'routes')
    const arg = call && call.arguments && call.arguments[0] && unwrap(call.arguments[0])
    if (arg && ts.isObjectLiteralExpression(arg)) {
      const rd = strLit(objProp(arg, 'rootDirectory'))
      if (rd) dir = nodePath.resolve(XROOT(), appDir, rd)
    }
    if (!fs.existsSync(dir)) return
    const files = []
    const walk = d => {
      for (const e of fs.readdirSync(d, { withFileTypes: true })) {
        if (e.name.startsWith('.') || e.name === 'node_modules') continue
        const p = nodePath.join(d, e.name)
        if (e.isDirectory()) walk(p)
        else if (/\.(tsx|ts|jsx|js)$/.test(e.name) && !e.name.endsWith('.d.ts') && !/\.(test|spec)\./.test(e.name)) files.push(p)
      }
    }
    walk(dir)
    const found = []
    for (const abs of files) {
      let id = nodePath.relative(dir, abs).split(nodePath.sep).join('/')
      id = id.replace(/\.(tsx|ts|jsx|js)$/, '')
      if (id.endsWith('/route')) id = id.slice(0, -'/route'.length)
      if (id === 'route') id = '_index'
      const segs = id ? splitId(id.replace(/\//g, '.')) : ['_index']
      found.push({ segs, module: rel(abs), file: rel(abs), line: 1 })
    }
    const isAnc = (a, b) => a.segs.length < b.segs.length && a.segs.every((s, i) => b.segs[i] === s)
    const indexChild = r => found.some(o => o.segs.length === r.segs.length + 1 && o.segs[o.segs.length - 1] === '_index' && isAnc(r, o))
    for (const r of found) {
      const last = r.segs[r.segs.length - 1]
      const isIndex = last === '_index'
      const pathless = last.startsWith('_') && last !== '_index'
      const descendants = found.some(o => isAnc(r, o))
      let role = 'page'
      if (pathless) role = 'layout'
      else if (isIndex) role = 'page'
      else if (indexChild(r)) role = 'layout'
      else if (descendants) role = 'both'
      const layouts = found.filter(o => isAnc(o, r)).sort((a, b) => a.segs.length - b.segs.length).map(o => o.module)
      pushRoute({ mode: 'flat', role, path: urlOf(isIndex ? r.segs.slice(0, -1) : r.segs), module: r.module,
        file: r.file, line: 1, layouts, component: null, index: isIndex })
    }
  }
  function addRoot() {
    if (!routes.some(r => r.mode === 'config' || r.mode === 'flat')) return
    const root = XROOT()
    let rootRel = null
    for (const ext of ['.tsx', '.ts', '.jsx', '.js']) {
      const abs = nodePath.resolve(root, appDir, 'root' + ext)
      if (fs.existsSync(abs)) { rootRel = rel(abs); break }
    }
    if (!rootRel) return
    for (const r of routes) {
      if (r.mode === 'code' || r.module === rootRel) continue
      r.layouts = [rootRel, ...(r.layouts || []).filter(x => x !== rootRel)]
    }
    if (!routes.some(r => r.role !== 'page' && r.module === rootRel))
      routes.push(fillModule({ mode: 'config', role: 'layout', path: '/', module: rootRel, file: rootRel, line: 1, layouts: [], component: null }))
  }
  function internalLocs(locs) {
    return (locs || []).filter(l => {
      if (!l || !l.value) return false
      if (l.kind === 'name') return true
      const v = String(l.value)
      if (v.startsWith('//') || /^[a-z][a-z0-9+.-]*:/i.test(v)) return false
      return true
    })
  }
  function routesFor(file, ownerId) {
    const hits = []
    for (const r of routes) {
      if (r.role === 'layout') continue
      if (r.module === file || r.file === file) hits.push(r)
    }
    if (ownerId) {
      const owned = hits.filter(r => r.component === ownerId)
      if (owned.length === 1) return owned
    }
    return hits
  }
  function chainOf(file, ownerId) {
    const hits = routesFor(file, ownerId)
    const paths = [...new Set(hits.map(r => r.path))]
    if (paths.length !== 1) return null
    const r = hits[0]
    const chain = []
    for (const lk of r.layouts || []) {
      let p = null
      if (String(lk).startsWith('react-router:')) p = lk.slice('react-router:'.length)
      else {
        const lay = routes.find(x => x.role !== 'page' && (x.module === lk || x.file === lk))
        if (lay) p = lay.path
      }
      if (p && chain[chain.length - 1] !== p) chain.push(p)
    }
    if (chain[chain.length - 1] !== r.path) chain.push(r.path)
    return chain
  }
  function absTo(chain, to) {
    if (!to || !chain || !chain.length) return null
    const raw = String(to).trim().split('?')[0].split('#')[0].trim()
    if (!raw || raw.startsWith('//') || /^[a-z][a-z0-9+.-]*:/i.test(raw)) return null
    if (raw.startsWith('/')) return colonToBrace(raw)
    let idx = chain.length - 1
    const extra = []
    for (const seg of raw.split('/')) {
      if (seg === '' || seg === '.') continue
      if (seg === '..') { if (idx > 0) idx--; continue }
      extra.push(seg)
    }
    let base = chain[idx] || '/'
    if (extra.length) base = (!base || base === '/') ? '/' + extra.join('/') : base.replace(/\/$/, '') + '/' + extra.join('/')
    return colonToBrace(base)
  }
  function resolveLocs(file, locs, ownerId) {
    const chain = chainOf(file, ownerId)
    const out = []
    for (const loc of locs) {
      if (loc.kind !== 'path') { out.push(loc); continue }
      if (String(loc.value).startsWith('/')) { out.push(loc); continue }
      const abs = absTo(chain, loc.value)
      if (abs) out.push({ ...loc, value: abs, conf: 'resolved' })
      else out.push(loc)
    }
    return out
  }
  function jsxLocs(open, name) {
    for (const p of open.attributes.properties) {
      if (!ts.isJsxAttribute(p) || !p.name || p.name.text !== name) continue
      if (!p.initializer) return []
      const v = p.initializer
      if (ts.isStringLiteral(v)) return routeLocs(v, 0)
      if (ts.isJsxExpression(v) && v.expression) return routeLocs(v.expression, 0)
    }
    return null
  }
  function fromLib(info) {
    if (!info) return false
    if (LIB.test(info.src || '')) return true
    if (info.recv && ts.isIdentifier(info.recv)) {
      const ns = binding(info.recv)
      return !!(ns && ns.exp === '*' && LIB.test(ns.src || ''))
    }
    return false
  }
  function boundTo(id, names, depth = 0) {
    // `const go = useNavigate()`, `import { useNavigate as useNav }`, `const useNav = useNavigate; const go = useNav()`.
    if (!id || depth > 4) return false
    try {
      const sym = checker.getSymbolAtLocation(id)
      const d = sym && (sym.declarations || [])[0]
      if (!d) return false
      if (ts.isImportSpecifier(d)) {
        const exp = (d.propertyName || d.name).text
        let imp = d
        while (imp && !ts.isImportDeclaration(imp)) imp = imp.parent
        const src = imp && ts.isStringLiteral(imp.moduleSpecifier) ? imp.moduleSpecifier.text : ''
        return names.has(exp) && LIB.test(src)
      }
      if (!ts.isVariableDeclaration(d) || !d.initializer) return false
      const init = unwrap(d.initializer)
      if (ts.isIdentifier(init)) return boundTo(init, names, depth + 1)
      if (!ts.isCallExpression(init)) return false
      const info = calleeInfo(init.expression)
      if (info && names.has(info.exp) && fromLib(info)) return true
      const callee = unwrap(init.expression)
      return ts.isIdentifier(callee) ? boundTo(callee, names, depth + 1) : false
    } catch { return false }
  }
  function historyDelta(arg) {
    arg = unwrap(arg)
    if (!arg) return false
    if (ts.isNumericLiteral(arg)) return true
    if (ts.isPrefixUnaryExpression(arg) && arg.operator === ts.SyntaxKind.MinusToken)
      return ts.isNumericLiteral(unwrap(arg.operand))
    if (ts.isIdentifier(arg)) {
      const init = varInit(arg)
      return !!(init && historyDelta(init))
    }
    return false
  }
  function formUrls(file, locs, ownerId) {
    const chain = chainOf(file, ownerId)
    const raw = (locs || []).map(l => l && l.value).filter(v => v != null && String(v) !== '')
    if (!raw.length) {
      const cur = currentUrl(file, ownerId)
      return { urls: cur ? [colonToBrace(cur)] : [], conf: 'resolved' }
    }
    const urls = []
    let relative = false
    for (const value of raw) {
      const v = String(value)
      if (v.startsWith('/')) urls.push(colonToBrace(v))
      else {
        relative = true
        const abs = absTo(chain, v)
        if (abs) urls.push(abs)
      }
    }
    return { urls: urls.filter(Boolean), conf: relative ? 'resolved' : 'exact' }
  }
  function colonToBrace(p) {
    if (!p) return p
    return String(p).split('/').map(s => {
      if (s === '*') return '{wildcard*?}'
      let m = /^:(\w+)\?$/.exec(s)
      if (m) return '{' + m[1] + '?}'
      m = /^:(\w+)$/.exec(s)
      if (m) return '{' + m[1] + '}'
      return s
    }).join('/')
  }
  function currentUrl(file, ownerId) {
    const chain = chainOf(file, ownerId)
    if (!chain) return null
    return chain[chain.length - 1]
  }

  for (const sf of sourceFiles) {
    if (isTest(sf)) continue
    const fromReal = realFile(sf)
    const visit = n => {
      if (ts.isCallExpression(n)) {
        const info = calleeInfo(n.expression)
        if (info && ROUTERS.has(info.exp) && LIB.test(info.src || ''))
          collectArray(n.arguments[0], '', [], fromReal, 0, 'code')
        if (info && info.exp === 'createRoutesFromElements' && LIB.test(info.src || ''))
          walkJsx(n.arguments[0], '', [], 0)
      }
      ts.forEachChild(n, visit)
    }
    // default export of the routes module (array or flatRoutes())
    for (const st of sf.statements) {
      if (ts.isExportAssignment(st)) collectConfig(st.expression, '', [], 0)
    }
    visit(sf)
    // <Routes> trees that are not arguments of createRoutesFromElements
    const visitJsx = n => {
      if ((ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n))) {
        const open = ts.isJsxElement(n) ? n.openingElement : n
        const b = jsxBinding(open.tagName)
        if (b && b.exp === 'Routes' && LIB.test(b.src || '')) walkJsx(n, '', [], 0)
      }
      ts.forEachChild(n, visitJsx)
    }
    visitJsx(sf)
  }
  if (framework && !routes.some(r => r.mode === 'flat' || r.mode === 'config')) {
    const dir = nodePath.resolve(XROOT(), appDir, 'routes')
    if (fs.existsSync(dir)) scanFlat(null)
  }
  addRoot()

  for (const sf of sourceFiles) {
    if (isTest(sf)) continue
    const r = rel(realFile(sf))
    const stack = []
    const fid = fileNode.get(realFile(sf))
    if (fid) stack.push(fid)
    const visit = node => {
      const own = declId.get(node)
      if (own) stack.push(own)
      const cur = stack.length ? stack[stack.length - 1] : fid
      if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) {
        const open = ts.isJsxElement(node) ? node.openingElement : node
        const b = jsxBinding(open.tagName)
        const line = lineOf(open, sf)
        if (b && NAV_TAGS.has(b.exp) && LIB.test(b.src || '')) {
          const locs = resolveLocs(r, internalLocs(jsxLocs(open, 'to') || []), cur)
          if (locs.length) nav.push({ src: cur, file: r, line, via: 'link', locs })
        }
        if (b && (b.exp === 'Link' || b.exp === 'default') && b.src === 'next/link') {
          const locs = internalLocs(jsxLocs(open, 'href') || [])
          if (locs.length) nav.push({ src: cur, file: r, line, via: 'link', locs })
        }
        const form = (b && b.exp === 'Form' && LIB.test(b.src || ''))
          || (ts.isPropertyAccessExpression(open.tagName) && open.tagName.name.text === 'Form'
              && ts.isIdentifier(open.tagName.expression) && boundTo(open.tagName.expression, new Set(['useFetcher'])))
        if (form) {
          const method = (jsxStr(open, 'method') || 'GET').toUpperCase()
          const got = formUrls(r, jsxLocs(open, 'action'), cur)
          if (got.urls.length) forms.push({ src: cur, file: r, line, client: 'react-router', method, urls: got.urls, url_conf: got.conf, base: null, base_conf: 'exact', base_via: [] })
        }
      }
      if (ts.isCallExpression(node)) {
        const info = calleeInfo(node.expression)
        const line = lineOf(node, sf)
        if (info && info.exp === 'redirect' && /^(react-router|react-router-dom|@remix-run\/react|@remix-run\/node|@react-router\/node)(\/|$)/.test(info.src || '') && node.arguments[0]) {
          const locs = resolveLocs(r, internalLocs(routeLocs(node.arguments[0], 0)), cur)
          if (locs.length) nav.push({ src: cur, file: r, line, via: 'redirect', locs })
        }
        if (info && ts.isIdentifier(unwrap(node.expression)) && node.arguments[0]
            && !historyDelta(node.arguments[0]) && boundTo(unwrap(node.expression), new Set(['useNavigate']))) {
          const locs = resolveLocs(r, internalLocs(routeLocs(node.arguments[0], 0)), cur)
          if (locs.length) nav.push({ src: cur, file: r, line, via: 'navigate', locs })
        }
        if (info && info.exp === 'load' && info.recv && ts.isIdentifier(info.recv) && boundTo(info.recv, new Set(['useFetcher'])) && node.arguments[0]) {
          const got = formUrls(r, routeLocs(node.arguments[0], 0), cur)
          if (got.urls.length) forms.push({ src: cur, file: r, line, client: 'react-router', method: 'GET', urls: got.urls, url_conf: got.conf, base: null, base_conf: 'exact', base_via: [] })
        }
        if (info && (info.exp === 'submit') && node.arguments.length && (
          (info.recv && ts.isIdentifier(info.recv) && boundTo(info.recv, new Set(['useFetcher'])))
          || (ts.isIdentifier(unwrap(node.expression)) && boundTo(unwrap(node.expression), new Set(['useSubmit']))))) {
          const opt = node.arguments[1] && unwrap(node.arguments[1])
          const m = opt && objProp(opt, 'method')
          const method = (m && strLit(m) || 'GET').toUpperCase()
          const act = opt && objProp(opt, 'action')
          const got = formUrls(r, act ? routeLocs(act, 0) : [], cur)
          if (got.urls.length) forms.push({ src: cur, file: r, line, client: 'react-router', method, urls: got.urls, url_conf: got.conf, base: null, base_conf: 'exact', base_via: [] })
        }
      }
      ts.forEachChild(node, visit)
      if (own) stack.pop()
    }
    visit(sf)
  }
  return { routes, nav, forms }
}
