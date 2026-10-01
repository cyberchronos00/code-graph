export async function verifyToken(request: any, reply: any) {
  if (!request.headers.authorization) reply.code(401).send();
}

export async function adminOnly(request: any, reply: any) {
  if (request.headers['x-role'] !== 'admin') reply.code(403).send();
}
