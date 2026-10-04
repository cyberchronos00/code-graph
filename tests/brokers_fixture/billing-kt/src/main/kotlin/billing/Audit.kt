package billing

import com.rabbitmq.client.ConnectionFactory
import io.nats.client.Nats

val auditExchange: String = System.getenv("AUDIT_EXCHANGE") ?: "audit"

fun publishAudit(region: String, msg: String) {
    val channel = ConnectionFactory().newConnection().createChannel()
    channel.basicPublish(auditExchange, "order.$region.audited", null, msg.toByteArray())
    val nc = Nats.connect("nats://nats:4222")
    nc.publish("stock.check.${region}", msg.toByteArray())
}
