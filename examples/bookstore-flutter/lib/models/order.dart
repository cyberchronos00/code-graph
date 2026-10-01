class OrderLine {
  OrderLine(this.bookId, this.quantity);

  final int bookId;
  final int quantity;

  Map<String, dynamic> toJson() => {'book_id': bookId, 'quantity': quantity};
}

class Order {
  Order({required this.id, required this.status, required this.totalCents});

  final int id;
  final String status;
  final int totalCents;

  factory Order.fromJson(Map<String, dynamic> json) => Order(
        id: json['id'] as int,
        status: json['status'] as String,
        totalCents: json['total_cents'] as int,
      );
}
