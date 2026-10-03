import SwiftUI

@main
struct AppMain: App {
    var body: some Scene {
        WindowGroup { ContentView() }
    }
}

struct ContentView: View {
    let items = ToolbarItems()
    var body: some View {
        Button("Close") {
            items.close()
            haptic()
            sharedTitle()
        }
    }
}
