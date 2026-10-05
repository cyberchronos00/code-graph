import { defineCollection, defineContentConfig } from '@nuxt/content'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const docsDir = resolve(dirname(fileURLToPath(import.meta.url)), '../docs')

export default defineContentConfig({
  collections: {
    docs: defineCollection({
      type: 'page',
      source: {
        cwd: docsDir,
        include: '**/*.md',
        prefix: '/docs',
        exclude: ['media/**']
      }
    })
  }
})
