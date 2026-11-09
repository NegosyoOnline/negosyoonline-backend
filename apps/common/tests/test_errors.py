from django.http import Http404
from django.test import SimpleTestCase
from ninja import NinjaAPI
from ninja.errors import HttpError
from ninja.testing import TestClient

from apps.common.errors import ApiError, error_body, register_error_handlers

# One module-level API with its own namespace: Ninja refuses two APIs that share one.
api = NinjaAPI(urls_namespace="test-errors")
register_error_handlers(api)


@api.get("/api-error")
def raise_api_error(request):
    raise ApiError("subdomain_taken", "That subdomain is taken.", status=409)


@api.get("/needs-int")
def needs_int(request, limit: int):
    return {"limit": limit}


@api.get("/missing")
def missing(request):
    raise Http404()


@api.get("/too-many")
def too_many(request):
    raise HttpError(429, "Slow down.")


@api.get("/crash")
def crash(request):
    raise RuntimeError("secret internal detail")


class ErrorShapeTests(SimpleTestCase):
    def setUp(self):
        self.client = TestClient(api)

    def test_error_body_omits_fields_when_there_are_none(self):
        self.assertEqual(
            error_body("x", "y"), {"error": {"code": "x", "message": "y"}}
        )

    def test_api_error_uses_its_code_message_and_status(self):
        response = self.client.get("/api-error")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"error": {"code": "subdomain_taken", "message": "That subdomain is taken."}},
        )

    def test_validation_error_is_400_and_names_the_field(self):
        response = self.client.get("/needs-int?limit=abc")

        self.assertEqual(response.status_code, 400)
        body = response.json()["error"]
        self.assertEqual(body["code"], "invalid_request")
        self.assertIn("limit", body["fields"])

    def test_http_404_uses_not_found(self):
        response = self.client.get("/missing")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_http_error_maps_status_to_code(self):
        response = self.client.get("/too-many")

        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json(),
            {"error": {"code": "rate_limited", "message": "Slow down."}},
        )

    def test_unexpected_exception_is_500_without_internal_detail(self):
        with self.assertLogs("apps.common.errors", level="ERROR"):
            response = self.client.get("/crash")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "server_error")
        self.assertNotIn("secret internal detail", response.content.decode())
