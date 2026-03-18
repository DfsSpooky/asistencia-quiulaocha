import os
import subprocess
import tarfile
from datetime import datetime
import shutil
from django.core.management.base import BaseCommand
from django.conf import settings

class Command(BaseCommand):
    help = 'Genera un backup completo de la base de datos y archivos media.'

    def handle(self, *args, **options):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_dir = os.path.join(settings.BASE_DIR, 'backups')
        backup_name = f'backup_asistencia_{timestamp}'
        temp_path = os.path.join(backup_dir, backup_name)
        
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
        if not os.path.exists(temp_path):
            os.makedirs(temp_path)

        self.stdout.write(self.style.SUCCESS(f'Iniciando backup: {backup_name}'))

        # 1. Backup de Base de Datos
        db_conf = settings.DATABASES['default']
        db_name = db_conf['NAME']
        db_user = db_conf['USER']
        db_pass = db_conf['PASSWORD']
        db_host = db_conf['HOST']
        db_port = db_conf['PORT']
        
        sql_file = os.path.join(temp_path, 'database.sql')
        
        env = os.environ.copy()
        env['PGPASSWORD'] = db_pass
        
        try:
            self.stdout.write('Exportando base de datos...')
            subprocess.run([
                'pg_dump',
                '-h', db_host,
                '-p', str(db_port),
                '-U', db_user,
                '--clean',
                '--if-exists',
                '--no-owner',
                '--no-privileges',
                '-f', sql_file,
                db_name
            ], env=env, check=True)
            self.stdout.write(self.style.SUCCESS('Base de datos exportada.'))
        except subprocess.CalledProcessError as e:
            self.stdout.write(self.style.ERROR(f'Error al exportar DB: {e}'))
            shutil.rmtree(temp_path, ignore_errors=True)
            return

        # 2. Backup de Media
        media_root = settings.MEDIA_ROOT
        media_tar = os.path.join(temp_path, 'media.tar.gz')
        
        if os.path.exists(media_root) and os.listdir(media_root):
            self.stdout.write('Comprimiendo carpeta media...')
            with tarfile.open(media_tar, "w:gz") as tar:
                tar.add(media_root, arcname=os.path.basename(media_root))
            self.stdout.write(self.style.SUCCESS('Carpeta media comprimida.'))
        else:
            self.stdout.write(self.style.WARNING('Carpeta media vacía o no encontrada.'))

        # 3. Empaque final
        final_tar = os.path.join(backup_dir, f'{backup_name}.tar.gz')
        self.stdout.write('Generando archivo final...')
        with tarfile.open(final_tar, "w:gz") as tar:
            tar.add(temp_path, arcname=backup_name)
        
        # Limpiar temporal
        shutil.rmtree(temp_path)

        self.stdout.write(self.style.SUCCESS(f'Backup completado: {final_tar}'))
        return final_tar

