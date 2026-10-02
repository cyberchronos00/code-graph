import express from 'express';
import request from 'supertest';
import { app, listOrders } from '../src/app';

// a small app built for the test: its routes are test setup, not application routes
const testApp = express();
testApp.get('/test-only/ping', (req: any, res: any) => res.send('pong'));
testApp.use('/mounted', express.Router().get('/inner', listOrders));

describe('orders', () => {
  it('lists orders', async () => {
    await request(app).get('/orders').expect(200);
  });
});
