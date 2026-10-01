const { Router } = require('express');
const controllers = require('../../controllers');

// a TypeScript module loaded with require(): `module.exports` (not `export`) is what require() returns
module.exports = function suppliersRoutes() {
  const router = Router();
  router.get('/', controllers.suppliers.list);
  router.post('/:id/orders', controllers.suppliers.placeOrder);
  return router;
};
