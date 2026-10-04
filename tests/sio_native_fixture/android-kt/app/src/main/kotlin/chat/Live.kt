package chat

import io.ktor.client.HttpClient
import io.ktor.client.plugins.websocket.webSocket
import io.ktor.server.application.Application
import io.ktor.server.routing.route
import io.ktor.server.routing.routing
import io.ktor.server.websocket.webSocket
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.WebSocketListener

fun Application.liveModule() {
    routing {
        route("/api") {
            webSocket("/chat") {
                send("hello")
            }
        }
    }
}

class LiveClient(private val client: HttpClient, private val ok: OkHttpClient) {
    suspend fun chat() {
        client.webSocket("ws://chat.example.com/api/chat") {
            send("hi")
        }
    }

    fun live(listener: WebSocketListener) {
        val request = Request.Builder().url("wss://chat.example.com/live").build()
        ok.newWebSocket(request, listener)
    }

    fun feed() {
        val request = Request.Builder().url("https://chat.example.com/feed").build()
        ok.newCall(request).execute()
    }
}
