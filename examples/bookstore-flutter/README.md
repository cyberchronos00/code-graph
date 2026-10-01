# bookstore-flutter (sample)

A small, fictional Flutter client for `examples/bookstore-django`, used by the tests and as a quick demo of the
Dart/Flutter plugin and `link`. It is never built or run; the extractor only parses it.

It contains **deliberate contract drift** so the payload check has something to report:

| where (client) | drift | expected issue |
|---|---|---|
| `BookRepository.fetchBook` | `/books/$id` without trailing slash (ninja route has one) | `trailing_slash` |
| `BookRepository.fetchReviews` | calls `/books/{id}/reviews/`, which does not exist | unmatched endpoint |
| `Book` (`book.g.dart`) | reads `author_name` (server sends a nested `author`) | `response_missing_key` |
| `Book.subtitle` | non-nullable, server field is `null=True` | `response_nullability` |
| `Book.price` | `num`, a `DecimalField` serialises as a string | `response_type` |
| `BookFormat` | has `audiobook`, misses `ebook` | `response_enum_values` |
| `OrderRepository.placeOrder` | sends `customerEmail` (server: `customer_email`) | `request_case_mismatch` |
| `OrderRepository.placeOrder` | `'gift': isGift ? true : null` sends an explicit null to a `bool` field | `request_nullability` |
| `Order.fromJson` | reads `total_cents` (server: `total`) | `response_missing_key` |
| `BooksBloc` | `BookSelected` dispatched without handler, `RefreshBooks` never dispatched, `failure` not handled by the UI | `state_flow` |
