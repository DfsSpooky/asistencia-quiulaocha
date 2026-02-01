import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

class ApiService {
  // Update this with your server URL.
  // For Android Emulator, 10.0.2.2 usually points to localhost.
  // Using the ngrok URL from settings.py would be more reliable if available.
  static const String baseUrl =
      'https://oversophisticated-dedra-overgross.ngrok-free.dev';

  Future<String?> login(String username, String password) async {
    final response = await http.post(
      Uri.parse('$baseUrl/api/login/'),
      body: {'username': username, 'password': password},
    );

    if (response.statusCode == 200) {
      final data = json.decode(response.body);
      final token = data['token'];
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('token', token);
      return token;
    }
    return null;
  }

  Future<List<dynamic>> getEventos() async {
    final prefs = await SharedPreferences.getInstance();
    final token = prefs.getString('token');
    final response = await http.get(
      Uri.parse('$baseUrl/api/eventos-activos/'),
      headers: {'Authorization': 'Token $token'},
    );

    if (response.statusCode == 200) {
      return json.decode(response.body);
    }
    return [];
  }

  Future<List<dynamic>> getUbicaciones() async {
    final prefs = await SharedPreferences.getInstance();
    final token = prefs.getString('token');
    final response = await http.get(
      Uri.parse('$baseUrl/api/ubicaciones/'),
      headers: {'Authorization': 'Token $token'},
    );

    if (response.statusCode == 200) {
      return json.decode(utf8.decode(response.bodyBytes));
    }
    return [];
  }

  Future<List<dynamic>> getDentroEvento(int eventoId) async {
    final prefs = await SharedPreferences.getInstance();
    final token = prefs.getString('token');
    final response = await http.get(
      Uri.parse('$baseUrl/api/dentro-evento/$eventoId/'),
      headers: {'Authorization': 'Token $token'},
    );

    if (response.statusCode == 200) {
      return json.decode(utf8.decode(response.bodyBytes));
    }
    return [];
  }

  Future<Map<String, dynamic>> registrarAsistencia({
    required String encodedDni,
    required int eventoId,
    required int? ubicacionId,
    required String tipoEscaneo,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    final token = prefs.getString('token');

    try {
      final response = await http
          .post(
            Uri.parse('$baseUrl/api/registrar-asistencia/'),
            headers: {
              'Authorization': 'Token $token',
              'Content-Type': 'application/json',
            },
            body: json.encode({
              'dni': encodedDni,
              'evento_id': eventoId,
              'ubicacion_id': ubicacionId,
              'tipo_escaneo': tipoEscaneo,
            }),
          )
          .timeout(const Duration(seconds: 10));

      if (response.statusCode == 200 || response.statusCode == 201) {
        return json.decode(utf8.decode(response.bodyBytes));
      } else {
        String errorMsg = 'Error del servidor: ${response.statusCode}';
        try {
          final data = json.decode(utf8.decode(response.bodyBytes));
          if (data.containsKey('error')) {
            errorMsg = data['error'];
          }
        } catch (_) {}
        return {'error': errorMsg};
      }
    } catch (e) {
      // Smart Offline Mode: Validación local básica
      String decodedDni = utf8.decode(base64.decode(encodedDni));

      if (tipoEscaneo == 'salida') {
        final currentlyInside = await getLocalInsideDnis();
        if (!currentlyInside.contains(decodedDni)) {
          return {
            'error':
                'Error Offline: No se encontró ingreso local previo para este DNI.',
          };
        }
      }

      // Guardar localmente
      final scanData = {
        'dni': encodedDni,
        'evento_id': eventoId,
        'ubicacion_id': ubicacionId,
        'tipo_escaneo': tipoEscaneo,
        'timestamp': DateTime.now().toIso8601String(),
      };

      await _savePendingScan(scanData);

      // Actualizar estado local para control de flujo
      if (tipoEscaneo == 'ingreso') {
        await _addLocalInside(decodedDni);
      } else {
        await _removeLocalInside(decodedDni);
      }

      return {
        'message': 'Escaneo guardado localmente (Sin conexión)',
        'offline': true,
      };
    }
  }

  Future<Set<String>> getLocalInsideDnis() async {
    final prefs = await SharedPreferences.getInstance();
    return (prefs.getStringList('local_inside') ?? []).toSet();
  }

  Future<void> syncLocalInside(List<dynamic> serverInside) async {
    final prefs = await SharedPreferences.getInstance();
    final dnis = serverInside.map((u) => u['dni'].toString()).toList();
    await prefs.setStringList('local_inside', dnis);
  }

  Future<void> _addLocalInside(String encodedDni) async {
    final prefs = await SharedPreferences.getInstance();
    final current = prefs.getStringList('local_inside') ?? [];
    if (!current.contains(encodedDni)) {
      current.add(encodedDni);
      await prefs.setStringList('local_inside', current);
    }
  }

  Future<void> _removeLocalInside(String encodedDni) async {
    final prefs = await SharedPreferences.getInstance();
    final current = prefs.getStringList('local_inside') ?? [];
    current.remove(encodedDni);
    await prefs.setStringList('local_inside', current);
  }

  Future<void> _savePendingScan(Map<String, dynamic> scan) async {
    final prefs = await SharedPreferences.getInstance();
    List<String> pending = prefs.getStringList('pending_scans') ?? [];
    pending.add(json.encode(scan));
    await prefs.setStringList('pending_scans', pending);
  }

  Future<List<Map<String, dynamic>>> getPendingScans() async {
    final prefs = await SharedPreferences.getInstance();
    List<String> pending = prefs.getStringList('pending_scans') ?? [];
    return pending.map((s) => json.decode(s) as Map<String, dynamic>).toList();
  }

  Future<void> clearPendingScans() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('pending_scans');
  }

  Future<void> saveRecentSuccessfulScan(Map<String, dynamic> scan) async {
    final prefs = await SharedPreferences.getInstance();
    List<String> recent = prefs.getStringList('recent_scans') ?? [];
    // Mantener solo los últimos 20
    recent.insert(0, json.encode(scan));
    if (recent.length > 20) recent = recent.sublist(0, 20);
    await prefs.setStringList('recent_scans', recent);
  }

  Future<List<Map<String, dynamic>>> getRecentSuccessfulScans() async {
    final prefs = await SharedPreferences.getInstance();
    List<String> recent = prefs.getStringList('recent_scans') ?? [];
    return recent.map((s) => json.decode(s) as Map<String, dynamic>).toList();
  }

  Future<int> syncPendingScans() async {
    final pending = await getPendingScans();
    if (pending.isEmpty) return 0;

    int syncedCount = 0;
    List<Map<String, dynamic>> stillPending = [];

    for (var scan in pending) {
      try {
        final result = await http.post(
          Uri.parse('$baseUrl/api/registrar-asistencia/'),
          headers: {
            'Authorization': 'Token ${await _getToken()}',
            'Content-Type': 'application/json',
          },
          body: json.encode(scan),
        );
        if (result.statusCode == 200 || result.statusCode == 201) {
          syncedCount++;
        } else {
          stillPending.add(scan);
        }
      } catch (e) {
        stillPending.add(scan);
      }
    }

    final prefs = await SharedPreferences.getInstance();
    await prefs.setStringList(
      'pending_scans',
      stillPending.map((s) => json.encode(s)).toList(),
    );
    return syncedCount;
  }

  Future<String?> _getToken() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString('token');
  }

  Future<void> logout() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('token');
  }
}
