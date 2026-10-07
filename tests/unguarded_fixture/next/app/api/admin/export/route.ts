import { getServerSession } from '../../../../lib/session'

export async function GET() {
  const session = await getServerSession()
  if (session?.user.role !== 'admin') throw new Error('forbidden')
  return Response.json([])
}
