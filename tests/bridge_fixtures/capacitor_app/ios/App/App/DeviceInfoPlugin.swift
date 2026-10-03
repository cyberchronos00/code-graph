import Capacitor

@objc(DeviceInfoPlugin)
public class DeviceInfoPlugin: CAPPlugin {
    @objc func getInfo(_ call: CAPPluginCall) {
        call.resolve()
    }
}
