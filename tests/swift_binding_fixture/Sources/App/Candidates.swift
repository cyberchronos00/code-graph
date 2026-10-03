import SwiftUI
import UIKit

final class FeedStore {
    func reload(force: Bool) {}
}

#if os(iOS)
final class InboxStore {
    func reload(force: Bool) {}
}
#endif

enum Refresher {
    static func refreshAll(id: Int) {
        let store = registry.store(for: id)
        store.reload(force: true)
    }
}

enum Drawing {
    static func shapes() -> Data {
        UIBezierPath().close()
        let outline = Path { p in
            p.close()
        }
        _ = outline
        return UIGraphicsPDFRenderer(bounds: .zero).pdfData { _ in }
    }
}
