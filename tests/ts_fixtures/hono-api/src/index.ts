import { Hono } from 'hono';
import { authors } from './authors';

const app = new Hono().basePath('/api');

app.route('/authors', authors);
app.get('/health', (c) => c.json({ ok: true }));

export default app;
