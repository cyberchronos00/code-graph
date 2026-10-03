import Capacitor
import Foundation

@objc(EchoPlugin)
public class EchoPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "EchoPlugin"
    public let jsName = "Echo"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "echo", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "ping", returnType: CAPPluginReturnNone)
    ]

    @objc func echo(_ call: CAPPluginCall) {
        call.resolve(["value": call.getString("value") ?? ""])
    }

    @objc func ping(_ call: CAPPluginCall) {
        call.resolve()
    }
}
