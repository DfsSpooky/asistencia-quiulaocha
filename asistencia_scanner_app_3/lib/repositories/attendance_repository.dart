import 'package:dio/dio.dart';

class AttendanceRepository {
  final Dio _dio;

  AttendanceRepository(this._dio);

  Future<List<dynamic>> getActiveEvents(String token) async {
    final response = await _dio.get(
      '/api/eventos-activos/',
      options: Options(headers: {'Authorization': 'Token $token'}),
    );
    return response.data ?? [];
  }

  Future<List<dynamic>> getLocations(String token) async {
    final response = await _dio.get(
      '/api/ubicaciones/',
      options: Options(headers: {'Authorization': 'Token $token'}),
    );
    return response.data ?? [];
  }

  Future<Map<String, dynamic>> registerAttendance({
    required String encodedDni,
    required int eventoId,
    required int? ubicacionId,
    required String tipoEscaneo,
    required String token,
  }) async {
    try {
      final response = await _dio.post(
        '/api/registrar-asistencia/',
        data: {
          'dni': encodedDni,
          'evento_id': eventoId,
          'ubicacion_id': ubicacionId,
          'tipo_escaneo': tipoEscaneo,
        },
        options: Options(headers: {'Authorization': 'Token $token'}),
      );
      return response.data;
    } on DioException catch (e) {
      if (e.response != null) {
        return e.response?.data is Map
            ? e.response?.data
            : {'error': 'Error del servidor: ${e.response?.statusCode}'};
      }
      return {'error': 'Error de conexión: ${e.message}'};
    }
  }

  Future<List<dynamic>> getPersonasDentro(int eventoId, String token) async {
    final response = await _dio.get(
      '/api/dentro-evento/$eventoId/',
      options: Options(headers: {'Authorization': 'Token $token'}),
    );
    return response.data ?? [];
  }

  Future<int> syncPendingScans(
    List<Map<String, dynamic>> scans,
    String token,
  ) async {
    int count = 0;
    for (var scan in scans) {
      try {
        await registerAttendance(
          encodedDni: scan['dni'],
          eventoId: scan['evento_id'],
          ubicacionId: scan['ubicacion_id'],
          tipoEscaneo: scan['tipo_escaneo'],
          token: token,
        );
        count++;
      } catch (e) {
        // debugPrint('Sync Error for scan: $e');
      }
    }
    return count;
  }
}
