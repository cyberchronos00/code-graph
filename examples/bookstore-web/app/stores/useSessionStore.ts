import { useApi } from '~/composables/useApi'

export const useSessionStore = defineStore('session', {
  actions: {
    async load() {
      const { api } = useApi()
      return api.get('/admin/session')
    }
  }
})
