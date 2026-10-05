<script setup lang="ts">
import { NoToneMapping } from 'three'

const pointerX = ref(0)
const pointerY = ref(0)
const pointerActive = ref(false)

function onMove(event: PointerEvent) {
  const el = event.currentTarget as HTMLElement
  const rect = el.getBoundingClientRect()
  pointerX.value = ((event.clientX - rect.left) / rect.width - 0.5) * 2
  pointerY.value = -((event.clientY - rect.top) / rect.height - 0.5) * 2
  pointerActive.value = true
}

function onLeave() {
  pointerActive.value = false
  pointerX.value = 0
  pointerY.value = 0
}
</script>

<template>
  <div
    class="graph-canvas h-full w-full"
    @pointermove="onMove"
    @pointerleave="onLeave"
  >
    <TresCanvas
      alpha
      :clear-alpha="0"
      clear-color="#000000"
      :tone-mapping="NoToneMapping"
      :antialias="true"
    >
      <HomeGraphWorld
        :pointer-x="pointerX"
        :pointer-y="pointerY"
        :pointer-active="pointerActive"
      />
    </TresCanvas>
  </div>
</template>

<style scoped>
.graph-canvas,
.graph-canvas :deep(div),
.graph-canvas :deep(canvas) {
  background: transparent !important;
}

.graph-canvas :deep(canvas) {
  display: block;
  width: 100% !important;
  height: 100% !important;
}
</style>
