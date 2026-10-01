const db = require('../services/db');

module.exports = {
  async lowStock(req, res) {
    res.json(await db('stock_items').where('quantity', '<', 5));
  },
  async dailyMovements(req, res) {
    res.json(await db('stock_movements').select('*'));
  },
};
