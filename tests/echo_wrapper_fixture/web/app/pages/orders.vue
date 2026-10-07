<script setup lang="ts">
import { ordersChannel, presenceChannel, privateChannel } from '~/composables/useRealtime'

declare function useStore(): { storeId: string }
const { storeId } = useStore()
privateChannel(`store.${storeId}.orders`).listen('.OrderShipped', (e: any) => refreshOrder(e.orderId))
ordersChannel(storeId).listen('.OrderShipped', (e: any) => refreshOrder(e.orderId))

declare function runtimeName(): string
presenceChannel(runtimeName())

function refreshOrder(_id: number) {}
</script>

<template><div /></template>
