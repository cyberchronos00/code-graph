part of 'books_bloc.dart';

sealed class BooksEvent {
  const BooksEvent();
}

class LoadBooks extends BooksEvent {
  const LoadBooks({this.query = ''});
  final String query;
}

class RefreshBooks extends BooksEvent {
  const RefreshBooks();
}

class BookSelected extends BooksEvent {
  const BookSelected(this.bookId);
  final int bookId;
}
