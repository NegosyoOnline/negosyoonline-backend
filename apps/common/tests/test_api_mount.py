from django.test import TestCase


class ApiMountTests(TestCase):
    def test_openapi_description_is_published(self):
        response = self.client.get("/v1/openapi.json", secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["info"]["title"], "NegosyoOnline API")

    def test_unknown_v1_path_answers_with_the_error_shape(self):
        response = self.client.get("/v1/no-such-endpoint/", secure=True)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_healthz_still_works(self):
        response = self.client.get("/healthz/", secure=True)

        self.assertEqual(response.status_code, 200)
