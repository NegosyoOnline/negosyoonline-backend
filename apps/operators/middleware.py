"""Requests for the hq host see only the hq pages, and are never cached or indexed."""
from django.core.exceptions import DisallowedHost
from django.http.request import split_domain_port

from apps.common.middleware import API_PREFIX
from apps.operators import checks, hq_views

HQ_URLCONF = "apps.operators.hq_urls"


class HqHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        hq_host = checks.active_hq_host()
        if not hq_host or self._host(request) != hq_host:
            return self.get_response(request)
        request.urlconf = HQ_URLCONF
        if request.path.startswith(API_PREFIX):
            # The API's origin middleware answers on its prefix before any URL is resolved.
            response = hq_views.not_found(request)
        else:
            response = self.get_response(request)
        # Set here so that error pages and the CSRF refusal carry them too.
        response["Cache-Control"] = "no-store"
        response["X-Robots-Tag"] = "noindex"
        return response

    @staticmethod
    def _host(request):
        try:
            return split_domain_port(request.get_host())[0]
        except DisallowedHost:
            return ""
