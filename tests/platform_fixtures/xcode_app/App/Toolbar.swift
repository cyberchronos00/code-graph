import SwiftUI

struct ToolbarItems {
#if targetEnvironment(macCatalyst)
    func close() { print("catalyst close") }
#else
    func close() { print("close") }
#endif
}

#if os(macOS)
// an AppKit-only path: the app builds for the Mac through Catalyst, where os(macOS) is false
func appKitOnly() {}
#endif

#if os(iOS)
// true on the Catalyst build too
func iosAndCatalyst() {}
#endif
