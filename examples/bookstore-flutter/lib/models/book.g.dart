// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'book.dart';

Book _$BookFromJson(Map<String, dynamic> json) => Book(
      id: (json['id'] as num).toInt(),
      title: json['title'] as String,
      price: (json['price'] as num).toDouble(),
      format: $enumDecode(_$BookFormatEnumMap, json['format']),
      subtitle: json['subtitle'] as String,
      authorName: json['author_name'] as String,
    );

Map<String, dynamic> _$BookToJson(Book instance) => <String, dynamic>{
      'id': instance.id,
      'title': instance.title,
      'price': instance.price,
      'format': _$BookFormatEnumMap[instance.format]!,
      'subtitle': instance.subtitle,
      'author_name': instance.authorName,
    };

const _$BookFormatEnumMap = {
  BookFormat.paperback: 'paperback',
  BookFormat.hardcover: 'hardcover',
  BookFormat.audiobook: 'audiobook',
};
