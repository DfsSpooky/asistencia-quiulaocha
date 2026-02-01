import 'package:flutter/material.dart';
import '../repositories/attendance_repository.dart';

class AttendanceProvider extends ChangeNotifier {
  final AttendanceRepository _repo;
  List<dynamic> _eventos = [];
  List<dynamic> _ubicaciones = [];
  bool _isLoading = false;

  AttendanceProvider(this._repo);

  List<dynamic> get eventos => _eventos;
  List<dynamic> get ubicaciones => _ubicaciones;
  bool get isLoading => _isLoading;

  Future<void> fetchInitialData(String token) async {
    _isLoading = true;
    notifyListeners();

    try {
      final results = await Future.wait([
        _repo.getActiveEvents(token),
        _repo.getLocations(token),
      ]);
      _eventos = results[0];
      _ubicaciones = results[1];
    } catch (e) {
      // debugPrint('Fetch Error: $e');
    }

    _isLoading = false;
    notifyListeners();
  }

  Future<Map<String, dynamic>> registerScan({
    required String encodedDni,
    required int eventoId,
    required int? ubicacionId,
    required String tipoEscaneo,
    required String token,
  }) async {
    return await _repo.registerAttendance(
      encodedDni: encodedDni,
      eventoId: eventoId,
      ubicacionId: ubicacionId,
      tipoEscaneo: tipoEscaneo,
      token: token,
    );
  }

  Future<List<dynamic>> fetchPersonasDentro(int eventoId, String token) async {
    return await _repo.getPersonasDentro(eventoId, token);
  }

  Future<int> syncSelectedScans(
    List<Map<String, dynamic>> scans,
    String token,
  ) async {
    _isLoading = true;
    notifyListeners();
    final count = await _repo.syncPendingScans(scans, token);
    _isLoading = false;
    notifyListeners();
    return count;
  }
}
