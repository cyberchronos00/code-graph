import { Hono } from 'hono';

export const authors = new Hono();

authors.get('/:id', (c) => c.json({ id: c.req.param('id') }));
authors.post('/', async (c) => c.json(await c.req.json(), 201));
