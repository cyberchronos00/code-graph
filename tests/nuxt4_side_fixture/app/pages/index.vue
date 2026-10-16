<script setup lang="ts">
const { recipes } = useRecipes()
</script>
<template><ul><li v-for="r in recipes" :key="r.id">{{ r.title }}</li></ul></template>
