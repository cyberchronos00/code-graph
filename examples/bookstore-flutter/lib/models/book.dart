import 'package:json_annotation/json_annotation.dart';

part 'book.g.dart';

enum BookFormat {
  @JsonValue('paperback')
  paperback,
  @JsonValue('hardcover')
  hardcover,
  @JsonValue('audiobook')
  audiobook,
}

@JsonSerializable(fieldRename: FieldRename.snake)
class Book {
  Book({required this.id, required this.title, required this.price, required this.format, required this.subtitle, required this.authorName});

  final int id;
  final String title;
  final double price;
  final BookFormat format;
  final String subtitle;
  final String authorName;

  factory Book.fromJson(Map<String, dynamic> json) => _$BookFromJson(json);

  Map<String, dynamic> toJson() => _$BookToJson(this);
}
