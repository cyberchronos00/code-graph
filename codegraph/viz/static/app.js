/* code-graph view: Cytoscape.js over the deterministic subgraph returned by /api/graph (or window.__STATIC__). */
(function () {
  'use strict'
  const STATIC = window.__STATIC__ || null
  const $ = (id) => document.getElementById(id)
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
  const KIND_COLOR = {
    method: '#3d6be0', function: '#62a8ea', script: '#62a8ea', composable: '#1aa39a', store: '#0f8a7e', component: '#7cc36b',
    module: '#9fbf8a', page: '#2e9d57', layout: '#2e9d57', app: '#2e9d57', http: '#e8a33d', route: '#e06c2b',
    command: '#8456d6', schedule: '#a06ad9', job: '#a06ad9', listener: '#a06ad9', observer: '#a06ad9', admin: '#cf4fa3',
    table: '#8b6b4a', column: '#b0906d', connection: '#d64545', config: '#7d8698', env: '#5f6878', setting: '#d4a017',
    request_key: '#1fa5c7', resolution: '#c2185b', class: '#5a6fa8', type: '#9aa5bd', property: '#7a86b8',
    client: '#6c4bd1', issue: '#8e6bbf', file: '#9aa0aa'
  }
  // plan overlay: role of a node in the planned change (codegraph/plans.py check result)
  const PLAN_MARK = { added: '+ ', modified: '~ ', missing: '! ', forbidden: '✕ ' }
  const PLAN_ROLE_LABEL = { added: 'planned (new)', modified: 'planned modification', missing: 'MISSING FROM PLAN', review: 'review (related)',
    covered: 'covered by plan', forbidden: 'forbidden target', context: 'context' }
  const PLAN_PRI = { forbidden: 4, added: 3, gap: 2, touches: 1 }
  const CONF_RANK = { heuristic: 1, resolved: 2, exact: 3 }
  const ENTRY_LABEL = { http_route: 'HTTP route', artisan_command: 'artisan', scheduled: 'schedule', queue_job: 'queue job',
    listener: 'listener', admin_panel: 'admin', observer: 'observer', ui_page: 'UI page', ui_layout: 'UI layout', ui_app: 'UI app' }
  let data = null; let byId = {}; let collapsed = new Set(); let cy = null; let selected = null

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

  function elements () {
    const els = []; const rep = {}
    const groups = {}; for (const g of data.groups) groups[g.id] = g
    for (const g of data.groups) {
      const gn = data.nodes.filter((n) => n.group === g.id)
      const ng = gn.filter(gated).length; const ne = gn.filter((n) => n.entry_kind).length
      const top = Object.entries(g.kinds).sort((a, b) => b[1] - a[1])
      if (collapsed.has(g.id)) {
        els.push({ group: 'nodes', data: { id: 'G:' + g.id, gid: g.id, label: `${g.label}\n${g.count} node${g.count > 1 ? 's' : ''}` +
          (ne ? ` · ${ne} entry` : '') + (ng ? ` · ${ng} gated` : ''), color: KIND_COLOR[top[0][0]] || '#888', size: 26 + Math.min(40, Math.sqrt(g.count) * 7),
          repo: g.repo, gatedFrac: ng / g.count }, classes: 'collapsed' + (ng === g.count ? ' allgated' : '') })
        for (const n of gn) rep[n.id] = 'G:' + g.id
      } else {
        els.push({ group: 'nodes', data: { id: 'G:' + g.id, gid: g.id, label: g.label + `  (${g.count})`, repo: g.repo }, classes: 'module ' + (g.side === 'fe' ? 'fe' : 'be') })
        for (const n of gn) {
          rep[n.id] = n.id
          const ek = n.entry_kind ? `\n[${ENTRY_LABEL[n.entry_kind] || n.entry_kind}]` : ''
          let cls = 'n'
          if (n.entry_kind) cls += ' entry'
          if (n.is_target) cls += ' target'
          if (gated(n)) cls += ' gatedn'
          if (n.plan_role) cls += ' p-' + n.plan_role
          if (n.plan_guard) cls += ' p-guard'
          if (n.kind === 'issue') cls += n.plan_linked ? ' p-linked' : ' p-unlinked'
          const mark = PLAN_MARK[n.plan_role] || ''
          els.push({ group: 'nodes', data: { id: n.id, parent: 'G:' + g.id, label: mark + shortName(n) + ek, color: KIND_COLOR[n.kind] || '#888', kind: n.kind }, classes: cls })
        }
      }
    }
    const agg = {}
    for (const e of data.edges) {
      const s = rep[e.src]; const d = rep[e.dst]
      if (!s || !d || s === d) continue
      const k = s + '→' + d
      const a = agg[k] || (agg[k] = { s, d, n: 0, best: 0, gated: 0, kinds: {}, raw: [], plan: null })
      a.n++; a.best = Math.max(a.best, CONF_RANK[e.confidence] || 1); if (e.gated) a.gated++
      if (e.plan && (PLAN_PRI[e.plan] || 0) > (PLAN_PRI[a.plan] || 0)) a.plan = e.plan
      a.kinds[e.kind] = (a.kinds[e.kind] || 0) + 1; a.raw.push(e)
    }
    for (const [k, a] of Object.entries(agg)) {
      const conf = ['', 'heuristic', 'resolved', 'exact'][a.best]
      let cls = 'c-' + conf
      if (a.gated === a.n) cls += ' gated'; else if (a.gated) cls += ' partgated'
      if (a.n > 1) cls += ' multi'
      let label = a.n > 1 ? String(a.n) : ''
      if (a.plan) {
        cls += ' pe-' + a.plan
        if (a.plan === 'forbidden') label = '✕ forbidden'
        else if (a.plan === 'added') label = '+ ' + Object.keys(a.kinds)[0]
        else if (a.plan === 'gap') label = Object.keys(a.kinds)[0].replace(/^PLAN_/, '').toLowerCase()
      }
      els.push({ group: 'edges', data: { id: 'E:' + k, source: a.s, target: a.d, n: a.n, label, w: Math.min(7, 1 + Math.log2(a.n + 1)), agg: a }, classes: cls })
    }
    return els
  }

  const STYLE = [
    { selector: 'node.n', style: { 'background-color': 'data(color)', label: 'data(label)', 'font-size': 11, 'text-wrap': 'wrap', 'text-max-width': 150,
      'text-valign': 'bottom', 'text-margin-y': 3, width: 16, height: 16, color: '#1d2330', 'text-background-color': '#fff', 'text-background-opacity': 0.75, 'text-background-padding': 1 } },
    { selector: 'node.entry', style: { shape: 'round-diamond', width: 24, height: 24, 'border-width': 2, 'border-color': '#1d2330', 'font-weight': 'bold' } },
    { selector: 'node.target', style: { shape: 'star', width: 30, height: 30, 'border-width': 3, 'border-color': '#000', 'font-size': 13, 'font-weight': 'bold' } },
    { selector: 'node.gatedn', style: { 'border-width': 3, 'border-style': 'dashed', 'border-color': '#d64545', 'background-opacity': 0.55 } },
    { selector: 'node.module', style: { label: 'data(label)', 'text-valign': 'top', 'text-halign': 'center', 'font-size': 12.5, 'font-weight': 'bold', color: '#384156',
      'background-color': '#eef1f6', 'background-opacity': 0.6, 'border-width': 1, 'border-color': '#b9c2d3', shape: 'round-rectangle', padding: 10 } },
    { selector: 'node.module.fe', style: { 'background-color': '#eaf6ec', 'border-color': '#9fd0ad' } },
    { selector: 'node.collapsed', style: { shape: 'round-rectangle', 'background-color': 'data(color)', 'background-opacity': 0.85, label: 'data(label)', 'text-wrap': 'wrap',
      'text-max-width': 190, 'font-size': 12.5, 'font-weight': 'bold', 'text-valign': 'bottom', 'text-margin-y': 4, width: 'data(size)', height: 'data(size)',
      'border-width': 2, 'border-color': '#384156', color: '#1d2330', 'text-background-color': '#fff', 'text-background-opacity': 0.8, 'text-background-padding': 2 } },
    { selector: 'node.collapsed.allgated', style: { 'border-color': '#d64545', 'border-style': 'dashed', 'border-width': 3 } },
    { selector: 'edge', style: { 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.8, width: 'data(w)', 'line-color': '#8a93a6', 'target-arrow-color': '#8a93a6',
      label: 'data(label)', 'font-size': 11, 'font-weight': 'bold', color: '#384156', 'text-background-color': '#fff', 'text-background-opacity': 0.9, 'text-background-padding': 1 } },
    { selector: 'edge.c-exact', style: { 'line-style': 'solid', 'line-color': '#4a5368', 'target-arrow-color': '#4a5368' } },
    { selector: 'edge.c-resolved', style: { 'line-style': 'dashed', 'line-dash-pattern': [7, 3] } },
    { selector: 'edge.c-heuristic', style: { 'line-style': 'dotted', 'line-color': '#b3b9c6', 'target-arrow-color': '#b3b9c6' } },
    { selector: 'edge.gated', style: { 'line-color': '#d64545', 'target-arrow-color': '#d64545', 'line-style': 'dashed', 'line-dash-pattern': [3, 3] } },
    { selector: 'edge.partgated', style: { 'line-color': '#e8a33d', 'target-arrow-color': '#e8a33d' } },
    // ---- plan overlay
    { selector: 'node.p-added', style: { 'background-color': '#2e9d57', 'border-width': 3, 'border-style': 'dashed', 'border-color': '#1b7a3d', shape: 'round-rectangle',
      width: 26, height: 18, color: '#145c2e', 'font-weight': 'bold' } },
    { selector: 'node.p-modified', style: { 'border-width': 5, 'border-color': '#f59f00', 'border-style': 'solid', width: 22, height: 22, 'font-weight': 'bold', color: '#8a5300' } },
    { selector: 'node.p-guard', style: { shape: 'hexagon', width: 28, height: 28 } },
    { selector: 'node.p-missing', style: { 'underlay-color': '#ff2e88', 'underlay-opacity': 0.32, 'underlay-padding': 9, 'underlay-shape': 'ellipse',
      'border-width': 3, 'border-color': '#d6006b', color: '#a3004f', 'font-weight': 'bold' } },
    { selector: 'node.p-review', style: { 'border-width': 2, 'border-style': 'dotted', 'border-color': '#b06ab3', 'background-opacity': 0.7 } },
    { selector: 'node.p-covered', style: { 'border-width': 2, 'border-color': '#2e9d57' } },
    { selector: 'node.p-forbidden', style: { 'border-width': 4, 'border-color': '#d62828', 'border-style': 'double', color: '#b01e1e', 'font-weight': 'bold' } },
    { selector: 'node.p-unlinked', style: { 'border-width': 3, 'border-color': '#d62828' } },
    { selector: 'edge.pe-added', style: { 'line-color': '#2e9d57', 'target-arrow-color': '#2e9d57', 'line-style': 'dashed', 'line-dash-pattern': [8, 4], width: 3.5,
      color: '#145c2e' } },
    { selector: 'edge.pe-forbidden', style: { 'line-color': '#d62828', 'target-arrow-color': '#d62828', 'line-style': 'solid', width: 4, 'mid-target-arrow-shape': 'tee',
      'mid-target-arrow-color': '#d62828', 'arrow-scale': 1.3, color: '#b01e1e', 'font-size': 12 } },
    { selector: 'edge.pe-gap', style: { 'line-color': '#ff2e88', 'target-arrow-color': '#ff2e88', 'line-style': 'dotted', width: 2.5, color: '#a3004f', 'font-size': 9.5,
      'font-weight': 'normal' } },
    { selector: 'edge.pe-touches', style: { 'line-color': '#8e6bbf', 'target-arrow-color': '#8e6bbf', 'line-style': 'dashed', 'line-dash-pattern': [2, 4], width: 1.2, opacity: 0.55 } },
    { selector: '.dim', style: { opacity: 0.18 } },
    { selector: 'edge.hl', style: { 'line-color': '#1d4ed8', 'target-arrow-color': '#1d4ed8', width: 4, opacity: 1, 'z-index': 99 } },
    { selector: 'edge.hl.gated', style: { 'line-color': '#d64545', 'target-arrow-color': '#d64545' } },
    { selector: 'node.hl', style: { opacity: 1, 'border-width': 3, 'border-color': '#1d4ed8' } },
    { selector: 'node:selected', style: { 'overlay-color': '#1d4ed8', 'overlay-opacity': 0.15 } }
  ]

  function layoutOpts () {
    if ($('layout').value === 'breadthfirst') return { name: 'breadthfirst', directed: true, spacingFactor: 1.1, animate: false, padding: 30 }
    return { name: 'fcose', quality: 'proof', animate: false, randomize: true, nodeDimensionsIncludeLabels: true, packComponents: true,
      nodeRepulsion: 9000, idealEdgeLength: 90, edgeElasticity: 0.3, nestingFactor: 0.15, gravity: 0.3, gravityCompound: 1.2,
      gravityRangeCompound: 1.4, numIter: 4000, tile: true, tilingPaddingVertical: 18, tilingPaddingHorizontal: 18, padding: 30 }
  }

  function render (keepView) {
    if (!data) return
    const els = elements()
    if (!cy) {
      cy = cytoscape({ container: $('cy'), elements: els, style: STYLE, wheelSensitivity: 0.25, minZoom: 0.05, maxZoom: 4 })
      cy.on('tap', 'node', (ev) => onTap(ev.target))
      cy.on('dbltap', 'node.module', (ev) => { collapsed.add(ev.target.data('gid')); render() })
      cy.on('tap', (ev) => { if (ev.target === cy) clearHl() })
    } else {
      cy.elements().remove(); cy.add(els)
    }
    const l = cy.layout(layoutOpts())
    l.one('layoutstop', () => { if (!keepView) cy.fit(undefined, 30); document.body.dataset.ready = '1' })
    document.body.dataset.ready = '0'
    l.run()
    status()
  }

  function status () {
    const m = data.meta; const nodes = data.nodes
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
      ` · ${nodes.length} nodes, ${data.edges.length} evidence edges, ${data.groups.length} modules (${collapsed.size} folded)` +
      ` · entry points ${ent}` + (ent ? ' (' + Object.entries(byk).map(([k, v]) => `${ENTRY_LABEL[k] || k} ${v}`).join(', ') + ')' : '') +
      (m.gate ? ` · gate <b>${esc(m.gate)}</b>: ${g} gated` : '') + (m.truncated ? ' · <b>truncated</b>' : '') +
      (data.title ? `<br><i>${esc(data.title)}</i>` : '')
  }

  function legend () {
    if (data.meta.mode === 'plan') {
      $('legend').innerHTML = '<span><span class="sw" style="background:#2e9d57;border:2px dashed #1b7a3d"></span>+ planned new</span>' +
        '<span><span class="sw" style="border:3px solid #f59f00;background:#fff"></span>~ planned modification (hexagon = guard)</span>' +
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
    const used = new Set(data.nodes.map((n) => n.kind))
    let h = Object.entries(KIND_COLOR).filter(([k]) => used.has(k)).map(([k, c]) => `<span><span class="sw" style="background:${c}"></span>${k}</span>`).join('')
    h += '<span>◆ entry point</span><span>★ target</span><span><span class="sw" style="border:2px dashed #d64545;background:#fff"></span>gated node</span>'
    h += '<span><span class="ln" style="border-top:2px solid #4a5368"></span>exact</span><span><span class="ln" style="border-top:2px dashed #8a93a6"></span>resolved</span>' +
      '<span><span class="ln" style="border-top:2px dotted #b3b9c6"></span>heuristic</span><span><span class="ln" style="border-top:2px dashed #d64545"></span>gated edge</span>' +
      '<span>boxes = modules (click folded box to open, double-click open box to fold); numbers on edges = folded evidence edges</span>'
    $('legend').innerHTML = h
  }

  function clearHl () { if (cy) cy.elements().removeClass('dim hl') }
  function highlightFrom (id) {
    clearHl()
    // evidence paths go from dependents towards targets/sinks: follow outgoing edges
    const start = cy.getElementById(id); if (!start.length) return
    const seen = start.union(start.successors()).union(start.predecessors())
    cy.elements().not(seen).not(seen.ancestors()).addClass('dim')
    seen.addClass('hl')
  }

  function onTap (el) {
    if (el.hasClass('collapsed')) { collapsed.delete(el.data('gid')); render(true); showGroup(el.data('gid')); return }
    if (el.hasClass('module')) { showGroup(el.data('gid')); return }
    highlightFrom(el.id()); showNode(el.id())
  }

  function showGroup (gid) {
    const g = data.groups.find((x) => x.id === gid); const ns = data.nodes.filter((n) => n.group === gid)
    $('panel').innerHTML = `<h2>${esc(g.label)}</h2><div>${g.count} nodes · ${Object.entries(g.kinds).map(([k, v]) => `${k} ${v}`).join(', ')}</div>` +
      '<h3>members</h3><table>' + ns.sort((a, b) => (a.depth || 0) - (b.depth || 0)).map((n) =>
      `<tr><td class="k">${esc(n.kind)}</td><td><a data-id="${esc(n.id)}">${esc(shortName(n))}</a>${n.entry_kind ? ` <span class="badge">${esc(ENTRY_LABEL[n.entry_kind] || n.entry_kind)}</span>` : ''}` +
      `${gated(n) ? ' <span class="badge gated">gated</span>' : ''}<div class="loc">${esc(n.file || '')}${n.line ? ':' + n.line : ''}</div></td></tr>`).join('') + '</table>'
    wireLinks()
  }

  function wireLinks () {
    for (const a of $('panel').querySelectorAll('a[data-id]')) a.onclick = () => {
      const id = a.dataset.id; const n = byId[id]
      if (n && collapsed.has(n.group)) { collapsed.delete(n.group); render(true) }
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
    const n = byId[id] || { id }
    const d = await getDetail(id)
    if (selected !== id) return
    const k = (d && d.kind) || n.kind
    let h = `<h2>${esc(shortName(d || n))}</h2><span class="kind" style="background:${KIND_COLOR[k] || '#888'}">${esc(k)}</span>`
    if (d && d.entry_kind) h += `<span class="badge">entry: ${esc(ENTRY_LABEL[d.entry_kind] || d.entry_kind)}</span>`
    if (n.is_target) h += '<span class="badge">query target</span>'
    if (n.plan_role) {
      h += `<div class="planbox p-${esc(n.plan_role)}"><b>${esc(PLAN_ROLE_LABEL[n.plan_role] || n.plan_role)}</b>${n.plan_check ? ` · check <code>${esc(n.plan_check)}</code>` : ''}` +
        (n.plan_why ? `<div>${esc(n.plan_why)}</div>` : '') + (n.plan_evidence ? `<div class="loc">${n.plan_evidence.map(esc).join('<br>')}</div>` : '') +
        (n.plan_items && n.plan_items.length > 1 ? `<div>all checks:<br>${n.plan_items.map(esc).join('<br>')}</div>` : '') +
        (n.plan_attrs ? `<pre>${esc(JSON.stringify(n.plan_attrs, null, 1))}</pre>` : '') + (n.plan_url ? `<div class="loc">${esc(n.plan_url)}</div>` : '') + '</div>'
    }
    if (n.gate_status) h += `<span class="badge ${n.gate_status === 'live' ? 'live' : 'gated'}">${esc(n.gate_status)}</span>`
    if (n.live === false) h += '<span class="badge gated">reached only via gated edges</span>'
    h += `<div class="loc">${esc((d && d.fqn) || n.fqn || id)}</div>`
    if (d && d.file) h += `<div class="loc">${esc(d.file)}${d.line ? ':' + d.line : ''}</div>`
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
      h += `<h3>source ${esc(s.file)}:${s.line}</h3><pre class="code">` + s.lines.map((l, i) => {
        const no = s.start + i; return `<span class="${no === s.line ? 'hl' : ''}">${String(no).padStart(5)}  ${esc(l)}</span>`
      }).join('\n') + '</pre>'
    }
    const sub = data.edges.filter((e) => e.src === id || e.dst === id)
    if (sub.length) {
      h += '<h3>evidence edges in this view</h3><table>' + sub.map((e) => {
        const other = e.src === id ? e.dst : e.src; const on = byId[other] || { id: other, kind: other.split(':')[0], name: other }
        return `<tr><td class="k">${e.src === id ? '→' : '←'} ${esc(e.kind)}${e.plan ? ` <span class="badge">${esc(e.plan)}</span>` : ''}<br>${esc(e.confidence)}${e.gated ? ` <span class="badge gated">gated: ${esc(e.gated)}</span>` : ''}</td>` +
          `<td><a data-id="${esc(other)}">${esc(shortName(on))}</a><div class="loc">${esc(e.at || '')}</div>${e.guard ? `<div>guard ${esc(JSON.stringify(e.guard))}</div>` : ''}</td></tr>`
      }).join('') + '</table>'
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
    $('panel').innerHTML = h
    wireLinks()
  }

  function setData (g, title) {
    data = g; if (title) data.title = title
    byId = {}; for (const n of data.nodes) byId[n.id] = n
    defaultCollapse(); legend(); render()
    if (data.report) $('panel').innerHTML = '<h2>plan check report</h2><div class="hint">click a node for its role, evidence and source</div><pre class="report">' + esc(data.report) + '</pre>'
  }

  function readHash () {
    const p = new URLSearchParams(location.hash.slice(1))
    if (p.get('mode')) $('mode').value = p.get('mode')
    if (p.getAll('spec').length) $('spec').value = p.getAll('spec').join(', ')
    if (p.has('sinks')) $('sinks').value = p.get('sinks')
    if (p.get('min_conf')) $('minconf').value = p.get('min_conf')
    if (p.get('layout')) $('layout').value = p.get('layout')
    return p
  }

  async function go (pushHash) {
    const specs = $('spec').value.split(',').map((s) => s.trim()).filter(Boolean)
    if (!specs.length) return
    const q = new URLSearchParams(); q.set('mode', $('mode').value); for (const s of specs) q.append('spec', s)
    if ($('mode').value === 'downstream') q.set('sinks', $('sinks').value)
    const hp = new URLSearchParams(location.hash.slice(1)); if ($('mode').value === 'plan' && hp.get('verify') === '1') q.set('verify', '1')
    q.set('min_conf', $('minconf').value)
    if (pushHash !== false) { const h = new URLSearchParams(q); if ($('layout').value !== 'fcose') h.set('layout', $('layout').value); history.replaceState(null, '', '#' + h.toString()) }
    $('status').textContent = 'loading…'
    const r = await fetch('/api/graph?' + q.toString()); const g = await r.json()
    if (!r.ok) { $('status').textContent = 'error: ' + (g.error || r.status); document.body.dataset.ready = 'error'; return }
    if (!g.nodes.length) { $('status').textContent = 'nothing matched; try search suggestions'; document.body.dataset.ready = 'empty'; return }
    setData(g)
    const p = new URLSearchParams(location.hash.slice(1))
    if (p.get('expand')) { for (const x of p.get('expand').split('|')) collapsed.delete(x); render() }
    if (p.get('focus')) {  // zoom to a node and its direct neighbours (e.g. the uncovered siblings around a table)
      const f = cy.getElementById(p.get('focus'))
      if (f.length) cy.fit(f.closedNeighborhood().union(f.closedNeighborhood().connectedNodes()), Number(p.get('pad') || 40))
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

  async function init () {
    $('collapse').onclick = () => { for (const g of data.groups) if (!g.targets) collapsed.add(g.id); render() }
    $('expand').onclick = () => { collapsed.clear(); render() }
    $('fit').onclick = () => cy && cy.fit(undefined, 30)
    $('layout').onchange = () => render()
    const sinkVis = () => { $('sinks').style.display = $('mode').value === 'downstream' ? '' : 'none' }
    $('mode').addEventListener('change', sinkVis); sinkVis()
    if (STATIC) {
      for (const id of ['preset', 'mode', 'spec', 'sinks', 'minconf', 'go']) $(id).disabled = true
      $('spec').value = STATIC.title
      setData(STATIC.graph, STATIC.title)
      return
    }
    $('go').onclick = () => go(); $('spec').oninput = suggest
    $('spec').onkeydown = (e) => { if (e.key === 'Enter') go() }
    const presets = await (await fetch('/api/presets')).json()
    $('preset').innerHTML += presets.map((p) => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join('')
    $('preset').onchange = () => {
      const p = presets.find((x) => x.id === $('preset').value); if (!p) return
      $('mode').value = p.mode; $('spec').value = p.specs.join(', '); if (p.sinks) $('sinks').value = p.sinks.join(',')
      go()
    }
    const h = readHash(); sinkVis()
    if (h.getAll('spec').length) go(false)
  }
  init()
})()
