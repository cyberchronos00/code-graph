import Foundation
import Vapor

func routes(_ app: Application) throws {
    app.webSocket("echo") { req, ws in
        ws.send("hi")
    }
    app.get("health") { req in "ok" }
}

final class LiveClient {
    func connect() {
        let url = URL(string: "wss://chat.example.com/echo")!
        let task = URLSession.shared.webSocketTask(with: url)
        task.resume()
    }
}
