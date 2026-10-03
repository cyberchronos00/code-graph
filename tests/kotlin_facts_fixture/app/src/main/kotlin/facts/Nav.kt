package facts

import androidx.navigation.NavGraphBuilder
import androidx.navigation.compose.composable
import androidx.navigation.navDeepLink
import kotlinx.serialization.Serializable

@Serializable data class TopicRoute(val id: String)
@Serializable object SearchKey
@Serializable object ForYouKey

fun NavGraphBuilder.topicScreen() {
    composable<TopicRoute>(deepLinks = listOf(navDeepLink<TopicRoute>(basePath = "https://example.com/topic"))) {
        TopicScreen()
    }
}

fun entries(builder: EntryBuilder) = builder.apply {
    entry<SearchKey> { SearchScreen() }
    entry<ForYouKey>(metadata = mapOf("a" to 1)) { ForYouScreen(onTopic = { navigator.navigate(TopicRoute(it)) }) }
}

fun TopicScreen() {}
fun SearchScreen() {}
fun ForYouScreen(onTopic: (String) -> Unit) {}
