package expo.modules.haptics

import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

class HapticsModule : Module() {
    override fun definition() = ModuleDefinition {
        Name("Haptics")

        Function("impact") { style: String ->
            vibrate(style)
        }

        AsyncFunction("selection") {
        }
    }

    private fun vibrate(style: String) {}
}
