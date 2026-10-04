import Foundation
import SocketIO

final class ChatClient {
    let manager = SocketManager(socketURL: URL(string: "https://chat.example.com")!, config: [.log(false)])
    var socket: SocketIOClient!
    var admin: SocketIOClient!

    func start() {
        socket = manager.defaultSocket
        admin = manager.socket(forNamespace: "/admin")
        socket.on(clientEvent: .connect) { _, _ in print("up") }
        socket.on("chat:message") { data, ack in
            print(data)
        }
        admin.on("kick") { data, _ in print(data) }
        socket.connect()
    }

    func send(_ text: String) {
        socket.emit("chat:send", text)
        socket.emitWithAck("chat:history", text).timingOut(after: 2) { rows in print(rows) }
    }
}
