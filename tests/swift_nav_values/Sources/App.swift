import SwiftUI

enum RouterDestination: Hashable {
  case accountDetail(id: String)
  case list(list: String)
  case tags(tags: [String])
  case settings
}

enum OtherRoute: Hashable {
  case settings
  case about
}

@Observable final class RouterPath {
  var path: [RouterDestination] = []
  func navigate(to: RouterDestination) { path.append(to) }
}

extension View {
  func withAppRouter() -> some View {
    navigationDestination(for: RouterDestination.self) { destination in
      switch destination {
      case .accountDetail(let id):
        AccountDetailView(accountId: id)
      case .list(let list), .tags(let list):
        TimelineView(name: "\(list)")
      case .settings:
        SettingsView()
      }
    }
  }
  func withOtherRouter() -> some View {
    navigationDestination(for: OtherRoute.self) { r in
      switch r {
      case .settings: SettingsView()
      default: AboutView()
      }
    }
  }
}

struct AccountDetailView: View { let accountId: String; var body: some View { Text(accountId) } }
struct TimelineView: View { let name: String; var body: some View { Text(name) } }
struct SettingsView: View { var body: some View { Text("s") } }
struct AboutView: View { var body: some View { Text("a") } }

struct ListsView: View {
  @Environment(RouterPath.self) private var routerPath
  var body: some View {
    List {
      NavigationLink(value: RouterDestination.list(list: "x")) { Text("x") }
      Button("open") { routerPath.navigate(to: .accountDetail(id: "1")) }
      Button("about") { routerPath.navigate(to: .about) }
      Button("ambiguous") { routerPath.navigate(to: .settings) }
      Button("other") { routerPath.navigate(to: OtherRoute.settings) }
    }
  }
}

@main struct DemoApp: App {
  var body: some Scene { WindowGroup { NavigationStack { ListsView().withAppRouter() } } }
}
