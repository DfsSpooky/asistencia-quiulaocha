import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:vibration/vibration.dart';
import 'package:audioplayers/audioplayers.dart';
import 'package:provider/provider.dart';
import 'providers/auth_provider.dart';
import 'providers/attendance_provider.dart';

class ScannerScreen extends StatefulWidget {
  final int eventoId;
  final int? ubicacionId;
  final String tipoEscaneo;
  final String eventoNombre;
  final Function(Map<String, dynamic>)? onScanSuccess;

  const ScannerScreen({
    super.key,
    required this.eventoId,
    this.ubicacionId,
    required this.tipoEscaneo,
    required this.eventoNombre,
    this.onScanSuccess,
  });

  @override
  State<ScannerScreen> createState() => _ScannerScreenState();
}

class _ScannerScreenState extends State<ScannerScreen> {
  final MobileScannerController controller = MobileScannerController();
  final _audioPlayer = AudioPlayer();
  bool _isProcessing = false;
  bool _isFlashOn = false;

  void _onDetect(BarcodeCapture capture) async {
    if (_isProcessing) return;
    final List<Barcode> barcodes = capture.barcodes;
    if (barcodes.isNotEmpty) {
      final String? code = barcodes.first.rawValue;
      if (code != null) {
        setState(() => _isProcessing = true);

        if (await Vibration.hasVibrator() == true) {
          Vibration.vibrate(duration: 100);
        }

        if (!mounted) return;
        final auth = context.read<AuthProvider>();
        final attendance = context.read<AttendanceProvider>();

        if (auth.token == null) return;

        final result = await attendance.registerScan(
          encodedDni: code,
          eventoId: widget.eventoId,
          ubicacionId: widget.ubicacionId,
          tipoEscaneo: widget.tipoEscaneo,
          token: auth.token!,
        );

        // Feedback sonoro
        if (result.containsKey('message')) {
          _audioPlayer.play(AssetSource('sounds/success.mp3'));
          if (widget.onScanSuccess != null) widget.onScanSuccess!(result);
        } else {
          _audioPlayer.play(AssetSource('sounds/error.mp3'));
        }

        if (!mounted) return;
        _showResult(result);
      }
    }
  }

  void _showResult(Map<String, dynamic> result) {
    bool isSuccess = result.containsKey('message');
    String message =
        result['message'] ?? result['error'] ?? 'Error desconocido';
    String? hora = result['hora'];

    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.transparent,
      isDismissible: false,
      builder: (context) => Container(
        decoration: BoxDecoration(
          color: const Color(0xFF0F172A), // Slate 900
          borderRadius: const BorderRadius.vertical(top: Radius.circular(40)),
          border: Border.all(
            color: (isSuccess ? Colors.teal : Colors.redAccent).withValues(
              alpha: 0.2,
            ),
            width: 2,
          ),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.5),
              blurRadius: 20,
              offset: const Offset(0, -5),
            ),
          ],
        ),
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Icono / Foto con efecto de brillo
            Stack(
              alignment: Alignment.center,
              children: [
                Container(
                  width: 120,
                  height: 120,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    boxShadow: [
                      BoxShadow(
                        color: (isSuccess ? Colors.teal : Colors.redAccent)
                            .withValues(alpha: 0.3),
                        blurRadius: 30,
                        spreadRadius: 5,
                      ),
                    ],
                  ),
                ),
                if (isSuccess && result['foto_perfil'] != null)
                  CircleAvatar(
                    radius: 56,
                    backgroundColor: const Color(0xFFEAB308),
                    child: CircleAvatar(
                      radius: 52,
                      backgroundImage: NetworkImage(result['foto_perfil']),
                    ),
                  )
                else
                  Container(
                    padding: const EdgeInsets.all(20),
                    decoration: BoxDecoration(
                      color: (isSuccess ? Colors.teal : Colors.redAccent)
                          .withValues(alpha: 0.1),
                      shape: BoxShape.circle,
                      border: Border.all(
                        color: (isSuccess ? Colors.teal : Colors.redAccent),
                        width: 4,
                      ),
                    ),
                    child: Icon(
                      isSuccess ? Icons.check_rounded : Icons.close_rounded,
                      size: 60,
                      color: (isSuccess ? Colors.teal : Colors.redAccent),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 24),

            // Título de Estado
            Text(
              isSuccess ? 'REGISTRO EXITOSO' : 'OCURRIÓ UN ERROR',
              style: GoogleFonts.outfit(
                fontSize: 14,
                fontWeight: FontWeight.w800,
                letterSpacing: 2,
                color: (isSuccess ? Colors.tealAccent : Colors.redAccent),
              ),
            ),
            const SizedBox(height: 8),

            // Mensaje Principal (Nombre)
            if (isSuccess && result['nombre'] != null)
              Text(
                result['nombre'],
                textAlign: TextAlign.center,
                style: GoogleFonts.outfit(
                  fontSize: 26,
                  fontWeight: FontWeight.bold,
                  color: Colors.white,
                ),
              ),

            const SizedBox(height: 12),

            // Descripción/Error
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
              decoration: BoxDecoration(
                color: Colors.white.withValues(alpha: 0.05),
                borderRadius: BorderRadius.circular(16),
              ),
              child: Text(
                message,
                textAlign: TextAlign.center,
                style: GoogleFonts.outfit(
                  fontSize: 16,
                  color: Colors.blueGrey[300],
                  height: 1.4,
                ),
              ),
            ),

            if (isSuccess && hora != null) ...[
              const SizedBox(height: 16),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const Icon(
                    Icons.access_time_rounded,
                    size: 20,
                    color: Color(0xFFEAB308),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    'Hora: $hora',
                    style: GoogleFonts.outfit(
                      fontSize: 18,
                      fontWeight: FontWeight.w600,
                      color: const Color(0xFFEAB308),
                    ),
                  ),
                ],
              ),
            ],

            const SizedBox(height: 32),

            // Botón de Acción
            SizedBox(
              width: double.infinity,
              height: 64,
              child: ElevatedButton(
                onPressed: () {
                  Navigator.pop(context);
                  if (mounted) {
                    setState(() => _isProcessing = false);
                  }
                },
                style: ElevatedButton.styleFrom(
                  backgroundColor: isSuccess
                      ? const Color(0xFFEAB308)
                      : Colors.white.withValues(alpha: 0.1),
                  foregroundColor: isSuccess ? Colors.black : Colors.white,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(20),
                  ),
                  elevation: 0,
                ),
                child: Text(
                  'CONTINUAR',
                  style: GoogleFonts.outfit(
                    fontSize: 18,
                    fontWeight: FontWeight.w800,
                    letterSpacing: 1.2,
                  ),
                ),
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
      backgroundColor: Colors.black,
      appBar: AppBar(
        title: Text('Escaneando: ${widget.tipoEscaneo.toUpperCase()}'),
        backgroundColor: Colors.black,
        actions: [
          IconButton(
            icon: Icon(
              _isFlashOn ? Icons.flashlight_on : Icons.flashlight_off,
              color: _isFlashOn ? const Color(0xFFEAB308) : Colors.white,
            ),
            onPressed: () {
              controller.toggleTorch();
              setState(() => _isFlashOn = !_isFlashOn);
            },
          ),
        ],
      ),
      body: Stack(
        children: [
          MobileScanner(controller: controller, onDetect: _onDetect),
          // Overlay
          Center(
            child: Container(
              width: 250,
              height: 250,
              decoration: BoxDecoration(
                border: Border.all(color: const Color(0xFFEAB308), width: 3),
                borderRadius: BorderRadius.circular(24),
                boxShadow: [
                  BoxShadow(
                    color: const Color(0xFFEAB308).withValues(alpha: 0.3),
                    blurRadius: 20,
                    spreadRadius: 2,
                  ),
                ],
              ),
            ),
          ),
          Positioned(
            bottom: 40,
            left: 0,
            right: 0,
            child: Center(
              child: Container(
                padding: EdgeInsets.symmetric(horizontal: 24, vertical: 12),
                decoration: BoxDecoration(
                  color: Colors.black54,
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Text(
                  widget.eventoNombre,
                  style: TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
