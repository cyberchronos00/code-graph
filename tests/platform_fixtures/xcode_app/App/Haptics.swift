import UIKit

#if !os(visionOS)
func haptic() { UIImpactFeedbackGenerator(style: .light).impactOccurred() }
#else
func haptic() {}
#endif
