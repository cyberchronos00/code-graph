/* Layered left-to-right layout and per-layer caller clusters for the code-graph view (#82).
   Pure functions over the /api/graph payload (no DOM, no Cytoscape): loaded by the page as window.CGLayered and by
   the unit tests under node (module.exports).

   layers(data)            node id -> layer (0 = leftmost). impact: entry side left, target right (from `depth`);
                           downstream / path: longest path from the sources over the evidence edges.
   build(data, opts)       visible units (nodes and clusters), node -> unit map, positions.
     opts.open             Map cluster id -> members shown (an open cluster keeps its slot; members go into the lane
                           to its right, so nothing outside it moves)
     opts.flat             no clusters (expand all)
     opts.threshold        a layer with more nodes than this is folded into clusters (default 12)
     opts.budget           at most this many units in a folded layer (default 7)
     opts.heightOf         unit -> row height (node + its wrapped label); rows are at least ROW / ROW_CLUSTER */
(function (root) {
  'use strict'
  const GENERIC = new Set(['packages', 'package', 'sources', 'source', 'src', 'lib', 'libs', 'app', 'apps', 'main',
    'java', 'kotlin', 'swift', 'internal', 'pkg', 'modules', 'code'])
  // geometry (model px): labels are at most 110 (leaves) / 130 (clusters) wide, so a member lane at LANE_DX fits
  // between two layers without touching their labels
  const GAP_X = 260
  const MIN_GAP_X = 160  // narrowest layer gap (leaf labels wrap at 110 px), used when nothing is clustered
  const LANE_DX = 135
  const ROW = 64         // leaf row pitch (node + up to three label lines)
  const ROW_CLUSTER = 90  // cluster box + up to three label lines
  const LANE_ROW = 66

  function folderKey (n) {
    const segs = String(n.file || '').split('/').slice(0, -1)
    for (const s of segs) if (s && !GENERIC.has(s.toLowerCase())) return s
    return segs.length ? segs[segs.length - 1] : (n.module || n.kind || '?')
  }

  function layers (data) {
    const nodes = data.nodes; const out = new Map()
    if (data.meta && data.meta.mode === 'impact' && nodes.every((n) => typeof n.depth === 'number')) {
      const maxD = Math.max(0, ...nodes.map((n) => n.depth))
      for (const n of nodes) out.set(n.id, maxD - n.depth)
      return out
    }
    // longest path from the sources (Kahn); a node on a cycle takes one past its deepest placed predecessor
    const ids = new Set(nodes.map((n) => n.id)); const preds = new Map(); const succ = new Map(); const indeg = new Map()
    for (const id of ids) { preds.set(id, []); succ.set(id, []); indeg.set(id, 0) }
    const seen = new Set()
    for (const e of data.edges) {
      if (!ids.has(e.src) || !ids.has(e.dst) || e.src === e.dst) continue
      const k = e.src + '\u0000' + e.dst; if (seen.has(k)) continue; seen.add(k)
      succ.get(e.src).push(e.dst); preds.get(e.dst).push(e.src); indeg.set(e.dst, indeg.get(e.dst) + 1)
    }
    const order = [...ids].sort()
    const queue = order.filter((id) => indeg.get(id) === 0)
    const deg = new Map(indeg)
    for (const id of queue) out.set(id, 0)
    for (let i = 0; i < queue.length; i++) {
      const id = queue[i]
      for (const d of succ.get(id)) {
        out.set(d, Math.max(out.get(d) || 0, out.get(id) + 1))
        deg.set(d, deg.get(d) - 1)
        if (deg.get(d) === 0) queue.push(d)
      }
    }
    for (const id of order) {
      if (out.has(id) && deg.get(id) <= 0) continue
      const placed = preds.get(id).filter((p) => out.has(p)).map((p) => out.get(p))
      out.set(id, placed.length ? Math.max(...placed) + 1 : (out.get(id) || 0))
    }
    return out
  }

  function clusterLayer (layer, ns, opts) {
    // module buckets (one-node modules pool by top-level package / folder); when that is still more than the budget,
    // the whole layer buckets by package / folder; the smallest buckets then pool into "+N"
    const budget = opts.budget || 7
    const bucketBy = (list, keyOf, how) => {
      const m = new Map()
      for (const n of list) { const k = keyOf(n); if (!m.has(k)) m.set(k, []); m.get(k).push(n) }
      return [...m].map(([k, members]) => ({ key: how[0] + ':' + k, how, label: k, members }))
    }
    let buckets = bucketBy(ns, (n) => n.group || '?', 'module')
    let singles = []
    const pooled = bucketBy(buckets.filter((b) => b.members.length === 1).map((b) => b.members[0]), folderKey, 'folder')
    buckets = buckets.filter((b) => b.members.length > 1).concat(pooled)
    if (buckets.length > budget) buckets = bucketBy(ns, folderKey, 'folder')
    singles = buckets.filter((b) => b.members.length === 1).map((b) => b.members[0])
    buckets = buckets.filter((b) => b.members.length > 1)
    const size = (b) => b.members.length
    buckets.sort((a, b) => size(b) - size(a) || (a.key < b.key ? -1 : 1))
    singles.sort((a, b) => (a.id < b.id ? -1 : 1))
    let rest = []
    while (buckets.length + singles.length + (rest.length ? 1 : 0) > budget && (singles.length || buckets.length > 1)) {
      if (singles.length) rest.push(singles.pop())
      else rest = rest.concat(buckets.pop().members)
    }
    if (rest.length === 1) { singles.push(rest[0]); rest = [] }
    if (rest.length) buckets.push({ key: 'rest', how: 'rest', label: '', members: rest })
    return { buckets: buckets.map((b) => ({ ...b, id: 'C:' + layer + ':' + b.key })), singles }
  }

  function build (data, opts) {
    opts = opts || {}
    const open = opts.open || new Map()
    const threshold = opts.threshold || 12
    const lay = layers(data)
    const byLayer = new Map()
    for (const n of [...data.nodes].sort((a, b) => (a.id < b.id ? -1 : 1))) {
      const l = lay.get(n.id) || 0
      if (!byLayer.has(l)) byLayer.set(l, []); byLayer.get(l).push(n)
    }
    const units = []; const rep = new Map()
    for (const l of [...byLayer.keys()].sort((a, b) => a - b)) {
      const ns = byLayer.get(l)
      const folded = !opts.flat && ns.length > threshold && !ns.some((n) => n.is_target && ns.length === 1)
      const { buckets, singles } = folded ? clusterLayer(l, ns.filter((n) => !n.is_target), opts) : { buckets: [], singles: ns }
      const tgt = folded ? ns.filter((n) => n.is_target) : []
      for (const n of tgt.concat(singles)) { units.push({ id: n.id, type: 'node', layer: l, node: n }); rep.set(n.id, n.id) }
      for (const b of buckets) {
        const members = b.members.slice().sort((a, c) => (a.id < c.id ? -1 : 1))
        const shown = open.has(b.id) ? Math.min(members.length, open.get(b.id)) : 0
        const u = { id: b.id, type: 'cluster', layer: l, how: b.how, key: b.label, members: members.map((m) => m.id),
          count: members.length, entries: members.filter((m) => m.entry_kind).length, gated: members.filter(gatedNode).length,
          kinds: countBy(members, (m) => m.kind), groups: new Set(members.map((m) => m.group)).size, open: shown > 0, shown }
        units.push(u)
        members.forEach((m, i) => {
          if (i < shown) { units.push({ id: m.id, type: 'node', layer: l, node: m, lane: b.id }); rep.set(m.id, m.id) } else rep.set(m.id, b.id)
        })
      }
    }
    // without clusters (no lanes to make room for) a drawing that is too wide narrows its layer gaps to fit opts.fitWidth
    let gap = GAP_X
    const nl = new Set(units.map((u) => u.layer)).size
    if (opts.fitWidth && nl > 1 && !units.some((u) => u.type === 'cluster')) {
      gap = Math.max(MIN_GAP_X, Math.min(GAP_X, Math.floor((opts.fitWidth - 140) / (nl - 1))))
    }
    return { units, rep, pos: positions(units, data.edges, rep, opts.heightOf, gap), layers: lay, gap }
  }

  function gatedNode (n) { return (n.gate_status && n.gate_status !== 'live') || n.live === false }
  function countBy (xs, f) { const o = {}; for (const x of xs) { const k = f(x); o[k] = (o[k] || 0) + 1 } return o }

  function positions (units, edges, rep, heightOf, gap) {
    gap = gap || GAP_X
    const hOf = (u) => Math.max(heightOf ? heightOf(u) : 0, u.lane ? LANE_ROW : (u.type === 'cluster' ? ROW_CLUSTER : ROW))
    // column units (not lane members), ordered by barycenter sweeps over the unit edges
    const col = units.filter((u) => !u.lane)
    const host = new Map(units.map((u) => [u.id, u.lane || u.id]))
    const layerOf = new Map(units.map((u) => [u.id, u.layer]))
    const nb = new Map(col.map((u) => [u.id, { left: [], right: [] }]))
    const seen = new Set()
    for (const e of edges) {
      let s = rep.get(e.src); let d = rep.get(e.dst)
      if (!s || !d || s === d) continue
      s = host.get(s); d = host.get(d)
      if (s === d || !nb.has(s) || !nb.has(d)) continue
      const k = s + '\u0000' + d; if (seen.has(k)) continue; seen.add(k)
      const [a, b] = layerOf.get(s) <= layerOf.get(d) ? [s, d] : [d, s]
      nb.get(a).right.push(b); nb.get(b).left.push(a)
    }
    const byLayer = new Map()
    for (const u of col) { if (!byLayer.has(u.layer)) byLayer.set(u.layer, []); byLayer.get(u.layer).push(u) }
    const ls = [...byLayer.keys()].sort((a, b) => a - b)
    const idx = new Map()
    for (const l of ls) {
      byLayer.get(l).sort((a, b) => weight(b) - weight(a) || (a.id < b.id ? -1 : 1))
      byLayer.get(l).forEach((u, i) => idx.set(u.id, i))
    }
    const bary = (u, side) => {
      const xs = nb.get(u.id)[side].map((v) => idx.get(v)).filter((v) => v !== undefined)
      return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : idx.get(u.id)
    }
    for (let sweep = 0; sweep < 6; sweep++) {
      const fwd = sweep % 2 === 0
      for (const l of fwd ? ls : ls.slice().reverse()) {
        const arr = byLayer.get(l)
        const key = new Map(arr.map((u) => [u.id, bary(u, fwd ? 'left' : 'right')]))
        arr.sort((a, b) => key.get(a.id) - key.get(b.id) || (a.id < b.id ? -1 : 1))
        arr.forEach((u, i) => idx.set(u.id, i))
      }
    }
    const pos = new Map()
    for (const l of ls) {
      const arr = byLayer.get(l)
      const hs = arr.map(hOf)
      const total = hs.reduce((a, b) => a + b, 0)
      let y = -total / 2
      arr.forEach((u, i) => { pos.set(u.id, { x: l * gap, y: y + anchor(u) }); y += hs[i] })
    }
    // lanes: an open cluster's members stack to the right of it, below any lane already placed at that x
    const laneBottom = new Map()
    for (const u of units) {
      if (u.type !== 'cluster' || !u.open) continue
      const p = pos.get(u.id); const x = p.x + LANE_DX
      let y = Math.max(p.y - anchor(u), (laneBottom.has(x) ? laneBottom.get(x) : -Infinity))
      for (const m of units) {
        if (m.lane !== u.id) continue
        const h = hOf(m)
        pos.set(m.id, { x, y: y + anchor(m) }); y += h; laneBottom.set(x, y)
      }
    }
    return pos
  }

  // node centre below its row top (labels hang below the node)
  function anchor (u) { return u.type === 'cluster' ? 26 : 12 }

  function weight (u) { return u.type === 'cluster' ? u.count : 1 }

  const api = { folderKey, layers, build, clusterLayer, GENERIC, GAP_X, MIN_GAP_X, LANE_DX }
  if (typeof module !== 'undefined' && module.exports) module.exports = api
  else root.CGLayered = api
})(typeof window !== 'undefined' ? window : this)
