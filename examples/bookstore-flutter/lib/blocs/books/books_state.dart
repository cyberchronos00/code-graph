part of 'books_bloc.dart';

enum BooksStatus { initial, loading, success, failure }

class BooksState {
  const BooksState({this.status = BooksStatus.initial, this.books = const []});

  final BooksStatus status;
  final List<Book> books;

  BooksState copyWith({BooksStatus? status, List<Book>? books}) =>
      BooksState(status: status ?? this.status, books: books ?? this.books);
}
