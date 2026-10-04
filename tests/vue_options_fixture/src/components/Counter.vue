<template>
  <button @click="inc">{{ count }}</button>
</template>

<script>
import { useCartStore } from '../stores/cart'

export default {
  data() {
    return { count: 0, label: 'x' }
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
