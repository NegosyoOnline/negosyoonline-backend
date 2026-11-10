from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.common.errors import ApiError
from apps.common.middleware import require_allowed_origin

DASH = "https://dash.example.test"


@override_settings(API_ALLOWED_ORIGINS=[DASH])
class RequireAllowedOriginTests(SimpleTestCase):
    def test_reads_pass_without_an_origin(self):
        require_allowed_origin(RequestFactory().get("/v1/me/"))

    def test_write_from_an_allowed_origin_passes(self):
        require_allowed_origin(RequestFactory().post("/v1/auth/logout/", HTTP_ORIGIN=DASH))

    def test_write_without_or_with_another_origin_is_refused(self):
        for extra in ({}, {"HTTP_ORIGIN": "https://shop1.example.test"}):
            with self.assertRaises(ApiError) as raised:
                require_allowed_origin(RequestFactory().post("/v1/auth/logout/", **extra))
            self.assertEqual(raised.exception.code, "origin_required")
            self.assertEqual(raised.exception.status, 403)
