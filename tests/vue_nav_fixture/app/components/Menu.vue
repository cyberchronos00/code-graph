<script setup lang="ts">
const router = useRouter()
const { push } = useRouter()
function open() {
  router.replace(bookmarkRoute())
  push({ name: 'settings-profile' })
  navigateTo(profileRoute())
  navigateTo(dynamicRoute('1'))
  router.push(computedTarget())
}
function computedTarget() {
  return '/nope/' + Math.random()
}
</script>
<template>
  <NuxtLink :to="profileRoute()">via helper</NuxtLink>
  <RouterLink to="/bookmarks">marks</RouterLink>
  <a href="/settings/profile">plain</a>
  <a href="https://example.com/out">out</a>
  <NuxtLink :to="'/users/' + uid">computed</NuxtLink>
</template>
