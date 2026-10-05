<script setup lang="ts">
import { createHomeGraph, type GraphKind } from '~/utils/home-graph'

const graph = createHomeGraph()

const points = graph.nodes.map((node) => {
  const depth = node.position[2] + 4.4
  const scale = 3.5 / depth
  return {
    ...node,
    x: 390 + node.position[0] * scale * 78,
    y: 180 - node.position[1] * scale * 72,
    r: Math.max(3.2, node.radius * scale * 78)
  }
})

const ordered = [...points].sort((a, b) => a.position[2] - b.position[2])

function fill(kind: GraphKind) {
  if (kind === 'lime') return 'var(--node-lime)'
  if (kind === 'violet') return 'var(--node-violet)'
  return 'var(--node-zinc)'
}
</script>

<template>
  <svg
    class="graph-fallback h-full w-full"
    viewBox="0 0 640 400"
    preserveAspectRatio="xMidYMid slice"
    aria-hidden="true"
  >
    <line
      v-for="(link, index) in graph.links"
      :key="index"
      :x1="points[link.a]!.x"
      :y1="points[link.a]!.y"
      :x2="points[link.b]!.x"
      :y2="points[link.b]!.y"
      stroke="var(--link)"
      stroke-width="1.2"
      stroke-linecap="round"
    />
    <circle
      v-for="point in ordered"
      :key="point.id"
      :cx="point.x"
      :cy="point.y"
      :r="point.r"
      :fill="fill(point.kind)"
    />
  </svg>
</template>

<style scoped>
.graph-fallback {
  --node-lime: #3f6212;
  --node-violet: #5b21b6;
  --node-zinc: #3f3f46;
  --link: #71717a;
}

.dark .graph-fallback {
  --node-lime: #a3e635;
  --node-violet: #c4b5fd;
  --node-zinc: #e4e4e7;
  --link: #a1a1aa;
}
</style>
