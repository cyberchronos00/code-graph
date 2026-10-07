package hooks

import io.ktor.server.application.Application
import io.ktor.server.routing.routing
import io.ktor.server.routing.post

fun deploy() {}
fun paid() {}

fun Application.module() {
    routing {
        post("/webhooks/github") {
            val eventName = call.request.headers["X-GitHub-Event"]
            when (eventName) {
                "push" -> deploy()
                "issues", "pull_request" -> paid()
                else -> Unit
            }
            val mode = "sync"
            when (mode) {
                "sync" -> deploy()
                "async" -> paid()
            }
            when (eventName.length) {
                "short" -> deploy()
                "long" -> paid()
            }
        }
        post("/webhooks/stripe") {
            val sig = call.request.headers["Stripe-Signature"]
            val event = mapOf("type" to "checkout.session.completed")
            when (event["type"]) {
                "checkout.session.completed" -> deploy()
                "invoice.paid" -> paid()
                else -> sig
            }
        }
    }
}
