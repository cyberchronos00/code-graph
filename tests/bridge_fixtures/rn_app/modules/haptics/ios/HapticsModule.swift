import ExpoModulesCore

public class HapticsModule: Module {
    public func definition() -> ModuleDefinition {
        Name("Haptics")

        Function("impact") { (style: String) in
            UIImpactFeedbackGenerator().impactOccurred()
        }
    }
}
