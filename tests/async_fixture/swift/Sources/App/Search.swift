import SwiftUI

final class SearchModel: ObservableObject {
    @Published var results: [String] = []
    @Published var query = ""
    var generation = 0

    func search(_ q: String) {
        Task {
            let found = await fetch(q)
            results = found
        }
    }

    func searchGuarded(_ q: String) {
        generation += 1
        let gen = generation
        Task {
            let found = await fetch(q)
            if gen != generation { return }
            results = found
        }
    }

    func searchCancellable(_ q: String) {
        Task {
            let found = await fetch(q)
            if Task.isCancelled { return }
            results = found
        }
    }

    func fetch(_ q: String) async -> [String] { [q] }
}
