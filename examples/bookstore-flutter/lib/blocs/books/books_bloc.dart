import 'package:flutter_bloc/flutter_bloc.dart';

import '../../models/book.dart';
import '../../repositories/book_repository.dart';

part 'books_event.dart';
part 'books_state.dart';

class BooksBloc extends Bloc<BooksEvent, BooksState> {
  BooksBloc(this._repository) : super(const BooksState()) {
    on<LoadBooks>(_onLoad);
    on<RefreshBooks>(_onRefresh);
  }

  final BookRepository _repository;

  Future<void> _onLoad(LoadBooks event, Emitter<BooksState> emit) async {
    emit(state.copyWith(status: BooksStatus.loading));
    try {
      final books = await _repository.fetchBooks(query: event.query);
      emit(state.copyWith(status: BooksStatus.success, books: books));
    } catch (_) {
      emit(state.copyWith(status: BooksStatus.failure));
    }
  }

  Future<void> _onRefresh(RefreshBooks event, Emitter<BooksState> emit) async {
    final books = await _repository.fetchBooks();
    emit(state.copyWith(status: BooksStatus.success, books: books));
  }
}
