<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue'
import {
  BufferGeometry,
  Float32BufferAttribute,
  Group,
  LineBasicMaterial,
  LineSegments,
  Mesh,
  MeshStandardMaterial,
  Raycaster,
  SphereGeometry,
  Vector2
} from 'three'
import { useLoop } from '@tresjs/core'
import { createHomeGraph, nodeBob, type GraphKind } from '~/utils/home-graph'

const props = defineProps<{
  pointerX: number
  pointerY: number
  pointerActive: boolean
}>()

const colorMode = useColorMode()
const graph = createHomeGraph()
const group = new Group()
const sphere = new SphereGeometry(1, 26, 18)
const raycaster = new Raycaster()
const ndc = new Vector2()

const linkPositions = new Float32Array(graph.links.length * 6)
const linkGeometry = new BufferGeometry()
linkGeometry.setAttribute('position', new Float32BufferAttribute(linkPositions, 3))
const linkMaterial = new LineBasicMaterial({ transparent: true, depthWrite: false })
group.add(new LineSegments(linkGeometry, linkMaterial))

const meshes: Mesh[] = []
const materials: MeshStandardMaterial[] = []

for (const node of graph.nodes) {
  const material = new MeshStandardMaterial({ metalness: 0.08, roughness: 0.45 })
  const mesh = new Mesh(sphere, material)
  mesh.userData.id = node.id
  mesh.userData.kind = node.kind
  mesh.position.set(node.position[0], node.position[1], node.position[2])
  mesh.scale.setScalar(node.radius)
  group.add(mesh)
  meshes.push(mesh)
  materials.push(material)
}

interface Pulse {
  a: number
  b: number
  t: number
  speed: number
  violet: boolean
}

const pulses: Pulse[] = graph.links
  .filter((_, index) => index % 6 === 0)
  .slice(0, 7)
  .map((link, index) => ({
    a: link.a,
    b: link.b,
    t: index / 7,
    speed: 0.22 + (index % 4) * 0.06,
    violet: index % 2 === 1
  }))

const pulseMeshes: Mesh[] = []
const pulseMaterials: MeshStandardMaterial[] = []
for (const pulse of pulses) {
  const material = new MeshStandardMaterial({
    metalness: 0.05,
    roughness: 0.28,
    transparent: true,
    depthWrite: false
  })
  const mesh = new Mesh(sphere, material)
  mesh.userData.kind = pulse.violet ? 'pulse-violet' : 'pulse-lime'
  mesh.scale.setScalar(0.05)
  group.add(mesh)
  pulseMeshes.push(mesh)
  pulseMaterials.push(material)
}

function palette(dark: boolean) {
  return {
    lime: dark ? '#d9f99d' : '#3f6212',
    violet: dark ? '#ddd6fe' : '#5b21b6',
    zinc: dark ? '#f4f4f5' : '#3f3f46',
    limeEmissive: dark ? '#a3e635' : '#4d7c0f',
    violetEmissive: dark ? '#a78bfa' : '#6d28d9',
    zincEmissive: dark ? '#a1a1aa' : '#52525b',
    emissive: dark ? 0.58 : 0.14,
    roughness: dark ? 0.36 : 0.5,
    link: dark ? '#f4f4f5' : '#3f3f46',
    linkOpacity: dark ? 0.62 : 0.55
  }
}

function paint(kind: GraphKind | 'pulse-lime' | 'pulse-violet', dark: boolean) {
  const colors = palette(dark)
  if (kind === 'violet' || kind === 'pulse-violet') {
    return { color: colors.violet, emissive: colors.violetEmissive }
  }
  if (kind === 'zinc') {
    return { color: colors.zinc, emissive: colors.zincEmissive }
  }
  return { color: colors.lime, emissive: colors.limeEmissive }
}

function applyTheme(dark: boolean) {
  const colors = palette(dark)
  for (const mesh of meshes) {
    const material = mesh.material as MeshStandardMaterial
    const tone = paint(mesh.userData.kind as GraphKind, dark)
    material.color.set(tone.color)
    material.emissive.set(tone.emissive)
    material.emissiveIntensity = colors.emissive
    material.roughness = colors.roughness
  }
  for (const mesh of pulseMeshes) {
    const material = mesh.material as MeshStandardMaterial
    const tone = paint(mesh.userData.kind as 'pulse-lime' | 'pulse-violet', dark)
    material.color.set(tone.color)
    material.emissive.set(tone.emissive)
    material.emissiveIntensity = dark ? 0.95 : 0.4
    material.roughness = colors.roughness
  }
  linkMaterial.color.set(colors.link)
  linkMaterial.opacity = colors.linkOpacity
}

group.scale.setScalar(1.72)
group.position.set(1.35, -0.05, 0)

applyTheme(colorMode.value === 'dark')
watch(() => colorMode.value, value => applyTheme(value === 'dark'))

const positionAttr = linkGeometry.getAttribute('position')
const { onBeforeRender } = useLoop()

onBeforeRender(({ elapsed, delta, camera }) => {
  const cam = camera.value
  const dt = Math.min(delta, 0.05)
  const dark = colorMode.value === 'dark'
  const baseEmissive = dark ? 0.58 : 0.14

  if (cam) {
    const yaw = elapsed * 0.08 + (props.pointerActive ? props.pointerX * 0.28 : 0)
    const lift = props.pointerActive ? props.pointerY * 0.55 : 0
    const radius = 8.6
    cam.position.set(Math.sin(yaw) * radius * 0.55, 0.35 + lift, Math.cos(yaw) * radius)
    cam.lookAt(0.85, 0.02, 0)
  }

  let hotId = -1
  if (props.pointerActive && cam) {
    ndc.set(props.pointerX, props.pointerY)
    raycaster.setFromCamera(ndc, cam)
    const hit = raycaster.intersectObjects(meshes, false)[0]
    if (hit) hotId = hit.object.userData.id as number
  }

  for (const mesh of meshes) {
    const node = graph.nodes[mesh.userData.id as number]!
    const y = nodeBob(node, elapsed)
    mesh.position.set(node.position[0], y, node.position[2])
    const hot = hotId === node.id
    const target = node.radius * (hot ? 1.75 : 1)
    mesh.scale.setScalar(mesh.scale.x + (target - mesh.scale.x) * 0.18)
    const material = mesh.material as MeshStandardMaterial
    const next = baseEmissive + (hot ? (dark ? 0.55 : 0.28) : 0)
    material.emissiveIntensity += (next - material.emissiveIntensity) * 0.2
  }

  graph.links.forEach((link, index) => {
    const a = graph.nodes[link.a]!
    const b = graph.nodes[link.b]!
    positionAttr.setXYZ(index * 2, a.position[0], nodeBob(a, elapsed), a.position[2])
    positionAttr.setXYZ(index * 2 + 1, b.position[0], nodeBob(b, elapsed), b.position[2])
  })
  positionAttr.needsUpdate = true

  pulses.forEach((pulse, index) => {
    pulse.t = (pulse.t + dt * pulse.speed) % 1
    const a = graph.nodes[pulse.a]!
    const b = graph.nodes[pulse.b]!
    const t = pulse.t
    const mesh = pulseMeshes[index]!
    mesh.position.set(
      a.position[0] + (b.position[0] - a.position[0]) * t,
      nodeBob(a, elapsed) + (nodeBob(b, elapsed) - nodeBob(a, elapsed)) * t,
      a.position[2] + (b.position[2] - a.position[2]) * t
    )
    const swell = 0.035 + Math.sin(t * Math.PI) * 0.045
    mesh.scale.setScalar(swell)
    const material = pulseMaterials[index]!
    material.opacity = 0.25 + Math.sin(t * Math.PI) * 0.75
  })
})

onBeforeUnmount(() => {
  sphere.dispose()
  linkGeometry.dispose()
  linkMaterial.dispose()
  for (const material of materials) material.dispose()
  for (const material of pulseMaterials) material.dispose()
})
</script>

<template>
  <TresPerspectiveCamera :position="[0.4, 0.35, 8.6]" :fov="48" />
  <TresAmbientLight :intensity="colorMode.value === 'dark' ? 0.32 : 0.92" />
  <TresDirectionalLight
    :position="[4.5, 6, 5]"
    :intensity="colorMode.value === 'dark' ? 0.75 : 1.2"
  />
  <TresPointLight
    :position="[-3.4, 1.6, 2.4]"
    color="#65a30d"
    :intensity="colorMode.value === 'dark' ? 8 : 1.6"
    :distance="16"
  />
  <TresPointLight
    :position="[3.2, -1.2, 2.2]"
    color="#7c3aed"
    :intensity="colorMode.value === 'dark' ? 7 : 1.15"
    :distance="16"
  />
  <primitive :object="group" />
</template>
