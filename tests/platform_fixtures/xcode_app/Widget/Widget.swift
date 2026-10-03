import WidgetKit
import SwiftUI

// compiled only by the AppWidget extension target (iOS, no Catalyst)
struct AppWidgetEntryView: View {
    var body: some View { Text(sharedTitle()) }
}

// an extension of an SDK type in a widget-only file: the type exists everywhere, its member only on iOS
extension URL {
    var widgetDeepLink: URL { self }
}
