<script setup lang="ts">
const props = defineProps<{ title: string, state: string }>()
const label = computed(() => taskTitle(props.title, props.state))
</script>

<template>
  <article>{{ label }}</article>
</template>
