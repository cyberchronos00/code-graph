import { loadShelf } from '../../../lib/shelf'

export async function GET() {
  const shelf = await loadShelf()
  return Response.json(shelf)
}
