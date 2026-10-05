<script setup lang="ts">
const show3d = ref(false)
const ready = ref(false)
const reduced = ref(false)
const failed = ref(false)

function webglOk() {
  try {
    const canvas = document.createElement('canvas')
    return !!(canvas.getContext('webgl2') || canvas.getContext('webgl'))
  }
  catch {
    return false
  }
}

let media: MediaQueryList | null = null

function sync() {
  reduced.value = !!media?.matches
  show3d.value = !reduced.value && !failed.value && webglOk()
}

onMounted(() => {
  media = window.matchMedia('(prefers-reduced-motion: reduce)')
  sync()
  ready.value = true
  media.addEventListener('change', sync)
})

onBeforeUnmount(() => {
  media?.removeEventListener('change', sync)
})

onErrorCaptured(() => {
  failed.value = true
  show3d.value = false
  return false
})
</script>

<template>
  <div class="graph-bleed h-full w-full">
    <ClientOnly>
      <LazyHomeGraphCanvas
        v-if="show3d"
        class="h-full w-full"
      />
      <HomeGraphFallback
        v-else
        class="h-full w-full"
      />
      <template #fallback>
        <HomeGraphFallback class="h-full w-full" />
      </template>
    </ClientOnly>
    <p class="sr-only">
      Decorative dependency graph of nodes and links filling the hero. Calls, data and boundaries are three clusters.
    </p>
    <p
      v-if="ready && !show3d"
      class="pointer-events-none absolute bottom-8 left-4 font-mono text-[11px] tracking-wide text-zinc-600 sm:left-6 dark:text-zinc-400"
    >
      {{ reduced ? 'Reduced motion — static graph.' : 'WebGL unavailable — static graph.' }}
    </p>
  </div>
</template>

<style scoped>
.graph-bleed {
  mask-image: radial-gradient(ellipse 92% 78% at 62% 46%, #000 28%, transparent 74%);
}
</style>
