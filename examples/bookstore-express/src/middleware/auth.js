function authenticate(req, res, next) {
  if (req.get('x-api-key') !== process.env.WAREHOUSE_API_KEY) return res.status(401).end();
  next();
}

module.exports = { authenticate };
