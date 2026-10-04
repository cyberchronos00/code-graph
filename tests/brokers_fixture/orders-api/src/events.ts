import { Kafka } from "kafkajs";
import * as amqp from "amqplib";
import Redis from "ioredis";
import { Topics, EXCHANGE } from "./topics";

const kafka = new Kafka({ clientId: "orders", brokers: ["kafka:9092"] });
const producer = kafka.producer();
const redis = new Redis(process.env.REDIS_URL);

export async function orderCreated(order: { id: string; region: string }) {
  await producer.send({ topic: Topics.OrderCreated, messages: [{ key: order.id, value: JSON.stringify(order) }] });
  const ch = await (await amqp.connect("amqp://rabbit")).createChannel();
  await ch.assertExchange(EXCHANGE, "topic", { durable: true });
  ch.publish(EXCHANGE, `order.${order.region}.created`, Buffer.from(JSON.stringify(order)));
  ch.sendToQueue("invoices", Buffer.from(order.id));
  await redis.publish("cache:invalidate", order.id);
}

export async function orderCancelled(id: string) {
  await producer.sendBatch({ topicMessages: [{ topic: Topics.OrderCancelled, messages: [{ value: id }] }] });
  await producer.send({ topic: process.env.AUDIT_TOPIC ?? "audit.orders", messages: [{ value: id }] });
}
