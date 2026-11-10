import json
import re

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings

ORIGIN = "https://dash.example.test"
EMAIL = "ana@example.test"


@override_settings(API_ALLOWED_ORIGINS=[ORIGIN])
class ApiTestCase(TestCase):
    def setUp(self):
        cache.clear()

    def send(self, method, path, data=None, client=None, **extra):
        extra.setdefault("HTTP_ORIGIN", ORIGIN)
        if extra["HTTP_ORIGIN"] is None:
            del extra["HTTP_ORIGIN"]
        call = getattr(client or self.client, method)
        return call(
            path, data=json.dumps(data or {}), content_type="application/json", secure=True, **extra
        )

    def post(self, path, data=None, **extra):
        return self.send("post", path, data, **extra)

    def get(self, path, client=None):
        return (client or self.client).get(path, secure=True)

    def last_code(self):
        return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

    def sign_in(self, email=EMAIL, client=None):
        cache.clear()
        self.post("/v1/auth/email-code/request/", {"email": email}, client=client)
        return self.post(
            "/v1/auth/email-code/verify/",
            {"email": email, "code": self.last_code(), "accept_policies": True},
            client=client,
        )
