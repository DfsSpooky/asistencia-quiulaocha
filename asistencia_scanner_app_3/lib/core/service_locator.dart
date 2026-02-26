import 'package:get_it/get_it.dart';
import 'package:dio/dio.dart';
import '../repositories/auth_repository.dart';
import '../repositories/attendance_repository.dart';
import '../api_service.dart'; // Mantener compatibilidad temporal

final getIt = GetIt.instance;

void setupServiceLocator() {
  // Dio
  getIt.registerLazySingleton<Dio>(
    () => Dio(
      BaseOptions(
        baseUrl: 'https://quiulacocha.theworkpc.com',
        connectTimeout: const Duration(seconds: 10),
        receiveTimeout: const Duration(seconds: 10),
      ),
    ),
  );

  // Repositories
  getIt.registerLazySingleton<AuthRepository>(
    () => AuthRepository(getIt<Dio>()),
  );
  getIt.registerLazySingleton<AttendanceRepository>(
    () => AttendanceRepository(getIt<Dio>()),
  );

  // Legacy Service (to be removed)
  getIt.registerLazySingleton<ApiService>(() => ApiService());
}
