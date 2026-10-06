import { slugify } from './format'

export interface Book { title: string; cents: number }

export function listBooks(): Book[] {
  return [{ title: 'Dune', cents: 999 }]
}

export function findBook(slug: string): Book | undefined {
  return listBooks().find((b) => slugify(b.title) === slug)
}
