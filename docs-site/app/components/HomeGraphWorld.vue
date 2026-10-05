<script setup lang="ts">
import { onBeforeUnmount, onMounted, watch } from 'vue'
import {
  BufferGeometry,
  CanvasTexture,
  Color,
  Float32BufferAttribute,
  Group,
  LineBasicMaterial,
  LineSegments,
  Mesh,
  MeshBasicMaterial,
  Raycaster,
  ShaderMaterial,
  SphereGeometry,
  Sprite,
  SpriteMaterial,
  Vector2,
  Vector3
} from 'three'
import { useLoop } from '@tresjs/core'
import { cgGraph, linkColor, toneForKind, type GraphKind } from '~/utils/cg-palette'
import { createHomeGraph, nodeBob } from '~/utils/home-graph'

const props = defineProps<{
  pointerX: number
  pointerY: number
  pointerActive: boolean
}>()

const VERT = `
varying vec3 vNormal;
varying vec3 vView;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vNormal = normalize(normalMatrix * normal);
  vView = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}
`

const FRAG = `
uniform vec3 uFill;
uniform vec3 uLine;
uniform float uFillAlpha;
uniform float uHot;
varying vec3 vNormal;
varying vec3 vView;
void main() {
  float nd = max(dot(normalize(vNormal), normalize(vView)), 0.0);
  float rim = pow(1.0 - nd, 2.85);
  float edge = smoothstep(0.4, 0.9, rim);
  float alpha = clamp(uFillAlpha * (1.0 - edge) + edge * (0.86 + uHot * 0.14), 0.0, 1.0);
  vec3 color = mix(uFill, uLine, edge);
  gl_FragColor = vec4(color, alpha);
}
`

const colorMode = useColorMode()
const graph = createHomeGraph()
const group = new Group()
const sphere = new SphereGeometry(1, 28, 20)
const raycaster = new Raycaster()
const ndc = new Vector2()
const projected = new Vector3()

function auraTexture() {
  const canvas = document.createElement('canvas')
  canvas.width = 128
  canvas.height = 128
  const ctx = canvas.getContext('2d')!
  const gradient = ctx.createRadialGradient(64, 64, 6, 64, 64, 64)
  gradient.addColorStop(0, 'rgba(255,255,255,0)')
  gradient.addColorStop(0.46, 'rgba(255,255,255,0)')
  gradient.addColorStop(0.68, 'rgba(255,255,255,0.7)')
  gradient.addColorStop(1, 'rgba(255,255,255,0)')
  ctx.fillStyle = gradient
  ctx.fillRect(0, 0, 128, 128)
  const texture = new CanvasTexture(canvas)
  texture.needsUpdate = true
  return texture
}

const glowMap = auraTexture()

const linkPositions = new Float32Array(graph.links.length * 6)
const linkColors = new Float32Array(graph.links.length * 6)
const linkGeometry = new BufferGeometry()
linkGeometry.setAttribute('position', new Float32BufferAttribute(linkPositions, 3))
linkGeometry.setAttribute('color', new Float32BufferAttribute(linkColors, 3))
const linkMaterial = new LineBasicMaterial({
  vertexColors: true,
  transparent: true,
  depthWrite: false,
  opacity: 0.85
})
group.add(new LineSegments(linkGeometry, linkMaterial))

const meshes: Mesh[] = []
const materials: ShaderMaterial[] = []
const auras: Sprite[] = []
const auraMaterials: SpriteMaterial[] = []

function shellMaterial(kind: GraphKind, dark: boolean) {
  const tone = toneForKind(kind, dark)
  return new ShaderMaterial({
    uniforms: {
      uFill: { value: new Color(tone.fill) },
      uLine: { value: new Color(tone.outline) },
      uFillAlpha: { value: dark ? cgGraph.fillAlpha.dark : cgGraph.fillAlpha.light },
      uHot: { value: 0 }
    },
    vertexShader: VERT,
    fragmentShader: FRAG,
    transparent: true,
    depthWrite: false,
    toneMapped: false
  })
}

for (const node of graph.nodes) {
  const material = shellMaterial(node.kind, colorMode.value === 'dark')
  const mesh = new Mesh(sphere, material)
  mesh.userData.id = node.id
  mesh.userData.kind = node.kind
  mesh.position.set(node.position[0], node.position[1], node.position[2])
  mesh.scale.setScalar(node.radius)
  mesh.renderOrder = 2
  const tone = toneForKind(node.kind, colorMode.value === 'dark')
  const auraMaterial = new SpriteMaterial({
    map: glowMap,
    color: new Color(tone.aura),
    transparent: true,
    depthWrite: false,
    opacity: colorMode.value === 'dark' ? 0.55 : 0.4,
    toneMapped: false
  })
  const aura = new Sprite(auraMaterial)
  aura.position.copy(mesh.position)
    aura.scale.setScalar(node.radius * 5.4)
  aura.renderOrder = 1
  group.add(aura)
  group.add(mesh)
  meshes.push(mesh)
  materials.push(material)
  auras.push(aura)
  auraMaterials.push(auraMaterial)
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
const pulseMaterials: MeshBasicMaterial[] = []
for (const pulse of pulses) {
  const material = new MeshBasicMaterial({
    color: pulse.violet ? cgGraph.edgeData.dark : cgGraph.pulse.dark,
    transparent: true,
    depthWrite: false,
    toneMapped: false
  })
  const mesh = new Mesh(sphere, material)
  mesh.scale.setScalar(0.045)
  mesh.renderOrder = 3
  group.add(mesh)
  pulseMeshes.push(mesh)
  pulseMaterials.push(material)
}

const labelLayer = document.createElement('div')
labelLayer.className = 'cg-label-layer'
const labelEls = new Map<number, HTMLDivElement>()

for (const node of graph.nodes) {
  if (!node.label) continue
  const el = document.createElement('div')
  el.className = 'cg-node-label'
  el.textContent = node.label
  labelLayer.appendChild(el)
  labelEls.set(node.id, el)
}

function paintLinks(dark: boolean) {
  const color = new Color()
  graph.links.forEach((link, index) => {
    const a = graph.nodes[link.a]!
    const b = graph.nodes[link.b]!
    color.set(linkColor(a.kind, b.kind, dark))
    linkColors.set([color.r, color.g, color.b, color.r, color.g, color.b], index * 6)
  })
  const attr = linkGeometry.getAttribute('color')
  attr.needsUpdate = true
}

function applyTheme(dark: boolean) {
  for (const mesh of meshes) {
    const material = mesh.material as ShaderMaterial
    const tone = toneForKind(mesh.userData.kind as GraphKind, dark)
    material.uniforms.uFill!.value.set(tone.fill)
    material.uniforms.uLine!.value.set(tone.outline)
    material.uniforms.uFillAlpha!.value = dark ? cgGraph.fillAlpha.dark : cgGraph.fillAlpha.light
  }
  for (let i = 0; i < auras.length; i++) {
    const kind = meshes[i]!.userData.kind as GraphKind
    auraMaterials[i]!.color.set(toneForKind(kind, dark).aura)
    auraMaterials[i]!.opacity = dark ? 0.55 : 0.4
  }
  pulseMaterials.forEach((material, index) => {
    const violet = pulses[index]!.violet
    material.color.set(violet
      ? (dark ? cgGraph.edgeData.dark : cgGraph.edgeData.light)
      : (dark ? cgGraph.pulse.dark : cgGraph.pulse.light))
  })
  paintLinks(dark)
}

group.scale.setScalar(1.72)
group.position.set(1.35, -0.05, 0)

applyTheme(colorMode.value === 'dark')
watch(() => colorMode.value, value => applyTheme(value === 'dark'))

const positionAttr = linkGeometry.getAttribute('position')
const { onBeforeRender } = useLoop()

onMounted(() => {
  const canvas = document.querySelector('.graph-canvas')
  canvas?.appendChild(labelLayer)
})

onBeforeRender(({ elapsed, delta, camera }) => {
  const cam = camera.value
  const dt = Math.min(delta, 0.05)
  const dark = colorMode.value === 'dark'

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

  for (let i = 0; i < meshes.length; i++) {
    const mesh = meshes[i]!
    const node = graph.nodes[mesh.userData.id as number]!
    const y = nodeBob(node, elapsed)
    mesh.position.set(node.position[0], y, node.position[2])
    const hot = hotId === node.id
    const target = node.radius * (hot ? 1.55 : 1)
    mesh.scale.setScalar(mesh.scale.x + (target - mesh.scale.x) * 0.18)
    const aura = auras[i]!
    aura.position.copy(mesh.position)
    aura.scale.setScalar(mesh.scale.x * 5.4)
    const material = mesh.material as ShaderMaterial
    const next = hot ? 1 : 0
    const hotUniform = material.uniforms.uHot!
    hotUniform.value += (next - hotUniform.value) * 0.2
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
    const swell = 0.03 + Math.sin(t * Math.PI) * 0.04
    mesh.scale.setScalar(swell)
    pulseMaterials[index]!.opacity = 0.35 + Math.sin(t * Math.PI) * 0.65
  })

  if (cam && labelLayer.isConnected) {
    const host = labelLayer.parentElement
    const width = host?.clientWidth || 1
    const height = host?.clientHeight || 1
    cam.updateMatrixWorld()
    for (const [id, el] of labelEls) {
      const mesh = meshes[id]!
      mesh.getWorldPosition(projected)
      const facing = projected.clone().sub(cam.position).normalize().dot(cam.getWorldDirection(new Vector3()))
      projected.project(cam)
      const x = (projected.x * 0.5 + 0.5) * width
      const y = (-projected.y * 0.5 + 0.5) * height
      el.style.transform = `translate(-50%, -140%) translate(${x}px, ${y}px)`
      const fade = Math.min(1, Math.max(0, (facing - 0.35) / 0.4))
      const onTitle = y < height * 0.56 && x < width * 0.7
      const onBody = y >= height * 0.56 && y < height * 0.86 && x < width * 0.5
      const onCopy = onTitle || onBody
      el.style.opacity = (projected.z > 1 || onCopy) ? '0' : fade.toFixed(3)
    }
  }
})

onBeforeUnmount(() => {
  sphere.dispose()
  linkGeometry.dispose()
  linkMaterial.dispose()
  glowMap.dispose()
  for (const material of materials) material.dispose()
  for (const material of auraMaterials) material.dispose()
  for (const material of pulseMaterials) material.dispose()
  labelLayer.remove()
})
</script>

<template>
  <TresPerspectiveCamera :position="[0.4, 0.35, 8.6]" :fov="48" />
  <primitive :object="group" />
</template>
