package app

class Thumbnails {
    private val cache = HashMap<String, String>()

    fun thumbnail(url: String, size: Int): String {
        val rendered = render(url, size)
        cache.put(url, rendered)
        return rendered
    }

    fun badge(url: String, size: Int): String {
        val rendered = render(url, size)
        cache["$url-$size"] = rendered
        return rendered
    }

    fun put(url: String, image: String) {
        cache[url] = image
    }

    private fun render(url: String, size: Int): String = url + size
}
