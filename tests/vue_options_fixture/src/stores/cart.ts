import { defineStore } from 'pinia'

export const useCartStore = defineStore('cart', {
  state: () => ({ items: [] as string[], total: 0 }),
  actions: {
    add(s: string) {
      this.items.push(s)
      this.total += 1
      return this.total
    },
  },
})
