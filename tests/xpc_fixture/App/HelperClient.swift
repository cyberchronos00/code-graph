import Foundation

class HelperClient {
    private var connection: NSXPCConnection?

    func connect() -> NSXPCConnection {
        let connection = NSXPCConnection(machServiceName: "com.example.Helper", options: .privileged)
        connection.remoteObjectInterface = NSXPCInterface(with: HelperProtocol.self)
        connection.resume()
        self.connection = connection
        return connection
    }

    func helper() -> HelperProtocol? {
        return connect().remoteObjectProxyWithErrorHandler({ error in
            print(error)
        }) as? HelperProtocol
    }

    func checkVersion() {
        helper()?.version { v in
            print(v)
        }
    }

    func speedUp() {
        guard let h = helper() else { return }
        h.setFanSpeed(id: 0, value: 3000)
    }

    func setFanSpeed(id: Int, value: Int) {
        helper()?.setFanSpeed(id: id, value: value)
    }

    func reset() {
        self.setFanSpeed(id: 0, value: 0)
    }

    func observe() {
        let center = CFNotificationCenterGetDarwinNotifyCenter()
        CFNotificationCenterAddObserver(center, nil, { _, _, _, _, _ in
            print("updated")
        }, "com.example.helper.updated" as CFString, nil, .deliverImmediately)
    }
}
