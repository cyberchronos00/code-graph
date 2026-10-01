import 'package:flutter/material.dart';

import 'author_page.dart';
import 'page_route.dart';

class BookReviewsPage extends StatelessWidget {
  const BookReviewsPage({super.key, required this.bookId});

  final int bookId;

  @override
  Widget build(BuildContext context) {
    return TextButton(
      // imperative navigation through a project route helper
      onPressed: () => Navigator.of(context).push(buildPageRoute(page: const AuthorPage())),
      child: Text('Reviews of book $bookId'),
    );
  }
}
