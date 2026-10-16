export default defineNuxtConfig({
  modules: ['@nuxtjs/i18n'],
  i18n: {
    defaultLocale: 'en',
    langDir: 'locales',
    locales: [
      { code: 'es', file: 'es.json' },
      { code: 'en', file: 'en.json' },
      { code: 'pt-BR', file: 'pt-BR.yaml' },
    ],
  },
})
