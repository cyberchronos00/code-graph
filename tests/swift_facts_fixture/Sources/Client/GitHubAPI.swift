import Foundation
import Moya

enum GitHub {
    case zen
    case userProfile(String)
    case repos(owner: String), createIssue(repo: String, title: String)
}

extension GitHub: TargetType {
    var baseURL: URL { URL(string: "https://api.github.com")! }

    var path: String {
        switch self {
        case .zen:
            return "/zen"
        case .userProfile(let name):
            return "/users/\(name.urlEscaped)"
        case .repos(let owner):
            return "/users/\(owner)/repos"
        case .createIssue(let repo, _):
            return "/repos/\(repo)/issues"
        }
    }

    var method: Moya.Method {
        switch self {
        case .createIssue:
            return .post
        default:
            return .get
        }
    }

    var task: Task { .requestPlain }
    var headers: [String: String]? { nil }
}

enum Status: TargetType {
    case health
    var baseURL: URL { URL(string: "https://status.example.com/api")! }
    var path: String { "/health" }
    var method: Moya.Method { .get }
    var task: Task { .requestPlain }
    var headers: [String: String]? { nil }
}

final class GitHubClient {
    let provider = MoyaProvider<GitHub>()
    private let status: MoyaProvider<Status> = MoyaProvider<Status>()

    func loadZen() {
        provider.request(.zen) { _ in }
    }

    func open(repo: String) {
        provider.request(.createIssue(repo: repo, title: "bug")) { _ in }
    }

    func check() {
        status.request(.health) { _ in }
    }
}

enum Config {
    static let apiBase = URL(string: ProcessInfo.processInfo.environment["API_BASE"] ?? "")!
}

/// Talks to the Vapor app in Sources/App.
enum TodoAPI: TargetType {
    case list, add(title: String)
    case remove(id: UUID)
    var baseURL: URL { Config.apiBase }
    var path: String {
        switch self {
        case .list, .add: "/todos"
        case .remove(let id): "/todos/\(id)"
        }
    }
    var method: Moya.Method {
        switch self {
        case .list: .get
        case .add: .post
        case .remove: .delete
        }
    }
    var task: Task { .requestPlain }
    var headers: [String: String]? { nil }
}

struct TodoClient {
    let api: MoyaProvider<TodoAPI>

    func all() async {
        _ = try? await api.requestPublisher(.list)
    }

    func remove(_ id: UUID) {
        api.request(.remove(id: id)) { _ in }
    }
}
