import { defineConfig } from 'astro/config'

export default defineConfig({
  srcDir: './app',
  base: '/shop',
  trailingSlash: 'never',
  redirects: {
    '/old-books': '/books/dune',
    '/legacy/[slug]': '/books/[slug]',
  },
  i18n: { locales: ['en', 'ja'], defaultLocale: 'en' },
})
