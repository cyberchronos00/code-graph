import 'dart:convert';

import 'package:http/http.dart' as http;

import '../config.dart';
import '../models/order.dart';

class OrderRepository {
  OrderRepository({required this.token});

  final String token;

  Future<Order> placeOrder(String email, List<OrderLine> lines, {bool isGift = false, String? note}) async {
    final response = await http.post(
      Uri.parse('$apiBase/api/orders/'),
      headers: {'Authorization': 'Bearer $token', 'Content-Type': 'application/json'},
      body: jsonEncode({
        'customerEmail': email,
        'lines': lines.map((l) => l.toJson()).toList(),
        'gift': isGift ? true : null,
        if (note != null) 'note': note,
      }),
    );
    if (response.statusCode != 200) {
      throw Exception('order failed: ${response.statusCode}');
    }
    return Order.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }
}
