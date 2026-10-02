import express from 'express';

export const app = express();

export function listOrders(req: any, res: any) {
  res.json([]);
}

app.get('/orders', listOrders);
app.post('/orders', (req: any, res: any) => {
  res.status(201).end();
});
