import SwiftUI

struct BookListView: View {
    let store: BookStore

    var body: some View {
        NavigationStack {
            List(store.books) { book in
                NavigationLink(destination: BookDetailView(store: store, bookId: book.id)) {
                    Text(book.title)
                }
            }
            .navigationTitle("Books")
            .task { await store.refresh() }
        }
    }
}
