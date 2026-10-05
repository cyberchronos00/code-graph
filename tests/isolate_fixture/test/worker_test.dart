import 'package:flutter/foundation.dart';
import 'package:test/test.dart';

int _square(int x) => x * x;

void main() {
  test('squares in the background', () async {
    expect(await compute(_square, 3), 9);
  });
}
