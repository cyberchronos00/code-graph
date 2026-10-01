import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import '../config.dart';

class ReviewRepository {
  Future<List<dynamic>> list() async {
    final r = await http.get(Uri.parse('$apiBase/drf/reviews/'));
    return jsonDecode(r.body) as List<dynamic>;
  }

  Future<int> upvote(int reviewId) async {
    final r = await http.post(Uri.parse('$apiBase/drf/reviews/$reviewId/upvote/'));
    final data = jsonDecode(r.body) as Map<String, dynamic>;
    return data['upvotes'] as int;
  }

  Future<WebSocket> stockUpdates() => WebSocket.connect('$wsBase/ws/stock/');
}
