import Foundation

enum Config {
    static let apiBaseURL = URL(string: "https://api.example.com/v2")!
    static var serverURL: String {
        Bundle.main.object(forInfoDictionaryKey: "SERVER_URL") as? String ?? ""
    }
    static let authURL = Bundle.main.infoDictionary?["AUTH_URL"] as? String ?? ""
}
