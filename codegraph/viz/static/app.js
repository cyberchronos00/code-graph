/* code-graph view: Cytoscape.js over the deterministic subgraph returned by /api/graph (or window.__STATIC__). */
(function () {
  'use strict'
  const STATIC = window.__STATIC__ || null
  const $ = (id) => document.getElementById(id)
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
  // node colour = family (#82 item 14): Okabe-Ito hues, darkened to >= 3:1 on the light and the dark canvas; inside a
  // family the shape tells kinds apart (KIND_SHAPE)
  const FAMILY = { code: '#0072b2', ui: '#00866a', entry: '#d55e00', data: '#a06a00', config: '#6b7280', jobs: '#b5638f',
    external: '#2b7fb8', findings: '#8a7f00' }
  const KIND_FAMILY = {
    method: 'code', function: 'code', script: 'code', class: 'code', type: 'code', property: 'code', field: 'code', file: 'code',
    composable: 'ui', store: 'ui', component: 'ui', module: 'ui', page: 'ui', layout: 'ui', app: 'ui',
    route: 'entry', command: 'jobs', schedule: 'jobs', job: 'jobs', listener: 'jobs', observer: 'jobs',
    table: 'data', column: 'data', config: 'config', env: 'config', setting: 'config', constant: 'config', enum_case: 'config',
    http: 'external', connection: 'external', client: 'external', request_key: 'external',
    resolution: 'findings', issue: 'findings', admin: 'jobs'
  }
  const KIND_COLOR = Object.fromEntries(Object.entries(KIND_FAMILY).map(([k, f]) => [k, FAMILY[f]]))
  const KIND_SHAPE = { method: 'ellipse', function: 'ellipse', script: 'ellipse', class: 'round-rectangle', type: 'round-tag', property: 'diamond',
    field: 'diamond', file: 'rectangle', page: 'round-rectangle', layout: 'round-rectangle', app: 'round-rectangle', component: 'ellipse',
    composable: 'triangle', store: 'barrel', module: 'rectangle', table: 'barrel', column: 'rectangle', route: 'round-triangle',
    http: 'round-hexagon', connection: 'round-hexagon', client: 'round-octagon', request_key: 'tag', command: 'round-pentagon',
    schedule: 'round-pentagon', job: 'round-pentagon', listener: 'vee', observer: 'vee', config: 'round-rectangle', env: 'rectangle',
    setting: 'tag', constant: 'rectangle', enum_case: 'tag', resolution: 'star', issue: 'round-diamond', admin: 'round-heptagon' }
  // a function is a small circle with a border, a method a plain circle (same family)
  const KIND_BORDER = { function: 2, script: 2 }

  // themes (#82 item 13): the style table is written in light colours; the dark theme maps each one (DARK)
  const DARK = {
    '#ffffff': '#12161d', '#fff': '#12161d', '#1d2330': '#e6e9ef', '#000': '#ffffff', '#384156': '#c3cad6', '#eef1f6': '#1c222c',
    '#eaf6ec': '#16261d', '#7d889c': '#8792a6', '#4f9a66': '#5fb07a', '#f5f7fa': '#191e27',
    '#4a5368': '#d0d6e0', '#767e90': '#a3acbc', '#848c9e': '#7c8597', '#d64545': '#ff6b6b', '#b26b00': '#e09a2c', '#1d4ed8': '#7aa7ff',
    '#2e9d57': '#3fbf6f', '#1b7a3d': '#3fbf6f', '#c77c00': '#f0a030', '#d6006b': '#ff4f9a', '#b06ab3': '#c98bcc', '#d62828': '#ff5c5c',
    '#ff2e88': '#ff5ca3', '#8e6bbf': '#a88ad6', '#145c2e': '#7ddc9c', '#8a5300': '#f0b060', '#a3004f': '#ff7ab5', '#b01e1e': '#ff8080'
  }
  let themePref = 'auto'; try { themePref = localStorage.getItem('cg.theme') || 'auto' } catch (e) { themePref = 'auto' }
  const darkMq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null
  function themeName () { return themePref === 'auto' ? (darkMq && darkMq.matches ? 'dark' : 'light') : themePref }
  function c (hex) { return themeName() === 'dark' ? (DARK[hex] || hex) : hex }
  function applyTheme () {
    document.documentElement.dataset.theme = themeName()
    const b = document.getElementById('theme'); if (b) b.textContent = 'theme: ' + themePref
    if (cy) { cy.style(makeStyle()); if (data && data.meta.mode !== 'plan') legend() }
  }
  // plan overlay: role of a node in the planned change (codegraph/plans.py check result)
  const PLAN_MARK = { added: '+ ', modified: '~ ', missing: '! ', forbidden: '✕ ' }
  const PLAN_ROLE_LABEL = { added: 'planned (new)', modified: 'planned modification', missing: 'MISSING FROM PLAN', review: 'review (related)',
    covered: 'covered by plan', forbidden: 'forbidden target', context: 'context' }
  const PLAN_PRI = { forbidden: 4, added: 3, gap: 2, touches: 1 }
  const CONF_RANK = { heuristic: 1, resolved: 2, exact: 3 }
  // confidence by dash pattern and width as well as colour (#82 item 7); every colour >= 3:1 on the canvas
  // (tests/test_viz.py reads these tables)
  const CANVAS = '#ffffff'; const MODULE_FILL = '#f5f7fa'   // a module box's fill (#eef1f6 at 0.6) on the canvas
  const CONF_EDGE = { exact: { color: '#4a5368', width: 2, style: 'solid' }, resolved: { color: '#767e90', width: 1.5, style: 'dashed' },
    heuristic: { color: '#848c9e', width: 1.5, style: 'dotted' } }
  const GATED = '#d64545'; const PARTGATED = '#b26b00'
  const hiddenConf = new Set()   // confidences hidden by the legend chips (client-side, no refetch)
  const ENTRY_LABEL = { http_route: 'HTTP route', websocket: 'websocket', artisan_command: 'artisan', management_command: 'manage.py', scheduled: 'schedule', queue_job: 'queue job',
    listener: 'listener', admin_panel: 'admin', observer: 'observer', ui_page: 'UI page', ui_layout: 'UI layout', ui_app: 'UI app',
    message_handler: 'message handler', cli_command: 'CLI command', main: 'main', ffi_export: 'FFI export', public_api: 'public API',
    test: 'test', bench: 'bench', example: 'example', build_script: 'build script' }
  let data = null; let byId = {}; let collapsed = new Set(); let cy = null; let selected = null
  // layered view (#82): open caller clusters (id -> members shown), `flat` = expand all, last build, module label prefix
  const L = window.CGLayered
  let open = new Map(); let flat = false; let lay = null; let modPrefix = ''; let hover = null; let lastCluster = null
  let presetsP = null
  const LEAF_FS = 12; const MIN_PX = 11; const MAX_FIT_ZOOM = 1.6
  const DEFAULT_MODE = { table: 'reaches', column: 'reaches', config: 'reaches', env: 'reaches', connection: 'reaches', setting: 'reaches',
    request_key: 'reaches', resolution: 'reaches', page: 'downstream', route: 'downstream', component: 'downstream', layout: 'downstream',
    app: 'downstream', http: 'downstream' }

  function shortName (n) {
    const k = n.kind; const nm = n.name || n.id
    if (k === 'method') return (n.fqn || nm).replace(/^.*\\/, '')
    if (k === 'column') return (n.id.split(':')[1] || nm).split('.').slice(1).join('.')
    if (k === 'table') return 'TABLE ' + nm
    if (k === 'component' || k === 'module') return nm.replace(/^.*\//, '')
    if (k === 'page') return 'page ' + nm
    if (k === 'function' || k === 'composable' || k === 'store') return (n.id.split('#')[1] || nm)
    if (k === 'resolution') return 'resolution ' + nm.replace(/^.*\\/, '')
    if (k === 'route' || k === 'http') return nm
    if (k === 'client') return nm.replace(/^[^/]+\//, '')
    if (k === 'issue') return 'issue ' + nm
    if (k === 'file') return nm.replace(/^.*\//, '')
    if (k === 'property') return (n.fqn || nm).replace(/^.*\\/, '')
    return nm
  }
  function gated (n) { return (n.gate_status && n.gate_status !== 'live') || n.live === false }
  function layoutKind () {
    const v = $('layout').value
    if (v === 'layered' || v === 'fcose') return v
    return data && ['impact', 'downstream', 'path'].includes(data.meta.mode) ? 'layered' : 'fcose'
  }
  function commonPrefix (labels) {
    // `Packages · X`, `Packages · Y`: drop the shared leading part (the full name stays in the tooltip and panel)
    const toks = labels.map((l) => String(l).split(' · '))
    if (toks.length < 2 || toks.some((t) => t.length < 2)) return ''
    let i = 0
    while (toks.every((t) => t.length > i + 1 && t[i] === toks[0][i])) i++
    return i ? toks[0].slice(0, i).join(' · ') + ' · ' : ''
  }
  function stripModule (l) {
    if (modPrefix && l.startsWith(modPrefix)) return l.slice(modPrefix.length)
    const t = l.split(' · ')   // a generic leading folder (`Packages · `, `Sources · `, `app · `) says nothing
    return t.length > 1 && L.GENERIC.has(t[0].toLowerCase()) ? t.slice(1).join(' · ') : l
  }
  const groupsById = () => { const o = {}; for (const g of data.groups) o[g.id] = g; return o }
  function modLabel (gid) {
    const g = groupsById()[gid]
    return stripModule(g ? g.label : String(gid))
  }
  // long identifiers may wrap after `.`, `/`, `\`, `::` and `_` (a zero-width space is a wrap point for Cytoscape)
  // and, inside a word longer than 16 characters, at camel-case humps (`NotificationsList​DataSource`)
  const breakable = (s) => String(s).replace(/(::|[./\\_])(?=[^\s\u200b:])/g, '$1\u200b')
    .replace(/[^\s\u200b]{17,}/g, (w) => w.replace(/([a-z0-9])(?=[A-Z])/g, '$1\u200b'))
  function leafLabel (n, withModule) {
    const ek = n.entry_kind ? `\n[${ENTRY_LABEL[n.entry_kind] || n.entry_kind}]` : ''
    return breakable((PLAN_MARK[n.plan_role] || '') + shortName(n)) + ek + (withModule ? '\n' + breakable(modLabel(n.group)) : '')
  }
  function leafClasses (n) {
    let cls = 'n'
    if (n.entry_kind) cls += ' entry'
    if (n.is_target) cls += ' target'
    if (gated(n)) cls += ' gatedn'
    if (n.plan_role) cls += ' p-' + n.plan_role
    if (n.plan_guard) cls += ' p-guard'
    if (n.kind === 'issue') cls += n.plan_linked ? ' p-linked' : ' p-unlinked'
    return cls
  }

  function defaultCollapse () {
    collapsed = new Set()
    const total = data.nodes.length
    if (total <= 45) return
    const lim = total > 250 ? 3 : 6
    for (const g of data.groups) {
      if (g.targets || g.plan) continue
      if (data.meta.mode === 'plan') { if (g.count > 4) collapsed.add(g.id); continue }
      if (g.count > lim || (g.id.startsWith('table:') && g.count > 1)) collapsed.add(g.id)  // columns fold into their table
    }
  }

  function elements () { return layoutKind() === 'layered' ? elementsLayered() : elementsClustered() }

  // rows: node + wrapped label lines (greedy fill at ~6.8 px per character, wrap points as in breakable())
  function labelLines (text, maxW, cw) {
    let n = 0
    for (const part of String(text).split('\n')) {
      let line = 0; n++
      for (const w of part.split(/[\s\u200b]+/)) {
        const len = w.length * cw
        if (line && line + cw + len > maxW) { n++; line = len } else line += (line ? cw : 0) + len
      }
    }
    return n
  }
  function rowHeight (u) {
    if (u.type === 'node') return 16 + 3 + 15 * labelLines(leafLabel(u.node, !u.lane), 110, 6.8) + 14
    const w = 30 + Math.min(46, Math.sqrt(u.count) * 7)
    return Math.max(20, w * 0.5) + 4 + 16 * labelLines(clusterLabel(u), 130, 7.4) + 16
  }
  function clusterLabel (u) {
    const word = data.meta.mode === 'impact' ? 'caller' : 'node'
    const n = u.count; const s = n > 1 ? 's' : ''
    let label = u.how === 'module' ? `${modLabel(u.key)} · ${n} ${word}${s}`
      : u.how === 'folder' ? `${u.key} · ${n} ${word}${s}` + (u.groups > 1 ? ` in ${u.groups} modules` : '')
        : `+${n} ${word}${s} in ${u.groups} module${u.groups > 1 ? 's' : ''}`
    if (u.entries) label += ` (${u.entries} entry)`
    if (u.gated) label += ` · ${u.gated} gated`
    if (u.open) label = `▾ ${u.how === 'rest' ? `+${n} ${word}s` : (u.how === 'module' ? modLabel(u.key) : u.key)} · ${u.shown} of ${n} shown`
    return breakable(label)
  }

  function elementsLayered () {
    lay = L.build(data, { open, flat, heightOf: rowHeight, fitWidth: Math.max(0, ($('cy').clientWidth || 0) - 60) })
    const els = []
    for (const u of lay.units) {
      const p = lay.pos.get(u.id)
      if (u.type === 'node') {
        const n = u.node
        els.push({ group: 'nodes', data: { id: n.id, label: leafLabel(n, !u.lane), color: KIND_COLOR[n.kind] || '#6b7280', kind: n.kind, shape: KIND_SHAPE[n.kind] || 'ellipse', bw: KIND_BORDER[n.kind] || 0 },
          position: { x: p.x, y: p.y }, classes: leafClasses(n) + (u.lane ? ' lane' : '') })
        continue
      }
      const top = Object.entries(u.kinds).sort((a, b) => b[1] - a[1])
      const n = u.count; const label = clusterLabel(u)
      const w = 30 + Math.min(46, Math.sqrt(n) * 7)
      els.push({ group: 'nodes', data: { id: u.id, cid: u.id, label, color: KIND_COLOR[top[0][0]] || '#6b7280', w, h: Math.max(20, w * 0.5), kinds: u.kinds },
        position: { x: p.x, y: p.y }, classes: 'cluster' + (u.open ? ' open' : '') + (u.gated === n ? ' allgated' : '') })
    }
    return els.concat(edgeEls((id) => lay.rep.get(id), ' lay'))
  }

  function elementsClustered () {
    lay = null
    const els = []; const rep = {}
    for (const g of data.groups) {
      const gn = data.nodes.filter((n) => n.group === g.id)
      const ng = gn.filter(gated).length; const ne = gn.filter((n) => n.entry_kind).length
      const top = Object.entries(g.kinds).sort((a, b) => b[1] - a[1])
      if (gn.length === 1 && !collapsed.has(g.id)) {   // no box around a single node: the module is its second label line
        const n = gn[0]; rep[n.id] = n.id
        els.push({ group: 'nodes', data: { id: n.id, label: leafLabel(n, true), color: KIND_COLOR[n.kind] || '#6b7280', kind: n.kind, shape: KIND_SHAPE[n.kind] || 'ellipse', bw: KIND_BORDER[n.kind] || 0 }, classes: leafClasses(n) })
        continue
      }
      if (collapsed.has(g.id)) {
        els.push({ group: 'nodes', data: { id: 'G:' + g.id, gid: g.id, label: `${modLabel(g.id)}\n${g.count} node${g.count > 1 ? 's' : ''}` +
          (ne ? ` · ${ne} entry` : '') + (ng ? ` · ${ng} gated` : ''), color: KIND_COLOR[top[0][0]] || '#6b7280', size: 26 + Math.min(40, Math.sqrt(g.count) * 7),
          repo: g.repo, gatedFrac: ng / g.count, kinds: g.kinds }, classes: 'collapsed' + (ng === g.count ? ' allgated' : '') })
        for (const n of gn) rep[n.id] = 'G:' + g.id
      } else {
        els.push({ group: 'nodes', data: { id: 'G:' + g.id, gid: g.id, label: modLabel(g.id) + `  (${g.count})`, repo: g.repo }, classes: 'module ' + (g.side === 'fe' ? 'fe' : 'be') })
        for (const n of gn) {
          rep[n.id] = n.id
          els.push({ group: 'nodes', data: { id: n.id, parent: 'G:' + g.id, label: leafLabel(n, false), color: KIND_COLOR[n.kind] || '#6b7280', kind: n.kind, shape: KIND_SHAPE[n.kind] || 'ellipse', bw: KIND_BORDER[n.kind] || 0 }, classes: leafClasses(n) })
        }
      }
    }
    return els.concat(edgeEls((id) => rep[id], ''))
  }

  function edgeEls (repOf, extra) {
    const els = []
    const agg = {}
    for (const e of data.edges) {
      const s = repOf(e.src); const d = repOf(e.dst)
      if (!s || !d || s === d) continue
      const k = s + '→' + d
      const a = agg[k] || (agg[k] = { s, d, n: 0, best: 0, gated: 0, kinds: {}, raw: [], plan: null })
      a.n++; a.best = Math.max(a.best, CONF_RANK[e.confidence] || 1); if (e.gated) a.gated++
      if (e.plan && (PLAN_PRI[e.plan] || 0) > (PLAN_PRI[a.plan] || 0)) a.plan = e.plan
      a.kinds[e.kind] = (a.kinds[e.kind] || 0) + 1; a.raw.push(e)
    }
    for (const [k, a] of Object.entries(agg)) {
      const conf = ['', 'heuristic', 'resolved', 'exact'][a.best]
      let cls = 'c-' + conf + extra
      if (a.gated === a.n) cls += ' gated'; else if (a.gated) cls += ' partgated'
      if (a.n > 1) cls += ' multi'
      let label = a.n > 1 ? String(a.n) : ''
      if (a.plan) {
        cls += ' pe-' + a.plan
        if (a.plan === 'forbidden') label = '✕ forbidden'
        else if (a.plan === 'added') label = '+ ' + Object.keys(a.kinds)[0]
        else if (a.plan === 'gap') label = Object.keys(a.kinds)[0].replace(/^PLAN_/, '').toLowerCase()
      }
      els.push({ group: 'edges', data: { id: 'E:' + k, source: a.s, target: a.d, n: a.n, label, w: CONF_EDGE[conf].width + Math.min(5, Math.log2(a.n)), conf, agg: a }, classes: cls })
    }
    return els
  }

  const makeStyle = () => [
    // labels below 11 rendered px are not drawn (level of detail, #82); the target, entry points, the hovered and the
    // selected node and their neighbours, clusters and modules get a larger font instead (lod())
    { selector: 'node.n', style: { 'background-color': 'data(color)', shape: 'data(shape)', 'border-width': 'data(bw)', 'border-color': c('#1d2330'), label: 'data(label)', 'font-size': LEAF_FS, 'min-zoomed-font-size': MIN_PX, 'text-wrap': 'wrap',
      'text-max-width': 110, 'text-valign': 'bottom', 'text-margin-y': 3, width: 16, height: 16, color: c('#1d2330'), 'text-background-color': c('#fff'),
      'text-background-opacity': 0.75, 'text-background-padding': 1 } },
    { selector: 'node.entry', style: { shape: 'round-diamond', width: 24, height: 24, 'border-width': 2, 'border-color': c('#1d2330'), 'font-weight': 'bold' } },
    { selector: 'node.target', style: { shape: 'star', width: 30, height: 30, 'border-width': 3, 'border-color': c('#000'), 'font-size': 14, 'font-weight': 'bold' } },
    { selector: 'node.gatedn', style: { 'border-width': 3, 'border-style': 'dashed', 'border-color': c('#d64545'), 'background-opacity': 0.55 } },
    { selector: 'node.module', style: { label: 'data(label)', 'text-valign': 'top', 'text-halign': 'center', 'font-size': 13, 'font-weight': 'bold', color: c('#384156'),
      'background-color': c('#eef1f6'), 'background-opacity': 0.6, 'border-width': 1, 'border-color': c('#7d889c'), shape: 'round-rectangle', padding: 10 } },
    { selector: 'node.module.fe', style: { 'background-color': c('#eaf6ec'), 'border-color': c('#4f9a66') } },
    { selector: 'node.collapsed', style: { shape: 'round-rectangle', 'background-color': 'data(color)', 'background-opacity': 0.85, label: 'data(label)', 'text-wrap': 'wrap',
      'text-max-width': 190, 'font-size': 13, 'font-weight': 'bold', 'text-valign': 'bottom', 'text-margin-y': 4, width: 'data(size)', height: 'data(size)',
      'border-width': 2, 'border-color': c('#384156'), color: c('#1d2330'), 'text-background-color': c('#fff'), 'text-background-opacity': 0.8, 'text-background-padding': 2 } },
    { selector: 'node.collapsed.allgated', style: { 'border-color': c('#d64545'), 'border-style': 'dashed', 'border-width': 3 } },
    { selector: 'node.cluster', style: { shape: 'round-rectangle', 'background-color': 'data(color)', 'background-opacity': 0.35, width: 'data(w)', height: 'data(h)',
      'border-width': 2, 'border-color': c('#384156'), label: 'data(label)', 'text-wrap': 'wrap', 'text-max-width': 130, 'font-size': 13, 'font-weight': 'bold',
      'text-valign': 'bottom', 'text-margin-y': 4, color: c('#1d2330'), 'text-background-color': c('#fff'), 'text-background-opacity': 0.85, 'text-background-padding': 2 } },
    { selector: 'node.cluster.open', style: { 'border-style': 'dashed', 'background-opacity': 0.12 } },
    { selector: 'node.cluster.allgated', style: { 'border-color': c('#d64545'), 'border-style': 'dashed', 'border-width': 3 } },
    { selector: 'edge', style: { 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.8, width: 'data(w)', 'line-color': c('#7d889c'), 'target-arrow-color': c('#7d889c'),
      label: 'data(label)', 'font-size': 12, 'min-zoomed-font-size': MIN_PX, 'font-weight': 'bold', color: c('#384156'), 'text-background-color': c('#fff'),
      'text-background-opacity': 0.9, 'text-background-padding': 1 } },
    { selector: 'edge.lay', style: { 'curve-style': 'taxi', 'taxi-direction': 'rightward', 'taxi-turn': '60%', 'taxi-turn-min-distance': 12 } },
    { selector: 'edge.c-exact', style: { 'line-style': 'solid', 'line-color': c(CONF_EDGE.exact.color), 'target-arrow-color': c(CONF_EDGE.exact.color) } },
    { selector: 'edge.c-resolved', style: { 'line-style': 'dashed', 'line-dash-pattern': [7, 3], 'line-color': c(CONF_EDGE.resolved.color),
      'target-arrow-color': c(CONF_EDGE.resolved.color) } },
    { selector: 'edge.c-heuristic', style: { 'line-style': 'dotted', 'line-dash-pattern': [1.5, 3], 'line-color': c(CONF_EDGE.heuristic.color),
      'target-arrow-color': c(CONF_EDGE.heuristic.color) } },
    { selector: 'edge.gated', style: { 'line-color': c(GATED), 'target-arrow-color': c(GATED), 'line-style': 'dashed', 'line-dash-pattern': [3, 3] } },
    { selector: 'edge.partgated', style: { 'line-color': c(PARTGATED), 'target-arrow-color': c(PARTGATED) } },
    { selector: 'edge.confhide', style: { display: 'none' } },
    // ---- plan overlay
    { selector: 'node.p-added', style: { 'background-color': c('#2e9d57'), 'border-width': 3, 'border-style': 'dashed', 'border-color': c('#1b7a3d'), shape: 'round-rectangle',
      width: 26, height: 18, color: c('#145c2e'), 'font-weight': 'bold' } },
    { selector: 'node.p-modified', style: { 'border-width': 5, 'border-color': c('#c77c00'), 'border-style': 'solid', width: 22, height: 22, 'font-weight': 'bold', color: c('#8a5300') } },
    { selector: 'node.p-guard', style: { shape: 'hexagon', width: 28, height: 28 } },
    { selector: 'node.p-missing', style: { 'underlay-color': c('#ff2e88'), 'underlay-opacity': 0.32, 'underlay-padding': 9, 'underlay-shape': 'ellipse',
      'border-width': 3, 'border-color': c('#d6006b'), color: c('#a3004f'), 'font-weight': 'bold' } },
    { selector: 'node.p-review', style: { 'border-width': 2, 'border-style': 'dotted', 'border-color': c('#b06ab3'), 'background-opacity': 0.7 } },
    { selector: 'node.p-covered', style: { 'border-width': 2, 'border-color': c('#2e9d57') } },
    { selector: 'node.p-forbidden', style: { 'border-width': 4, 'border-color': c('#d62828'), 'border-style': 'double', color: c('#b01e1e'), 'font-weight': 'bold' } },
    { selector: 'node.p-unlinked', style: { 'border-width': 3, 'border-color': c('#d62828') } },
    { selector: 'edge.pe-added', style: { 'line-color': c('#2e9d57'), 'target-arrow-color': c('#2e9d57'), 'line-style': 'dashed', 'line-dash-pattern': [8, 4], width: 3.5,
      color: c('#145c2e') } },
    { selector: 'edge.pe-forbidden', style: { 'line-color': c('#d62828'), 'target-arrow-color': c('#d62828'), 'line-style': 'solid', width: 4, 'mid-target-arrow-shape': 'tee',
      'mid-target-arrow-color': c('#d62828'), 'arrow-scale': 1.3, color: c('#b01e1e'), 'font-size': 12 } },
    { selector: 'edge.pe-gap', style: { 'line-color': c('#ff2e88'), 'target-arrow-color': c('#ff2e88'), 'line-style': 'dotted', width: 2.5, color: c('#a3004f'), 'font-size': 9.5,
      'font-weight': 'normal' } },
    { selector: 'edge.pe-touches', style: { 'line-color': c('#8e6bbf'), 'target-arrow-color': c('#8e6bbf'), 'line-style': 'dashed', 'line-dash-pattern': [2, 4], width: 1.2, opacity: 0.55 } },
    { selector: '.dim', style: { opacity: 0.18 } },
    { selector: 'edge.hl', style: { 'line-color': c('#1d4ed8'), 'target-arrow-color': c('#1d4ed8'), width: 4, opacity: 1, 'z-index': 99 } },
    { selector: 'edge.hl.gated', style: { 'line-color': c('#d64545'), 'target-arrow-color': c('#d64545') } },
    { selector: 'node.hl', style: { opacity: 1, 'border-width': 3, 'border-color': c('#1d4ed8') } },
    { selector: 'node:selected', style: { 'overlay-color': c('#1d4ed8'), 'overlay-opacity': 0.15 } }
  ]

  // performance (#82 item 15): fcose 'default' quality; after an expand / collapse the nodes keep their positions and
  // the layout only refines them (randomize off, fewer iterations), so the picture does not move
  function layoutOpts (incremental) {
    if (layoutKind() === 'layered') return { name: 'preset', fit: false, animate: false, padding: 30 }
    return { name: 'fcose', quality: 'default', animate: false, randomize: !incremental, nodeDimensionsIncludeLabels: true, packComponents: true,
      nodeRepulsion: 9000, idealEdgeLength: 90, edgeElasticity: 0.3, nestingFactor: 0.15, gravity: 0.3, gravityCompound: 1.2,
      gravityRangeCompound: 1.4, numIter: incremental ? 800 : 2500, tile: true, tilingPaddingVertical: 18, tilingPaddingHorizontal: 18, padding: 30 }
  }

  let incremental = false; let anchorId = null; let pinned = null
  function render (keepView) {
    if (!data) return
    const els = elements()
    if (!cy) {
      cy = cytoscape({ container: $('cy'), elements: els, style: makeStyle(), layout: { name: 'preset' }, minZoom: 0.05, maxZoom: 4,
        pixelRatio: Math.min(2, window.devicePixelRatio || 1) })
      cy.on('tap', 'node', (ev) => onTap(ev.target))
      cy.on('cxttap', 'node', (ev) => showMenu(ev.target, ev))
      cy.on('tap', hideMenu)
      cy.on('dbltap', 'node.module', (ev) => { collapsed.add(ev.target.data('gid')); render() })
      cy.on('tap', (ev) => { if (ev.target === cy) { clearHl(); lastCluster = null; if (!STATIC) setHash((h) => h.delete('select')) } })
      let pending = false
      cy.on('zoom', () => { if (!pending) { pending = true; requestAnimationFrame(() => { pending = false; lod() }) } })
      cy.on('mouseover', 'node', (ev) => { hover = ev.target.id(); showTip(ev); lod() })
      cy.on('mousemove', 'node', (ev) => moveTip(ev))
      cy.on('mouseout', 'node', () => { hover = null; $('tip').hidden = true; lod() })
      $('cy').tabIndex = 0
      window.__cgCy = cy  // read by tools/shoot.mjs (label sizes, overlap, item counts)
    } else {
      const prev = keepView ? new Map(cy.nodes().map((n) => [n.id(), { ...n.position() }])) : null
      // opening / folding a cluster can change the layer gap: the cluster the user clicked stays where it was on screen
      const a = keepView && anchorId && cy.getElementById(anchorId)
      pinned = a && a.length ? { id: anchorId, at: { ...a.renderedPosition() } } : null
      cy.elements().remove(); cy.add(els)
      if (prev && layoutKind() !== 'layered') cy.nodes().forEach((n) => { const p = prev.get(n.id()); if (p) n.position(p) })
      incremental = !!prev && layoutKind() !== 'layered'
    }
    const t0 = performance.now()
    const l = cy.layout(layoutOpts(incremental)); incremental = false
    l.one('layoutstop', () => {
      window.__cgLayoutMs = performance.now() - t0
      if (!keepView) initialFit()
      else if (pinned) { const n = cy.getElementById(pinned.id); if (n.length) { const r = n.renderedPosition(); cy.panBy({ x: pinned.at.x - r.x, y: pinned.at.y - r.y }) } }
      pinned = null; lod(); document.body.dataset.ready = '1'
    })
    document.body.dataset.ready = '0'
    l.run()
    applyConf()
    if (data.meta.mode !== 'plan') legend()
  }

  // legend chips: hide / show the edges whose strongest evidence is resolved or heuristic, without a refetch
  function applyConf () {
    if (cy) cy.edges().forEach((e) => { e.toggleClass('confhide', hiddenConf.has(e.data('conf'))) })
    status()
    for (const b of document.querySelectorAll('#legend .chip')) b.setAttribute('aria-pressed', String(!hiddenConf.has(b.dataset.conf)))
  }
  function toggleConf (c) { if (hiddenConf.has(c)) hiddenConf.delete(c); else hiddenConf.add(c); applyConf() }

  function initialFit () {
    // the first view always fits every node (#82: no column cut off at the canvas edge). When that leaves leaf labels
    // under 11 px they hide (the target, entries and the hovered neighbourhood keep theirs) and a "readable zoom"
    // button zooms to the target and the layer next to it instead
    const btn = $('fitall'); btn.hidden = true
    cy.fit(undefined, 30)
    if (cy.zoom() > MAX_FIT_ZOOM) { cy.zoom(MAX_FIT_ZOOM); cy.center() }  // a handful of nodes is not blown up to poster size
    if (cy.zoom() >= (MIN_PX + 0.5) / LEAF_FS || cy.nodes().length <= 1) return
    btn.textContent = 'readable zoom'; btn.title = 'zoom to the target and its neighbours at legible label size (f fits all again)'
    btn.hidden = false
  }

  function focusCore () {
    // the target and the layer next to it at >= 11 px; a target at the edge of the drawing (impact: right,
    // downstream: left) stays at that edge of the canvas
    const need = (MIN_PX + 0.5) / LEAF_FS
    const tg = cy.nodes('.target')
    const core = tg.length ? tg.closedNeighborhood().nodes() : cy.nodes('.entry')
    if (!core.length) return
    cy.fit(core, 40)
    cy.zoom(Math.min(1.0, Math.max(need, cy.zoom()))); cy.center(core)
    const all = cy.nodes().boundingBox(); const bb = core.boundingBox(); const z = cy.zoom(); const W = cy.width(); const pan = cy.pan()
    const tx = tg.length ? tg.boundingBox() : bb
    if (tx.x2 >= all.x2 - 1) cy.pan({ x: W - 40 - bb.x2 * z, y: pan.y })
    else if (tx.x1 <= all.x1 + 1) cy.pan({ x: 40 - bb.x1 * z, y: pan.y })
    lod()
  }

  function lod () {
    // level of detail: leaves below 11 rendered px hide (min-zoomed-font-size); these keep a readable label
    if (!cy) return
    const z = cy.zoom()
    // entry-point labels stay readable while zooming out until they would grow into their neighbours (z < 0.8)
    const keep = cy.nodes(z >= 0.8 ? '.target, .entry, :selected' : '.target, :selected')
    let k = keep
    if (hover) { const h = cy.getElementById(hover); k = k.union(h).union(h.neighborhood().nodes()) }
    const sel = cy.nodes(':selected'); if (sel.length) k = k.union(sel.neighborhood().nodes())
    cy.batch(() => {
      cy.nodes('.n').forEach((n) => {
        if (k.has(n)) { const base = n.hasClass('target') ? 14 : LEAF_FS; n.style('font-size', Math.min(40, Math.max(base, (MIN_PX + 0.5) / z))) } else n.removeStyle('font-size')
      })
      // cluster / module labels grow to stay readable while zooming out, until they would outgrow their 130 px wrap
      // and run into the next column (z < 0.8); below that they hide like leaf labels
      cy.nodes('.cluster, .module, .collapsed').forEach((n) => n.style(z >= 0.8 ? { 'font-size': Math.min(60, Math.max(13, 12.5 / z)), 'min-zoomed-font-size': 0 } : { 'font-size': 13, 'min-zoomed-font-size': MIN_PX }))
    })
  }

  function tipHtml (el) {
    if (el.hasClass('cluster')) {
      const u = lay && lay.units.find((x) => x.id === el.id())
      return `<b>${esc(el.data('label').split('\n')[0].replace(/\u200b/g, ''))}</b><div>${u ? u.count : ''} nodes · click to ${u && u.open ? (u.shown < u.count ? 'show 20 more' : 'fold') : 'open'}</div>`
    }
    if (el.hasClass('module') || el.hasClass('collapsed')) {
      const g = groupsById()[el.data('gid')]; return `<b>${esc(g ? g.label : el.id())}</b><div>${g ? g.count : ''} nodes</div>`
    }
    const n = byId[el.id()] || { id: el.id(), kind: el.data('kind') }
    return `<b>${esc(n.fqn || n.name || n.id)}</b><div>${esc(n.kind)}${n.entry_kind ? ' · entry: ' + esc(ENTRY_LABEL[n.entry_kind] || n.entry_kind) : ''}` +
      `${n.depth != null ? ' · depth ' + n.depth : ''}${n.path_confidence ? ' · path ' + esc(n.path_confidence) : ''}</div>` +
      (n.file ? `<div class="loc">${esc(n.file)}${n.line ? ':' + n.line : ''}</div>` : '') + (n.group ? `<div>${esc((groupsById()[n.group] || {}).label || '')}</div>` : '')
  }
  function showTip (ev) { const t = $('tip'); t.innerHTML = tipHtml(ev.target); t.hidden = false; moveTip(ev) }
  function moveTip (ev) {
    const t = $('tip'); const oe = ev.originalEvent; if (!oe || t.hidden) return
    const x = Math.min(window.innerWidth - t.offsetWidth - 8, oe.clientX + 14); const y = Math.min(window.innerHeight - t.offsetHeight - 8, oe.clientY + 14)
    t.style.left = x + 'px'; t.style.top = y + 'px'
  }

  function status () {
    const m = data.meta; const nodes = data.nodes
    $('cy').setAttribute('aria-label', `${m.mode || 'graph'} ${(m.specs || []).join(', ')}: ${nodes.length} nodes, ${data.edges.length} evidence edges, ` +
      `${nodes.filter((n) => n.entry_kind).length} entry points. Press L for a list view, ? for keyboard shortcuts.`)
    $('truncbanner').hidden = !m.truncated
    if ($('listview') && !$('listview').hidden) renderList()
    const ent = nodes.filter((n) => n.entry_kind).length; const g = nodes.filter(gated).length
    const byk = {}; for (const n of nodes) if (n.entry_kind) byk[n.entry_kind] = (byk[n.entry_kind] || 0) + 1
    if (m.mode === 'plan') {
      const s = m.summary || {}
      $('status').innerHTML = `<b>plan</b> ${esc(m.plan)}${m.verify ? ' <b>[verify]</b>' : ''} · ${esc(m.title || '')}<br>` +
        `<span style="color:#a3004f"><b>${s.missing_from_plan}</b> missing from plan</span> · ${s.review} review · ${s.covered} covered · ` +
        `<span style="color:#b01e1e">${s.forbidden_paths_present} forbidden path(s) present</span> · ${s.open_findings_touching} open findings (${s.unlinked_open_findings} not linked) · ` +
        `refs ${s.references - s.unresolved}/${s.references} resolve` + (s.verify ? ` · verify ${esc(JSON.stringify(s.verify))}` : '')
      return
    }
    $('status').innerHTML = `<b>${esc(m.mode)}</b> ${esc((m.specs || []).join(', '))}` + (m.sinks ? ` · sinks ${esc(m.sinks.join(','))}` : '') +
      ` · ${nodes.length} nodes, ${evidenceCount()}, ` + (lay ? `${lay.units.filter((u) => !u.lane).length} items (${lay.units.filter((u) => u.type === 'cluster').length} clusters, ${open.size} open)`
        : `${data.groups.length} modules (${collapsed.size} folded)`) +
      ` · entry points ${ent}` + (ent ? ' (' + Object.entries(byk).map(([k, v]) => `${ENTRY_LABEL[k] || k} ${v}`).join(', ') + ')' : '') +
      (m.gate ? ` · gate <b>${esc(m.gate)}</b>: ${g} gated` : '') + (m.truncated ? ' · <b>truncated</b>' : '') +
      (data.title ? ` · <i>${esc(data.title)}</i>` : '')
  }

  function evidenceCount () {
    const n = data.edges.length
    if (!hiddenConf.size) return `${n} evidence edges`
    const shown = data.edges.filter((e) => !hiddenConf.has(e.confidence)).length
    return `${shown} of ${n} evidence edges shown (${[...hiddenConf].join(', ')} hidden)`
  }

  function legend () {
    if (data.meta.mode === 'plan') {
      $('legend').innerHTML = '<span><span class="sw" style="background:#2e9d57;border:2px dashed #1b7a3d"></span>+ planned new</span>' +
        '<span><span class="sw" style="border:3px solid #c77c00;background:#fff"></span>~ planned modification (hexagon = guard)</span>' +
        '<span><span class="sw" style="background:#ff2e88;opacity:.6;border-radius:6px"></span>! uncovered: missing from plan</span>' +
        '<span><span class="sw" style="border:2px dotted #b06ab3;background:#fff"></span>review</span>' +
        '<span><span class="sw" style="border:2px solid #2e9d57;background:#fff"></span>covered</span>' +
        '<span><span class="sw" style="border:3px double #d62828;background:#fff"></span>✕ forbidden target</span>' +
        '<span><span class="ln" style="border-top:3px dashed #2e9d57"></span>planned edge</span>' +
        '<span><span class="ln" style="border-top:4px solid #d62828"></span>⊣ forbidden path (still in code)</span>' +
        '<span><span class="ln" style="border-top:3px dotted #ff2e88"></span>check relation (not a code edge)</span>' +
        '<span><span class="ln" style="border-top:2px dashed #8e6bbf"></span>filed issue touches (red border = not linked in plan)</span>' +
        '<span>grey edges = real indexed evidence (file:line)</span>'
      return
    }
    // only the encodings present in this view, kinds with counts (#82 item 8); a kind chip dims everything else
    const used = {}; for (const n of data.nodes) used[n.kind] = (used[n.kind] || 0) + 1
    let h = Object.entries(used).sort((a, b) => b[1] - a[1]).map(([k, cnt0]) =>
      `<button type="button" class="chip kchip" data-kind="${esc(k)}" aria-pressed="${kindHl === k}" title="highlight ${esc(k)} nodes">` +
      `<span class="sw" style="background:${KIND_COLOR[k] || '#6b7280'}"></span>${esc(k)} ${cnt0}</button>`).join('')
    const has = (sel) => cy && cy.elements(sel).length > 0
    if (data.nodes.some((n) => n.entry_kind)) h += '<span>◆ entry point</span>'
    if (data.nodes.some((n) => n.is_target)) h += '<span>★ target</span>'
    if (data.nodes.some(gated)) h += `<span><span class="sw" style="border:2px dashed ${c(GATED)};background:${c('#fff')}"></span>gated node</span>`
    const cnt = {}; for (const e of data.edges) cnt[e.confidence] = (cnt[e.confidence] || 0) + 1
    h += Object.entries(CONF_EDGE).filter(([cf]) => cnt[cf]).map(([cf, v]) => {
      const sample = `<span class="ln" style="border-top:${v.width + 0.5}px ${v.style} ${c(v.color)}"></span>${cf} ${cnt[cf] || 0}`
      return cf === 'exact' ? `<span>${sample}</span>`
        : `<button type="button" class="chip" data-conf="${cf}" aria-pressed="${!hiddenConf.has(cf)}" title="show / hide ${cf} edges">${sample}</button>`
    }).join('')
    if (has('edge.gated')) h += `<span><span class="ln" style="border-top:2px dashed ${c(GATED)}"></span>gated edge</span>`
    if (has('edge.partgated')) h += `<span><span class="ln" style="border-top:2px solid ${c(PARTGATED)}"></span>partly gated</span>`
    const boxes = []
    if (has('node.cluster')) boxes.push('rounded box = caller cluster with its count (click to open, again for 20 more, Esc folds); a node\'s second line is its module')
    if (has('node.module')) boxes.push('framed box = module (double-click to fold)')
    if (has('node.collapsed')) boxes.push('filled box = folded module (click to open)')
    if (has('edge.multi')) boxes.push('numbers on edges = folded evidence edges')
    if (boxes.length) h += '<span>' + boxes.join('; ') + '</span>'
    $('legend').innerHTML = h
    for (const b of document.querySelectorAll('#legend .chip[data-conf]')) b.addEventListener('click', () => toggleConf(b.dataset.conf))
    for (const b of document.querySelectorAll('#legend .kchip')) b.addEventListener('click', () => highlightKind(b.dataset.kind))
  }

  // a kind chip: that kind's nodes (and the clusters / folded modules holding one) stay, the rest dims
  let kindHl = null
  function highlightKind (k) {
    const again = kindHl === k
    clearHl()
    if (!again && cy) {
      kindHl = k
      const keep = cy.nodes().filter((n) => n.data('kind') === k || (n.data('kinds') && n.data('kinds')[k]))
      cy.elements().not(keep).not(keep.ancestors()).addClass('dim')
      keep.addClass('hl')
    }
    for (const b of document.querySelectorAll('#legend .kchip')) b.setAttribute('aria-pressed', String(b.dataset.kind === kindHl))
  }

  function clearHl () {
    kindHl = null
    for (const b of document.querySelectorAll('#legend .kchip')) b.setAttribute('aria-pressed', 'false')
    if (cy) cy.elements().removeClass('dim hl')
  }
  function highlightFrom (id) {
    clearHl()
    // evidence paths go from dependents towards targets/sinks: follow outgoing edges
    const start = cy.getElementById(id); if (!start.length) return
    const seen = start.union(start.successors()).union(start.predecessors())
    cy.elements().not(seen).not(seen.ancestors()).addClass('dim')
    seen.addClass('hl')
  }

  // shareable URLs (#82 item 11): the hash carries the query plus select / expand / layout, written on interaction;
  // a new query is a history entry (Back returns to the previous one), view changes replace the current entry
  function setHash (mut, push) {
    const h = new URLSearchParams(location.hash.slice(1)); mut(h)
    const s = '#' + h.toString(); if (s === location.hash) return
    if (push) history.pushState(null, '', s); else history.replaceState(null, '', s)
  }
  function sameQuery (a, b) {
    return ['mode', 'sinks', 'min_conf'].every((k) => a.get(k) === b.get(k)) && a.getAll('spec').join('\n') === b.getAll('spec').join('\n')
  }
  function copyLink () {
    const url = location.href; const btn = $('copylink')
    const done = (ok) => { btn.textContent = ok ? 'copied' : 'copy failed'; setTimeout(() => { btn.textContent = 'copy link' }, 1500) }
    if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(url).then(() => done(true), () => done(false)); return }
    const t = document.createElement('textarea'); t.value = url; document.body.appendChild(t); t.select()
    let ok = false; try { ok = document.execCommand('copy') } catch (e) { ok = false }
    t.remove(); done(ok)
  }

  function saveExpand () {
    const h = new URLSearchParams(location.hash.slice(1))
    const v = lay ? [...open].map(([id, n]) => (n === 20 ? id : id + '~' + n)) : []
    if (v.length) h.set('expand', v.join('|')); else h.delete('expand')
    history.replaceState(null, '', '#' + h.toString())
  }
  function applyExpand (list) {
    for (const x of list) {
      if (x.startsWith('C:')) { const [id, n] = x.split('~'); open.set(id, Number(n) || 20); continue }
      collapsed.delete(x)   // a module id (clustered layout); in the layered layout: the clusters holding its nodes
      if (layoutKind() === 'layered') {
        const b = L.build(data, { open, flat })
        for (const u of b.units) if (u.type === 'cluster' && u.members.some((m) => (byId[m] || {}).group === x)) open.set(u.id, Math.max(open.get(u.id) || 0, Math.min(u.count, 20)))
      }
    }
  }
  // keyboard (#82 item 10): / search, f fit, + - zoom, 0 reset, arrows move the selection along the edges
  // (← caller, → callee; ↑ ↓ within the column), Enter / Space opens a cluster or shows a node, ? help
  // list view (#82 item 14): the same subgraph as a tree by depth / layer, for screen readers and keyboard use
  function renderList () {
    const lv = $('listview'); if (!data) return
    const lyr = L.layers(data); const by = new Map()
    for (const n of data.nodes) { const k = n.depth != null ? n.depth : lyr.get(n.id); if (!by.has(k)) by.set(k, []); by.get(k).push(n) }
    const word = data.meta.mode === 'impact' ? 'depth' : 'layer'
    lv.innerHTML = `<b>${esc(data.meta.mode)} ${esc((data.meta.specs || []).join(', '))}</b> · ${data.nodes.length} nodes` +
      [...by.keys()].sort((a, b) => a - b).map((k) => `<h3>${word} ${k} (${by.get(k).length})</h3><ul role="group">` +
        by.get(k).sort((a, b) => shortName(a).localeCompare(shortName(b))).map((n) => `<li role="treeitem"><a tabindex="0" data-id="${esc(n.id)}">${esc(shortName(n))}</a>` +
          `<span class="k">${esc(n.kind)}${n.entry_kind ? ' · ' + esc(ENTRY_LABEL[n.entry_kind] || n.entry_kind) : ''}${n.is_target ? ' · target' : ''} · ${esc(n.file || '')}</span></li>`).join('') + '</ul>').join('')
    for (const a of lv.querySelectorAll('a[data-id]')) {
      a.onclick = () => showNode(a.dataset.id)
      a.onkeydown = (e) => { if (e.key === 'Enter') { showNode(a.dataset.id); e.stopPropagation() } }
    }
  }
  function toggleList () { const lv = $('listview'); lv.hidden = !lv.hidden; $('listbtn').setAttribute('aria-pressed', String(!lv.hidden)); if (!lv.hidden) { renderList(); const a = lv.querySelector('a'); if (a) a.focus() } }

  function keyNav (e) {
    const k = e.key
    if ((k === 'Enter' || k === ' ') && /^(BUTTON|A|SUMMARY)$/.test((e.target && e.target.tagName) || '')) return
    if (k === '/') { const ls = $('lsearch'); const t = ls && ls.offsetParent ? ls : $('spec'); t.focus(); t.select(); e.preventDefault(); return }
    if (k === '?') { $('help').hidden = !$('help').hidden; e.preventDefault(); return }
    if (k === 'l' || k === 'L') { if (data) toggleList(); e.preventDefault(); return }
    if (!cy || !data) return
    const centre = { x: cy.width() / 2, y: cy.height() / 2 }
    if (k === 'f') { initialFit(); lod() }
    else if (k === '+' || k === '=') cy.zoom({ level: Math.min(cy.maxZoom(), cy.zoom() * 1.25), renderedPosition: centre })
    else if (k === '-' || k === '_') cy.zoom({ level: Math.max(cy.minZoom(), cy.zoom() / 1.25), renderedPosition: centre })
    else if (k === '0') initialFit()
    else if (k.startsWith('Arrow')) moveSel(k.slice(5).toLowerCase())
    else if (k === 'Enter' || k === ' ') {
      const cur = cy.$('node:selected')[0]; if (!cur) return
      if (cur.hasClass('cluster') || cur.hasClass('collapsed')) {
        const cid = cur.id(); onTap(cur); const el = cy.getElementById(cid); if (el.length) el.select()   // keep the place
      } else { highlightFrom(cur.id()); showNode(cur.id()) }
    } else return
    e.preventDefault()
  }
  function selectEl (el) {
    cy.$(':selected').unselect(); el.select()
    const bb = el.renderedBoundingBox(); const ext = { w: cy.width(), h: cy.height() }
    if (bb.x1 < 0 || bb.y1 < 0 || bb.x2 > ext.w || bb.y2 > ext.h) cy.center(el)
    if (el.hasClass('n')) { highlightFrom(el.id()); showNode(el.id()) } else { clearHl(); el.addClass('hl') }
    $('cy').focus()
  }
  function moveSel (dir) {
    const nodes = cy.nodes().filter((n) => !n.isParent() && n.visible())
    let cur = cy.$('node:selected')[0]
    if (!cur) { cur = nodes.filter('.target')[0] || nodes[0]; if (cur) selectEl(cur); return }
    const p = cur.position(); let cand
    const lane = lay && cur.hasClass('cluster') && cur.hasClass('open') && dir === 'right'
      ? nodes.filter((n) => lay.units.some((u) => u.id === n.id() && u.lane === cur.id())) : null
    if (lane && lane.length) cand = lane.sort((a, b) => a.position('y') - b.position('y'))
    else if (dir === 'left' && lay && cur.hasClass('lane')) {   // from a member back to its cluster
      const u = lay.units.find((x) => x.id === cur.id()); cand = u ? cy.getElementById(u.lane) : cy.collection()
    } else if (dir === 'left' || dir === 'right') {
      cand = (dir === 'left' ? cur.incomers('node') : cur.outgoers('node')).filter((n) => n.visible())
      if (!cand.length) cand = nodes.filter((n) => (dir === 'left' ? n.position('x') < p.x - 20 : n.position('x') > p.x + 20))
      cand = cand.sort((a, b) => Math.abs(a.position('y') - p.y) + Math.abs(a.position('x') - p.x) / 4 - Math.abs(b.position('y') - p.y) - Math.abs(b.position('x') - p.x) / 4)
    } else {
      cand = nodes.filter((n) => Math.abs(n.position('x') - p.x) < 20 && (dir === 'up' ? n.position('y') < p.y - 1 : n.position('y') > p.y + 1))
        .sort((a, b) => Math.abs(a.position('y') - p.y) - Math.abs(b.position('y') - p.y))
    }
    if (cand.length) selectEl(cand[0])
  }

  // export (#82 item 12): the whole drawing at 2x as PNG (on the current canvas colour), or the visible subgraph as JSON
  function exportName (ext) {
    const m = (data && data.meta) || {}
    return ['cg', m.mode || 'view', ...(m.specs || [])].join('-').replace(/[^\w.-]+/g, '_').slice(0, 120) + '.' + ext
  }
  function download (blob, name) {
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name
    document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove() }, 500)
  }
  function exportPng () {
    if (!cy) return
    download(cy.png({ full: true, scale: 2, bg: getComputedStyle($('cy')).backgroundColor || '#fff', output: 'blob', maxWidth: 16384, maxHeight: 16384 }), exportName('png'))
  }
  function exportJson () {
    if (!cy) return
    const vis = new Set(cy.nodes(':visible').map((n) => n.id()))
    const nodes = data.nodes.filter((n) => vis.has(n.id) || (lay && vis.has(lay.rep.get(n.id))))
    const ids = new Set(nodes.map((n) => n.id))
    const out = { meta: data.meta, url: location.href, nodes, edges: data.edges.filter((e) => ids.has(e.src) && ids.has(e.dst) && !hiddenConf.has(e.confidence)) }
    download(new Blob([JSON.stringify(out, null, 1)], { type: 'application/json' }), exportName('json'))
  }

  function toggleCluster (id) {
    const u = lay && lay.units.find((x) => x.id === id); if (!u) return
    if (!u.open) open.set(id, 20); else if (u.shown < u.count) open.set(id, u.shown + 20); else open.delete(id)
    lastCluster = open.has(id) ? id : null
    anchorId = id; saveExpand(); render(true); anchorId = null
    const v = lay.units.find((x) => x.id === id); if (v) showCluster(v)
  }
  function foldCluster (id) { if (open.delete(id)) { anchorId = id; saveExpand(); render(true); anchorId = null } lastCluster = null }

  function showCluster (u) {
    const ns = u.members.map((m) => byId[m]).filter(Boolean)
    $('panelbody').innerHTML = `<h2>${esc(cy.getElementById(u.id).data('label').split('\n')[0].replace(/^▾ /, '').replace(/\u200b/g, ''))}</h2><div>${u.count} nodes · ${Object.entries(u.kinds).map(([k, v]) => `${k} ${v}`).join(', ')}` +
      `${u.open ? ` · showing ${u.shown}` : ' · folded'}</div><h3>members</h3><table>` + ns.map((n) =>
      `<tr><td class="k">${esc(n.kind)}</td><td><a data-id="${esc(n.id)}">${esc(shortName(n))}</a>${n.entry_kind ? ` <span class="badge">${esc(ENTRY_LABEL[n.entry_kind] || n.entry_kind)}</span>` : ''}` +
      `${gated(n) ? ' <span class="badge gated">gated</span>' : ''}<div class="loc">${esc(n.file || '')}${n.line ? ':' + n.line : ''}</div></td></tr>`).join('') + '</table>'
    wireLinks()
  }

  function onTap (el) {
    if (el.hasClass('cluster')) { toggleCluster(el.id()); return }
    if (el.hasClass('collapsed')) { collapsed.delete(el.data('gid')); render(true); showGroup(el.data('gid')); return }
    if (el.hasClass('module')) { showGroup(el.data('gid')); return }
    highlightFrom(el.id()); showNode(el.id())
  }

  function showGroup (gid) {
    const g = data.groups.find((x) => x.id === gid); const ns = data.nodes.filter((n) => n.group === gid)
    $('panelbody').innerHTML = `<h2>${esc(g.label)}</h2><div>${g.count} nodes · ${Object.entries(g.kinds).map(([k, v]) => `${k} ${v}`).join(', ')}</div>` +
      '<h3>members</h3><table>' + ns.sort((a, b) => (a.depth || 0) - (b.depth || 0)).map((n) =>
      `<tr><td class="k">${esc(n.kind)}</td><td><a data-id="${esc(n.id)}">${esc(shortName(n))}</a>${n.entry_kind ? ` <span class="badge">${esc(ENTRY_LABEL[n.entry_kind] || n.entry_kind)}</span>` : ''}` +
      `${gated(n) ? ' <span class="badge gated">gated</span>' : ''}<div class="loc">${esc(n.file || '')}${n.line ? ':' + n.line : ''}</div></td></tr>`).join('') + '</table>'
    wireLinks()
  }

  const DB_HINT = 'DB'
  let wrapCode = false
  function truncMiddle (s, n) { s = String(s || ''); if (s.length <= n) return s; const k = Math.floor((n - 1) / 2); return s.slice(0, k) + '…' + s.slice(s.length - (n - 1 - k)) }
  function nodeActions (id) {
    const tgt = data.nodes.filter((n) => n.is_target && n.id !== id).map((n) => n.id)
    const out = [['impact of this', 'impact', [id]], ['downstream of this', 'downstream', [id]]]
    if (tgt.length) out.push(['path from here to the target', 'path', [id, tgt[0]]])
    return out
  }
  function runAction (mode, spec) {
    $('mode').value = mode; $('spec').value = spec
    $('mode').dispatchEvent(new Event('change')); hideMenu(); go()
  }
  function copyText (txt, btn) {
    const done = (ok) => { if (!btn) return; const t = btn.textContent; btn.textContent = ok ? 'copied' : 'copy failed'; setTimeout(() => { btn.textContent = t }, 1200) }
    if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(txt).then(() => done(true), () => done(false)); return }
    const t = document.createElement('textarea'); t.value = txt; document.body.appendChild(t); t.select()
    let ok = false; try { ok = document.execCommand('copy') } catch (e) { ok = false }
    t.remove(); done(ok)
  }
  function wirePanel () {
    for (const b of $('panelbody').querySelectorAll('[data-copy]')) b.onclick = () => copyText(b.dataset.copy, b)
    for (const b of $('panelbody').querySelectorAll('.act')) b.onclick = () => runAction(b.dataset.mode, b.dataset.spec)
    const w = $('panelbody').querySelector('.wrapbtn')
    if (w) w.onclick = () => { wrapCode = !wrapCode; w.setAttribute('aria-pressed', String(wrapCode)); $('panelbody').querySelector('pre.code').classList.toggle('wrap', wrapCode) }
  }
  // what the view shows: callers per depth, entry points by kind, gated counts (top of the target's panel)
  function viewSummary () {
    const m = data.meta || {}; const ns = data.nodes
    const byD = {}; for (const n of ns) if (n.depth != null && !n.is_target) byD[n.depth] = (byD[n.depth] || 0) + 1
    const ek = {}; for (const n of ns) if (n.entry_kind) ek[n.entry_kind] = (ek[n.entry_kind] || 0) + 1
    const g = ns.filter(gated).length
    let h = `<div class="summary"><h3>view: ${esc(m.mode || '')} · ${ns.length} nodes · ${data.edges.length} evidence edges</h3>`
    if (Object.keys(byD).length) {
      const word = m.mode === 'impact' ? 'callers' : 'nodes'
      h += `<table class="bydepth"><tr><td class="k">depth</td>${Object.keys(byD).sort((a, b) => a - b).map((d) => `<td>${d}</td>`).join('')}</tr>` +
        `<tr><td class="k">${word}</td>${Object.keys(byD).sort((a, b) => a - b).map((d) => `<td>${byD[d]}</td>`).join('')}</tr></table>`
    }
    h += '<div>entry points: ' + (Object.keys(ek).length ? Object.entries(ek).map(([k, v]) => `<span class="badge">${esc(ENTRY_LABEL[k] || k)} × ${v}</span>`).join('') : 'none') + '</div>'
    if (m.gate || g) h += `<div>gated: ${g}${m.gate ? ` (gate ${esc(m.gate)})` : ''}</div>`
    return h + '</div>'
  }

  // right-click menu with the node actions
  function hideMenu () { $('ctxmenu').hidden = true }
  function showMenu (el, ev) {
    if (STATIC || !el.hasClass('n')) return
    const m = $('ctxmenu'); const p = ev.renderedPosition || { x: 0, y: 0 }; const r = $('cy').getBoundingClientRect()
    m.innerHTML = nodeActions(el.id()).map(([lab, mode, specs]) => `<button type="button" role="menuitem" data-mode="${mode}" data-spec="${esc(specs.join(', '))}">${lab}</button>`).join('') +
      `<button type="button" role="menuitem" data-show="1">details</button>`
    m.style.left = (r.left + p.x) + 'px'; m.style.top = (r.top + p.y) + 'px'; m.hidden = false
    for (const b of m.querySelectorAll('button')) b.onclick = () => { if (b.dataset.show) { hideMenu(); highlightFrom(el.id()); showNode(el.id()) } else runAction(b.dataset.mode, b.dataset.spec) }
    m.querySelector('button').focus()
  }

  // resizable / collapsible panel (#82 item 9): drag the left edge (or ← → on it), collapse to a rail; kept in localStorage
  function setPanel (w, collapsed) {
    const max = Math.round(window.innerWidth * 0.5)
    w = Math.max(260, Math.min(max, w))
    document.documentElement.style.setProperty('--panel', (collapsed ? 36 : w) + 'px')
    document.body.classList.toggle('pcollapsed', !!collapsed)
    $('phandle').setAttribute('aria-valuenow', String(collapsed ? 36 : w))
    $('pcollapse').textContent = collapsed ? '⟨' : '⟩'; $('pcollapse').setAttribute('aria-expanded', String(!collapsed))
    try { localStorage.setItem('cg.panel', JSON.stringify({ w, collapsed: !!collapsed })) } catch (e) { /* private mode */ }
    if (cy) cy.resize()
  }
  function panelState () {
    let st = null; try { st = JSON.parse(localStorage.getItem('cg.panel') || 'null') } catch (e) { st = null }
    return st || { w: parseInt(getComputedStyle($('panel')).width, 10) || 360, collapsed: false }
  }
  function initPanel () {
    const st0 = panelState(); if (localStorage.getItem('cg.panel')) setPanel(st0.w, st0.collapsed)
    $('pcollapse').onclick = () => { const st = panelState(); setPanel(st.w, !st.collapsed) }
    const h = $('phandle')
    h.onpointerdown = (e) => {
      h.setPointerCapture(e.pointerId)
      const move = (ev) => setPanel(window.innerWidth - ev.clientX, false)
      h.onpointermove = move; h.onpointerup = () => { h.onpointermove = null; h.onpointerup = null }
    }
    h.onkeydown = (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
      const st = panelState(); setPanel(st.w + (e.key === 'ArrowLeft' ? 30 : -30), false); e.preventDefault(); e.stopPropagation()
    }
  }

  function wireLinks () {
    for (const a of $('panelbody').querySelectorAll('a[data-id]')) a.onclick = () => {
      const id = a.dataset.id; const n = byId[id]
      if (n && !lay && collapsed.has(n.group)) { collapsed.delete(n.group); render(true) }
      if (n && lay && lay.rep.get(id) !== id) {   // folded in a cluster: open it far enough to show the node
        const u = lay.units.find((x) => x.id === lay.rep.get(id))
        if (u) { open.set(u.id, Math.ceil((u.members.indexOf(id) + 1) / 20) * 20); saveExpand(); render(true) }
      }
      if (cy.getElementById(id).length) { cy.$(':selected').unselect(); cy.getElementById(id).select(); highlightFrom(id) }
      showNode(id)
    }
  }

  async function getDetail (id) {
    if (STATIC) return STATIC.details[id] || null
    const r = await fetch('/api/node?id=' + encodeURIComponent(id)); return r.ok ? r.json() : null
  }

  async function showNode (id) {
    selected = id
    if (!STATIC) setHash((h) => h.set('select', id))
    const n = byId[id] || { id }
    const d = await getDetail(id)
    if (selected !== id) return
    const k = (d && d.kind) || n.kind
    const fqn = (d && d.fqn) || n.fqn || id
    const file = (d && d.file) || n.file; const line = (d && d.line) || n.line
    const spec = n.kind === 'method' || n.kind === 'function' ? fqn : id
    const cli = `cg impact '${spec.replace(/'/g, "'\\''")}' --db ${DB_HINT}`
    // sticky header (#82 item 9): name, kind, file:line, copy buttons, editor link, node actions
    let h = `<div class="phead"><h2>${esc(shortName(d || n))}</h2><span class="kind" style="background:${KIND_COLOR[k] || '#6b7280'}">${esc(k)}</span>`
    if (d && d.entry_kind) h += `<span class="badge">entry: ${esc(ENTRY_LABEL[d.entry_kind] || d.entry_kind)}</span>`
    if (n.is_target) h += '<span class="badge">query target</span>'
    if (file) h += `<div class="loc" title="${esc(file)}${line ? ':' + line : ''}">${esc(truncMiddle(file, 52))}${line ? ':' + line : ''}</div>`
    h += '<div class="pacts">' + [['id', id], ['FQN', fqn], ['CLI', cli]].map(([lab, v]) =>
      `<button type="button" class="pbtn" data-copy="${esc(v)}" title="copy ${esc(v)}">copy ${lab}</button>`).join('')
    const abs = d && d.snippet && d.snippet.abs
    if (abs && !STATIC) h += `<a class="pbtn" href="vscode://file/${encodeURI(abs)}:${d.snippet.line}" title="open ${esc(abs)} in VS Code">open in editor</a>`
    if (!STATIC) {
      h += '<br>' + nodeActions(id).map(([lab, mode, specs]) =>
        `<button type="button" class="pbtn act" data-mode="${mode}" data-spec="${esc(specs.join(', '))}">${lab}</button>`).join('')
    }
    h += '</div></div>'
    if (n.is_target) h += viewSummary()
    if (n.plan_role) {
      h += `<div class="planbox p-${esc(n.plan_role)}"><b>${esc(PLAN_ROLE_LABEL[n.plan_role] || n.plan_role)}</b>${n.plan_check ? ` · check <code>${esc(n.plan_check)}</code>` : ''}` +
        (n.plan_why ? `<div>${esc(n.plan_why)}</div>` : '') + (n.plan_evidence ? `<div class="loc">${n.plan_evidence.map(esc).join('<br>')}</div>` : '') +
        (n.plan_items && n.plan_items.length > 1 ? `<div>all checks:<br>${n.plan_items.map(esc).join('<br>')}</div>` : '') +
        (n.plan_attrs ? `<pre>${esc(JSON.stringify(n.plan_attrs, null, 1))}</pre>` : '') + (n.plan_url ? `<div class="loc">${esc(n.plan_url)}</div>` : '') + '</div>'
    }
    if (n.gate_status) h += `<span class="badge ${n.gate_status === 'live' ? 'live' : 'gated'}">${esc(n.gate_status)}</span>`
    if (n.live === false) h += '<span class="badge gated">reached only via gated edges</span>'
    h += `<div class="loc">${esc(fqn)}</div>`
    if (n.depth != null) h += `<div>depth ${n.depth}${n.path_confidence ? ` · path confidence <b>${esc(n.path_confidence)}</b>` : ''}${n.class ? ` · class ${esc(n.class)}` : ''}</div>`
    const ek = (d && d.entry_kinds) || n.entry_kinds
    if (ek && Object.keys(ek).length) {
      h += '<h3>reached from entry kinds</h3>' + Object.entries(ek).map(([k2, v]) => `<span class="badge">${esc(ENTRY_LABEL[k2] || k2)} × ${v}</span>`).join('')
      const lek = (d && d.live_entry_kinds) || n.live_entry_kinds
      if (lek && d && d.gate) h += `<div>live under gate <b>${esc(d.gate)}</b>: ` + (Object.keys(lek).length ? Object.entries(lek).map(([k2, v]) => `<span class="badge live">${esc(ENTRY_LABEL[k2] || k2)} × ${v}</span>`).join('') : '<span class="badge gated">none</span>') + '</div>'
    }
    if (n.gate_evidence) {
      const g = n.gate_evidence
      h += `<h3>gate evidence</h3><pre>${esc(g.kind)} @ ${esc(g.at)}\n${esc(g.from)} -> ${esc(g.to)}\nguard: ${esc(JSON.stringify(g.guard))}</pre>`
    }
    if (d && d.doc) h += `<h3>docblock</h3><pre>${esc(d.doc)}</pre>`
    if (d && d.snippet) {
      const s = d.snippet
      h += `<h3 class="src" title="${esc(s.file)}:${s.line}">Source <span class="loc">${esc(truncMiddle(s.file, 40))}:${s.line}</span>` +
        `<button type="button" class="pbtn wrapbtn" aria-pressed="${wrapCode}">wrap</button></h3><pre class="code${wrapCode ? ' wrap' : ''}" tabindex="0" aria-label="source">` + s.lines.map((l, i) => {
        const no = s.start + i; return `<span class="${no === s.line ? 'hl' : ''}">${String(no).padStart(5)}  ${esc(l)}</span>`
      }).join('\n') + '</pre>'
    }
    const sub = data.edges.filter((e) => e.src === id || e.dst === id)
    if (sub.length) {
      // grouped by direction, then confidence (strongest first), sorted by depth and name; collapsible
      h += `<h3>evidence edges in this view: ${sub.length}</h3>`
      for (const [lab, side] of [['callers / sources', 'in'], ['callees / targets', 'out']]) {
        const es = sub.filter((e) => (side === 'out' ? e.src === id : e.dst === id))
        if (!es.length) continue
        h += `<details class="egrp" open><summary>${side === 'out' ? '→' : '←'} ${lab}: ${es.length}</summary>`
        for (const conf of ['exact', 'resolved', 'heuristic']) {
          const ec = es.filter((e) => (e.confidence || 'heuristic') === conf)
          if (!ec.length) continue
          const other = (e) => (e.src === id ? e.dst : e.src)
          ec.sort((a, b) => ((byId[other(a)] || {}).depth ?? 99) - ((byId[other(b)] || {}).depth ?? 99) ||
            shortName(byId[other(a)] || { id: other(a) }).localeCompare(shortName(byId[other(b)] || { id: other(b) })))
          h += `<details class="egrp" ${ec.length <= 25 ? 'open' : ''}><summary>${conf}: ${ec.length}</summary><table>` + ec.map((e) => {
            const o = other(e); const on = byId[o] || { id: o, kind: o.split(':')[0], name: o }
            return `<tr><td class="k">${esc(e.kind)}${e.plan ? ` <span class="badge">${esc(e.plan)}</span>` : ''}${on.depth != null ? `<br>depth ${on.depth}` : ''}${e.gated ? ` <span class="badge gated">gated: ${esc(e.gated)}</span>` : ''}</td>` +
              `<td><a data-id="${esc(o)}">${esc(shortName(on))}</a><div class="loc">${esc(e.at || '')}</div>${e.guard ? `<div>guard ${esc(JSON.stringify(e.guard))}</div>` : ''}</td></tr>`
          }).join('') + '</table></details>'
        }
        h += '</details>'
      }
    }
    if (d) {
      for (const [lab, list, cnt] of [['outgoing (DB)', d.out_edges, d.out_count], ['incoming (DB)', d.in_edges, d.in_count]]) {
        if (!list || !list.length) continue
        h += `<h3>${lab}: ${cnt}${cnt > list.length ? ` (first ${list.length})` : ''}</h3><table>` + list.map((e) =>
          `<tr><td class="k">${esc(e.kind)}<br>${esc(e.confidence)}${e.gated ? ' <span class="badge gated">gated</span>' : ''}</td><td>${esc(e.other)}<div class="loc">${esc(e.at)}</div>` +
          (Object.keys(e.attrs || {}).length ? `<div>${esc(JSON.stringify(e.attrs)).slice(0, 300)}</div>` : '') + '</td></tr>').join('') + '</table>'
      }
      const a = d.attrs || {}
      const keep = Object.fromEntries(Object.entries(a).filter(([k2]) => !['repo', 'parent'].includes(k2)))
      if (Object.keys(keep).length) h += `<h3>attributes</h3><pre>${esc(JSON.stringify(keep, null, 1)).slice(0, 4000)}</pre>`
    }
    $('panelbody').innerHTML = h
    wireLinks(); wirePanel()
  }

  function setData (g, title) {
    data = g; if (title) data.title = title
    byId = {}; for (const n of data.nodes) byId[n.id] = n
    modPrefix = commonPrefix(data.groups.map((g) => g.label))
    open = new Map(); flat = false; lastCluster = null
    defaultCollapse(); render(); legend()
    if (data.report) $('panelbody').innerHTML = '<h2>plan check report</h2><div class="hint">click a node for its role, evidence and source</div><pre class="report">' + esc(data.report) + '</pre>'
  }

  function readHash () {
    const p = new URLSearchParams(location.hash.slice(1))
    if (p.get('mode')) $('mode').value = p.get('mode')
    if (p.getAll('spec').length) $('spec').value = p.getAll('spec').join(', ')
    if (p.has('sinks')) $('sinks').value = p.get('sinks')
    if (p.get('min_conf')) $('minconf').value = p.get('min_conf')
    if (p.get('layout')) $('layout').value = p.get('layout') === 'breadthfirst' ? 'layered' : p.get('layout')
    return p
  }

  async function go (pushHash) {
    const specs = $('spec').value.split(',').map((s) => s.trim()).filter(Boolean)
    if (!specs.length) return
    const q = new URLSearchParams(); q.set('mode', $('mode').value); for (const s of specs) q.append('spec', s)
    if ($('mode').value === 'downstream') q.set('sinks', $('sinks').value)
    const hp = new URLSearchParams(location.hash.slice(1)); if ($('mode').value === 'plan' && hp.get('verify') === '1') q.set('verify', '1')
    q.set('min_conf', $('minconf').value)
    if (pushHash !== false) {
      const h = new URLSearchParams(q); if ($('layout').value !== 'auto') h.set('layout', $('layout').value)
      const old = new URLSearchParams(location.hash.slice(1))
      if (old.getAll('spec').length && !sameQuery(old, h)) history.pushState(null, '', '#' + h.toString())
      else history.replaceState(null, '', '#' + h.toString())
    }
    hideLanding()
    $('status').textContent = 'loading…'
    const r = await fetch('/api/graph?' + q.toString()); const g = await r.json()
    if (!r.ok) { $('status').textContent = 'error: ' + (g.error || r.status); document.body.dataset.ready = 'error'; return }
    if (!g.nodes.length) { $('status').textContent = 'nothing matched; try search suggestions'; document.body.dataset.ready = 'empty'; return }
    setData(g)
    const p = new URLSearchParams(location.hash.slice(1))
    if (p.get('expand')) { applyExpand(p.get('expand').split('|')); render() }
    if (p.get('focus')) {  // zoom to a node and its direct neighbours (e.g. the uncovered siblings around a table)
      const f = cy.getElementById(p.get('focus'))
      if (f.length) cy.fit(f.closedNeighborhood().union(f.closedNeighborhood().connectedNodes()), Number(p.get('pad') || 40))
    }
    const tgt = data.nodes.find((n) => n.is_target)
    if (!p.get('select') && tgt && data.meta.mode !== 'plan' && !data.report) {   // fill the panel with the target summary
      if (cy.getElementById(tgt.id).length) cy.getElementById(tgt.id).select()
      showNode(tgt.id)
    }
    if (p.get('select')) {
      setTimeout(() => {
        const id = p.get('select')
        if (cy.getElementById(id).length && p.get('hl') !== '0') { cy.getElementById(id).select(); highlightFrom(id) }
        showNode(id)
      }, 50)
    }
  }

  let sugT = null
  function suggest () {
    clearTimeout(sugT)
    sugT = setTimeout(async () => {
      const last = $('spec').value.split(',').pop().trim(); if (last.length < 3 || STATIC) return
      const r = await fetch('/api/search?limit=25&q=' + encodeURIComponent(last)); const rows = await r.json()
      $('sugg').innerHTML = rows.map((x) => `<option value="${esc(x.kind === 'method' ? x.fqn : (x.kind === 'page' ? 'page:' + x.name : x.id))}">${esc(x.kind)} ${esc(x.file || '')}</option>`).join('')
    }, 200)
  }

  // ---------------------------------------------------------------- landing page: overview, search, starter queries
  const getJSON = async (u) => { const r = await fetch(u); return r.ok ? r.json() : null }
  function hideLanding () { $('landing').hidden = true; $('legendbox').hidden = false }
  function runPreset (p) {
    $('mode').value = p.mode; $('spec').value = p.specs.join(', '); if (p.sinks) $('sinks').value = p.sinks.join(',')
    $('mode').dispatchEvent(new Event('change')); go()
  }
  function runHit (x) {
    $('mode').value = DEFAULT_MODE[x.kind] || 'impact'; $('spec').value = x.id
    $('mode').dispatchEvent(new Event('change')); go()
  }
  async function landing () {
    const box = $('landing'); box.hidden = false; $('legendbox').hidden = true; $('fitall').hidden = true
    document.body.dataset.ready = '0'
    $('status').textContent = ''
    const [meta, st, presets] = await Promise.all([getJSON('/api/meta'), getJSON('/api/stats'), presetsP])
    const fmt = (n) => Number(n || 0).toLocaleString('en-US')
    const top = (o, k) => Object.entries(o || {}).slice(0, k).map(([a, b]) => `${esc(ENTRY_LABEL[a] || a)} ${fmt(b)}`).join(' · ')
    const conf = (st && st.confidence) || {}; const tot = (conf.exact || 0) + (conf.resolved || 0) + (conf.heuristic || 0) || 1
    const pc = (k) => Math.round(100 * (conf[k] || 0) / tot) + '%'
    const kinds = Object.keys((st && st.node_kinds) || {})
    const chipKinds = ['method', 'function', 'class', 'page', 'route', 'component', 'table', 'column', 'config', 'env', 'enum_case', 'constant'].filter((k) => kinds.includes(k))
    box.innerHTML = `<h1>${esc((meta && meta.project) || 'code-graph')}</h1><div class="sub">indexed ${esc((meta && meta.indexed_at) || '?')}` +
      `${meta && meta.db ? ' · ' + esc(String(meta.db).replace(/^.*\//, '')) : ''}</div>` +
      (st ? `<div class="stats"><div class="stat"><div class="n">${fmt(st.nodes)}</div><div class="l">nodes</div><div class="kinds">${top(st.node_kinds, 6)}</div></div>` +
        `<div class="stat"><div class="n">${fmt(st.edges)}</div><div class="l">edges</div><div class="kinds">exact ${pc('exact')} · resolved ${pc('resolved')} · heuristic ${pc('heuristic')}</div></div>` +
        `<div class="stat"><div class="n">${fmt(st.entry_points)}</div><div class="l">entry points</div><div class="kinds">${top(st.entry_kinds, 5) || 'none'}</div></div></div>` : '') +
      '<div class="search"><input id="lsearch" type="search" placeholder="Search a symbol, table, route… (↑ ↓ Enter)" autocomplete="off" aria-label="search the graph" aria-controls="lhits"></div>' +
      `<div class="chips" role="group" aria-label="kind">${['all'].concat(chipKinds).map((k) => `<button class="chip" data-kind="${k === 'all' ? '' : k}" aria-pressed="${k === 'all'}">${k}</button>`).join('')}</div>` +
      '<ul class="hits" id="lhits" role="listbox"></ul>' +
      '<h2>Starter queries</h2>' + ((presets || []).length ? '<div class="cards">' + presets.map((p, i) =>
        `<button class="card" data-i="${i}"><div class="t">${esc(p.label)}</div>${p.why ? `<div class="w">${esc(p.why)}</div>` : ''}` +
        `<code>${esc(p.cli || (p.mode + ' ' + p.specs.join(', ')))}</code></button>`).join('') + '</div>' : '<div class="sub">no starter queries for this graph; search a node above</div>')
    for (const c of box.querySelectorAll('.card')) c.onclick = () => runPreset(presets[Number(c.dataset.i)])
    let kind = ''; let hits = []; let cur = -1; let t = null
    const inp = $('lsearch')
    const paint = () => {
      $('lhits').innerHTML = hits.map((x, i) => `<li role="option" data-i="${i}" aria-selected="${i === cur}"><span class="k">${esc(x.kind)}</span>` +
        `<span class="nm">${esc(shortName(x))}</span><span class="f">${esc(x.file || '')}${x.line ? ':' + x.line : ''}</span>` +
        `<span class="q">↵ ${esc(DEFAULT_MODE[x.kind] || 'impact')}</span></li>`).join('')
      for (const li of $('lhits').querySelectorAll('li')) li.onclick = () => runHit(hits[Number(li.dataset.i)])
    }
    const search = () => {
      clearTimeout(t)
      t = setTimeout(async () => {
        const q = inp.value.trim(); if (q.length < 2) { hits = []; cur = -1; paint(); return }
        hits = (await getJSON('/api/search?fuzzy=1&limit=12&q=' + encodeURIComponent(q) + (kind ? '&kind=' + kind : ''))) || []; cur = hits.length ? 0 : -1; paint()
      }, 150)
    }
    inp.oninput = search
    inp.onkeydown = (e) => {
      if (e.key === 'ArrowDown' && hits.length) { cur = (cur + 1) % hits.length; paint(); e.preventDefault() }
      if (e.key === 'ArrowUp' && hits.length) { cur = (cur - 1 + hits.length) % hits.length; paint(); e.preventDefault() }
      if (e.key === 'Enter' && hits[cur]) runHit(hits[cur])
    }
    for (const c of box.querySelectorAll('.chip')) {
      c.onclick = () => {
        kind = c.dataset.kind
        for (const o of box.querySelectorAll('.chip')) o.setAttribute('aria-pressed', String(o === c))
        search(); inp.focus()
      }
    }
    inp.focus()
    document.body.dataset.ready = 'landing'
  }

  async function init () {
    $('collapse').onclick = () => {
      if (lay) { open.clear(); flat = false; saveExpand() } else for (const g of data.groups) if (!g.targets) collapsed.add(g.id)
      render()
    }
    $('expand').onclick = () => { if (layoutKind() === 'layered') { open.clear(); flat = true } else collapsed.clear(); render() }
    $('fit').onclick = () => { if (cy) { initialFit(); lod() } }
    $('fitall').onclick = () => { if (cy) focusCore() }
    $('layout').onchange = () => {
      if (!STATIC) setHash((h) => { if ($('layout').value === 'auto') h.delete('layout'); else h.set('layout', $('layout').value); h.delete('expand') })
      if (data) { open.clear(); flat = false; render() }
    }
    $('copylink').onclick = copyLink
    $('listbtn').onclick = toggleList
    $('theme').onclick = () => {
      themePref = { auto: 'light', light: 'dark', dark: 'auto' }[themePref] || 'auto'
      try { localStorage.setItem('cg.theme', themePref) } catch (e) { /* private mode */ }
      applyTheme()
    }
    if (darkMq && darkMq.addEventListener) darkMq.addEventListener('change', () => { if (themePref === 'auto') applyTheme() })
    applyTheme()
    initPanel()
    $('exportpng').onclick = exportPng; $('exportjson').onclick = exportJson
    $('helpclose').onclick = () => { $('help').hidden = true }
    $('more').onclick = (e) => { const g = $('viewgrp'); const o = g.classList.toggle('open'); $('more').setAttribute('aria-expanded', String(o)); e.stopPropagation() }
    document.addEventListener('click', (e) => { if (!$('viewgrp').contains(e.target)) { $('viewgrp').classList.remove('open'); $('more').setAttribute('aria-expanded', 'false') } })
    document.addEventListener('keydown', (e) => {
      const typing = /^(INPUT|SELECT|TEXTAREA)$/.test((e.target && e.target.tagName) || '')
      if (e.key === 'Escape') {
        $('viewgrp').classList.remove('open')
        if (typing) return
        if (lastCluster) foldCluster(lastCluster); else clearHl()
        if (!$('help').hidden) $('help').hidden = true
        hideMenu()
      } else if (e.key === 'Backspace' && !typing && lastCluster) { foldCluster(lastCluster); e.preventDefault() }
      else if (!typing && !e.ctrlKey && !e.metaKey && !e.altKey) keyNav(e)
    })
    const sinkVis = () => { $('sinks').style.display = $('mode').value === 'downstream' ? '' : 'none' }
    $('mode').addEventListener('change', sinkVis); sinkVis()
    if (STATIC) {
      for (const id of ['mode', 'spec', 'sinks', 'minconf', 'go']) $(id).disabled = true
      $('home').removeAttribute('href')
      $('spec').value = STATIC.title
      if ([...$('mode').options].some((o) => o.value === STATIC.graph.meta.mode)) $('mode').value = STATIC.graph.meta.mode
      setData(STATIC.graph, STATIC.title)
      return
    }
    $('home').onclick = (e) => { e.preventDefault(); history.pushState(null, '', location.pathname + location.search); landing() }
    window.addEventListener('popstate', () => { const h = readHash(); sinkVis(); if (h.getAll('spec').length) go(false); else landing() })
    $('go').onclick = () => go(); $('spec').oninput = suggest
    $('spec').onkeydown = (e) => { if (e.key === 'Enter') go() }
    presetsP = getJSON('/api/presets').then((x) => x || [])
    const h = readHash(); sinkVis()
    if (h.getAll('spec').length) go(false); else landing()
  }
  init()
})()
