import cgDark from './shiki/cg-dark.json'
import cgLight from './shiki/cg-light.json'
import { rewriteDocLinks } from './rewrite-doc-links'
import { docRoutes } from './app/utils/docs-nav'

export default defineNuxtConfig({
  modules: ['@nuxt/ui', '@nuxt/content'],

  css: ['~/assets/css/main.css'],

  colorMode: {
    preference: 'system',
    fallback: 'light',
    classSuffix: ''
  },

  fonts: {
    families: [
      { name: 'Geist', provider: 'google', weights: [400, 500, 600, 700] },
      { name: 'Geist Mono', provider: 'google', weights: [400, 500, 600] },
      { name: 'Noto Sans JP', provider: 'google', weights: [400, 500, 700] }
    ]
  },

  icon: {
    serverBundle: 'local',
    clientBundle: {
      scan: true
    }
  },

  content: {
    experimental: {
      sqliteConnector: 'native'
    },
    build: {
      markdown: {
        toc: {
          searchDepth: 4
        },
        highlight: {
          theme: {
            default: cgLight,
            light: cgLight,
            dark: cgDark
          },
          langs: ['python', 'sql', 'json', 'yaml', 'console', 'shell']
        }
      }
    }
  },

  routeRules: {
    '/docs': { redirect: '/docs/install' }
  },

  nitro: {
    preset: 'cloudflare_module',
    cloudflare: {
      deployConfig: true,
      nodeCompat: true
    },
    prerender: {
      crawlLinks: true,
      routes: ['/', ...docRoutes]
    }
  },

  compatibilityDate: '2026-10-01',

  hooks: {
    'content:file:beforeParse'(ctx: { file: { body: string } }) {
      if (typeof ctx.file.body === 'string') {
        ctx.file.body = rewriteDocLinks(ctx.file.body)
      }
    }
  }
})
