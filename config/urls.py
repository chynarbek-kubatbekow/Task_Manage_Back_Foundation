"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.authtoken.views import obtain_auth_token

from app.views import MonitoringHealthView

urlpatterns = [
    path('', MonitoringHealthView.as_view(), name='root-health'),
    path('health/', MonitoringHealthView.as_view(), name='health'),
    path('health', MonitoringHealthView.as_view(), name='health-no-slash'),
    path('admin/', admin.site.urls),
    path('api/v1/', include('app.urls')),
    path('api/auth/', include('rest_framework.urls')),
    path('api/auth/token/', obtain_auth_token, name='api-token'),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]
