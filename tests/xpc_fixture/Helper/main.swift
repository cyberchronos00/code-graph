import Foundation

let helperNotification = "com.example.helper.updated"

class Helper: NSObject, NSXPCListenerDelegate, HelperProtocol {
    private let listener: NSXPCListener

    override init() {
        self.listener = NSXPCListener(machServiceName: "com.example.Helper")
        super.init()
        self.listener.delegate = self
    }

    func listener(_ listener: NSXPCListener, shouldAcceptNewConnection newConnection: NSXPCConnection) -> Bool {
        newConnection.exportedInterface = NSXPCInterface(with: HelperProtocol.self)
        newConnection.exportedObject = self
        newConnection.resume()
        return true
    }

    func version(completion: @escaping (String) -> Void) {
        completion("1.0")
    }

    func setFanSpeed(id: Int, value: Int) {
        let center = CFNotificationCenterGetDarwinNotifyCenter()
        CFNotificationCenterPostNotification(center, CFNotificationName(helperNotification as CFString), nil, nil, true)
    }
}
