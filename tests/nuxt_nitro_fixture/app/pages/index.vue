<script setup lang="ts">
const { data: plants } = await useFetch('/api/plants')
const { addPlant } = usePlants()
</script>
<template><ul><li v-for="p in plants" :key="p.id" @click="addPlant(p.name)">{{ p.name }}</li></ul></template>
