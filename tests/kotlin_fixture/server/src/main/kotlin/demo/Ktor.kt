package demo

import io.ktor.server.application.Application
import io.ktor.server.routing.Route

class OrderService {
    fun find(id: String): String = id
    fun all(): List<String> = listOf()
}

fun Application.module() {
    val service = OrderService()
    routing {
        get("/health") {
            call.respondText("ok")
        }
        authenticate("jwt") {
            route("/v1/orders") {
                get("/{id}") {
                    call.respond(service.find(call.parameters["id"]!!))
                }
            }
        }
        adminRoutes(service)
    }
}

fun Route.adminRoutes(service: OrderService) {
    post("/admin/orders") {
        call.respond(service.all())
    }
}
