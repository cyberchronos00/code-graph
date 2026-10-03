import Foundation
import UIKit

final class VideoModel {
    func resume() {}
    func pause() {}

    func load() async -> Int {
        await withCheckedContinuation { continuation in
            continuation.resume(returning: 1)                   // CheckedContinuation.resume(returning:): no edge
        }
    }

    func restart() { resume() }                                  // the real caller
}

final class Client {
    func post(endpoint: String) {}
    static func make() -> Client { Client() }
    func send() { post(endpoint: "/x") }                         // real
}

final class SafariManager {
    func open(_ url: URL) {}
}

final class Notifier {
    let client = Client()
    func announce(_ url: URL) {
        NotificationCenter.default.post(name: .init("x"), object: nil)   // Foundation: no edge
        UIApplication.shared.open(url)                                    // UIKit: no edge
        client.post(endpoint: "/y")                                       // typed: kept
        let c = Client.make()                                             // static on the type: kept
        c.send()
    }

    func wrong(_ c: Client) {
        _ = c.make()                                                      // static member on a value: no edge
    }
}
