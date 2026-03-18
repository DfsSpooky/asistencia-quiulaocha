from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from asistencia.models import Usuario, Asistencia, Justificacion
import pandas as pd
import math

class Command(BaseCommand):
    help = 'Importa el padrón desde PADRON 2026.csv, eliminando previamente la base de datos de usuarios.'

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.WARNING("=== Iniciando proceso de migración de datos ==="))

        # 1. Limpiar base de datos
        self.stdout.write("Vaciando base de datos...")
        Asistencia.objects.all().delete()
        Justificacion.objects.all().delete()
        Usuario.objects.all().delete()
        User.objects.filter(is_superuser=False, is_staff=False).delete()
        self.stdout.write(self.style.SUCCESS("Datos anteriores eliminados correctamente."))

        # 2. Leer CSV
        file_path = 'Padron usuarios.csv'
        try:
            # Try utf-8 first
            df = pd.read_csv(file_path, sep=';', encoding='utf-8')
        except UnicodeDecodeError:
            df = pd.read_csv(file_path, sep=';', encoding='latin1')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error al leer el archivo {file_path}: {e}"))
            return

        # Encontrar la fila de cabecera (en este CSV está en la primera fila, index 0 / header=0)
        # Probaremos buscarla por si acaso, si no, asumimos 0
        header_idx: int = 0
        header_found_in_data = False
        for i, row in df.iterrows():
            if row.astype(str).str.contains('DNI', case=False).any():
                header_idx = int(i)
                header_found_in_data = True
                break

        # En el padron nuevo las columnas estan directamente en el Df original que carga pandas
        # así que comprobemos si están en las columnas mismas
        if any('DNI' in str(c).upper() for c in df.columns):
            self.stdout.write("Fila de cabecera encontrada en la primera fila (header).")
            # Ya está cargado bien
        elif header_found_in_data:
            self.stdout.write(f"Fila de cabecera encontrada en el índice {header_idx + 1}.")
            try:
                df = pd.read_csv(file_path, sep=';', header=header_idx + 1, encoding='utf-8')
            except UnicodeDecodeError:
                df = pd.read_csv(file_path, sep=';', header=header_idx + 1, encoding='latin1')
        else:
            self.stdout.write(self.style.ERROR("No se encontró una fila con la columna 'DNI'."))
            return

        df = df.dropna(how='all')

        # Normalizar nombres de columnas a mayúsculas
        df.columns = [str(col).upper().strip() for col in df.columns]

        # Identificar columnas
        dni_col = next((col for col in df.columns if 'DNI' in str(col).replace('.', '')), None)
        nombres_col = next((col for col in df.columns if 'NOMBRES' in str(col)), None)

        if not dni_col or not nombres_col:
            self.stdout.write(self.style.ERROR(f"No se identificaron las columnas clave. Detectadas: {df.columns.tolist()}"))
            return

        self.stdout.write(f"Columna DNI identificada como: '{dni_col}'")
        self.stdout.write(f"Columna Nombres identificada como: '{nombres_col}'")

        count_activos: int = 0
        count_exonerados: int = 0
        count_pasivos: int = 0
        count_errores: int = 0

        for index, row in df.iterrows():
            dni_val = str(row[dni_col]).strip()
            
            # Limpiar DNI: si se leyó como float 12345678.0, quitar el .0
            if dni_val.endswith('.0'):
                dni_val = dni_val[:-2]
            
            # Completar con ceros a la izquierda si tiene menos de 8
            dni_val = dni_val.zfill(8)
            
            # Generar DNI dummy si no existe o es inválido para no perder al usuario
            if len(dni_val) != 8 or not dni_val.isdigit() or dni_val in ['00000NAN', '0000NONE', '0000000N']:
                # DNI dummy: 99 + index formateado a 6 digitos
                dni_val = f"99{str(index).zfill(6)}"
                
            nombres_completos = str(row[nombres_col]).strip()
            if pd.isna(row[nombres_col]) or nombres_completos.upper() == 'NAN':
                continue

            # Separar nombres y apellidos usando la coma como delimitador
            # Formato esperado: "APELLIDOS, NOMBRES"
            import re
            
            if ',' in nombres_completos:
                partes = nombres_completos.split(',', 1) # Split only on the first comma
                # Clean each part strictly after splitting
                apellidos_part = re.sub(r'[^a-zA-ZáéíóúÁÉÍÓÚñÑ\s]', ' ', partes[0])
                nombres_part = re.sub(r'[^a-zA-ZáéíóúÁÉÍÓÚñÑ\s]', ' ', partes[1])
                
                apellido = re.sub(r'\s+', ' ', apellidos_part).strip()
                nombre = re.sub(r'\s+', ' ', nombres_part).strip()
                
                # Fallback if cleaning removed everything
                if not apellido: apellido = "-"
                if not nombre: nombre = "-"
            else:
                # Si no hay coma, limpiamos todo y dividimos a la mitad como antes
                nombres_completos = re.sub(r'[^a-zA-ZáéíóúÁÉÍÓÚñÑ\s]', ' ', nombres_completos)
                nombres_completos = re.sub(r'\s+', ' ', nombres_completos).strip()
                
                partes = nombres_completos.split(' ')
                if len(partes) >= 3:
                    apellido = f"{partes[0]} {partes[1]}"
                    nombre = " ".join(partes[2:])
                elif len(partes) == 2:
                    apellido = partes[0]
                    nombre = partes[1]
                else:
                    apellido = nombres_completos
                    nombre = "-"

            # Determinar estado buscando las palabras clave en toda la fila (como strings)
            row_str = " ".join(row.astype(str).tolist()).upper()
            
            if 'EXONERADO' in row_str or 'EXONE' in row_str:
                estado = Usuario.ESTADO_EXONERADO
                count_exonerados += 1
            elif 'NO CALIFICADO' in row_str:
                estado = Usuario.ESTADO_PASIVO
                count_pasivos += 1
            else:
                estado = Usuario.ESTADO_ACTIVO
                count_activos += 1

            # Crear el usuario
            try:
                if not Usuario.objects.filter(dni=dni_val).exists():
                    usuario_obj = Usuario(
                        dni=dni_val,
                        nombre=nombre.upper(),
                        apellido=apellido.upper(),
                        estado=estado
                    )
                    usuario_obj.save()
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error al guardar a DNI: {dni_val} - {e}"))
                count_errores += 1

        self.stdout.write(self.style.SUCCESS(f"=== Resumen ==="))
        self.stdout.write(f"Usuarios Activos creados: {count_activos}")
        self.stdout.write(f"Usuarios Exonerados creados: {count_exonerados}")
        self.stdout.write(f"Usuarios Pasivos creados: {count_pasivos}")
        self.stdout.write(f"Errores: {count_errores}")
        self.stdout.write(self.style.SUCCESS("Proceso completado."))
