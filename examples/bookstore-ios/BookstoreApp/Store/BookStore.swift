import Foundation
import Observation

@Observable
final class BookStore {
    let api: BooksAPI
    var books: [BookDTO] = []
    var lastOrder: OrderOut?

    init(api: BooksAPI) {
        self.api = api
    }

    func refresh() async {
        books = (try? await api.books()) ?? []
    }

    func detail(_ id: Int) async -> BookDTO? {
        try? await api.book(id)
    }

    func checkout(bookId: Int, email: String) async {
        let order = OrderIn(customerEmail: email, lines: [OrderLine(bookId: bookId, quantity: 1)])
        lastOrder = try? await api.placeOrder(order)
    }
}
