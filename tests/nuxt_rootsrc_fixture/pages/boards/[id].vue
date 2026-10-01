<script setup lang="ts">
const route = useRoute()
const { list, move } = useTasks()
const tasks = ref(await list(Number(route.params.id)))
</script>

<template>
  <TaskCard v-for="t in tasks" :key="t.id" :title="t.title" :state="t.state" @click="move(t.id, 'done')" />
</template>
