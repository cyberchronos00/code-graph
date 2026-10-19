package shelf

interface Labeled {
    val title: String
}

abstract class Card : Labeled {
    abstract val stock: Int
    open val note: String
        get() = "n"
}

class Tag : Labeled {
    override val title: String
        get() = "t"
}

class Note(override val title: String) : Card() {
    override val stock: Int
        get() = title.length

    override val note: String
        get() = title
}
