<script setup lang="ts">
defineProps<{
  label: string
  /** Selects this panel when it matches `tabs` `default-value`. Defaults to the item index. */
  value?: string | number
  icon?: string
  badge?: string
}>()
</script>

<template>
  <div>
    <slot />
  </div>
</template>
