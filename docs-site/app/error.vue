<script setup lang="ts">
import type { NuxtError } from '#app'

defineProps<{
  error: NuxtError
}>()

useSeoMeta({
  title: 'Page not found',
  description: 'That page is not in the code-graph docs.'
})
</script>

<template>
  <UApp>
    <AppHeader />
    <UError :error="error" />
    <AppFooter />
  </UApp>
</template>
