<script setup lang="ts">
const route = useRoute()
const { data: plant } = await useLazyFetch(`/api/plants/${route.params.id}`)
</script>
<template><h1>{{ plant?.name }}</h1></template>
