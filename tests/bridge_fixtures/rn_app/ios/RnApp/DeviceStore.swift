import Foundation

@objc(DeviceStore)
class DeviceStore: NSObject {
    @objc func getItem(_ key: String) -> String? {
        return UserDefaults.standard.string(forKey: key)
    }
}
