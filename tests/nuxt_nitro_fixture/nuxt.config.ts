export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  extends: ['./layers/admin'],
  serverHandlers: [
    { route: '/api/feed', method: 'get', handler: '~~/server/handlers/feed.ts' },
  ],
  nitro: {
    handlers: [{ route: '/rss.xml', handler: './server/handlers/rss.ts' }],
    routeRules: {
      '/api/legacy/**': { proxy: 'https://legacy.plant-shop.test/**' },
    },
  },
})
