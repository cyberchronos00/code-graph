<template>
  <button @click="inc">{{ count }}</button>
  <span v-for="label in labels" :key="label">{{ label }}</span>
  <input @input="label = $event.target.value" :title="label" />
  <button @click="count++">more</button>
</template>

<script>
import { useCartStore } from '../stores/cart'

export default {
  data() {
    return { count: 0, label: 'x', labels: [] }
  },
  methods: {
    inc() {
      this.count++
      const cart = useCartStore()
      cart.total = 0
      return cart.items.length + useCartStore().total
    },
  },
}
</script>
