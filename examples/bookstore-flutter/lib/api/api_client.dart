import 'package:dio/dio.dart';

import '../config.dart';

class ApiClient {
  ApiClient() : dio = Dio(BaseOptions(baseUrl: '$apiBase/api'));

  final Dio dio;
}
