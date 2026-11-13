"""The one API of the backend. Routers from each app are added here in later slices."""
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from ninja import NinjaAPI

from apps.common.errors import error_body, register_error_handlers
from apps.accounts.api import router as accounts_router
from apps.accounts.api_connections import router as connections_router
from apps.accounts.api_facebook import router as facebook_router
from apps.billing.api import router as billing_router
from apps.catalog.api import router as catalog_router
from apps.meta_ads.api_owner import router as meta_router
from apps.pack.api_owner import router as pack_owner_router
from apps.payments.api import router as payments_router
from apps.sante.api import router as sante_router
from apps.shop.api_owner import router as shop_owner_router
from apps.sites.api import router as sites_router
from apps.sites.api_public import router as public_sites_router
from apps.statements.api import router as statements_router

api = NinjaAPI(
    title="NegosyoOnline API",
    version="1.0.0",
    urls_namespace="v1",
    openapi_url="/openapi.json",
    docs_url="/docs" if settings.DEBUG else None,
)
register_error_handlers(api)
api.add_router("", accounts_router)
api.add_router("", facebook_router)
api.add_router("", connections_router)
api.add_router("", sites_router)
api.add_router("", billing_router)
api.add_router("", statements_router)
api.add_router("", public_sites_router)
api.add_router("", sante_router)
api.add_router("", catalog_router)
api.add_router("", payments_router)
api.add_router("", pack_owner_router)
api.add_router("", shop_owner_router)
api.add_router("", meta_router)


@csrf_exempt  # so a write to an unknown path gets this JSON 404, not Django's HTML CSRF page
def api_not_found(request, *args, **kwargs):
    """Unknown /v1/ paths answer in the API's error shape, not Django's HTML 404."""
    return JsonResponse(error_body("not_found", "No such endpoint."), status=404)
