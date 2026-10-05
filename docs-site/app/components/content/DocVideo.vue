<script setup lang="ts">
defineProps<{
  src: string
  poster?: string
}>()
</script>

<template>
  <video
    class="my-4 w-full rounded-lg"
    controls
    preload="metadata"
    playsinline
    :src="src"
    :poster="poster"
  />
</template>
