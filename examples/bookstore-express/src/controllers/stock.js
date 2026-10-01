const stockService = require('../services/stock-service');

exports.show = async (req, res) => {
  res.json(await stockService.level(req.params.sku));
};

exports.reserve = async (req, res) => {
  const { sku, quantity } = req.body;
  res.json(await stockService.reserve(sku, quantity));
};

exports.movements = async (req, res) => {
  res.json(await stockService.movements(req.params.sku));
};

exports.recordMovement = async (req, res) => {
  res.status(201).json(await stockService.recordMovement(req.params.sku, req.body));
};

exports.releaseReservation = (id) => stockService.release(id);
