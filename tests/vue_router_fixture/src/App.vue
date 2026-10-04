<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
function go(id: string) {
  router.push({ name: 'user' })
  router.push('/settings/profile')
  router.push(`/users/${id}`)
}
</script>
<template>
  <RouterLink to="/">home</RouterLink>
  <router-link :to="{ name: 'settings-profile' }">profile</router-link>
</template>
