import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:provider/provider.dart';
import 'providers/auth_provider.dart';
import 'providers/attendance_provider.dart';
import 'scanner_screen.dart';
import 'login_screen.dart';

import 'package:get_it/get_it.dart';
import 'repositories/auth_repository.dart';
import 'package:marquee/marquee.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int? _selectedEventoId;
  int? _selectedUbicacionId;
  String _tipoEscaneo = 'ingreso';
  final List<Map<String, dynamic>> _recentScans = [];
  int _sessionCount = 0;
  bool _isSyncing = false;
  List<dynamic> _dentroEvento = [];
  String? _logoUrl;
  String _institutionName = 'Quiulacocha';

  @override
  void initState() {
    super.initState();
    _loadData();
    _loadConfig();
  }

  Future<void> _loadConfig() async {
    try {
      final repo = GetIt.instance<AuthRepository>();
      final config = await repo.getSystemConfig();
      if (config != null && mounted) {
        setState(() {
          if (config['logo_url'] != null) _logoUrl = config['logo_url'];
          if (config['nombre_institucion'] != null) {
            _institutionName = config['nombre_institucion'];
          }
        });
      }
    } catch (_) {}
  }

  void _loadData() async {
    final auth = context.read<AuthProvider>();
    final attendance = context.read<AttendanceProvider>();

    if (auth.token == null) return;

    await attendance.fetchInitialData(auth.token!);

    setState(() {
      if (attendance.eventos.isNotEmpty && _selectedEventoId == null) {
        _selectedEventoId = attendance.eventos[0]['id'];
      }
      if (attendance.ubicaciones.isNotEmpty && _selectedUbicacionId == null) {
        _selectedUbicacionId = attendance.ubicaciones[0]['id'];
      }
    });

    if (_selectedEventoId != null) {
      _loadDentroEvento();
    }
  }

  void _loadDentroEvento() async {
    if (_selectedEventoId == null) return;
    final auth = context.read<AuthProvider>();
    final attendance = context.read<AttendanceProvider>();

    if (auth.token == null) return;

    final dentro = await attendance.fetchPersonasDentro(
      _selectedEventoId!,
      auth.token!,
    );
    setState(() {
      _dentroEvento = dentro;
    });
  }

  Future<void> _handleSync() async {
    final auth = context.read<AuthProvider>();
    final attendance = context.read<AttendanceProvider>();
    if (auth.token == null) return;

    setState(() => _isSyncing = true);

    // For now, sync empty/placeholder since the local database migration isn't done yet
    // but the architecture is ready
    final count = await attendance.syncSelectedScans([], auth.token!);

    if (count > 0 && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Sincronizados $count registros pendientes')),
      );
    }
    setState(() => _isSyncing = false);
    _loadData();
  }

  void _logout() async {
    await context.read<AuthProvider>().logout();
    if (mounted) {
      Navigator.of(
        context,
      ).pushReplacement(MaterialPageRoute(builder: (_) => const LoginScreen()));
    }
  }

  void _showDentroList() {
    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.transparent,
      builder: (context) => Container(
        decoration: BoxDecoration(
          color: const Color(0xFF1E293B),
          borderRadius: const BorderRadius.vertical(top: Radius.circular(32)),
        ),
        padding: const EdgeInsets.all(24),
        child: Column(
          children: [
            Text(
              'Personas en el Evento',
              style: GoogleFonts.outfit(
                fontSize: 20,
                fontWeight: FontWeight.bold,
                color: Colors.white,
              ),
            ),
            const SizedBox(height: 16),
            Expanded(
              child: _dentroEvento.isEmpty
                  ? const Center(child: Text('Nadie ha ingresado aún'))
                  : ListView.builder(
                      itemCount: _dentroEvento.length,
                      itemBuilder: (context, index) {
                        final person = _dentroEvento[index];
                        return ListTile(
                          leading: CircleAvatar(
                            backgroundImage: person['foto_perfil'] != null
                                ? NetworkImage(person['foto_perfil'])
                                : null,
                            child: person['foto_perfil'] == null
                                ? const Icon(Icons.person)
                                : null,
                          ),
                          title: Text(
                            person['nombre'],
                            style: const TextStyle(color: Colors.white),
                          ),
                          subtitle: Text(
                            'DNI: ${person['dni']}',
                            style: const TextStyle(color: Colors.blueGrey),
                          ),
                          trailing: Text(
                            person['hora_ingreso'],
                            style: const TextStyle(color: Colors.tealAccent),
                          ),
                        );
                      },
                    ),
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF1A1C36),
      appBar: AppBar(
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (_logoUrl != null)
              Image.network(
                _logoUrl!,
                height: 40,
                errorBuilder: (_, _, _) =>
                    Image.asset('assets/images/logo.png', height: 40),
              )
            else
              Image.asset('assets/images/logo.png', height: 40),
            const SizedBox(width: 12),
            Expanded(
              child: SizedBox(
                height: 30, // Limited height for the marquee
                child: Marquee(
                  text: _institutionName.toUpperCase(),
                  style: GoogleFonts.outfit(
                    fontWeight: FontWeight.bold,
                    color: Colors.white,
                    fontSize: 16,
                  ),
                  scrollAxis: Axis.horizontal,
                  crossAxisAlignment: CrossAxisAlignment.center,
                  blankSpace: 50.0,
                  velocity: 30.0,
                  pauseAfterRound: const Duration(seconds: 2),
                  startPadding: 10.0,
                  accelerationDuration: const Duration(seconds: 1),
                  accelerationCurve: Curves.linear,
                  decelerationDuration: const Duration(milliseconds: 500),
                  decelerationCurve: Curves.easeOut,
                ),
              ),
            ),
          ],
        ),
        backgroundColor: Colors.transparent,
        elevation: 0,
        actions: [
          IconButton(
            icon: const Icon(Icons.logout, color: Colors.blueGrey),
            onPressed: _logout,
          ),
        ],
      ),
      body: context.watch<AttendanceProvider>().isLoading
          ? const Center(child: CircularProgressIndicator())
          : SingleChildScrollView(
              padding: const EdgeInsets.all(24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Configuración de Escaneo',
                    style: GoogleFonts.outfit(
                      fontSize: 24,
                      fontWeight: FontWeight.bold,
                      color: Colors.white,
                    ),
                  ),
                  SizedBox(height: 32),
                  Consumer<AttendanceProvider>(
                    builder: (context, attendance, _) => _buildDropdown(
                      label: 'Seleccionar Evento',
                      icon: Icons.event,
                      value: _selectedEventoId,
                      items: attendance.eventos.map((e) {
                        return DropdownMenuItem<int>(
                          value: e['id'],
                          child: Text(e['nombre']),
                        );
                      }).toList(),
                      onChanged: (val) {
                        setState(() => _selectedEventoId = val);
                        _loadDentroEvento();
                      },
                    ),
                  ),
                  SizedBox(height: 20),
                  Consumer<AttendanceProvider>(
                    builder: (context, attendance, _) => _buildDropdown(
                      label: 'Seleccionar Ubicación',
                      icon: Icons.location_on,
                      value: _selectedUbicacionId,
                      items: attendance.ubicaciones.map((u) {
                        return DropdownMenuItem<int>(
                          value: u['id'],
                          child: Text(u['nombre']),
                        );
                      }).toList(),
                      onChanged: (val) =>
                          setState(() => _selectedUbicacionId = val),
                    ),
                  ),
                  SizedBox(height: 32),
                  Text(
                    'Tipo de Registro',
                    style: GoogleFonts.outfit(
                      fontSize: 16,
                      color: Colors.blueGrey[400],
                    ),
                  ),
                  SizedBox(height: 12),
                  Row(
                    children: [
                      _buildTipoOption(
                        'ingreso',
                        'Entrada',
                        Icons.login,
                        Colors.teal,
                      ),
                      SizedBox(width: 16),
                      _buildTipoOption(
                        'salida',
                        'Salida',
                        Icons.logout,
                        const Color(0xFFFACC15), // Yellow for output
                      ),
                    ],
                  ),
                  SizedBox(height: 32),
                  // Dashboard de Sesión
                  Row(
                    children: [
                      _buildStatCard(
                        'Escaneos',
                        _sessionCount.toString(),
                        Icons.qr_code_scanner,
                        const Color(0xFFEAB308),
                      ),
                      const SizedBox(width: 12),
                      _buildStatCard(
                        'En Evento',
                        _dentroEvento.length.toString(),
                        Icons.group_rounded,
                        Colors.tealAccent,
                        onTap: _showDentroList,
                      ),
                      const SizedBox(width: 12),
                      _buildStatCard(
                        'Pendientes',
                        _recentScans
                            .where((s) => s.containsKey('timestamp'))
                            .length
                            .toString(),
                        Icons.cloud_upload_outlined,
                        _isSyncing ? Colors.blue : Colors.blueGrey,
                        onTap: _handleSync,
                      ),
                    ],
                  ),
                  const SizedBox(height: 32),
                  Text(
                    'Historial Reciente',
                    style: GoogleFonts.outfit(
                      fontSize: 18,
                      fontWeight: FontWeight.bold,
                      color: Colors.white,
                    ),
                  ),
                  const SizedBox(height: 12),
                  SizedBox(
                    height: 100,
                    child: _recentScans.isEmpty
                        ? Center(
                            child: Text(
                              'No hay escaneos recientes',
                              style: TextStyle(color: Colors.blueGrey[600]),
                            ),
                          )
                        : ListView.builder(
                            scrollDirection: Axis.horizontal,
                            itemCount: _recentScans.length,
                            itemBuilder: (context, index) {
                              final scan = _recentScans[index];
                              return _buildHistoryItem(scan);
                            },
                          ),
                  ),
                  const SizedBox(height: 32),
                  SizedBox(
                    width: double.infinity,
                    height: 64,
                    child: ElevatedButton.icon(
                      onPressed: (_selectedEventoId == null)
                          ? null
                          : () {
                              final attendance = context
                                  .read<AttendanceProvider>();
                              Navigator.of(context).push(
                                MaterialPageRoute(
                                  builder: (_) => ScannerScreen(
                                    eventoId: _selectedEventoId!,
                                    ubicacionId: _selectedUbicacionId,
                                    tipoEscaneo: _tipoEscaneo,
                                    eventoNombre: attendance.eventos.firstWhere(
                                      (e) => e['id'] == _selectedEventoId,
                                    )['nombre'],
                                    onScanSuccess: (result) {
                                      setState(() {
                                        _sessionCount++;
                                        // Agregar al historial reciente local
                                        _recentScans.insert(0, result);
                                        if (_recentScans.length > 20) {
                                          _recentScans.removeLast();
                                        }
                                      });
                                      // _apiService.saveRecentSuccessfulScan(result);
                                      _loadDentroEvento();
                                    },
                                  ),
                                ),
                              );
                            },
                      icon: Icon(Icons.camera_alt),
                      label: Text(
                        'Abrir Escáner',
                        style: GoogleFonts.outfit(
                          fontSize: 18,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFFEAB308),
                        foregroundColor: const Color(0xFF0F172A),
                        padding: const EdgeInsets.symmetric(vertical: 16),
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(20),
                        ),
                        elevation: 12,
                        shadowColor: const Color(
                          0xFFEAB308,
                        ).withValues(alpha: 0.4),
                      ),
                    ),
                  ),
                ],
              ),
            ),
    );
  }

  Widget _buildDropdown({
    required String label,
    required IconData icon,
    required dynamic value,
    required List<DropdownMenuItem<int>> items,
    required Function(int?) onChanged,
  }) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: GoogleFonts.outfit(fontSize: 16, color: Colors.blueGrey[400]),
        ),
        SizedBox(height: 8),
        Container(
          padding: EdgeInsets.symmetric(horizontal: 16),
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: 0.05),
            borderRadius: BorderRadius.circular(12),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<int>(
              value: value,
              items: items,
              onChanged: onChanged,
              dropdownColor: Color(0xFF1E293B),
              style: TextStyle(color: Colors.white, fontSize: 16),
              isExpanded: true,
              icon: Icon(
                Icons.keyboard_arrow_down,
                color: Colors.blueGrey[400],
              ),
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildTipoOption(
    String value,
    String label,
    IconData icon,
    Color color,
  ) {
    bool isSelected = _tipoEscaneo == value;
    return Expanded(
      child: GestureDetector(
        onTap: () => setState(() => _tipoEscaneo = value),
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 16),
          decoration: BoxDecoration(
            color: isSelected
                ? color.withValues(alpha: 0.2)
                : Colors.white.withValues(alpha: 0.05),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(
              color: isSelected ? color : Colors.transparent,
              width: 2,
            ),
          ),
          child: Column(
            children: [
              Icon(icon, color: isSelected ? color : Colors.blueGrey[400]),
              const SizedBox(height: 8),
              Text(
                label,
                style: GoogleFonts.outfit(
                  color: isSelected ? Colors.white : Colors.blueGrey[400],
                  fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildStatCard(
    String label,
    String value,
    IconData icon,
    Color color, {
    VoidCallback? onTap,
  }) {
    return Expanded(
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: 0.05),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: Colors.white.withValues(alpha: 0.1)),
          ),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: color.withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Icon(icon, color: color, size: 20),
              ),
              const SizedBox(height: 8),
              Text(
                label,
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.blueGrey[400], fontSize: 11),
              ),
              Text(
                value,
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 16,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildHistoryItem(Map<String, dynamic> scan) {
    bool isOffline = scan.containsKey('timestamp');
    String? nombre = scan['nombre'];
    String dni = scan['dni'] ?? '---';
    String? hora = scan['hora'];
    bool isIngreso = scan['tipo_escaneo'] == 'ingreso';

    return Container(
      width: 180,
      margin: const EdgeInsets.only(right: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.05),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(
          color: isOffline
              ? Colors.orange.withValues(alpha: 0.2)
              : Colors.teal.withValues(alpha: 0.1),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Row(
            children: [
              Icon(
                isIngreso ? Icons.login_rounded : Icons.logout_rounded,
                color: isIngreso ? Colors.tealAccent : const Color(0xFFFACC15),
                size: 14,
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  isIngreso ? 'ENTRADA' : 'SALIDA',
                  style: GoogleFonts.outfit(
                    color: isIngreso
                        ? Colors.tealAccent
                        : const Color(0xFFFACC15),
                    fontSize: 9,
                    fontWeight: FontWeight.w900,
                    letterSpacing: 0.5,
                  ),
                ),
              ),
              Text(
                hora ?? (isOffline ? '...' : 'Hoy'),
                style: GoogleFonts.outfit(
                  color: Colors.blueGrey[400],
                  fontSize: 10,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            nombre ?? 'DNI: $dni',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: GoogleFonts.outfit(
              color: Colors.white,
              fontWeight: FontWeight.bold,
              fontSize: 14,
            ),
          ),
          const SizedBox(height: 4),
          Row(
            children: [
              Icon(
                isOffline ? Icons.cloud_off : Icons.cloud_done,
                size: 10,
                color: isOffline ? Colors.orange : Colors.blueGrey[400],
              ),
              const SizedBox(width: 4),
              Text(
                isOffline ? 'Pendiente' : 'Sincronizado',
                style: TextStyle(
                  color: isOffline ? Colors.orange : Colors.blueGrey[400],
                  fontSize: 10,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
