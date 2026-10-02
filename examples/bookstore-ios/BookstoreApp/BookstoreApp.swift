import SwiftUI

@main
struct BookstoreApp: App {
    @State private var store = BookStore(api: BooksAPI())

    var body: some Scene {
        WindowGroup {
            BookListView(store: store)
        }
    }
}
