const { withTiming } = require('../middleware/timing');

/** Lazy controller registry: modules are loaded on first use. */
module.exports = {
  get reports() {
    return withTiming(require('./reports'));
  },
  get suppliers() {
    return withTiming(require('./suppliers').controller);
  },
};
