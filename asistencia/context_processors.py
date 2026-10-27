from django.core.cache import cache
from .models import ConfiguracionSistema

def sistema_config(request):
    config = cache.get('sistema_config')
    if config is None:
        try:
            config = ConfiguracionSistema.objects.first()
            if config is not None:
                cache.set('sistema_config', config, timeout=300)
        except Exception:
            config = None
    return {'sistema_config': config}
