<script setup lang="ts">
const { t } = useI18n()
const heading = t('orders.title')
</script>
<template>
  <h1>{{ heading }}</h1>
  <OrderRow :id="1" />
</template>
