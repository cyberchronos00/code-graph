import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:go_router/go_router.dart';

import 'api/api_client.dart';
import 'blocs/books/books_bloc.dart';
import 'pages/book_detail_page.dart';
import 'pages/book_reviews_page.dart';
import 'pages/books_page.dart';
import 'repositories/book_repository.dart';

final router = GoRouter(
  routes: [
    GoRoute(path: '/', builder: (context, state) => const BooksPage()),
    GoRoute(
      path: '/books/:id',
      builder: (context, state) => BookDetailPage(bookId: int.parse(state.pathParameters['id']!)),
      routes: [
        // nested route: the full path is /books/:id/reviews; the Page wrapper is looked through to its child
        GoRoute(
          path: 'reviews',
          pageBuilder: (context, state) => NoTransitionPage(
            child: BookReviewsPage(bookId: int.parse(state.pathParameters['id']!)),
          ),
        ),
      ],
    ),
  ],
);

void main() {
  runApp(
    BlocProvider(
      create: (_) => BooksBloc(BookRepository(ApiClient())),
      child: MaterialApp.router(routerConfig: router),
    ),
  );
}
