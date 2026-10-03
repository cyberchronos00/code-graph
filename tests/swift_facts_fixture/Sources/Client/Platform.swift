import Foundation

#if os(iOS)
func deviceKind() -> String { "phone" }
#else
func deviceKind() -> String { "computer" }
#endif

@available(macOS, unavailable)
struct Haptics {
    func play() {}
}

@available(iOS 17, macOS 14, *)
func newAPI() {}

func describe() -> String {
    deviceKind()
}
