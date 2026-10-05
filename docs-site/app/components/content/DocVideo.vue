<script setup lang="ts">
withDefaults(defineProps<{
  src: string
  poster?: string
  label?: string
  framed?: boolean
}>(), {
  framed: true
})
</script>

<template>
  <video
    :class="framed ? 'my-4 w-full rounded-lg' : 'block w-full bg-zinc-950'"
    controls
    preload="metadata"
    playsinline
    :src="src"
    :poster="poster"
    :aria-label="label"
  />
</template>
