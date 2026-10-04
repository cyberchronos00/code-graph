package chat

import io.socket.client.Ack
import io.socket.client.IO
import io.socket.client.Socket
import io.socket.emitter.Emitter

class ChatClient(url: String) {
    private val socket: Socket = IO.socket(url)

    private val onMessage = Emitter.Listener { args -> println(args) }

    fun start() {
        socket.on(Socket.EVENT_CONNECT) { println("up") }
        socket.on("chat:message", onMessage)
        socket.on("room:joined") { args -> println(args) }
        socket.connect()
    }

    fun send(text: String) {
        socket.emit("chat:send", text)
        socket.emit("chat:history", text, Ack { rows -> println(rows) })
    }
}
