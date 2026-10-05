import Foundation

@objc public protocol HelperProtocol {
    func version(completion: @escaping (String) -> Void)
    func setFanSpeed(id: Int, value: Int)
}
