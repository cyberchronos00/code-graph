const jayson = require("jayson");

function sum(a, b) {
  const client = jayson.client.http({ port: 3000 });
  client.request("add", [a, b], () => undefined);
}

module.exports = { sum };
