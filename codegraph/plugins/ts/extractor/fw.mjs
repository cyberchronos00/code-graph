// Framework facts for code-graph's TypeScript extractor (NestJS, Next.js, Express/Fastify/Koa/Hono, ORMs, env).
//
// The extractor resolves symbols; this module only *describes* syntax that server frameworks give meaning to,
// as plain JSON, and the Python framework plugins (plugins/nest, plugins/nextjs, plugins/express) interpret it:
//
//   classes       decorators (+ evaluated args), constructor params (type, @Inject token), decorated properties,
//                 methods (decorators, param decorators/types)
//   calls         router-style calls: x.get('/p', ...handlers), x.use('/p', sub), x.register(fn, {prefix}), ...
//   member_calls  this.<prop>.<method>(...) and ORM / event / queue calls (prisma.user.findMany, emit('evt'), ...)
//   instances     declarations referenced as receivers/arguments (router variables, params, class properties)
//   modules       per file: directives ('use server'/'use client'), exports (+ node ids), req.method checks
//   env / config_defs  process.env / import.meta.env / ConfigService.get keys, registerAs() namespaces
//
// Values are described, never executed: {s} string (URL-template folded by evalStr), {n}, {b}, {arr}, {obj},
// {fn,node,body}, {ref,key,node}, {call,node,args,recv,ret}, {new,node,args}, {require,module,exp}, {expr}.
import nodePath from 'node:path'
const ROUTE_VERBS = new Set(['get', 'post', 'put', 'patch', 'delete', 'del', 'head', 'options', 'all', 'any'])
const ROUTER_METHODS = new Set([...ROUTE_VERBS, 'use', 'lazyUse', 'route', 'register', 'mount', 'basePath', 'prefix',
  'setGlobalPrefix', 'enableVersioning', 'useGlobalGuards', 'useGlobalInterceptors', 'useGlobalPipes', 'useGlobalFilters',
  'on', 'method', 'addHook', 'group', 'routes', 'connectMicroservice'])
const HTTP_UPPER = new Set(['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'])
const DATA_METHODS = new Set([
  // Prisma
  'findUnique', 'findUniqueOrThrow', 'findFirst', 'findFirstOrThrow', 'findMany', 'create', 'createMany', 'createManyAndReturn',
  'update', 'updateMany', 'updateManyAndReturn', 'upsert', 'delete', 'deleteMany', 'count', 'aggregate', 'groupBy',
  // TypeORM / Sequelize / Mongoose
  'find', 'findOne', 'findOneBy', 'findBy', 'findAndCount', 'findAndCountBy', 'findOneOrFail', 'findOneByOrFail', 'findByIds',
  'save', 'insert', 'remove', 'softDelete', 'softRemove', 'restore', 'increment', 'decrement', 'exist', 'exists', 'existsBy',
  'createQueryBuilder', 'query', 'findAll', 'findByPk', 'findOrCreate', 'bulkCreate', 'destroy',
  'findById', 'findByIdAndUpdate', 'findByIdAndDelete', 'findOneAndUpdate', 'findOneAndDelete', 'findOneAndReplace',
  'updateOne', 'deleteOne', 'insertMany', 'countDocuments', 'estimatedDocumentCount', 'distinct', 'replaceOne', 'bulkWrite',
  // Kysely / Knex / Drizzle
  'selectFrom', 'insertInto', 'updateTable', 'deleteFrom', 'replaceInto', 'mergeInto', 'from', 'into', 'table',
  'innerJoin', 'leftJoin', 'rightJoin', 'fullJoin',
])
const EVENT_METHODS = new Set(['emit', 'emitAsync', 'add', 'addBulk', 'send', 'publish', 'dispatch'])

export function collectFrameworkFacts(X) {
  const { ts, checker, sourceFiles, rel, realFile, lineOf, declId, declToNode, resolveSymbol, evalStr, render, unwrap, projectSf, fileNode } = X
  const classes = [], calls = [], memberCalls = [], env = [], configDefs = [], modules = {}, provideObjs = []
  const instances = {}
  let budget = 400000   // describe() node budget (very large repos)

  // source file a require()/import specifier points at; relative specs are also resolved by path, because a .ts file
  // without import/export statements (only `module.exports = ...`) is a script, not a module, for the checker
  const sfByPath = new Map(sourceFiles.map(sf => [sf.fileName, sf]))
  function sfOfSpec(spec) {
    try {
      const sym = checker.getSymbolAtLocation(spec)
      const d = sym && (sym.declarations || [])[0]
      if (d && ts.isSourceFile(d)) return d
    } catch { }
    const t = spec.text
    if (!t || !t.startsWith('.')) return null
    const base = nodePath.resolve(nodePath.dirname(spec.getSourceFile().fileName), t)
    for (const ext of ['', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '/index.ts', '/index.tsx', '/index.js', '/index.jsx']) {
      const sf = sfByPath.get(base + ext)
      if (sf) return sf
    }
    return null
  }
  const text = (n, max = 120) => { try { return n.getText().replace(/\s+/g, ' ').slice(0, max) } catch { return '?' } }
  const isStrish = e => e && (ts.isStringLiteralLike(e) || ts.isTemplateExpression(e) ||
    (ts.isBinaryExpression(e) && e.operatorToken.kind === ts.SyntaxKind.PlusToken))
  const rootIdent = e => { e = unwrap(e); while (e && (ts.isPropertyAccessExpression(e) || ts.isElementAccessExpression(e) || ts.isCallExpression(e))) e = unwrap(e.expression); return e && ts.isIdentifier(e) ? e : null }

  function importSource(expr) {
    const id = rootIdent(expr)
    if (!id) return null
    let sym
    try { sym = checker.getSymbolAtLocation(id) } catch { return null }
    const d = sym && (sym.declarations || [])[0]
    if (!d) return null
    let p = d
    if (ts.isImportSpecifier(p) || ts.isImportClause(p) || ts.isNamespaceImport(p) || ts.isNamedImports(p)) {
      while (p && !ts.isImportDeclaration(p)) p = p.parent
      return p && ts.isStringLiteral(p.moduleSpecifier) ? p.moduleSpecifier.text : null
    }
    if (ts.isImportEqualsDeclaration(p) && p.moduleReference && ts.isExternalModuleReference(p.moduleReference)) return p.moduleReference.expression.text
    if (ts.isBindingElement(p)) { while (p && !ts.isVariableDeclaration(p)) p = p.parent }
    if (p && ts.isVariableDeclaration(p) && p.initializer) {
      let i = unwrap(p.initializer)
      while (i && ts.isPropertyAccessExpression(i)) i = unwrap(i.expression)
      if (i && ts.isAwaitExpression(i)) i = unwrap(i.expression)
      if (i && ts.isCallExpression(i) && ((ts.isIdentifier(i.expression) && i.expression.text === 'require') || i.expression.kind === ts.SyntaxKind.ImportKeyword) && i.arguments[0] && ts.isStringLiteralLike(i.arguments[0])) return i.arguments[0].text
    }
    return null
  }
  X.importSource = importSource

  function symOf(e) {
    e = unwrap(e)
    try {
      if (ts.isIdentifier(e)) return e.parent && ts.isShorthandPropertyAssignment(e.parent) && e.parent.name === e
        ? checker.getShorthandAssignmentValueSymbol(e.parent) : checker.getSymbolAtLocation(e)
      if (ts.isPropertyAccessExpression(e)) return checker.getSymbolAtLocation(e.name)
    } catch { }
    return null
  }
  // method names chained after a call: db('t').where(..).insert(..) -> ['where', 'insert']
  function chainAfter(call) {
    const out = []
    for (let n = call; out.length < 12; ) {
      const pa = n.parent
      if (!pa || !ts.isPropertyAccessExpression(pa) || pa.expression !== n || !pa.parent || !ts.isCallExpression(pa.parent)) break
      out.push(pa.name.text); n = pa.parent
    }
    return out
  }
  // `registry.posts.browse` where `registry.posts` is a getter / property whose value wraps `require('./posts')`
  // (lazy controller registries: `get posts() { return pipeline(require('./posts'), utils) }`): the member of the
  // required module's exported object
  const isRequireCall = n => n && ts.isCallExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === 'require' && n.arguments[0] && ts.isStringLiteralLike(n.arguments[0])
  // object literal an expression evaluates to (identifier -> initializer, require('./x') -> the module's exported object)
  function objOfExpr(o, depth = 0) {
    o = o && unwrap(o)
    if (!o || depth > 3) return null
    if (ts.isIdentifier(o)) { const d2 = origDecl(checker.getSymbolAtLocation(o)); return d2 && ts.isVariableDeclaration(d2) && d2.initializer ? objOfExpr(d2.initializer, depth + 1) : null }
    if (isRequireCall(o)) { const r = objOfModule(o.arguments[0], null, depth + 1); return r && r.named === undefined ? r : null }
    return ts.isObjectLiteralExpression(o) ? o : null
  }
  function objOfModule(spec, named, depth = 0) {
    try {
      if (depth > 3) return null
      const sfd = sfOfSpec(spec)
      if (!sfd || !projectSf(sfd)) return null
      const ms = sfd.symbol ? checker.getMergedSymbol(sfd.symbol) : {}
      const objOf = d => {
        if (d && ts.isPropertyAccessExpression(d) && d.parent && ts.isBinaryExpression(d.parent)) d = d.parent   // module.exports.x = ...
        const o = d && (ts.isBinaryExpression(d) ? d.right : ts.isVariableDeclaration(d) ? d.initializer : ts.isExportAssignment(d) ? d.expression : null)
        return objOfExpr(o, depth + 1)
      }
      // require('./x').controller on an ES module: the named export
      if (named && ms.exports && ms.exports.get(named) && !ms.exports.get('export=')) return { named: objOf(origDecl(ms.exports.get(named))) }
      const eq = ms.exports && ms.exports.get('export=')
      if (eq) return objOf(origDecl(eq))
      // `module.exports = x` in a .ts file (not a CommonJS export for the checker, but what require() returns at runtime)
      for (const st of sfd.statements) {
        const e = ts.isExpressionStatement(st) && st.expression
        if (e && ts.isBinaryExpression(e) && e.operatorToken.kind === ts.SyntaxKind.EqualsToken && e.left.getText(sfd) === 'module.exports') return objOf(e)
      }
      return null
    } catch { return null }
  }
  // `X.prop` where X comes from `const X = require('./x')` (or `require('./x').member`) in a file where the checker
  // does not treat require() as an import (.ts files): the declaration of `prop` in the required module's object
  function holderViaRequire(expr) {
    expr = unwrap(expr)
    if (!ts.isPropertyAccessExpression(expr) || !ts.isIdentifier(expr.expression)) return null
    let sym; try { sym = checker.getSymbolAtLocation(expr.expression) } catch { return null }
    const vd = sym && (sym.declarations || [])[0]
    let init = vd && ts.isVariableDeclaration(vd) && vd.initializer && unwrap(vd.initializer)
    let member = null
    if (init && ts.isPropertyAccessExpression(init)) { member = init.name.text; init = unwrap(init.expression) }
    if (!isRequireCall(init)) return null
    let o = objOfModule(init.arguments[0], member)
    if (o && o.named !== undefined) o = o.named
    else if (o && member) {
      const p = o.properties.find(p => p.name && p.name.getText().replace(/['"]/g, '') === member)
      o = p && ts.isPropertyAssignment(p) ? objOfExpr(p.initializer) : null
    }
    return o ? o.properties.find(p => p.name && p.name.getText().replace(/['"]/g, '') === expr.name.text) || null : null
  }
  function memberThroughRegistry(e) {
    const holder = symOf(e.expression)
    let hd = holder && (holder.declarations || [])[0]
    if (!hd || !projectSf(hd.getSourceFile())) hd = holderViaRequire(e.expression)
    if (!hd || !projectSf(hd.getSourceFile())) return null
    const fnLike = ts.isGetAccessorDeclaration(hd) || ts.isMethodDeclaration(hd) ? hd : null
    const exprs = fnLike ? returnsOf(fnLike) : (ts.isPropertyAssignment(hd) && hd.initializer ? [hd.initializer] : [])
    for (const r of exprs) {
      let found = null
      const v = n => {
        if (found) return
        if (ts.isCallExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === 'require' && n.arguments[0] && ts.isStringLiteralLike(n.arguments[0])) {
          const named = n.parent && ts.isPropertyAccessExpression(n.parent) && n.parent.expression === n ? n.parent.name.text : null
          let o = objOfModule(n.arguments[0], named)
          if (o && o.named !== undefined) o = o.named
          // require('./x').controller
          else if (o && named) {
            const p = o.properties.find(p => p.name && p.name.getText() === n.parent.name.text)
            const i = p && ts.isPropertyAssignment(p) ? unwrap(p.initializer) : null
            o = i && ts.isObjectLiteralExpression(i) ? i : (i && ts.isIdentifier(i) ? (() => { const d = origDecl(symOf(i)); const x = d && ts.isVariableDeclaration(d) && d.initializer && unwrap(d.initializer); return x && ts.isObjectLiteralExpression(x) ? x : null })() : null)
          }
          if (o) found = o
          return
        }
        ts.forEachChild(n, v)
      }
      v(r)
      if (!found) continue
      const p = found.properties.find(p => p.name && p.name.getText().replace(/['"]/g, '') === e.name.text)
      if (!p) return null
      const init = ts.isPropertyAssignment(p) ? unwrap(p.initializer) : null
      const node = declId.get(p) || (init && declId.get(init))
      if (node) return { node, via_registry: true }
      if (init && ts.isObjectLiteralExpression(init)) {
        const fns = init.properties.map(q => declId.get(q) || (ts.isPropertyAssignment(q) && declId.get(unwrap(q.initializer)))).filter(Boolean)
        if (fns.length) return { obj_fns: fns, via_registry: true }
      }
      return null
    }
    return null
  }
  // original declaration behind imports / re-exports / export= / `const a = b` aliases
  function origDecl(sym) {
    for (let i = 0; sym && i < 12; i++) {
      if (sym.flags & ts.SymbolFlags.Alias) { try { sym = checker.getAliasedSymbol(sym) } catch { return null } continue }
      const ds = sym.declarations || []
      const d = ds.find(x => projectSf(x.getSourceFile())) || ds[0]
      if (!d) return null
      if (ts.isShorthandPropertyAssignment(d)) { sym = checker.getShorthandAssignmentValueSymbol(d); continue }
      if (ts.isExportSpecifier(d)) { try { sym = checker.getExportSpecifierLocalTargetSymbol(d) } catch { return null } continue }
      if (ts.isExportAssignment(d)) { const ex = unwrap(d.expression); if (ts.isIdentifier(ex)) { sym = checker.getSymbolAtLocation(ex); continue } return d }
      if (ts.isBinaryExpression(d) && d.operatorToken.kind === ts.SyntaxKind.EqualsToken) { const r = unwrap(d.right); if (ts.isIdentifier(r)) { sym = checker.getSymbolAtLocation(r); continue } return d }
      if (ts.isVariableDeclaration(d) && d.initializer && projectSf(d.getSourceFile())) {
        const init = unwrap(d.initializer)
        if (ts.isIdentifier(init)) { const s2 = checker.getSymbolAtLocation(init); if (s2 && s2 !== sym) { sym = s2; continue } }
      }
      return d
    }
    return null
  }
  function keyOfDecl(d) {
    const sf = d.getSourceFile()
    if (!projectSf(sf)) return null
    let nm = ''
    try { nm = d.name ? d.name.getText(sf) : (ts.isBinaryExpression(d) ? d.left.getText(sf) : '') } catch { }
    return `${rel(realFile(sf))}:${lineOf(d, sf)}:${nm.slice(0, 60)}`
  }
  function enclosingFnNode(n) {
    for (let p = n.parent; p; p = p.parent) if (declId.has(p)) return declId.get(p)
    return null
  }
  function addInstance(key, d) {
    if (instances[key] !== undefined) return
    instances[key] = null
    const sf = d.getSourceFile()
    const inst = { file: rel(realFile(sf)), line: lineOf(d, sf), name: d.name ? text(d.name, 60) : null }
    if (ts.isParameter(d)) {
      inst.kind = 'param'
      const fn = d.parent
      inst.fn = declToNode(fn) || enclosingFnNode(d)
      inst.index = fn.parameters ? fn.parameters.indexOf(d) : null
      if (d.type) inst.type = text(d.type, 80)
    } else if (ts.isVariableDeclaration(d) || ts.isPropertyDeclaration(d) || ts.isPropertyAssignment(d)) {
      inst.kind = ts.isVariableDeclaration(d) ? 'var' : 'prop'
      if (d.initializer) inst.init = describe(d.initializer, 1)
      if (d.type) inst.type = text(d.type, 80)
    } else if (ts.isBinaryExpression(d)) {
      inst.kind = 'assign'; inst.init = describe(d.right, 1)
    } else if (ts.isExportAssignment(d)) {
      inst.kind = 'var'; inst.name = 'default'; inst.init = describe(d.expression, 1)
    } else inst.kind = ts.SyntaxKind[d.kind]
    instances[key] = inst
  }
  // returned expressions of a project function (1 level)
  function returnsOf(fn) {
    if (!fn) return []
    if (ts.isArrowFunction(fn) && !ts.isBlock(fn.body)) return [fn.body]
    const out = []
    const v = n => { if (ts.isReturnStatement(n) && n.expression) out.push(n.expression); if (!ts.isFunctionLike(n)) ts.forEachChild(n, v) }
    if (fn.body) ts.forEachChild(fn.body, v)
    return out.slice(0, 4)
  }
  function fnDeclFromSym(sym) {
    const d = origDecl(sym)
    if (!d) return null
    if (ts.isFunctionDeclaration(d) || ts.isMethodDeclaration(d) || ts.isArrowFunction(d) || ts.isFunctionExpression(d)) return d
    if ((ts.isVariableDeclaration(d) || ts.isPropertyDeclaration(d) || ts.isPropertyAssignment(d)) && d.initializer) {
      const i = unwrap(d.initializer)
      if (ts.isArrowFunction(i) || ts.isFunctionExpression(i)) return i
    }
    if (ts.isBinaryExpression(d)) { const r = unwrap(d.right); if (ts.isArrowFunction(r) || ts.isFunctionExpression(r)) return r }
    if (ts.isExportAssignment(d)) { const r = unwrap(d.expression); if (ts.isArrowFunction(r) || ts.isFunctionExpression(r)) return r }
    return null
  }
  function moduleExport(spec, from) {
    // require('./x') / import('./x'): the module's export= / default export value
    try {
      const d = sfOfSpec(spec)
      if (!d || !projectSf(d)) return null
      const ms = d.symbol ? checker.getMergedSymbol(d.symbol) : null
      const exps = ms ? checker.getExportsOfModule(ms) : []
      const ex = (ms && ms.exports && ms.exports.get('export=')) || exps.find(s => s.name === 'default')
      const res = { module: fileNode.get(realFile(d)) || null }
      if (!ex) {
        // `module.exports = x` in a .ts file: not an export for the checker, but what require() returns at runtime
        for (const st of d.statements) {
          const e = ts.isExpressionStatement(st) && st.expression
          if (e && ts.isBinaryExpression(e) && e.operatorToken.kind === ts.SyntaxKind.EqualsToken && e.left.getText(d) === 'module.exports') { res.exp = describe(e.right, 2); break }
        }
      }
      if (ex) {
        const od = origDecl(ex)
        if (od) {
          if (ts.isBinaryExpression(od)) res.exp = describe(od.right, 2)
          else if (ts.isExportAssignment(od)) res.exp = describe(od.expression, 2)
          else if (ts.isFunctionDeclaration(od)) res.exp = { fn: declToNode(od), ret: returnsOf(od).map(r => describe(r, 3)) }
          else { const k = keyOfDecl(od); if (k) { addInstance(k, od); res.exp = { ref: od.name ? text(od.name, 60) : '?', key: k, node: declToNode(od) } } }
        }
      }
      return res
    } catch { return null }
  }

  function describe(e, depth = 0) {
    e = unwrap(e)
    if (!e) return null
    if (--budget < 0 || depth > 7) return { expr: text(e, 60) }
    if (isStrish(e)) {
      const v = evalStr(e)
      return v.vals.length === 1 ? { s: render(v.vals[0]), conf: v.conf } : { ss: v.vals.map(render), conf: v.conf }
    }
    if (ts.isNumericLiteral(e)) return { n: Number(e.text) }
    if (e.kind === ts.SyntaxKind.TrueKeyword || e.kind === ts.SyntaxKind.FalseKeyword) return { b: e.kind === ts.SyntaxKind.TrueKeyword }
    if (e.kind === ts.SyntaxKind.NullKeyword || (ts.isIdentifier(e) && e.text === 'undefined')) return { nul: 1 }
    if (ts.isRegularExpressionLiteral(e)) return { re: e.text }
    if (ts.isArrayLiteralExpression(e)) return { arr: e.elements.slice(0, 80).map(x => ts.isSpreadElement(x) ? { spread: describe(x.expression, depth + 1) } : describe(x, depth + 1)) }
    if (ts.isObjectLiteralExpression(e)) {
      const o = {}
      for (const p of e.properties.slice(0, 60)) {
        if (ts.isPropertyAssignment(p)) o[p.name.getText().replace(/^['"`]|['"`]$/g, '')] = describe(p.initializer, depth + 1)
        else if (ts.isShorthandPropertyAssignment(p)) o[p.name.text] = describe(p.name, depth + 1)
        else if (ts.isMethodDeclaration(p) && p.name) { const rs = depth < 3 ? returnsOf(p) : []; o[p.name.getText()] = { fn: declToNode(p), line: lineOf(p), ret: rs.length ? rs.map(x => describe(x, depth + 2)) : undefined } }
        else if (ts.isSpreadAssignment(p)) o['...' + Object.keys(o).length] = { spread: describe(p.expression, depth + 1) }
      }
      return { obj: o, line: lineOf(e) }
    }
    if (ts.isArrowFunction(e) || ts.isFunctionExpression(e)) {
      const out = { fn: declToNode(e) || null, line: lineOf(e) }
      if (ts.isArrowFunction(e) && !ts.isBlock(e.body) && depth < 4) out.body = describe(e.body, depth + 1)
      else if (depth < 3) { const rs = returnsOf(e); if (rs.length) out.ret = rs.map(r => describe(r, depth + 2)) }
      return out
    }
    if (ts.isClassExpression(e)) return { cls: declToNode(e) || null }
    if (ts.isIdentifier(e) || ts.isPropertyAccessExpression(e) || ts.isElementAccessExpression(e)) {
      if (ts.isPropertyAccessExpression(e) || ts.isIdentifier(e)) {
        const v = evalStr(e)
        if (v.vals.length && v.vals.every(x => !/\u0001/.test(x)) && v.vals.length <= 8) return v.vals.length === 1 ? { s: v.vals[0], conf: v.conf, ref: text(e, 80) } : { ss: v.vals, conf: v.conf, ref: text(e, 80) }
      }
      const out = { ref: text(e, 80) }
      const sym = symOf(e)
      if (sym) {
        const t = resolveSymbol(sym)
        if (t) out.node = t.id
        const d = origDecl(sym)
        if (d) {
          const k = keyOfDecl(d)
          if (k) { out.key = k; addInstance(k, d) }
          if (!out.node && (ts.isPropertyAssignment(d) || ts.isVariableDeclaration(d)) && d.initializer && ts.isObjectLiteralExpression(unwrap(d.initializer))) {
            // handler objects (e.g. { permissions, query(frame) {} }): the functions declared directly in it
            const fns = unwrap(d.initializer).properties.map(p => declId.get(p) || (ts.isPropertyAssignment(p) && declId.get(unwrap(p.initializer)))).filter(Boolean)
            if (fns.length) out.obj_fns = fns
          }
        }
      }
      if (ts.isPropertyAccessExpression(e) && !out.node && !out.obj_fns) {
        const t = memberThroughRegistry(e)
        if (t) Object.assign(out, t)
      }
      if (ts.isPropertyAccessExpression(e) && !out.node && !out.key && depth < 4) out.of = describe(e.expression, depth + 1)
      if (!out.node && !out.key && ts.isIdentifier(e)) { const m = importSource(e); if (m) out.mod = m }   // imported from a package
      return out
    }
    if (ts.isCallExpression(e)) {
      const c = unwrap(e.expression)
      if (ts.isIdentifier(c) && c.text === 'require' && e.arguments[0] && ts.isStringLiteralLike(e.arguments[0])) {
        return { require: e.arguments[0].text, ...(moduleExport(e.arguments[0], e) || {}) }
      }
      const out = { call: text(c, 80) }
      const mod = importSource(c); if (mod) out.mod = mod
      const sym = symOf(ts.isPropertyAccessExpression(c) ? c : c)
      if (sym) {
        const t = resolveSymbol(sym); if (t) out.node = t.id
        if (depth < 3) { const fd = fnDeclFromSym(sym); if (fd && projectSf(fd.getSourceFile())) { const rs = returnsOf(fd); if (rs.length) out.ret = rs.map(r => describe(r, depth + 2)) } }
        // `const f = require('./x'); f()`: the function exported by the module (through `module.exports = require(..)` chains)
        if (!out.ret && depth < 3 && ts.isIdentifier(c)) {
          const vd = (sym.declarations || [])[0]
          const ri = vd && ts.isVariableDeclaration(vd) && vd.initializer && unwrap(vd.initializer)
          if (ri && ts.isCallExpression(ri) && ts.isIdentifier(ri.expression) && ri.expression.text === 'require' && ri.arguments[0] && ts.isStringLiteralLike(ri.arguments[0])) {
            let x = (moduleExport(ri.arguments[0], ri) || {}).exp
            for (let i = 0; x && x.require && i < 4; i++) x = x.exp
            if (x && x.ret) out.ret = x.ret
            if (x && x.fn && !out.node) out.node = x.fn
          }
        }
      }
      out.args = e.arguments.slice(0, 12).map(a => describe(a, depth + 1))
      if (ts.isPropertyAccessExpression(c)) out.recv = describe(c.expression, depth + 1)
      return out
    }
    if (ts.isNewExpression(e)) {
      const out = { new: text(e.expression, 80) }
      const mod = importSource(e.expression); if (mod) out.mod = mod
      const sym = symOf(e.expression); if (sym) { const t = resolveSymbol(sym); if (t) out.node = t.id }
      out.args = (e.arguments || []).slice(0, 8).map(a => describe(a, depth + 1))
      return out
    }
    if (ts.isAwaitExpression(e)) return describe(e.expression, depth)
    if (ts.isConditionalExpression(e)) return { cond: [describe(e.whenTrue, depth + 1), describe(e.whenFalse, depth + 1)] }
    if (ts.isBinaryExpression(e) && [ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(e.operatorToken.kind))
      return { cond: [describe(e.left, depth + 1), describe(e.right, depth + 1)] }
    return { expr: text(e, 60) }
  }
  X.describe = describe

  function typeInfo(tn) {
    if (!tn) return null
    const out = { text: text(tn, 100) }
    let ref = tn
    while (ref && (ts.isArrayTypeNode(ref) || ts.isParenthesizedTypeNode(ref))) ref = ref.elementType || ref.type
    if (ref && ts.isUnionTypeNode(ref)) ref = ref.types.find(t => ts.isTypeReferenceNode(t)) || ref
    if (ref && ts.isTypeReferenceNode(ref)) {
      out.name = ts.isQualifiedName(ref.typeName) ? ref.typeName.right.text : ref.typeName.text
      const sym = checker.getSymbolAtLocation(ts.isQualifiedName(ref.typeName) ? ref.typeName.right : ref.typeName)
      const t = sym && resolveSymbol(sym); if (t) out.id = t.id
      const mod = importSource(ts.isQualifiedName(ref.typeName) ? ref.typeName.left : ref.typeName); if (mod) out.mod = mod
      if (ref.typeArguments) out.args = ref.typeArguments.slice(0, 3).map(typeInfo)
    }
    if (ts.isArrayTypeNode(tn)) out.array = true
    return out
  }
  function decs(n) {
    let ds = []
    try { ds = (ts.getDecorators ? ts.getDecorators(n) : n.decorators) || [] } catch { ds = [] }
    return ds.map(d => {
      const e = unwrap(d.expression)
      if (ts.isCallExpression(e)) return { name: text(e.expression, 60), mod: importSource(e.expression) || undefined, args: e.arguments.map(a => describe(a, 0)), line: lineOf(d) }
      return { name: text(e, 60), mod: importSource(e) || undefined, args: [], line: lineOf(d) }
    })
  }
  const hasMod = (n, k) => !!(ts.getCombinedModifierFlags(n) & k)

  // ---------- classes ----------
  for (const sf of sourceFiles) {
    if (realFile(sf).endsWith('.vue')) continue
    const visit = n => {
      if ((ts.isClassDeclaration(n) || ts.isClassExpression(n)) && declId.has(n)) {
        const c = { id: declId.get(n), name: n.name ? n.name.text : null, file: rel(realFile(sf)), line: lineOf(n, sf),
          abstract: hasMod(n, ts.ModifierFlags.Abstract) || undefined, decorators: decs(n), ctor: [], props: [], methods: [] }
        for (const h of n.heritageClauses || []) {
          for (const t of h.types) {
            const sym = checker.getSymbolAtLocation(t.expression)
            const r = sym && resolveSymbol(sym)
            const info = { text: text(t, 100), id: r ? r.id : undefined, mod: importSource(t.expression) || undefined,
              args: t.typeArguments ? t.typeArguments.map(typeInfo) : undefined }
            if (ts.isCallExpression(unwrap(t.expression))) info.call = describe(t.expression, 1)
            if (h.token === ts.SyntaxKind.ExtendsKeyword) c.extends = info; else (c.implements = c.implements || []).push(info)
          }
        }
        for (const m of n.members) {
          if (ts.isConstructorDeclaration(m)) {
            c.ctor_line = lineOf(m, sf)
            for (const p of m.parameters) c.ctor.push({ name: text(p.name, 60), line: lineOf(p, sf), type: typeInfo(p.type), decorators: decs(p),
              prop: hasMod(p, ts.ModifierFlags.ParameterPropertyModifier) || undefined })
          } else if (ts.isPropertyDeclaration(m) && m.name) {
            const ds = decs(m)
            const pr = { name: text(m.name, 60), line: lineOf(m, sf), type: typeInfo(m.type), decorators: ds, optional: !!m.questionToken || undefined,
              node: declId.get(m) || undefined, static: hasMod(m, ts.ModifierFlags.Static) || undefined }
            if (m.initializer && (ds.length || !declId.get(m))) pr.init = describe(m.initializer, 1)
            c.props.push(pr)
          } else if ((ts.isMethodDeclaration(m) || ts.isGetAccessor(m)) && m.name) {
            c.methods.push({ name: text(m.name, 60), id: declId.get(m) || undefined, line: lineOf(m, sf), decorators: decs(m),
              static: hasMod(m, ts.ModifierFlags.Static) || undefined,
              params: m.parameters.map(p => ({ name: text(p.name, 60), type: typeInfo(p.type), decorators: decs(p) })) })
          }
        }
        classes.push(c)
      }
      ts.forEachChild(n, visit)
    }
    ts.forEachChild(sf, visit)
  }

  // ---------- calls / member calls / env / modules ----------
  const isProcessEnv = e => { e = unwrap(e); return e && ((ts.isPropertyAccessExpression(e) && e.name.text === 'env' && /^(process|Bun)$/.test(text(e.expression))) || text(e) === 'import.meta.env') }
  // env read through a wrapper (#103): `env.X` / `environment.X` (outline's Environment), or a value parsed from
  // process.env (`EnvSchema.safeParse(process.env).data`, `plainToInstance(EnvDto, process.env)`, Joi `validate`)
  const ENV_WRAP = /^(env|environment)$/i
  const ENV_KEY = /^[A-Z][A-Z0-9_]*[A-Z0-9]$/
  function envDerived(e, depth = 0) {
    e = unwrap(e)
    if (!e || depth > 5) return false
    if (isProcessEnv(e)) return true
    if (ts.isAwaitExpression(e)) return envDerived(e.expression, depth + 1)
    if (ts.isCallExpression(e)) return e.arguments.some(a => isProcessEnv(a))
    if (ts.isPropertyAccessExpression(e) && /^(data|value|env)$/.test(e.name.text)) return envDerived(e.expression, depth + 1)
    if (ts.isIdentifier(e)) {
      let d
      try { const sym = checker.getSymbolAtLocation(e); d = sym && (sym.declarations || [])[0] } catch { return false }
      if (!d) return false
      if (ts.isVariableDeclaration(d) && d.initializer) return envDerived(d.initializer, depth + 1)
      if (ts.isBindingElement(d)) { let p = d.parent; while (p && !ts.isVariableDeclaration(p)) p = p.parent; return !!(p && p.initializer && envDerived(p.initializer, depth + 1)) }
    }
    return false
  }
  const envVia = e => {
    e = unwrap(e)
    if (!e || isProcessEnv(e)) return null
    if (ts.isIdentifier(e) && ENV_WRAP.test(e.text)) return 'env wrapper ' + e.text
    return envDerived(e) ? 'env schema ' + text(e, 40) : null
  }
  const thisProp = e => { e = unwrap(e); return e && ts.isPropertyAccessExpression(e) && unwrap(e.expression).kind === ts.SyntaxKind.ThisKeyword ? e.name.text : null }
  function chainOf(e) {
    // a.b.c -> ['a','b','c'] (identifiers / this only), else null
    const out = []
    e = unwrap(e)
    while (e && ts.isPropertyAccessExpression(e)) { out.unshift(e.name.text); e = unwrap(e.expression) }
    if (e && ts.isIdentifier(e)) out.unshift(e.text)
    else if (e && e.kind === ts.SyntaxKind.ThisKeyword) out.unshift('this')
    else return null
    return out
  }
  function classOf(n) { for (let p = n.parent; p; p = p.parent) if (ts.isClassLike(p)) return declId.get(p) || null; return null }

  for (const sf of sourceFiles) {
    const real = realFile(sf), r = rel(real), fid = fileNode.get(real), isVue = real.endsWith('.vue')
    const mod = { directives: [], server_fns: [], method_checks: [] }
    for (const st of sf.statements) {
      if (ts.isExpressionStatement(st) && ts.isStringLiteral(st.expression)) mod.directives.push(st.expression.text); else break
    }
    const stack = [fid]
    const visit = node => {
      const own = !isVue && declId.get(node)
      if (own) stack.push(own)
      const cur = stack[stack.length - 1]
      // DI provider objects anywhere ({ provide: TOKEN, useClass | useValue | useFactory | useExisting }): providers
      // listed through spread arrays, custom provider files and dynamic modules (static forRoot() { return {...} })
      if (ts.isObjectLiteralExpression(node) && budget > 0 && node.properties.some(p => ts.isPropertyAssignment(p) && p.name && ts.isIdentifier(p.name) && p.name.text === 'provide'))
        provideObjs.push({ file: r, line: lineOf(node, sf), desc: describe(node, 1) })
      // inline 'use server' functions (server actions declared in components)
      if (own && ts.isFunctionLike(node) && node.body && ts.isBlock(node.body)) {
        const s0 = node.body.statements[0]
        if (s0 && ts.isExpressionStatement(s0) && ts.isStringLiteral(s0.expression) && s0.expression.text === 'use server') mod.server_fns.push(own)
      } else if (own && (ts.isVariableDeclaration(node) || ts.isPropertyAssignment(node)) && node.initializer) {
        const f = unwrap(node.initializer)
        if (f && (ts.isArrowFunction(f) || ts.isFunctionExpression(f)) && f.body && ts.isBlock(f.body)) {
          const s0 = f.body.statements[0]
          if (s0 && ts.isExpressionStatement(s0) && ts.isStringLiteral(s0.expression) && s0.expression.text === 'use server') mod.server_fns.push(own)
        }
      }
      // env reads
      // (`process.env.X = ...` writes the key: not a read, #103)
      const assigned = node.parent && ts.isBinaryExpression(node.parent) && node.parent.left === node && node.parent.operatorToken.kind === ts.SyntaxKind.EqualsToken
      if (assigned && (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) && isProcessEnv(node.expression)) { }
      else if (ts.isPropertyAccessExpression(node) && isProcessEnv(node.expression)) env.push({ src: cur, key: node.name.text, file: r, line: lineOf(node, sf), via: text(node.expression) })
      else if (ts.isPropertyAccessExpression(node) && !assigned && ENV_KEY.test(node.name.text) && envVia(node.expression))
        env.push({ src: cur, key: node.name.text, file: r, line: lineOf(node, sf), via: envVia(node.expression) })
      else if (ts.isElementAccessExpression(node) && isProcessEnv(node.expression) && node.argumentExpression && ts.isStringLiteralLike(node.argumentExpression))
        env.push({ src: cur, key: node.argumentExpression.text, file: r, line: lineOf(node, sf), via: text(node.expression) })
      else if (ts.isVariableDeclaration(node) && ts.isObjectBindingPattern(node.name) && node.initializer && isProcessEnv(node.initializer)) {
        for (const el of node.name.elements) env.push({ src: cur, key: text(el.propertyName || el.name), file: r, line: lineOf(node, sf), via: text(node.initializer) })
      }
      // req.method === 'POST' / switch (req.method) { case 'POST': }
      // (`method` may also be destructured: const { method } = req)
      const isMethodRef = x => (ts.isPropertyAccessExpression(x) && x.name.text === 'method') || (ts.isIdentifier(x) && x.text === 'method')
      if (ts.isBinaryExpression(node) && [ts.SyntaxKind.EqualsEqualsEqualsToken, ts.SyntaxKind.EqualsEqualsToken].includes(node.operatorToken.kind)) {
        const [a, b] = [unwrap(node.left), unwrap(node.right)]
        for (const [x, y] of [[a, b], [b, a]]) if (isMethodRef(x) && ts.isStringLiteralLike(y) && HTTP_UPPER.has(y.text.toUpperCase()))
          mod.method_checks.push({ src: cur, method: y.text.toUpperCase(), line: lineOf(node, sf) })
      }
      if (ts.isSwitchStatement(node) && isMethodRef(unwrap(node.expression))) {
        for (const cl of node.caseBlock.clauses) if (ts.isCaseClause(cl) && ts.isStringLiteralLike(cl.expression) && HTTP_UPPER.has(cl.expression.text.toUpperCase()))
          mod.method_checks.push({ src: cur, method: cl.expression.text.toUpperCase(), line: lineOf(cl, sf) })
      }
      if (ts.isCallExpression(node)) {
        const c = unwrap(node.expression)
        if (ts.isPropertyAccessExpression(c)) {
          const m = c.name.text
          const a0 = node.arguments[0] && unwrap(node.arguments[0])
          // router-style calls
          let take = false
          if (ROUTER_METHODS.has(m)) {
            const recvIsChain = ts.isCallExpression(unwrap(c.expression)) && ts.isPropertyAccessExpression(unwrap(unwrap(c.expression).expression)) &&
              (ROUTE_VERBS.has(unwrap(unwrap(c.expression).expression).name.text) || unwrap(unwrap(c.expression).expression).name.text === 'route')
            if (ROUTE_VERBS.has(m)) take = (node.arguments.length >= 2 && (isStrish(a0) || (a0 && ts.isArrayLiteralExpression(a0)) || (a0 && ts.isIdentifier(a0)) || (a0 && ts.isRegularExpressionLiteral(a0)))) || (recvIsChain && node.arguments.length >= 1)
            else if (m === 'on') take = node.arguments.length >= 3 && a0 && (ts.isArrayLiteralExpression(a0) || (ts.isStringLiteralLike(a0) && HTTP_UPPER.has(a0.text.toUpperCase())))
            else if (m === 'routes') take = node.arguments.length === 0
            else take = node.arguments.length >= 1 || m === 'routes'
            if (m === 'get' && node.arguments.length === 2 && a0 && ts.isStringLiteralLike(a0) && !/^\/|^\*$|^:/.test(a0.text)) take = false // map.get('k', d)
          }
          if (take && budget > 0) {
            const chain = []
            let base = unwrap(c.expression)
            while (ts.isCallExpression(base) && ts.isPropertyAccessExpression(unwrap(base.expression)) && chain.length < 12) {
              const bc = unwrap(base.expression)
              if (!(ROUTE_VERBS.has(bc.name.text) || ['route', 'use', 'basePath', 'prefix', 'on'].includes(bc.name.text))) break
              chain.unshift({ method: bc.name.text, args: base.arguments.slice(0, 1).map(a => describe(a, 2)), line: lineOf(base, sf) })
              base = unwrap(bc.expression)
            }
            // `const api = Router().use(a).use(b)` / `export default new Hono().route(..)`: the declaration owning the chain
            let owner
            if (ts.isCallExpression(base)) {
              let top = node
              while (top.parent && ts.isPropertyAccessExpression(top.parent) && top.parent.expression === top && top.parent.parent && ts.isCallExpression(top.parent.parent)) top = top.parent.parent
              const od = top.parent && (ts.isVariableDeclaration(top.parent) || ts.isExportAssignment(top.parent)) ? top.parent : null
              const ok = od && keyOfDecl(od)
              if (ok) { owner = ok; addInstance(ok, od) }
            }
            calls.push({ src: cur, file: r, line: lineOf(node, sf), method: m, recv: describe(base, 0), chain: chain.length ? chain : undefined,
              recv_text: text(base, 60), args: node.arguments.slice(0, 16).map(a => describe(a, 0)), owner })
          }
          // this.<prop>.<method>(...) and data/event calls
          const tp = thisProp(c.expression)
          const ch = tp ? null : chainOf(c.expression)
          if (tp || (ch && (DATA_METHODS.has(m) || EVENT_METHODS.has(m)) && ch[0] !== 'Array' && ch[0] !== 'Buffer' && ch[0] !== 'Object' && ch[0] !== 'Promise')
              || (!ch && (m === 'from' || m === 'into' || m === 'table' || m === 'innerJoin' || m === 'leftJoin' || m === 'returning'))) {
            const mc = { src: cur, cls: classOf(node) || undefined, file: r, line: lineOf(node, sf), method: m }
            if (tp) mc.prop = tp
            else if (ch) { mc.chain = ch; const rs = symOf(c.expression); if (rs) { const d = origDecl(rs); if (d) { const k = keyOfDecl(d); if (k) { mc.key = k; addInstance(k, d) } } } }
            else mc.root = (rootIdent(c.expression) || {}).text
            if (a0 && (isStrish(a0) || ts.isIdentifier(a0) || ts.isPropertyAccessExpression(a0) || ts.isObjectLiteralExpression(a0) && DATA_METHODS.has(m))) mc.a0 = describe(a0, 2)
            if (ch || mc.root) mc.recv_type = (() => { try { return checker.typeToString(checker.getTypeAtLocation(c.expression)).slice(0, 80) } catch { return undefined } })()
            memberCalls.push(mc)
          } else if (!tp && !ch && (DATA_METHODS.has(m) || EVENT_METHODS.has(m))) {
            // chained receivers (db.select().from(t), knex('t').where(...))
            const rt = rootIdent(c.expression)
            if (rt && a0) memberCalls.push({ src: cur, cls: classOf(node) || undefined, file: r, line: lineOf(node, sf), method: m, root: rt.text, a0: describe(a0, 2), chained: true })
          }
          // ConfigService.get('KEY')
          if ((m === 'get' || m === 'getOrThrow') && a0 && ts.isStringLiteralLike(a0)) {
            let rt = ''
            try { rt = checker.typeToString(checker.getTypeAtLocation(c.expression)) } catch { }
            if (/ConfigService/.test(rt) || /(^|\.)(config|configService|cfg)$/i.test(text(c.expression))) env.push({ src: cur, key: a0.text, file: r, line: lineOf(node, sf), via: 'ConfigService.' + m })
          }
          if (m === 'get' && a0 && ts.isStringLiteralLike(a0) && /^(Deno\.env)$/.test(text(c.expression))) env.push({ src: cur, key: a0.text, file: r, line: lineOf(node, sf), via: 'Deno.env' })
        } else if (ts.isIdentifier(c)) {
          // knex('table') / registerAs('ns', () => ({...}))
          if (c.text === 'registerAs' && node.arguments[0] && ts.isStringLiteralLike(node.arguments[0]) && node.arguments[1]) {
            const ns = node.arguments[0].text
            const f = unwrap(node.arguments[1])
            let body = f && (ts.isArrowFunction(f) || ts.isFunctionExpression(f)) ? (ts.isBlock(f.body) ? returnsOf(f)[0] : f.body) : null
            body = body && unwrap(body)
            if (body && ts.isObjectLiteralExpression(body)) {
              const walkObj = (o, prefix) => {
                for (const p of o.properties) {
                  if (!ts.isPropertyAssignment(p) && !ts.isShorthandPropertyAssignment(p)) continue
                  const k = `${prefix}.${p.name.getText().replace(/['"]/g, '')}`
                  const init = ts.isPropertyAssignment(p) ? unwrap(p.initializer) : null
                  if (init && ts.isObjectLiteralExpression(init)) { walkObj(init, k); continue }
                  const envs = []
                  const v = n => { if (ts.isPropertyAccessExpression(n) && isProcessEnv(n.expression)) envs.push(n.name.text); else if (ts.isElementAccessExpression(n) && isProcessEnv(n.expression) && n.argumentExpression && ts.isStringLiteralLike(n.argumentExpression)) envs.push(n.argumentExpression.text); ts.forEachChild(n, v) }
                  if (init) v(init)
                  configDefs.push({ ns, key: k, env: envs, file: r, line: lineOf(p, sf), src: cur })
                }
              }
              walkObj(body, ns)
            }
          }
          if (/^(knex|db|trx|sql)$/.test(c.text) && node.arguments.length === 1 && ts.isStringLiteralLike(node.arguments[0]))
            memberCalls.push({ src: cur, cls: classOf(node) || undefined, file: r, line: lineOf(node, sf), method: 'knex', root: c.text, a0: { s: node.arguments[0].text }, chain: chainAfter(node) })
        }
      }
      ts.forEachChild(node, visit)
      if (own) stack.pop()
    }
    ts.forEachChild(sf, visit)
    // exports
    if (!isVue) {
      const exps = []
      try {
        const ms = sf.symbol && checker.getMergedSymbol(sf.symbol)
        const eq = ms && ms.exports && ms.exports.get('export=')   // CommonJS module.exports = x / TS export = x
        const list = ms ? checker.getExportsOfModule(ms) : []
        for (const s of eq && !list.includes(eq) ? [eq, ...list] : list) {
          if (budget <= 0) break
          const t = resolveSymbol(s)
          const d = origDecl(s)
          const ex = { name: s.name, node: t ? t.id : undefined }
          if (d) {
            const dsf = d.getSourceFile()
            ex.line = lineOf(d, dsf)
            if (!t || ['config', 'runtime', 'dynamic', 'revalidate', 'matcher', 'metadata', 'preferredRegion', 'maxDuration', 'default', 'export='].includes(s.name)) {
              if ((ts.isVariableDeclaration(d) || ts.isPropertyAssignment(d)) && d.initializer) ex.desc = describe(d.initializer, 1)
              else if (ts.isBindingElement(d)) { let p = d; while (p && !ts.isVariableDeclaration(p)) p = p.parent; if (p && p.initializer) ex.desc = { destructured_from: describe(p.initializer, 1) } }
              else if (ts.isExportAssignment(d)) ex.desc = describe(d.expression, 1)
              else if (ts.isBinaryExpression(d)) ex.desc = describe(d.right, 1)
            }
          }
          exps.push(ex)
        }
      } catch { }
      mod.exports = exps
    }
    if (mod.directives.length || mod.server_fns.length || mod.method_checks.length || (mod.exports && mod.exports.length)) modules[r] = mod
  }
  // functions whose parameter is used as a router (`export default (app) => { app.get(...) }`, fastify plugins):
  // their call sites bind the parameter to the argument instance
  const paramFns = new Set()
  for (const c of calls) {
    const k = c.recv && c.recv.key, inst = k && instances[k]
    if (inst && inst.kind === 'param' && inst.fn) paramFns.add(inst.fn)
  }
  const bindCalls = []
  if (paramFns.size) {
    for (const sf of sourceFiles) {
      if (realFile(sf).endsWith('.vue')) continue
      const r = rel(realFile(sf))
      const v = node => {
        if (ts.isCallExpression(node) && node.arguments.length) {
          const c = unwrap(node.expression)
          let fnId = null
          if (ts.isIdentifier(c) || ts.isPropertyAccessExpression(c)) { const sym = symOf(c); const t = sym && resolveSymbol(sym); fnId = t && t.id }
          else if (ts.isCallExpression(c) && ts.isIdentifier(unwrap(c.expression)) && unwrap(c.expression).text === 'require' && c.arguments[0]) {
            const me = moduleExport(c.arguments[0], c); fnId = me && me.exp && me.exp.fn
          }
          if (fnId && paramFns.has(fnId)) bindCalls.push({ fn: fnId, file: r, line: lineOf(node, sf), args: node.arguments.slice(0, 6).map(a => describe(a, 1)) })
        }
        ts.forEachChild(node, v)
      }
      ts.forEachChild(sf, v)
    }
  }
  // External-system clients (#103): `new Pool({ host })` (pg), `new Redis(url)` (ioredis), `createClient({ url })`
  // (redis), `nodemailer.createTransport({ host, port })`, `mongoose.connect(url)`, `new MongoClient(url)`,
  // `amqplib.connect(url)`, `new Kafka({ brokers })`, `new Sequelize(url)`, `knex({ client, connection })`,
  // `ldap.createClient({ url })`, `new S3Client({ endpoint })`, and `connect({ host })` on an ssh2 / ssh2-sftp-client /
  // basic-ftp client. Addresses are kept as a literal, an env key (`process.env.X`, `|| 'default'`, env wrappers
  // `env.X` / `environment.X`) or a ConfigService key; passwords only as the env key or the literal's location.
  const clients = []
  const CLIENT_MODS = {
    'pg': ['postgres', ['Pool', 'Client']], 'pg-pool': ['postgres', ['default']], 'postgres': ['postgres', ['default']],
    'mysql': ['mysql', ['createPool', 'createConnection', 'createPoolCluster']],
    'mysql2': ['mysql', ['createPool', 'createConnection', 'createPoolCluster']],
    'mysql2/promise': ['mysql', ['createPool', 'createConnection', 'createPoolCluster']],
    'ioredis': ['redis', ['default', 'Redis', 'Cluster']], 'redis': ['redis', ['createClient', 'createCluster']],
    '@redis/client': ['redis', ['createClient']], 'nodemailer': ['smtp', ['createTransport']],
    'mongoose': ['mongodb', ['connect', 'createConnection']], 'mongodb': ['mongodb', ['MongoClient', 'MongoClient.connect']],
    'amqplib': ['amqp', ['connect']], 'amqplib/callback_api': ['amqp', ['connect']], 'amqp-connection-manager': ['amqp', ['connect']],
    'kafkajs': ['kafka', ['Kafka']], 'sequelize': ['sql', ['Sequelize']], 'sequelize-typescript': ['sql', ['Sequelize']],
    'knex': ['sql', ['default', 'knex']], 'ldapjs': ['ldap', ['createClient']], '@aws-sdk/client-s3': ['s3', ['S3Client']],
    '@elastic/elasticsearch': ['elasticsearch', ['Client']], 'memjs': ['memcached', ['Client.create']],
    'ssh2': ['ssh', ['Client#connect']], 'ssh2-sftp-client': ['ssh', ['default#connect']], 'basic-ftp': ['ftp', ['Client#access']],
  }
  const MOD_RE = new RegExp(`['"](${Object.keys(CLIENT_MODS).map(k => k.replace(/[/.@-]/g, '\\$&')).join('|')})['"]`)
  const DIALECTS = { postgres: 'postgres', postgresql: 'postgres', pg: 'postgres', mysql: 'mysql', mysql2: 'mysql', mariadb: 'mysql', mssql: 'mssql', tedious: 'mssql', oracledb: 'oracle', sqlite: null, sqlite3: null, 'better-sqlite3': null }
  // the imported name an expression refers to: {mod, name} ('default' for a default import / a require()d module)
  function importedName(e) {
    e = unwrap(e)
    const path = []
    while (e && ts.isPropertyAccessExpression(e)) { path.unshift(e.name.text); e = unwrap(e.expression) }
    if (!e || !ts.isIdentifier(e)) return null
    let sym
    try { sym = checker.getSymbolAtLocation(e) } catch { return null }
    const d = sym && (sym.declarations || [])[0]
    if (!d) return null
    const mod = importSource(e)
    if (!mod || !CLIENT_MODS[mod]) return null
    let base
    if (ts.isImportSpecifier(d)) base = [(d.propertyName || d.name).text]
    else if (ts.isImportClause(d)) base = ['default']
    else if (ts.isNamespaceImport(d) || ts.isImportEqualsDeclaration(d)) base = []
    else if (ts.isBindingElement(d)) base = [text(d.propertyName || d.name, 60)]
    else if (ts.isVariableDeclaration(d)) base = []              // const x = require('m')
    else return null
    const parts = [...base, ...path]
    const name = parts.length ? parts.join('.') : 'default'
    // `import ioredis from 'ioredis'; new ioredis.Redis()` / `mysql.createPool`: drop a leading 'default.'
    return { mod, name: name.startsWith('default.') ? name.slice(8) : name }
  }
  // a project class extending a client class (`class RedisAdapter extends Redis`, outline): its `new`, `new this()`
  // and `super()` calls construct that client
  const subclassOf = new Map()
  function clientClass(cls, depth = 0) {
    if (!cls || depth > 4) return null
    if (subclassOf.has(cls)) return subclassOf.get(cls)
    subclassOf.set(cls, null)
    let hit = null
    for (const h of cls.heritageClauses || []) {
      if (h.token !== ts.SyntaxKind.ExtendsKeyword || !h.types[0]) continue
      const ex = h.types[0].expression
      hit = importedName(ex)
      if (!hit) { const sym = symOf(ex), d = sym && origDecl(sym); if (d && ts.isClassLike(d) && projectSf(d.getSourceFile())) hit = clientClass(d, depth + 1) }
    }
    subclassOf.set(cls, hit)
    return hit
  }
  function ctorOf(e, node) {
    e = unwrap(e)
    if (!e) return null
    if (e.kind === ts.SyntaxKind.SuperKeyword || e.kind === ts.SyntaxKind.ThisKeyword) {
      let p = node.parent
      while (p && !ts.isClassLike(p)) p = p.parent
      return p ? clientClass(p) : null
    }
    const im = importedName(e)
    if (im) return im
    const sym = symOf(e), d = sym && origDecl(sym)
    return d && ts.isClassLike(d) && projectSf(d.getSourceFile()) ? clientClass(d) : null
  }
  const UPPER = ENV_KEY
  const CONV = /^(to\w*|parse\w*|Number|String|Boolean|parseInt|parseFloat|required|optional|must\w*)$/
  // ['lit', s] | ['env', KEY, default] | ['config', key] | ['obj', ObjectLiteral] | ['arr', ArrayLiteral] | null
  function valOf(e, depth = 0) {
    e = unwrap(e)
    if (!e || depth > 6) return null
    if (ts.isStringLiteralLike(e)) return ['lit', e.text]
    if (ts.isNumericLiteral(e)) return ['lit', e.text]
    if (ts.isObjectLiteralExpression(e)) return ['obj', e]
    if (ts.isArrayLiteralExpression(e)) return ['arr', e]
    if (ts.isAwaitExpression(e)) return valOf(e.expression, depth + 1)
    if (ts.isPropertyAccessExpression(e) && isProcessEnv(e.expression)) return ['env', e.name.text, null]
    if (ts.isElementAccessExpression(e) && isProcessEnv(e.expression) && e.argumentExpression && ts.isStringLiteralLike(e.argumentExpression)) return ['env', e.argumentExpression.text, null]
    if (ts.isPropertyAccessExpression(e) && ENV_KEY.test(e.name.text) && envVia(e.expression)) return ['env', e.name.text, null]
    if (ts.isBinaryExpression(e) && [ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(e.operatorToken.kind)) {
      const l = valOf(e.left, depth + 1)
      if (l && l[0] === 'env') { const r = valOf(e.right, depth + 1); return ['env', l[1], r && r[0] === 'lit' && r[1] ? r[1] : l[2]] }
      if (l) return l
      // `url || env.REDIS_URL`: an unknown first operand falls back to an env key, never to a bare literal default
      const r = valOf(e.right, depth + 1)
      return r && r[0] === 'env' ? r : null
    }
    if (ts.isCallExpression(e)) {
      const c = unwrap(e.expression), m = ts.isPropertyAccessExpression(c) ? c.name.text : ts.isIdentifier(c) ? c.text : ''
      const a0 = e.arguments[0] && unwrap(e.arguments[0])
      if ((m === 'get' || m === 'getOrThrow') && a0 && ts.isStringLiteralLike(a0) && ts.isPropertyAccessExpression(c)) {
        let rt = ''
        try { rt = checker.typeToString(checker.getTypeAtLocation(c.expression)) } catch { }
        if (/ConfigService/.test(rt) || /(^|\.)(config|configService|cfg)$/i.test(text(c.expression))) return UPPER.test(a0.text) ? ['env', a0.text, null] : ['config', a0.text]
      }
      if (CONV.test(m) && a0) return valOf(a0, depth + 1)
      return null
    }
    if (ts.isIdentifier(e) || ts.isPropertyAccessExpression(e)) {
      const sym = symOf(e), d = sym && origDecl(sym)
      if (!d || !projectSf(d.getSourceFile())) return null
      if ((ts.isVariableDeclaration(d) || ts.isPropertyDeclaration(d) || ts.isPropertyAssignment(d)) && d.initializer) return valOf(d.initializer, depth + 1)
      if (ts.isBindingElement(d)) {          // const { DB_URL } = process.env
        let p = d.parent
        while (p && !ts.isVariableDeclaration(p)) p = p.parent
        if (p && p.initializer && isProcessEnv(p.initializer)) return ['env', text(d.propertyName || d.name, 60), null]
      }
    }
    return null
  }
  // the expression of `key` in an object literal (also through spreads / shorthand), or undefined
  function prop(o, names, depth = 0) {
    if (!o || depth > 4) return undefined
    let hit
    for (const p of o.properties) {
      if ((ts.isPropertyAssignment(p) || ts.isShorthandPropertyAssignment(p)) && p.name && names.includes(text(p.name, 60).replace(/['"]/g, '')))
        hit = ts.isPropertyAssignment(p) ? p.initializer : p.name
      else if (ts.isSpreadAssignment(p)) { const v = valOf(p.expression); if (v && v[0] === 'obj') { const h = prop(v[1], names, depth + 1); if (h !== undefined) hit = h } }
    }
    return hit
  }
  const URL_KEYS = ['url', 'uri', 'connectionString', 'connection_string', 'dsn', 'node', 'endpoint']
  const pack = v => v && (v[0] === 'lit' || v[0] === 'env' || v[0] === 'config') ? v : null
  function clientFact(proto, mod, name, call, src, r, sf) {
    const args = (call.arguments || []).map(unwrap)
    const f = { var: `${mod}${name === 'default' ? '' : '.' + name.replace(/^default\./, '')}()`, client: mod, protocol: proto, src, file: r, line: lineOf(call, sf), resource: null }
    let a0 = args[0] ? valOf(args[0]) : null
    // knex({ client: 'pg', connection: url | { host } }), new Sequelize(url | db, user, pw, { host, dialect })
    if (mod === 'knex' && a0 && a0[0] === 'obj') {
      const cl = valOf(prop(a0[1], ['client', 'dialect'])), cn = valOf(prop(a0[1], ['connection']))
      if (cl && cl[0] === 'lit') { if (!(cl[1] in DIALECTS) || DIALECTS[cl[1]] === null) return null; f.protocol = DIALECTS[cl[1]] }
      a0 = cn
    }
    if (mod.startsWith('sequelize')) {
      const opts = [...args].reverse().map(a => valOf(a)).find(v => v && v[0] === 'obj')
      const dl = opts && valOf(prop(opts[1], ['dialect']))
      if (dl && dl[0] === 'lit') { if (!(dl[1] in DIALECTS) || DIALECTS[dl[1]] === null) return null; f.protocol = DIALECTS[dl[1]] }
      if (a0 && a0[0] !== 'obj' && args.length >= 3) { f.resource = a0[0] === 'lit' ? a0[1] : null; a0 = opts || null }
    }
    if (mod === 'ioredis' && a0 && a0[0] === 'lit' && /^\d+$/.test(a0[1])) {      // new Redis(6379, 'cache')
      const h = args[1] ? pack(valOf(args[1])) : null
      if (!h) return null
      f.host = h; f.port = a0
      return f
    }
    if (!a0) return null
    if (a0[0] === 'obj') {
      const o = a0[1]
      let u = prop(o, URL_KEYS)
      if (u === undefined && proto === 'kafka') { const b = valOf(prop(o, ['brokers'])); if (b && b[0] === 'arr' && b[1].elements.length) u = b[1].elements[0] }
      if (u === undefined && proto === 'elasticsearch') { const b = valOf(prop(o, ['nodes'])); if (b && b[0] === 'arr' && b[1].elements.length) u = b[1].elements[0] }
      if (u === undefined && proto === 'redis') { const so = valOf(prop(o, ['socket'])); if (so && so[0] === 'obj') { const h = prop(so[1], ['host']); if (h !== undefined) { f.host = pack(valOf(h)); f.port = pack(valOf(prop(so[1], ['port']))) } } }
      if (u !== undefined) {
        const v = pack(valOf(u))
        if (!v) return null
        if (v[0] === 'lit' && !v[1].includes('://')) { if (proto === 's3') return null; f.host = v; f.port = pack(valOf(prop(o, ['port']))) }
        else f.url = v
      } else if (!f.host) {
        const h = prop(o, ['host', 'hostname', 'server'])
        if (h === undefined) return null
        f.host = pack(valOf(h))
        if (!f.host) return null
        if (f.host[0] === 'lit' && f.host[1].includes('://')) { f.url = f.host; delete f.host }
        else {
          f.port = pack(valOf(prop(o, ['port'])))
          const sec = prop(o, ['secure'])         // nodemailer { secure: true } without a port: implicit TLS on 465
          if (!f.port && proto === 'smtp' && sec && unwrap(sec).kind === ts.SyntaxKind.TrueKeyword) f.port = ['lit', '465']
        }
      }
      const db = valOf(prop(o, ['database', 'db', 'dbName']))
      if (db && db[0] === 'lit' && !f.resource) f.resource = db[1]
      let pw = prop(o, ['password', 'pass', 'passwd'])
      if (pw === undefined) { const au = valOf(prop(o, ['auth'])); if (au && au[0] === 'obj') pw = prop(au[1], ['pass', 'password']) }
      if (pw !== undefined) {
        const pv = valOf(pw)
        f.password = pv && (pv[0] === 'env' || pv[0] === 'config') ? [pv[0], pv[1]] : pv && pv[0] === 'lit' && pv[1] ? ['literal', `${r}:${lineOf(pw, sf)}`] : null
      }
      return f.url || f.host ? f : null
    }
    if (a0[0] === 'arr') return null
    if (proto === 's3' || proto === 'ssh' || proto === 'ftp') return null
    if (a0[0] === 'lit' && !a0[1].includes('://')) return null
    f.url = a0
    return f
  }
  // files to scan: the ones importing a client module, and the ones importing a file that declares a client subclass
  const stems = new Set()
  for (const sf of sourceFiles) {
    if (realFile(sf).endsWith('.vue') || !MOD_RE.test(sf.text)) continue
    for (const st of sf.statements) if (ts.isClassDeclaration(st) && clientClass(st)) stems.add(nodePath.basename(realFile(sf)).replace(/\.[cm]?[jt]sx?$/, ''))
  }
  const SUB_RE = stems.size ? new RegExp(`['"][^'"]*\\b(${[...stems].map(x => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})(\\.[cm]?[jt]sx?)?['"]`) : null
  for (const sf of sourceFiles) {
    if (realFile(sf).endsWith('.vue') || !(MOD_RE.test(sf.text) || (SUB_RE && SUB_RE.test(sf.text)))) continue
    const real = realFile(sf), r = rel(real), fid = fileNode.get(real)
    const v = node => {
      const isNew = ts.isNewExpression(node), isCall = ts.isCallExpression(node)
      if ((isNew || isCall) && node.arguments && node.arguments.length) {
        const c = unwrap(node.expression)
        let im = (isNew || (isCall && c.kind === ts.SyntaxKind.SuperKeyword)) ? ctorOf(c, node) : importedName(c), memberOf = null
        // client.connect({ host }) / client.access({ host }) on `new Client()` from ssh2 / ssh2-sftp-client / basic-ftp
        if (!im && isCall && ts.isPropertyAccessExpression(c)) {
          const sym = symOf(c.expression), d = sym && origDecl(sym)
          const init = d && (ts.isVariableDeclaration(d) || ts.isPropertyDeclaration(d)) && d.initializer && unwrap(d.initializer)
          if (init && ts.isNewExpression(init)) { const ci = importedName(init.expression); if (ci) { im = { mod: ci.mod, name: `${ci.name}#${c.name.text}` }; memberOf = ci } }
        }
        if (im) {
          const [proto, names] = CLIENT_MODS[im.mod]
          if (names.includes(im.name) && (!memberOf || isCall)) {
            const f = clientFact(proto, im.mod, im.name.replace('#', '.'), node, enclosingFnNode(node) || fid, r, sf)
            if (f && f.src) clients.push(f)
          }
        }
      }
      ts.forEachChild(node, v)
    }
    ts.forEachChild(sf, v)
  }
  // MCP servers (#102): new McpServer({ name }) / new Server({ name }), and server.registerTool / tool / registerPrompt /
  // prompt / registerResource / resource(name, ..., handler) in files importing @modelcontextprotocol/sdk
  const mcp = [], mcpServers = []
  const MCP_REG = new Map([['registerTool', 'tool'], ['tool', 'tool'], ['registerPrompt', 'prompt'], ['prompt', 'prompt'],
    ['registerResource', 'resource'], ['resource', 'resource']])
  const strOf = e => { e = unwrap(e); if (!e) return null; try { const v = evalStr(e); return v.vals.length === 1 && v.conf !== 'heuristic' ? render(v.vals[0]) : null } catch { return null } }
  const serverName = nw => {
    const o = nw.arguments && unwrap(nw.arguments[0])
    if (!o || !ts.isObjectLiteralExpression(o)) return null
    const p = o.properties.find(x => ts.isPropertyAssignment(x) && x.name.getText() === 'name')
    return p ? strOf(p.initializer) : null
  }
  const isServerNew = e => { e = unwrap(e); return e && ts.isNewExpression(e) && /^(McpServer|Server)$/.test(text(e.expression, 40).split('.').pop()) }
  const serverOf = e => {
    const sym = symOf(e), d = sym && origDecl(sym)
    if (!d) return {}
    if (ts.isVariableDeclaration(d) && d.initializer && isServerNew(d.initializer)) return { name: serverName(unwrap(d.initializer)) }
    if (ts.isParameter(d)) return { param: true }
    return {}
  }
  for (const sf of sourceFiles) {
    if (realFile(sf).endsWith('.vue') || !sf.text.includes('@modelcontextprotocol/sdk')) continue
    const r = rel(realFile(sf))
    const v = node => {
      if (ts.isNewExpression(node) && isServerNew(node)) mcpServers.push({ file: r, line: lineOf(node, sf), name: serverName(node) })
      if (ts.isCallExpression(node) && node.arguments.length >= 2) {
        const c = unwrap(node.expression)
        if (ts.isPropertyAccessExpression(c) && MCP_REG.has(c.name.text)) {
          const kind = MCP_REG.get(c.name.text), args = node.arguments.map(unwrap)
          let name = strOf(args[0])
          if (kind === 'resource') {          // registerResource(name, 'uri' | new ResourceTemplate('uri{x}', ..), ..)
            const u = args[1]
            name = ts.isNewExpression(u) && u.arguments && u.arguments[0] ? strOf(u.arguments[0]) : strOf(u)
          }
          const h = args[args.length - 1]
          let handler = null
          if (ts.isArrowFunction(h) || ts.isFunctionExpression(h)) handler = declToNode(h)
          else if (ts.isIdentifier(h) || ts.isPropertyAccessExpression(h)) { const sym = symOf(h); const t = sym && resolveSymbol(sym); handler = t && t.id }
          // `{x}` placeholders only from a literal URI template (`new ResourceTemplate('notes://{id}')`, also through
          // consts); a computed name / uri (`registerResource(name, uri, ..)`, `'file://' + id`) folds runtime values
          // into `{..}`: skipped
          // (a placeholder must be written as `{id}` in the file, not only as a `${id}` substitution)
          if (name && [...name.matchAll(/\{([^{}]+)\}/g)].some(m => !new RegExp('(^|[^$])\\{' + m[1].replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\}').test(sf.text))) name = null
          if (name && handler) mcp.push({ kind, method: c.name.text, name, handler, file: r, line: lineOf(node, sf), server: serverOf(c.expression) })
        }
      }
      ts.forEachChild(node, v)
    }
    ts.forEachChild(sf, v)
  }
  return { classes, calls, member_calls: memberCalls, instances, modules, env, config_defs: configDefs, provide_objs: provideObjs, bind_calls: bindCalls, budget_left: budget,
    mcp, mcp_servers: mcpServers, clients }
}
