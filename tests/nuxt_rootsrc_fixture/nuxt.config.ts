export default defineNuxtConfig({
  ssr: false,
  runtimeConfig: {
    public: {
      // API origin incl. its path prefix; overridden per deployment by NUXT_PUBLIC_API_BASE
      apiBase: process.env.NUXT_PUBLIC_API_BASE || 'http://localhost:8000/api',
      mapsKey: '',
    },
  },
})
