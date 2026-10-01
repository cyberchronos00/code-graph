import 'package:flutter/material.dart';

/// Route helper used by the app instead of calling MaterialPageRoute directly.
Route<T> buildPageRoute<T>({required Widget page}) {
  return MaterialPageRoute<T>(builder: (_) => page);
}
