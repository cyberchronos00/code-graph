import Foundation

final class UsersAPI {
  func user(id: Int) async throws -> Data {
    var components = URLComponents(string: "https://api.example.com")!
    components.path = "/v1/users/\(id)"
    components.queryItems = [URLQueryItem(name: "full", value: "1")]
    let (data, _) = try await URLSession.shared.data(from: components.url!)
    return data
  }

  func search(q: String) async throws -> Data {
    var c = URLComponents()
    c.scheme = "https"
    c.host = "search.example.com"
    c.path = "/search"
    var req = URLRequest(url: c.url!)
    req.httpMethod = "POST"
    let (data, _) = try await URLSession.shared.data(for: req)
    return data
  }
}
