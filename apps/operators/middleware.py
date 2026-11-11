"""Requests for the hq host see only the hq pages, and are never cached or indexed."""
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import DisallowedHost
from django.http.request import split_domain_port

from apps.common.middleware import API_PREFIX
from apps.operators import checks, hq_views, staff_views

HQ_URLCONF = "apps.operators.hq_urls"


class HqHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        hq_host = checks.active_hq_host()
        if not hq_host or self._host(request) != hq_host:
            return self.get_response(request)
        request.urlconf = HQ_URLCONF
        staff_origin = (
            checks.active_staff_origin() if request.path.startswith(staff_views.PREFIX) else ""
        )
        if request.path.startswith(API_PREFIX):
            # The API's origin middleware answers on its prefix before any URL is resolved.
            response = hq_views.not_found(request)
        elif staff_origin:
            # Answered here, before the form-token check: the one allowed Origin replaces it.
            response = staff_views.dispatch(request, staff_origin)
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


class HqUserMiddleware:
    """On the hq host the user is the one the hq session names, and nobody else.

    Runs after Django's AuthenticationMiddleware and replaces what it set, so a Django password
    session opens nothing on hq. `request.hq_session` is the live hq session at any stage.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(request, "urlconf", None) == HQ_URLCONF:
            session = hq_views.load_session(request)
            request.hq_session = session
            request.user = session.identity.user if hq_views.is_verified(session) else AnonymousUser()
        return self.get_response(request)
