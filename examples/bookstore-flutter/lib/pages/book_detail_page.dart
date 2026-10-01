import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models/book.dart';
import '../repositories/book_repository.dart';

class BookDetailPage extends StatelessWidget {
  const BookDetailPage({super.key, required this.bookId});

  final int bookId;

  @override
  Widget build(BuildContext context) {
    final repo = BookRepository(ApiClient());
    return FutureBuilder<Book>(
      future: repo.fetchBook(bookId),
      builder: (context, snap) => Text(snap.data?.title ?? '...'),
    );
  }
}
