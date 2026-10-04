import { ref, reactive } from 'vue'

export function useCart() {
  const total = ref(0)
  const state = reactive({ items: [] as string[], open: false })
  function add(s: string) {
    total.value++
    state.open = true
    state.items.push(s)
    return total.value
  }
  return { total, add }
}
