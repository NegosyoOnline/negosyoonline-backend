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
from django.urls import include, path, re_path

from apps.health.views import readyz
from apps.inquiries.stream import stream as inquiries_stream
from apps.notifications import gateway
from apps.storefront import api as storefront
from config.api import api, api_not_found


def healthz(_request):
    return JsonResponse({"status": "ok", "commit": settings.RELEASE_COMMIT})

urlpatterns = [
    # No admin here: the Django admin is switched off.
    path("healthz/", healthz, name="healthz"),
    path("readyz/", readyz, name="readyz"),
    # The SMS phone's calls. Outside /v1/, so they are not in the published API.
    path("gateway/v1/poll", gateway.poll),
    path("gateway/v1/report", gateway.report),
    path("gateway/v1/received", gateway.received),
    # The storefronts server's calls. Outside /v1/, so they are not in the published API.
    path("storefront/v1/", include(storefront.urlpatterns, namespace=storefront.namespace)),
    # Live Inbox push (server-sent events); a plain async view, ahead of the ninja include.
    path("v1/sites/<str:site_id>/inquiries/stream/", inquiries_stream),
    path("v1/", api.urls),
    re_path(r"^v1/", api_not_found),
]

if settings.XENDIT_FAKE and not settings.APP_ENVIRONMENT:  # the local preview's fake Xendit page; needs DJANGO_DEBUG=true and no APP_ENVIRONMENT
    from apps.payments import fake_views
    urlpatterns.insert(0, path("fake-xendit/invoice/<str:invoice_id>/", fake_views.invoice))
