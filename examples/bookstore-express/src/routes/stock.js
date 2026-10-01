const express = require('express');
const stock = require('../controllers/stock');
const { asyncHandler } = require('../middleware/async-handler');
const { authenticate } = require('../middleware/auth');

const router = express.Router();

router.get('/:sku', stock.show);
router.post('/reserve', authenticate, asyncHandler(stock.reserve));
router
  .route('/:sku/movements')
  .get(stock.movements)
  .post(authenticate, stock.recordMovement);
router.delete('/reservations/:id', authenticate, async (req, res) => {
  await stock.releaseReservation(req.params.id);
  res.status(204).end();
});

module.exports = router;
