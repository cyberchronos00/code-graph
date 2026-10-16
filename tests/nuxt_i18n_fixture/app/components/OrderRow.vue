<script setup lang="ts">
defineProps<{ id: number }>()
</script>
<template>
  <button>{{ $t('orders.cancel') }}</button>
</template>
