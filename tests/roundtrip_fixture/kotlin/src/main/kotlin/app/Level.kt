package app

annotation class Composable

class LevelStore {
    var level: Float = 0f

    fun save(v: Float) {
        level = v.coerceIn(0f, 10f)
    }
}

@Composable
fun LevelPicker(store: LevelStore) {
    var value by remember { mutableStateOf(store.level) }
    Slider(value = value, onValueChange = { store.save(it) }, valueRange = 0f..100f)
}

fun <T> remember(f: () -> T): T = f()
fun <T> mutableStateOf(v: T): T = v
@Composable
fun Slider(value: Float, onValueChange: (Float) -> Unit, valueRange: ClosedFloatingPointRange<Float>) {}
