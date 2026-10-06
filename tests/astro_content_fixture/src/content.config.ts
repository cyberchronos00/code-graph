import { defineCollection, reference, z } from 'astro:content'
import { glob, file } from 'astro/loaders'

const blog = defineCollection({
  loader: glob({ pattern: '**/*.{md,mdx}', base: './src/data/blog' }),
  schema: z.object({ title: z.string(), author: reference('authors') }),
})

const authors = defineCollection({
  loader: file('src/data/authors.json'),
})

export const collections = { blog, authors }
