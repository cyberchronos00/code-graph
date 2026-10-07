import dns from 'node:dns/promises'

export async function GET(request: Request) {
  const host = new URL(request.url).searchParams.get('host')
  const found = await dns.lookup(host)
  return Response.json(found)
}
