import Koa from 'koa';
import Router from '@koa/router';

const app = new Koa();
const router = new Router({ prefix: '/shelves' });

async function listShelves(ctx: any) {
  ctx.body = [];
}

router.get('/', listShelves);
router.put('/:id', async (ctx: any) => {
  ctx.status = 204;
});

app.use(router.routes());
export default app;
