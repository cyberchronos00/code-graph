import SwiftUI

func fitToGamut(_ v: Double) -> Double { min(max(v, 0), 10) }

final class LevelStore {
    var level: Double = 0
    var tint: Double = 0
    var plain: Double = 0

    func save(_ v: Double) {
        level = fitToGamut(v)
    }

    func saveTint(_ v: Double) {
        let c = v.clamped(to: 0...5)
        tint = c
    }

    func setPlain(_ v: Double) {
        plain = v
    }
}

struct LevelPicker: View {
    let store: LevelStore
    @State private var value: Double

    init(store: LevelStore) {
        self.store = store
        _value = State(initialValue: store.level)
    }

    var body: some View {
        Slider(value: $value, in: 0...100)
            .onChange(of: value) { store.save($0) }
    }
}

func reset(_ s: LevelStore) {
    s.setPlain(round(3.7))
}
