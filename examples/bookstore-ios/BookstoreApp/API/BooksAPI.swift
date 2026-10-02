import Foundation

struct BookDTO: Codable, Identifiable {
    let id: Int
    let title: String
    let price: String
}

struct OrderLine: Codable { let bookId: Int; let quantity: Int }
struct OrderIn: Codable { let customerEmail: String; let lines: [OrderLine] }
struct OrderOut: Codable { let id: Int; let total: String }

/// URLSession client for the bookstore-django API.
final class BooksAPI {
    // the iOS simulator reaches the development server on the host as localhost
    let baseURL = URL(string: "http://localhost:8000")!
    let session = URLSession.shared

    func books() async throws -> [BookDTO] {
        let url = baseURL.appendingPathComponent("api/books/")
        let (data, _) = try await session.data(from: url)
        return try JSONDecoder().decode([BookDTO].self, from: data)
    }

    func book(_ id: Int) async throws -> BookDTO {
        let url = URL(string: "\(baseURL)/api/books/\(id)/")!
        let (data, _) = try await URLSession.shared.data(from: url)
        return try JSONDecoder().decode(BookDTO.self, from: data)
    }

    func placeOrder(_ order: OrderIn) async throws -> OrderOut {
        var request = URLRequest(url: baseURL.appendingPathComponent("api/orders/"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(order)
        let (data, _) = try await session.data(for: request)
        return try JSONDecoder().decode(OrderOut.self, from: data)
    }
}
