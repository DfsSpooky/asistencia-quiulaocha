import 'package:dio/dio.dart';
import 'package:shared_preferences/shared_preferences.dart';

class AuthRepository {
  final Dio _dio;

  AuthRepository(this._dio);

  Future<String?> login(String username, String password) async {
    print('🚀 Attempting Login via AuthRepository...');
    print('Target: /api/login/');
    try {
      final response = await _dio.post(
        '/api/login/',
        data: {'username': username, 'password': password},
      );

      if (response.statusCode == 200) {
        final token = response.data['token'];
        final prefs = await SharedPreferences.getInstance();
        await prefs.setString('token', token);
        return token;
      }
    } catch (e) {
      print('🔥 CRITICAL LOGIN ERROR (Dio): $e');
      if (e is DioException) {
        print('Dio Error Type: ${e.type}');
        print('Dio Error Message: ${e.message}');
        print('Dio Error Response: ${e.response}');
      }
    }
    return null;
  }

  Future<void> logout() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('token');
  }

  Future<String?> getToken() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString('token');
  }

  Future<Map<String, dynamic>?> getSystemConfig() async {
    try {
      final response = await _dio.get('/api/config-sistema/');
      return response.data;
    } catch (e) {
      print('Config Fetch Error: $e');
      return null;
    }
  }
}
