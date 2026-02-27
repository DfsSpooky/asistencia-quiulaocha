from django.core.management.base import BaseCommand
from asistencia.models import Usuario
from faker import Faker
import random
from datetime import date, timedelta
from django.db import transaction

class Command(BaseCommand):
    help = 'Popula la base de datos con usuarios ficticios'

    def add_arguments(self, parser):
        parser.add_argument('amount', type=int, nargs='?', default=100)

    def handle(self, *args, **options):
        amount = options['amount']
        fake = Faker(['es_ES', 'es_MX']) # Usar locales en español
        
        self.stdout.write(f'Creando {amount} usuarios ficticios...')
        
        count = 0
        errores = 0
        
        with transaction.atomic():
            for _ in range(amount):
                try:
                    # Generar DNI único (8 dígitos)
                    dni = fake.unique.random_number(digits=8, fix_len=True)
                    
                    # Generar nombre y apellido
                    genero = random.choice(['M', 'F'])
                    if genero == 'M':
                        nombre = fake.first_name_male()
                        apellido = fake.last_name()
                    else:
                        nombre = fake.first_name_female()
                        apellido = fake.last_name()
                    
                    # Generar fecha nacimiento (edad entre 18 y 80)
                    fecha_nacimiento = fake.date_of_birth(minimum_age=18, maximum_age=80)
                    
                    # Determinar estado basado en edad (simulado)
                    estado = Usuario.ESTADO_ACTIVO
                    edad = (date.today() - fecha_nacimiento).days // 365
                    if edad >= 65:
                        estado = Usuario.ESTADO_EXONERADO
                    elif random.random() < 0.1: # 10% de probabilidad de ser pasivo
                        estado = Usuario.ESTADO_PASIVO
                        
                    Usuario.objects.create(
                        nombre=nombre,
                        apellido=apellido,
                        dni=str(dni),
                        fecha_nacimiento=fecha_nacimiento,
                        estado=estado,
                        # Opcional: foto_perfil podría ser generada o dejada en null
                    )
                    count += 1
                    
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f'Error creando usuario: {str(e)}'))
                    errores += 1
        
        self.stdout.write(self.style.SUCCESS(f'¡Éxito! Se crearon {count} usuarios. ({errores} fallidos)'))
