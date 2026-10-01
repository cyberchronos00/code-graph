<script setup lang="ts">
defineProps<{ rows: Array<{ title: string, minor: number }> }>()
</script>

<template>
  <table>
    <tr v-for="row in rows" :key="row.title">
      <td>{{ row.title }}</td>
      <td>{{ formatMoney(row.minor, 'EUR') }}</td>
    </tr>
  </table>
</template>
