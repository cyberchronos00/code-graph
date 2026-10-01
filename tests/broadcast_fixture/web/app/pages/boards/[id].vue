<script setup lang="ts">
import { useBoardRealtime } from '~/composables/useBoardRealtime'
import { taskLabel } from '~/utils/format'

const route = useRoute()
const stop = useBoardRealtime(Number(route.params.id), 1)
const tasks = await $fetch(`/api/boards/${route.params.id}/tasks`)
const label = (t: { title: string, state: string }) => taskLabel(t.title, t.state)
onBeforeUnmount(stop)
</script>

<template>
  <ul><li v-for="t in tasks" :key="t.id">{{ label(t) }}</li></ul>
</template>
