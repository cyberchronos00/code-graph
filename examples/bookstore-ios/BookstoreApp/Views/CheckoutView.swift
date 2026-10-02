import SwiftUI

struct CheckoutView: View {
    let store: BookStore
    let bookId: Int
    @State private var email = ""

    var body: some View {
        Form {
            TextField("Email", text: $email)
            Button("Place order") {
                Task { await store.checkout(bookId: bookId, email: email) }
            }
        }
    }
}
