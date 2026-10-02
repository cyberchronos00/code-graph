# bookstore-ios

A small SwiftUI client (NavigationStack, sheets, URLSession) for the [`bookstore-django`](../bookstore-django) API.
It is indexed by cg's Swift plugin and linked to the Django sample:

```bash
cg index examples/bookstore-django --db /tmp/dj.db
cg index examples/bookstore-ios    --db /tmp/ios.db
cg link --backend /tmp/dj.db --frontend /tmp/ios.db --db /tmp/ios-link.db
cg path "page:swift:CheckoutView" "table:catalog_order" --db /tmp/ios-link.db
```

- `BookstoreApp.swift`: the `@main` SwiftUI `App` with its `WindowGroup` root view.
- `API/BooksAPI.swift`: URLSession calls (`data(from:)`, `URLRequest` with `httpMethod = "POST"`).
- `Views/`: `NavigationLink(destination:)` and `.sheet { }` navigation between the screens.

The app is not meant to be built; it only needs to be a realistic input for the indexer.
