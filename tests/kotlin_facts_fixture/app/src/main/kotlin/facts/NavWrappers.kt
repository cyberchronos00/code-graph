package facts

import androidx.navigation.NavController
import androidx.navigation.NavGraphBuilder
import androidx.navigation.compose.composable
import kotlinx.serialization.Serializable

// project wrappers around typed destinations (#99): calls of the wrapper are typed pages
inline fun <reified T : Any> NavGraphBuilder.composableWithPushTransitions(
    noinline content: @Composable (NavBackStackEntry) -> Unit,
) {
    this.composable<T>(enterTransition = { slideIn() }) { content(it) }
}

inline fun <reified T : SettingsRoute> NavGraphBuilder.settingsDestination(noinline onBack: () -> Unit) {
    composableWithPushTransitions<T> { SettingsScreen(onBack) }
}

@Serializable object VaultRoute

@Serializable sealed class SettingsRoute {
    @Serializable data object Standard : SettingsRoute()
    @Serializable data object PreAuth : SettingsRoute()
}

@Serializable sealed class UnlockRoute {
    @Serializable data object Standard : UnlockRoute()
}

fun NavGraphBuilder.vaultDestination() {
    composableWithPushTransitions<VaultRoute> {
        VaultScreen()
    }
    settingsDestination<SettingsRoute.Standard>(onBack = {})
    settingsDestination<SettingsRoute.PreAuth>(onBack = {})
    composableWithPushTransitions<UnlockRoute.Standard> { UnlockScreen() }
}

fun NavController.navigateToVault() {
    this.navigate(route = VaultRoute, navOptions = null)
}

fun NavController.navigateToSettings() {
    navigate(route = SettingsRoute.PreAuth)
}

// a nested class of the same short name elsewhere does not capture the composable call
data class ScreenData(val x: Int) {
    data class VaultScreen(val id: Int)
}

fun VaultScreen() {}
fun SettingsScreen(onBack: () -> Unit) {}
fun UnlockScreen() {}
