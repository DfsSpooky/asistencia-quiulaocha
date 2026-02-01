import 'package:flutter/material.dart';
import '../repositories/auth_repository.dart';

class AuthProvider extends ChangeNotifier {
  final AuthRepository _repo;
  String? _token;
  bool _isLoading = false;

  AuthProvider(this._repo);

  String? get token => _token;
  bool get isLoading => _isLoading;
  bool get isAuthenticated => _token != null;

  Future<void> checkLoginStatus() async {
    _token = await _repo.getToken();
    notifyListeners();
  }

  Future<bool> login(String username, String password) async {
    _isLoading = true;
    notifyListeners();

    _token = await _repo.login(username, password);

    _isLoading = false;
    notifyListeners();
    return _token != null;
  }

  Future<void> logout() async {
    await _repo.logout();
    _token = null;
    notifyListeners();
  }
}
