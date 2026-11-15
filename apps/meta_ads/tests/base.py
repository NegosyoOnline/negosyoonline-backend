"""Helpers for the Meta Pixel and Conversions API tests. Meta is never called: send._open is
replaced by a fake that records each request and answers as the test says."""
import io
import json
import urllib.error
from unittest import mock

from celery import current_app
from cryptography.fernet import Fernet
from django.test import override_settings

from apps.meta_ads import crypto
from apps.meta_ads.models import MetaSettings

KEY = Fernet.generate_key().decode()  # made for this test run; never a real value
TOKEN = "TESTTOKEN" + "abcdefghij" * 6  # 69 characters, made up
PIXEL = "123456789012345"
CODE = "TEST12345"

_eager = {}


def setUpModule():  # noqa: N802 - unittest's name; each test module imports it
    """Run Celery tasks in-process for these tests. Settings do this only without REDIS_URL, so
    under the PostgreSQL and Redis configuration .delay() would queue to the broker and nothing
    would send."""
    _eager["on"] = current_app.conf.task_always_eager
    _eager["propagates"] = current_app.conf.task_eager_propagates
    current_app.conf.task_always_eager = True
    current_app.conf.task_eager_propagates = True


def tearDownModule():  # noqa: N802
    current_app.conf.task_always_eager = _eager.get("on", False)
    current_app.conf.task_eager_propagates = _eager.get("propagates", False)


switched_on = override_settings(META_EVENTS_ENABLED=True, META_CREDENTIALS_KEY=KEY)


def configure(site, *, pixel=PIXEL, token=TOKEN, code=CODE):
    """A site's Meta settings, with the token encrypted as the route would."""
    with override_settings(META_CREDENTIALS_KEY=KEY):
        return MetaSettings.objects.update_or_create(
            site=site,
            defaults={
                "pixel_id": pixel, "token_encrypted": crypto.encrypt(token) if token else "",
                "test_event_code": code,
            },
        )[0]


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = json.dumps(body).encode() if body is not None else b""

    def read(self, size=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(status, code=None, fbtrace_id=None):
    body = json.dumps({"error": {"code": code, "message": "SECRET MESSAGE FROM META", "fbtrace_id": fbtrace_id}}).encode()
    return urllib.error.HTTPError("https://graph.facebook.com/x", status, "x", {}, io.BytesIO(body))


class FakeMeta:
    """Patches the network. `answers` is a list used in order (the last repeats): a status
    number, a (number, body) pair for an answer with a JSON body, or an exception to raise."""

    def __init__(self, *answers):
        self.answers = list(answers) or [200]
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        answer = self.answers[min(len(self.requests), len(self.answers)) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return FakeResponse(*answer) if isinstance(answer, tuple) else FakeResponse(answer)

    def __enter__(self):
        self.patcher = mock.patch("apps.meta_ads.send._open", self)
        self.patcher.start()
        return self

    def __exit__(self, *exc):
        self.patcher.stop()
        return False

    def bodies(self):
        return [json.loads(request.data) for request, _ in self.requests]

    def events(self):
        return [body["data"][0] for body in self.bodies()]
