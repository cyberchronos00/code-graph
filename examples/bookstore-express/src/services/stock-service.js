const db = require('./db');

async function level(sku) {
  return db('stock_items').where({ sku }).first();
}

async function reserve(sku, quantity) {
  return db('reservations').insert({ sku, quantity });
}

async function movements(sku) {
  return db('stock_movements').where({ sku }).orderBy('created_at', 'desc');
}

async function recordMovement(sku, movement) {
  return db('stock_movements').insert({ sku, ...movement });
}

async function release(id) {
  return db('reservations').where({ id }).del();
}

module.exports = { level, reserve, movements, recordMovement, release };
