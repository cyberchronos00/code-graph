import Foundation
import Alamofire
#if os(iOS)
import UIKit
#endif

struct InventoryClient {
    func reserve(sku: String) {
        AF.request("https://inventory.example.com/v2/stock/\(sku)", method: .post)
    }

    func ping(base: String) {
        AF.request("\(base)/v1/ping")
    }
}

#if os(iOS)
func deviceName() -> String { UIDevice.current.name }
#elseif os(macOS)
func deviceName() -> String { Host.current().localizedName ?? "mac" }
#endif

func sharedGreeting() -> String { "hello" }

#if os(iOS)
final class OrdersViewController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        showDetail()
    }

    func showDetail() {
        navigationController?.pushViewController(OrderDetailController(), animated: true)
    }
}

final class OrderDetailController: UIViewController {
    override func viewDidLoad() { super.viewDidLoad() }
}
#endif
