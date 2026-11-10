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
from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, re_path

from apps.operators import admin_views
from config.api import api, api_not_found


def healthz(_request):
    return JsonResponse({"status": "ok", "commit": settings.RELEASE_COMMIT})

urlpatterns = [
    # The second step of admin sign-in. Before the admin, so the admin cannot shadow them.
    path("admin/two-step/", admin_views.two_step_code),
    path("admin/two-step/sign-out/", admin_views.two_step_sign_out),
    path('admin/', admin.site.urls),
    path("healthz/", healthz, name="healthz"),
    path("v1/", api.urls),
    re_path(r"^v1/", api_not_found),
]
