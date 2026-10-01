import { FastifyInstance } from 'fastify';
import { adminOnly } from '../auth';

async function listBooks() {
  return [];
}

export async function booksRoutes(fastify: FastifyInstance) {
  fastify.get('/', listBooks);
  fastify.route({
    method: 'POST',
    url: '/',
    preHandler: [adminOnly],
    schema: { body: { type: 'object', properties: { title: { type: 'string' }, isbn: { type: 'string' } } } },
    handler: async (request) => request.body,
  });
  fastify.delete('/:id', { preHandler: adminOnly }, async (request) => ({ deleted: (request.params as any).id }));
}
