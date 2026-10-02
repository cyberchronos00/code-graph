# bookstore-android

A small Kotlin Android client (Jetpack Compose + Navigation, Retrofit, WorkManager) for the
[`bookstore-django`](../bookstore-django) API. It is indexed by cg's Kotlin plugin and linked to the Django sample:

```bash
cg index examples/bookstore-django  --db /tmp/dj.db
cg index examples/bookstore-android --db /tmp/android.db
cg link --backend /tmp/dj.db --frontend /tmp/android.db --db /tmp/android-link.db
cg path "page:kotlin:checkout/{bookId}" "table:catalog_order" --db /tmp/android-link.db
```

- `api/BooksApi.kt`: Retrofit interface (`@GET("api/books/")` ...) with the base URL from `Retrofit.Builder().baseUrl(...)`.
- `ui/Screens.kt`: Compose Navigation destinations (`composable("books/{bookId}")`) and `navController.navigate(...)`.
- `AndroidManifest.xml`: the launcher activity with a deep link, and a broadcast receiver.
- `work/SyncWorker.kt`: a `CoroutineWorker` (a `queue_job` entry point).

The app is not meant to be built; it only needs to be a realistic input for the indexer.
