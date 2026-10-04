package billing

import org.springframework.amqp.rabbit.annotation.Exchange
import org.springframework.amqp.rabbit.annotation.Queue
import org.springframework.amqp.rabbit.annotation.QueueBinding
import org.springframework.amqp.rabbit.annotation.RabbitListener
import org.springframework.amqp.core.ExchangeTypes
import org.springframework.kafka.annotation.KafkaListener
import org.springframework.kafka.core.KafkaTemplate
import org.springframework.stereotype.Component

object Topics {
    const val INVOICES = "billing.invoices"
}

@Component
class Listeners(private val kafka: KafkaTemplate<String, String>) {
    @KafkaListener(topics = ["orders.created"], groupId = "billing")
    fun onOrderCreated(payload: String) {
        kafka.send(Topics.INVOICES, payload)
    }

    @RabbitListener(
        bindings = [QueueBinding(
            value = Queue("billing.region"),
            exchange = Exchange("shop.events", type = ExchangeTypes.TOPIC),
            key = ["order.eu.*"],
        )]
    )
    fun onRegionOrder(body: String) {
    }

    @RabbitListener(queues = ["invoices"])
    fun onInvoice(body: String) {
    }
}
