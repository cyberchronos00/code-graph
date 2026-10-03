import SwiftUI

final class Formatter {
    func format(amount: Int) -> String { "\(amount)" }
    func format(date: Int, style: Int = 0) -> String { "\(date)" }
    static func shared() -> Formatter { Formatter() }
}

final class Report {
    let f = Formatter()
    func render() -> String {
        f.format(amount: 1) + f.format(date: 2) + f.format(date: 3, style: 1)
    }
    func bad() -> String { f.format(currency: 1) }                        // no declaration takes currency:
}

extension View {
    func cardStyle(padding: Double = 8) -> some View { self.padding(padding) }
}

extension Font {
    func emphasized(_ on: Bool) -> Font { on ? self.bold() : self }
}

struct Card: View {
    var body: some View {
        Text("x").font(Font.body.emphasized(true)).cardStyle()               // project extensions: kept
    }
}
