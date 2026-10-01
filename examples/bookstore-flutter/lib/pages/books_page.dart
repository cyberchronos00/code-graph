import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:go_router/go_router.dart';

import '../blocs/books/books_bloc.dart';

class BooksPage extends StatefulWidget {
  const BooksPage({super.key});

  @override
  State<BooksPage> createState() => _BooksPageState();
}

class _BooksPageState extends State<BooksPage> {
  @override
  void initState() {
    super.initState();
    context.read<BooksBloc>().add(const LoadBooks());
  }

  @override
  Widget build(BuildContext context) {
    return BlocBuilder<BooksBloc, BooksState>(
      builder: (context, state) {
        if (state.status == BooksStatus.loading) {
          return const Center(child: CircularProgressIndicator());
        }
        return ListView(
          children: [
            for (final b in state.books)
              ListTile(
                title: Text(b.title),
                onTap: () {
                  context.read<BooksBloc>().add(BookSelected(b.id));
                  context.go('/books/${b.id}');
                },
              ),
          ],
        );
      },
    );
  }
}
