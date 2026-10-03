import Foundation

final class OrdersClient {
    func orders() async throws -> Data {
        let url = Config.apiBaseURL.appendingPathComponent("orders")
        let (data, _) = try await URLSession.shared.data(from: url)
        return data
    }

    func profile() async throws -> Data {
        let url = URL(string: "\(Config.serverURL)/users/me")!
        let (data, _) = try await URLSession.shared.data(from: url)
        return data
    }

    func token() async throws -> Data {
        let url = URL(string: "\(Config.authURL)/token")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        let (data, _) = try await URLSession.shared.data(for: request)
        return data
    }

    func status() async throws -> Data {
        let url = URL(string: "https://api.example.com/v2/status")!
        let (data, _) = try await URLSession.shared.data(from: url)
        return data
    }
}
