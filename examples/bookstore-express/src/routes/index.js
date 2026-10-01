const { Router } = require('express');
const stockRouter = require('./stock');
const warehouseRouter = require('./warehouses');
const reportsRouter = require('./reports');
const suppliersRoutes = require('./suppliers');
const { authenticate } = require('../middleware/auth');

const router = Router();

router.use('/stock', stockRouter);
router.use('/warehouses', authenticate, warehouseRouter);
router.use('/reports', reportsRouter);
router.use('/suppliers', suppliersRoutes());   // a router built by a factory function

module.exports = router;
