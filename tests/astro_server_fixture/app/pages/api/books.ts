import type { APIContext } from 'astro'
import { saveBook } from '../../lib/books'

export async function POST({ request, redirect }: APIContext): Promise<Response> {
  saveBook(await request.text())
  return redirect('/books/dune', 303)
}
