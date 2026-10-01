const db = require('../services/db');

module.exports = {
  async list(req, res) {
    res.json(await db('warehouses').select('*'));
  },
  async update(req, res) {
    await db('warehouses').where({ id: req.params.id }).update(req.body);
    res.status(204).end();
  },
};
