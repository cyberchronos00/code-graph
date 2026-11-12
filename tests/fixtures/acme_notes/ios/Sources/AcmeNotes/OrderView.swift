import SwiftUI

struct OrderView: View {
    var body: some View {
        Text("acme_orders.submit")
    }

    func cancelLabel() -> String {
        String(localized: "acme_orders.cancel")
    }

    func noteLabel() -> String {
        NSLocalizedString("acme_orders.note", comment: "order note")
    }
}
