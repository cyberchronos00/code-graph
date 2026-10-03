import SwiftUI

struct Panel: View {
    @State private var gate = SlotGate<Image>()
    let client = SomeClient()

    var body: some View {
        Text("x").onDisappear { gate.cancel() }
    }

    func appear() {
        gate.requestFull()
    }

    func check(a: Int, b: Int) -> Bool {
        if !Chrome.shouldAutoPresent() { return false }
        return !Preview.matches(a, b)
    }

    func local() {
        let g = SlotGate<String>()
        g.cancel()
    }

    func untyped() {
        var inside = false
        inside.toggle()
        let path = Path()
        path.close()
        let renderer = UIGraphicsPDFRenderer(bounds: .zero)
        _ = renderer.pdfData { _ in }
        client.close()
    }

    func unknown(hud: Any) {
        mystery.toggle()
    }

    func compare(x: Int, a: Int) -> Bool {
        let ok = a
            < Scorer.score(x)
        let sum = a
            + Scorer.score(x)
        return ok && sum > 0
    }
}
