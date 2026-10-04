const jayson = require("jayson");

function add(args, callback) {
  callback(null, args[0] + args[1]);
}

const methods = {
  add: add,
  echo: function (args, callback) {
    callback(null, args);
  },
};

module.exports = new jayson.Server(methods);
