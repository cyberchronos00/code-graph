<script setup lang="ts">
const router = useRouter()
function open(uid: string, slug: string) {
  router.push(`/users/${uid}`)
  router.push(`/${slug}`)
}
</script>
<template>
  <Teleport to="body"><div /></Teleport>
  <UButton to="/bookmarks">marks</UButton>
  <a href="/favicon.ico">icon</a>
  <a href="relative/page">relative</a>
</template>
