import '../api/api_client.dart';
import '../models/book.dart';

class BookRepository {
  BookRepository(this._client);

  final ApiClient _client;

  Future<List<Book>> fetchBooks({String query = ''}) async {
    final res = await _client.dio.get('/books/', queryParameters: {'q': query});
    return (res.data as List).map((e) => Book.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<Book> fetchBook(int id) async {
    final res = await _client.dio.get('/books/$id');
    return Book.fromJson(res.data as Map<String, dynamic>);
  }

  Future<List<dynamic>> fetchReviews(int bookId) async {
    final res = await _client.dio.get('/books/$bookId/reviews/');
    return res.data as List<dynamic>;
  }
}
