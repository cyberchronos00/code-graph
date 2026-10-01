const db = require('../services/db');

// an ES module: the registry picks the named export with require('./suppliers').controller
export const controller = {
  async list(req: any, res: any) {
    res.json(await db('suppliers').select('*'));
  },
  async placeOrder(req: any, res: any) {
    res.json(await db('supplier_orders').insert({ supplier_id: req.params.id }));
  },
};
