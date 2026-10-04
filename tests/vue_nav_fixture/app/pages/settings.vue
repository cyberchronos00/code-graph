<script setup lang="ts">
const router = useRouter()
function go() {
  router.push('/settings/profile')
}
</script>
<template>
  <button @click="go" />
</template>
