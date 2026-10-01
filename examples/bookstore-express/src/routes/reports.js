const { Router } = require('express');
const controllers = require('../controllers');

// a chain owned by the declaration, and handlers looked up through a lazy controller registry
const router = Router()
  .get('/low-stock', controllers.reports.lowStock)
  .get('/movements/daily', controllers.reports.dailyMovements);

module.exports = router;
