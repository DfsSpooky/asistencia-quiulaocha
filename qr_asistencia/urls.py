from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from asistencia.views import custom_login, custom_logout

urlpatterns = [
    path('gestionsegura/', admin.site.urls),
    path('', include('asistencia.urls')),
    path('login/', custom_login, name='login'),
    path('logout/', custom_logout, name='logout'),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

if settings.DEBUG or getattr(settings, "SERVE_MEDIA_FILES", False):
    from django.urls import re_path
    from django.views.static import serve
    media_url = settings.MEDIA_URL.lstrip("/")
    urlpatterns += [
        re_path(rf"^{media_url}(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    ]
