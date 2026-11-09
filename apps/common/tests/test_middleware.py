from django.test import TestCase, override_settings

DASH = "https://dash.example.test"
STOREFRONT = "https://shop1.example.test"


@override_settings(API_ALLOWED_ORIGINS=[DASH])
class ApiOriginMiddlewareTests(TestCase):
    def test_allowed_origin_gets_cors_headers_with_credentials(self):
        response = self.client.get("/v1/openapi.json", secure=True, HTTP_ORIGIN=DASH)

        self.assertEqual(response["Access-Control-Allow-Origin"], DASH)
        self.assertEqual(response["Access-Control-Allow-Credentials"], "true")
        self.assertIn("Origin", response["Vary"])

    def test_other_origin_gets_no_cors_headers_on_reads(self):
        response = self.client.get("/v1/openapi.json", secure=True, HTTP_ORIGIN=STOREFRONT)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_other_origin_is_refused_on_writes(self):
        response = self.client.post("/v1/anything/", secure=True, HTTP_ORIGIN=STOREFRONT)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "origin_not_allowed")

    def test_write_without_origin_passes_the_middleware(self):
        response = self.client.post("/v1/anything/", secure=True)

        self.assertEqual(response.status_code, 404)

    def test_preflight_from_allowed_origin(self):
        response = self.client.options(
            "/v1/anything/",
            secure=True,
            HTTP_ORIGIN=DASH,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["Access-Control-Allow-Origin"], DASH)
        self.assertIn("POST", response["Access-Control-Allow-Methods"])

    def test_preflight_from_other_origin_has_no_cors_headers(self):
        response = self.client.options(
            "/v1/anything/",
            secure=True,
            HTTP_ORIGIN=STOREFRONT,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )

        self.assertEqual(response.status_code, 204)
        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_paths_outside_v1_are_untouched(self):
        response = self.client.get("/healthz/", secure=True, HTTP_ORIGIN=DASH)

        self.assertNotIn("Access-Control-Allow-Origin", response)
