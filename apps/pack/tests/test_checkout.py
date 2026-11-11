"""The contact step, the live check and the checkout token."""
from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.members.models import Member
from apps.pack import constants as C
from apps.pack.models import CheckoutToken
from apps.pack.tests.base import BASE, HOST, NOT_FOUND, OTHER_HOST, PackTestCase, EMAIL, MOBILE


class ContactTests(PackTestCase):
    def test_first_call_answers_the_raw_token_once_and_stores_only_its_hash(self):
        first = self.contact().json()
        self.assertTrue(first["token"])
        row = self.token_row(first["token"])
        self.assertNotEqual(row.token_hash, first["token"])
        self.assertEqual(row.pending_contact["email"], EMAIL)
        again = self.post("contact/", token=first["token"], **self.contact_fields()).json()
        self.assertEqual(again["token"], "")
        self.assertEqual(CheckoutToken.objects.count(), 1)

    def test_a_contact_call_creates_no_member(self):
        self.start()
        self.assertEqual(Member.objects.count(), 0)

    def test_every_use_slides_the_expiry_two_hours_on(self):
        raw = self.start()
        CheckoutToken.objects.update(expires_at=timezone.now() + timedelta(minutes=5))
        self.post("contact/", token=raw, **self.contact_fields())
        left = (self.token_row(raw).expires_at - timezone.now()).total_seconds()
        self.assertGreater(left, C.TOKEN_TTL_SECONDS - 60)

    def test_invalid_contact_is_422_with_field_messages_and_no_token(self):
        response = self.contact(full_name="", mobile="123", email="nope")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["message"], C.MSG_CHECK_FIELDS)
        self.assertEqual(CheckoutToken.objects.count(), 0)

    def test_the_agreement_and_enrollment_ticks_are_required(self):
        self.assertEqual(self.contact(agreement_accept="").status_code, 422)
        self.assertEqual(self.contact(agreement_accept="").json()["error"]["code"], "agreement_required")
        refused = self.contact(enrollment_authorized="")
        self.assertEqual(refused.json()["error"]["message"], C.MSG_ENROLLMENT_REQUIRED)
        self.assertEqual(CheckoutToken.objects.count(), 0)

    def test_an_unknown_agreement_version_is_required_not_changed(self):
        refused = self.contact(agreement_version_id="999999")
        self.assertEqual(refused.json()["error"]["code"], "agreement_required")

    def test_a_member_with_a_live_order_blocks_both_contacts(self):
        self.placed()
        refused = self.contact()
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json()["error"]["code"], "contact_in_use")

    def test_a_contact_held_in_another_live_store_is_refused(self):
        self.placed()
        refused = self.post("contact/", host=OTHER_HOST, **self.contact_fields())
        self.assertEqual(refused.status_code, 409)

    def test_an_abandoned_checkout_holds_no_contact(self):
        from apps.members import services
        services.create_member(self.site, full_name="Old", mobile=MOBILE, email=EMAIL,
                               email_state="pending", source="onboarding")
        self.assertEqual(self.contact().status_code, 200)


class ThrottleTests(PackTestCase):
    def test_contact_is_limited_per_ip_to_20_an_hour_before_any_judgement(self):
        for _ in range(20):
            self.contact(email="bad")
        response = self.contact(email="bad")
        self.assertEqual(response.status_code, 429)

    def test_contact_is_limited_per_token_to_10_an_hour(self):
        raw = self.start()
        for i in range(10):
            self.post("contact/", token=raw, ip=f"203.0.113.{i + 1}", **self.contact_fields())
        self.assertEqual(
            self.post("contact/", token=raw, ip="203.0.113.99", **self.contact_fields()).status_code, 429
        )
        self.assertEqual(self.post("contact/", token=raw, ip="203.0.113.98", **self.contact_fields()).status_code, 429)

    def test_check_is_limited_to_60_an_hour_per_ip(self):
        for _ in range(60):
            self.post("check/", mobile="", email="")
        self.assertEqual(self.post("check/", mobile="", email="").status_code, 429)


class CheckTests(PackTestCase):
    def check(self, mobile="", email=""):
        response = self.post("check/", mobile=mobile, email=email)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["errors"]

    def test_free_contacts_have_no_errors_and_nothing_is_saved(self):
        self.assertEqual(self.check(MOBILE, EMAIL), {})
        self.assertEqual(CheckoutToken.objects.count(), 0)

    def test_a_malformed_email_is_marked_a_partial_mobile_is_not(self):
        self.assertIn("email", self.check("0917", "nope"))
        self.assertNotIn("mobile", self.check("0917", ""))

    def test_a_taken_contact_is_marked(self):
        self.placed()
        errors = self.check(MOBILE, EMAIL)
        self.assertIn("mobile", errors)

    def test_each_contact_of_a_member_with_a_live_order_is_marked_alone(self):
        self.placed()
        self.assertIn("email", self.check("", EMAIL))
        self.assertIn("mobile", self.check(MOBILE, ""))


class SurfaceTests(PackTestCase):
    def test_off_switch_answers_404(self):
        with override_settings(PACK_ENABLED=False):
            self.assertEqual(self.contact().status_code, 404)
            self.assertEqual(self.post("check/", mobile="", email="").status_code, 404)

    def test_unknown_foreign_and_non_sante_hosts_share_one_404(self):
        from apps.sante.tests.base import retemplate
        retemplate(self.other_site)
        for host in ("nobody.negosyotest.com", OTHER_HOST, "example.org"):
            response = self.post("check/", host=host, mobile="", email="")
            self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND), host)

    def test_the_server_credential_is_required(self):
        self.assertEqual(self.post("check/", secret=None, mobile="", email="").status_code, 401)
        self.assertEqual(self.post("check/", secret="wrong", mobile="", email="").status_code, 401)

    def test_a_token_of_another_site_is_not_valid_here(self):
        raw = self.start()
        response = self.post("contact/", host=OTHER_HOST, token=raw, **self.contact_fields(
            email="x@buyer.test", mobile="09170000001"))
        self.assertEqual(CheckoutToken.objects.count(), 2)  # a new token, the foreign one ignored
        self.assertTrue(response.json()["token"])
