import { listBooks } from '../../lib/books'

export function GET(): Response {
  return new Response(JSON.stringify(listBooks()))
}

export async function POST({ request }: { request: Request }): Promise<Response> {
  return new Response(await request.text(), { status: 201 })
}
