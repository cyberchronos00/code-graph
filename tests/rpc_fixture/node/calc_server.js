const thrift = require("thrift");
const Calculator = require("./gen-nodejs/Calculator");

function divide(a, b, result) {
  result(null, a / b);
}

const server = thrift.createServer(Calculator, {
  // the handler map
  divide: divide,
  zip: function (result) {
    result(null);
  },
});

server.listen(9090);
