<script setup lang="ts">
definePageMeta({
  layout: 'landing'
})

useSeoMeta({
  title: 'code-graph',
  titleTemplate: '%s',
  description: 'A deterministic dependency graph for the code you and your agent are about to change.'
})
</script>

<template>
  <div>
    <HomeHero />
    <HomeWhy />
    <HomeShowcase />
    <HomeStacks />
    <HomeExplore />
    <HomeContributors />
  </div>
</template>
