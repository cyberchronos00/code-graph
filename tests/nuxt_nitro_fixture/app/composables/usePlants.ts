export function usePlants() {
  async function addPlant(name: string) {
    return await $fetch('/api/plants', { method: 'POST', body: { name } })
  }
  async function loadOrders() {
    return await $fetch('/api/orders')
  }
  return { addPlant, loadOrders }
}
