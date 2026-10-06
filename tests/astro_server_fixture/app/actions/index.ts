import { defineAction } from 'astro:actions'
import { saveBook } from '../lib/books'

export const server = {
  addBook: defineAction({
    accept: 'form',
    handler: async (input: { title: string }) => saveBook(input.title),
  }),
  removeBook: defineAction({ handler: async (input: { id: string }) => input.id }),
  shelf: {
    clear: defineAction({ handler: async () => 0 }),
  },
}
