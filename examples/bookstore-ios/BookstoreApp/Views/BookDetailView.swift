import SwiftUI

struct BookDetailView: View {
    let store: BookStore
    let bookId: Int
    @State private var book: BookDTO?
    @State private var showCheckout = false

    var body: some View {
        VStack {
            Text(book?.title ?? "")
            Button("Buy") { showCheckout = true }
        }
        .task { book = await store.detail(bookId) }
        .sheet(isPresented: $showCheckout) {
            CheckoutView(store: store, bookId: bookId)
        }
    }
}
