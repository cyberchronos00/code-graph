<script setup lang="ts">
import Echo from 'laravel-echo'

const echo = ref<Echo<'reverb'> | null>(null)

function connect(userId: number) {
  echo.value = new Echo({ broadcaster: 'reverb', key: useRuntimeConfig().public.reverbKey })
  echo.value.private(`App.Models.User.${userId}`).listen('.OrderShipped', refresh)
}

function refresh() {}
</script>
<template><div /></template>
