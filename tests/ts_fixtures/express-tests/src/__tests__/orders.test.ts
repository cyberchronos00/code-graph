import express from 'express';
import { listOrders } from '../app';

const router = express.Router();
router.delete('/test-only/orders/:id', listOrders);

test('router mounts', () => {
  const a = express();
  a.use('/x', router);
  a.get('/test-only/health', (req: any, res: any) => res.send('ok'));
});
