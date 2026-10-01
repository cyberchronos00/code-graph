const { randomUUID } = require('crypto');

exports.requestId = (req, res, next) => {
  res.set('x-request-id', randomUUID());
  next();
};
