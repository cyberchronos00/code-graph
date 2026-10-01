const express = require('express');
const warehouses = require('../controllers/warehouses');

const router = express.Router();

router.get('/', warehouses.list);
router.put('/:id', warehouses.update);

module.exports = router;
