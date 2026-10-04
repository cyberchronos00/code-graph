const { formatOrder } = require("./format");
const amqp = require("amqplib");

async function publishOrder(order) {
  const conn = await amqp.connect("amqp://localhost");
  const ch = await conn.createChannel();
  ch.sendToQueue("orders", Buffer.from(formatOrder(order)));
}

module.exports = { publishOrder };
