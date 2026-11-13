"""CORS for the allowed browser origins, and refusal of writes from any other origin."""
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.cache import patch_vary_headers

from apps.common.errors import ApiError, error_body

# The largest existing file limit is 20 MiB (the Santé agreement); the rest is the multipart form.
MAX_REQUEST_BYTES = 21 * 1024 * 1024
API_PREFIX = "/v1/"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
ALLOW_HEADERS = "content-type"
PREFLIGHT_MAX_AGE = "600"


def require_allowed_origin(request):
    """Sign-in and cookie-backed endpoints call this: a write must name an allowed Origin."""
    if request.method in SAFE_METHODS:
        return
    if request.headers.get("Origin") not in settings.API_ALLOWED_ORIGINS:
        raise ApiError(
            "origin_required", "This request must come from the site or the dashboard.", status=403
        )


class ApiOriginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(API_PREFIX):
            return self.get_response(request)

        origin = request.headers.get("Origin")
        allowed = bool(origin) and origin in settings.API_ALLOWED_ORIGINS

        if origin and not allowed and request.method not in SAFE_METHODS:
            return JsonResponse(
                error_body("origin_not_allowed", "This origin may not call the API."),
                status=403,
            )

        is_preflight = (
            request.method == "OPTIONS"
            and origin
            and request.headers.get("Access-Control-Request-Method")
        )
        if is_preflight:
            response = HttpResponse(status=204)
            if allowed:
                response["Access-Control-Allow-Methods"] = ALLOW_METHODS
                response["Access-Control-Allow-Headers"] = ALLOW_HEADERS
                response["Access-Control-Max-Age"] = PREFLIGHT_MAX_AGE
        else:
            response = self.get_response(request)

        if allowed:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Credentials"] = "true"
        patch_vary_headers(response, ["Origin"])
        return response


class RequestSizeCapMiddleware:
    """Answer 413 before any body is read when the declared size is above MAX_REQUEST_BYTES.

    A body sent without a Content-Length is never read by Django (its stream is empty), so
    there is nothing more to cap.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            declared = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            declared = 0
        if declared > MAX_REQUEST_BYTES:
            return JsonResponse(
                error_body("payload_too_large", "The request is too large."), status=413
            )
        return self.get_response(request)
