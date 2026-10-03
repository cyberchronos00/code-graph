package app

class FeedStore {
    fun reload(force: Boolean) {}
}

class InboxStore {
    fun reload(force: Boolean) {}
}

class Archive {
    fun restore(id: Int) {}
}

object Refresher {
    fun refreshAll(id: Int) {
        val store = registry.store(id)
        store.reload(true)
        storage.find(id).restore(id)
    }

    fun refreshFeed(feed: FeedStore) {
        feed.reload(false)
    }

    fun fromLibrary(id: Int, client: HttpClient) {
        LibraryCache.restore(id)
        client.reload(true)
    }
}

fun archiveFrom(id: Int) {
    Archive().restore(id)
}
