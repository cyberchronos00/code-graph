import Fastify from 'fastify';
import { booksRoutes } from './routes/books';
import { verifyToken } from './auth';

const app = Fastify();

app.addHook('onRequest', verifyToken);
app.register(booksRoutes, { prefix: '/api/books' });
app.get('/ping', async () => 'pong');

export default app;
