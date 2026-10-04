import SwiftUI

struct DetailView: View {
    @State private var title = ""
    let seed: String

    var body: some View {
        Text(title)
            .onAppear { title = seed }
            .onTapGesture {
                Task {
                    let t = await load(seed)
                    title = t
                }
            }
    }

    func load(_ s: String) async -> String { s }
}
