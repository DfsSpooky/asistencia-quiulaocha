from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from asistencia.views import custom_login, custom_logout

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('asistencia.urls')),
    path('login/', custom_login, name='login'),
    path('logout/', custom_logout, name='logout'),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT) + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)