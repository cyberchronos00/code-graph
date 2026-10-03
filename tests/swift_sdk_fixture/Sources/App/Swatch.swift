import SwiftUI

enum Palette {
    static func accessibilityIdentifier(forCode code: String) -> String { "swatch.\(code)" }
    static func weight(forExtraIndex index: Int) -> Double { Double(index) / 10 }
}

struct Coordinator {
    func draw(in view: Int) {}
    func accessibilityLabel(role: String, hex: String) -> String { role + hex }
}

struct SwatchView: View {
    let code: String
    var body: some View {
        Canvas { context, size in
            context.draw(Text("x"), at: .zero)
        }
        .font(Font.system(size: 12).weight(.bold))
        .accessibilityLabel("canvas")
        .accessibilityIdentifier("canvas-id")
        .accessibilityIdentifier(Palette.accessibilityIdentifier(forCode: code))
    }
}
