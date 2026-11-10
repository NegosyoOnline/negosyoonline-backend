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
from django.http import JsonResponse
from django.urls import path, re_path

from apps.notifications import gateway
from config.api import api, api_not_found


def healthz(_request):
    return JsonResponse({"status": "ok", "commit": settings.RELEASE_COMMIT})

urlpatterns = [
    # No admin here: the admin lists are served only on the hq host (apps/operators/hq_urls.py).
    path("healthz/", healthz, name="healthz"),
    # The SMS phone's calls. Outside /v1/, so they are not in the published API.
    path("gateway/v1/poll", gateway.poll),
    path("gateway/v1/report", gateway.report),
    path("gateway/v1/received", gateway.received),
    path("v1/", api.urls),
    re_path(r"^v1/", api_not_found),
]
