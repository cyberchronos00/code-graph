<script setup lang="ts">
definePageMeta({
  layout: 'landing'
})

const title = 'code-graph'
const socialTitle = 'code-graph: see what a change affects'
const description = 'A deterministic dependency graph for the code you and your agent are about to change.'

useSeoMeta({
  title,
  titleTemplate: '%s',
  description,
  ogTitle: socialTitle,
  twitterTitle: socialTitle,
  ogDescription: description,
  twitterDescription: description
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
