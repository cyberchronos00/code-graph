export type GraphKind = 'lime' | 'violet' | 'zinc'

export interface GraphNode {
  id: number
  kind: GraphKind
  position: [number, number, number]
  radius: number
  phase: number
  /** Bookstore example name. Only hubs and a readable subset are labeled. */
  label?: string
}

const exampleLabels: Record<GraphKind, string[]> = {
  lime: [
    'StockService::reserve',
    'OrderController::store',
    'BookController::store',
    'SyncWarehouseCommand',
    'useApi',
    'http:POST /v1/orders',
    'composables'
  ],
  violet: [
    'connection:warehouse',
    'table:books'
  ],
  zinc: [
    'page:/reports',
    'Filament BookResource',
    'route matches'
  ]
}

export interface GraphLink {
  a: number
  b: number
}

function mulberry32(seed: number) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6D2B79F5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function dist(a: [number, number, number], b: [number, number, number]) {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])
}

export function createHomeGraph() {
  const rand = mulberry32(0xC0DE6A)
  const kinds: GraphKind[] = ['lime', 'violet', 'zinc']
  const centers: Record<GraphKind, [number, number, number]> = {
    lime: [-1.45, 0.55, 0.15],
    violet: [1.5, 0.2, -0.25],
    zinc: [0.1, -1.2, 0.4]
  }
  const counts: Record<GraphKind, number> = { lime: 14, violet: 12, zinc: 10 }
  const nodes: GraphNode[] = []

  for (const kind of kinds) {
    for (let i = 0; i < counts[kind]; i++) {
      const [cx, cy, cz] = centers[kind]
      const spread = kind === 'zinc' ? 1.05 : 1.35
      const hub = i === 0 ? 0.07 : 0
      nodes.push({
        id: nodes.length,
        kind,
        position: [
          cx + (rand() - 0.5) * spread * 2,
          cy + (rand() - 0.5) * spread * 1.45,
          cz + (rand() - 0.5) * spread * 1.55
        ],
        radius: 0.085 + rand() * 0.055 + hub,
        phase: rand() * Math.PI * 2
      })
    }
  }

  const labeled: Record<GraphKind, number> = { lime: 0, violet: 0, zinc: 0 }
  for (const node of nodes) {
    const names = exampleLabels[node.kind]
    const index = labeled[node.kind]
    if (index < names.length) node.label = names[index]
    labeled[node.kind] = index + 1
  }

  const links: GraphLink[] = []
  const seen = new Set<string>()
  const add = (a: number, b: number) => {
    if (a === b) return
    const key = a < b ? `${a}:${b}` : `${b}:${a}`
    if (seen.has(key)) return
    seen.add(key)
    links.push({ a, b })
  }

  for (let i = 0; i < nodes.length; i++) {
    const nearest = nodes
      .map((node, index) => ({ index, d: dist(nodes[i]!.position, node.position) }))
      .filter(item => item.index !== i)
      .sort((p, q) => p.d - q.d)
      .slice(0, 2)
    for (const item of nearest) add(i, item.index)
  }

  const byKind: Record<GraphKind, GraphNode[]> = { lime: [], violet: [], zinc: [] }
  for (const node of nodes) byKind[node.kind].push(node)

  const bridges: Array<[GraphKind, GraphKind]> = [
    ['lime', 'violet'],
    ['violet', 'zinc'],
    ['zinc', 'lime']
  ]
  for (const [left, right] of bridges) {
    for (let i = 0; i < 2; i++) {
      const a = byKind[left][Math.floor(rand() * byKind[left].length)]!
      const b = byKind[right][Math.floor(rand() * byKind[right].length)]!
      add(a.id, b.id)
    }
  }

  return { nodes, links }
}

export function nodeBob(node: GraphNode, elapsed: number) {
  return node.position[1] + Math.sin(elapsed * 0.85 + node.phase) * 0.075
}
