import { getServerSession } from '../../../lib/session'

export async function GET() {
  const session = await getServerSession()
  if (!session) {
    return Response.json({ error: 'unauthorized' }, { status: 401 })
  }
  return Response.json([])
}
