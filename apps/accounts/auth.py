"""Authentication for signed-in subscriber endpoints: the session cookie."""
from django.conf import settings
from ninja.security import APIKeyCookie

from apps.accounts.sessions import find_request_session
from apps.common.middleware import require_allowed_origin


class SessionCookieAuth(APIKeyCookie):
    param_name = settings.ACCOUNT_SESSION_COOKIE

    def __init__(self):
        # Django's CSRF token is not used: the Origin allowlist plus SameSite=Lax replace it.
        super().__init__(csrf=False)

    def __call__(self, request):
        # Read every cookie value from the raw header, not the single one Django keeps.
        return self.authenticate(request, None)

    def authenticate(self, request, key):
        require_allowed_origin(request)
        session = find_request_session(request)
        if session is None:
            return None
        request.account = session.account
        request.account_session = session
        return session.account
