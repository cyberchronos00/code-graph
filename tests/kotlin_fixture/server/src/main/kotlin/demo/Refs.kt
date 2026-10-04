package demo

class Navigator

fun Navigator.navigateToTopic(id: String) {}

class VM {
    fun followTopic(id: String, on: Boolean) {}
}

class Widget {
    fun go(a: Int) {}
    fun go(a: Int, b: Int) {}
}

class Foo(val n: Int)

fun helper(x: Int) {}

fun take1(cb: (Int) -> Unit) {}

fun take2(cb: (Int, Int) -> Unit) {}

fun take0(cb: () -> Unit) {}

fun screen(navigator: Navigator, viewModel: VM = hiltViewModel()) {
    onTopic(navigator::navigateToTopic)
    row(viewModel::followTopic)
    direct(hiltViewModel<VM>()::followTopic)
    run(::helper)
    make(::Foo)
    fun local(n: Int) {}
    run(::local)
}

fun onTopic(cb: (String) -> Unit) {}

fun row(cb: (String, Boolean) -> Unit) {}

fun direct(cb: (String, Boolean) -> Unit) {}

fun run(cb: (Int) -> Unit) {}

fun make(cb: (Int) -> Foo) {}

fun callsOne(w: Widget) {
    take1(w::go)
}

fun callsBoth(w: Widget) {
    val x = w::go
}

fun callsNone(w: Widget) {
    take0(w::go)
}

class Screen {
    val viewModel: VM by viewModels()
    fun wire() {
        row(viewModel::followTopic)
    }
}
