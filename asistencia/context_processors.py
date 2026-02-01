from .models import ConfiguracionSistema

def sistema_config(request):
    try:
        config = ConfiguracionSistema.objects.first()
    except ConfiguracionSistema.DoesNotExist:
        config = None
    return {'sistema_config': config}