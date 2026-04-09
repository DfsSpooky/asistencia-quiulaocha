class AppConfig {
  static const String _configuredApiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'https://quiulacocha.theworkpc.com',
  );

  static String get apiBaseUrl {
    if (_configuredApiBaseUrl.endsWith('/')) {
      return _configuredApiBaseUrl.substring(0, _configuredApiBaseUrl.length - 1);
    }
    return _configuredApiBaseUrl;
  }
}
