<script setup lang="ts">
import { linkColor, toneForKind, type GraphKind } from '~/utils/cg-palette'
import { createHomeGraph } from '~/utils/home-graph'

const colorMode = useColorMode()
const dark = computed(() => colorMode.value === 'dark')
const graph = createHomeGraph()

const points = graph.nodes.map((node) => {
  const depth = node.position[2] + 4.4
  const scale = 3.5 / depth
  return {
    ...node,
    x: 430 + node.position[0] * scale * 78,
    y: 180 - node.position[1] * scale * 72,
    r: Math.max(3.2, node.radius * scale * 78)
  }
})

const ordered = [...points].sort((a, b) => a.position[2] - b.position[2])
const labeled = ordered.filter(point => point.label)

function stroke(kind: GraphKind) {
  return toneForKind(kind, dark.value).outline
}

function fill(kind: GraphKind) {
  return toneForKind(kind, dark.value).fill
}

function aura(kind: GraphKind) {
  return toneForKind(kind, dark.value).aura
}

function edge(a: GraphKind, b: GraphKind) {
  return linkColor(a, b, dark.value)
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
      :stroke="edge(points[link.a]!.kind, points[link.b]!.kind)"
      stroke-width="1.25"
      stroke-linecap="round"
    />
    <g
      v-for="point in ordered"
      :key="point.id"
    >
      <circle
        :cx="point.x"
        :cy="point.y"
        :r="point.r * 2.1"
        :fill="aura(point.kind)"
        fill-opacity="0.35"
      />
      <circle
        :cx="point.x"
        :cy="point.y"
        :r="point.r"
        :fill="fill(point.kind)"
        :fill-opacity="dark ? 0.1 : 0.16"
        :stroke="stroke(point.kind)"
        stroke-width="1.6"
      />
    </g>
    <g
      v-for="point in labeled"
      :key="`${point.id}-label`"
    >
      <text
        :x="point.x"
        :y="point.y - point.r - 6"
        text-anchor="middle"
        class="graph-label"
      >
        {{ point.label }}
      </text>
    </g>
  </svg>
</template>

<style scoped>
.graph-label {
  fill: var(--cg-label-ink);
  font-family: var(--font-mono), ui-monospace, monospace;
  font-size: 8px;
  paint-order: stroke;
  stroke: var(--cg-label-scrim);
  stroke-width: 3px;
  stroke-linejoin: round;
}
</style>
