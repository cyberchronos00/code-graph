<script setup lang="ts">
import Echo from 'laravel-echo'
import Pusher from 'pusher-js'
import { ref, shallowRef } from 'vue'

const echo = ref<Echo<'reverb'> | null>(null)

function connect(userId: number) {
  echo.value = new Echo({ broadcaster: 'reverb', key: 'local' })
  echo.value.private(`App.Models.User.${userId}`).listen('.OrderShipped', () => {})
  echo.value.leave(`App.Models.User.${userId}`)
  echo.value.disconnect()
}

const held = ref<Echo<'reverb'> | null>(null)
held.value.private('orders.held').listen('Held', () => {})

const orders = {
  echo: null as Echo<'reverb'> | null,
}
orders.echo.private('orders.desk').listen('DeskUpdated', () => {})

function useOrdersEcho() {
  const client = new Echo({ broadcaster: 'reverb', key: 'local' })
  return { client }
}
useOrdersEcho().client.private(`App.Models.User.${1}`).listen('.OrderShipped', () => {})

const pusher = shallowRef<Pusher | null>(null)
pusher.value = new Pusher('key', { cluster: 'eu' })
pusher.value.subscribe('private-orders.desk').bind('OrderShipped', () => {})

const plain = new Echo({ broadcaster: 'reverb', key: 'local' })
plain.private('orders.plain').listen('Plain', () => {})

function unknown(box: { private: (name: string) => void }, userId: number) {
  box.private('App.Models.User.9')
  box.private(`not-a-${userId}`)
  box.private(String(userId))
}
</script>
<template><div /></template>
