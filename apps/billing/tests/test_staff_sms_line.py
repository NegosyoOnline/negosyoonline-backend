"""HQ's Disable / Re-enable of a Site's own SMS line under /staff/v1/subscribers/sms-line/: behind
HQ_SUBSCRIBERS_ENABLED and SMS_SITE_LINES_ENABLED, a reason and a fresh authenticator code,
one audit entry each, one active SmsLineDisable row per Site, and the effect on the owner API."""
import json
import re

from django.core import mail
from django.core.cache import cache
from django.test import Client, override_settings

from apps.accounts.tests.base import ORIGIN
from apps.billing.tests.base import make_live_subscription as _make_subscription
from apps.notifications import sms
from apps.notifications.models import SmsDevice, SmsLineDisable
from apps.notifications.tests.base import gateway_on, make_phone
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff
from apps.sites.models import Site

DISABLE = "/staff/v1/subscribers/sms-line/disable/"
REENABLE = "/staff/v1/subscribers/sms-line/reenable/"
DETAIL = "/staff/v1/subscribers/detail/"
ACTIONS = ("hq_sms_line_disabled", "hq_sms_line_reenabled", "hq_sms_line_reauth_failed")


@on_staff
@gateway_on
@override_settings(
    HQ_SUBSCRIBERS_ENABLED=True, SMS_SITE_LINES_ENABLED=True, BILLING_ENABLED=True,
    BILLING_LAUNCH_PROMO_ENABLED=True, SITE_PARENT_HOST="shops.test",
    SMS_GATEWAY_PUBLIC_URL="http://127.0.0.1:8000", API_ALLOWED_ORIGINS=[ORIGIN],
    ACCOUNT_EMAIL_CHANGE_HARDENED=True,
)
class SmsLineStaffTestCase(StaffTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.identity = self.enrolled()
        self.sub = _make_subscription(email="ana@example.test", name="anas-store")
        self.site = self.sub.site
        self.other = _make_subscription(email="ben@example.test", name="bens-store").site

    def fresh(self):
        self.identity.refresh_from_db()
        self.identity.totp_last_step = 0
        self.identity.save(update_fields=["totp_last_step"])
        return self.code()

    def line(self, path, site=None, **fields):
        body = {"site_id": (site or self.site).public_id, "reason": "Abuse reported", **fields}
        if "code" not in body:
            body["code"] = self.fresh()
        return self.call("POST", path, body)

    def audit(self, *actions):
        return list(OperatorAuditEntry.objects.filter(action__in=actions or ACTIONS).order_by("pk"))

    def active(self, site=None):
        return SmsLineDisable.objects.filter(site=site or self.site, reenabled_at__isnull=True)


class SwitchAndSessionTests(SmsLineStaffTestCase):
    def test_either_switch_off_answers_404_and_writes_nothing(self):
        self.staff_sign_in()
        for flags in ({"HQ_SUBSCRIBERS_ENABLED": False}, {"SMS_SITE_LINES_ENABLED": False}):
            with override_settings(**flags):
                for path in (DISABLE, REENABLE):
                    self.assertError(self.line(path), 404, "not_found")
        self.assertFalse(SmsLineDisable.objects.exists())
        self.assertEqual(self.audit(), [])

    def test_no_session_and_another_origin_are_refused(self):
        body = {"site_id": self.site.public_id, "reason": "x", "code": "123456"}
        for path in (DISABLE, REENABLE):
            self.assertError(self.call("POST", path, body), 401, "unauthenticated")
        self.staff_sign_in()
        self.assertError(
            self.call("POST", DISABLE, body, origin="https://evil.example"), 403, "origin_not_allowed"
        )
        self.assertFalse(SmsLineDisable.objects.exists())


class DisableTests(SmsLineStaffTestCase):
    def setUp(self):
        super().setUp()
        self.staff_sign_in()

    def test_disable_writes_one_row_one_audit_entry_and_answers_the_line_state(self):
        response = self.line(DISABLE, reason="  Abuse reported  ")
        self.assertEqual(response.status_code, 200, response.content)
        state = response.json()["sms_line"]
        self.assertEqual(
            (state["disabled"], state["reason"], state["has_line"]), (True, "Abuse reported", False)
        )
        row = self.active().get()
        self.assertEqual((row.disabled_by, row.reason), (self.identity.user, "Abuse reported"))
        entries = self.audit("hq_sms_line_disabled")
        self.assertEqual(len(entries), 1)
        self.assertEqual(
            (entries[0].target_id, entries[0].reason, entries[0].operator),
            (self.site.public_id, "Abuse reported", self.identity.user),
        )
        self.assertFalse(sms.site_has_line(self.site))

    def test_reason_is_required_trimmed_and_bounded(self):
        for reason in ("", "   ", None, 5, "x" * 301):
            with self.subTest(reason=reason):
                self.assertError(self.line(DISABLE, reason=reason), 400, "invalid_request")
        self.assertEqual(self.line(DISABLE, reason="y" * 300).status_code, 200)

    def test_a_fresh_authenticator_code_is_required(self):
        for code in (None, "", "12345", self.wrong_code(), self.recovery_codes[0]):
            with self.subTest(code=code):
                self.assertError(self.line(DISABLE, code=code), 403, "reauth_failed")
        used = self.fresh()
        self.assertEqual(self.line(DISABLE, code=used).status_code, 200)
        self.assertError(self.line(REENABLE, code=used), 403, "reauth_failed")  # a used code
        self.assertEqual(len(self.audit("hq_sms_line_reauth_failed")), 6)
        self.assertEqual(self.active().count(), 1)

    def test_disabling_twice_is_409_and_leaves_one_row(self):
        self.line(DISABLE)
        self.assertError(self.line(DISABLE), 409, "already_disabled")
        self.assertEqual(SmsLineDisable.objects.filter(site=self.site).count(), 1)
        self.assertEqual(len(self.audit("hq_sms_line_disabled")), 1)

    def test_a_site_that_does_not_exist_is_404(self):
        for site_id in ("site_nope", 5, ""):
            self.assertError(
                self.call("POST", DISABLE, {"site_id": site_id, "reason": "x", "code": self.fresh()}),
                404 if site_id == "site_nope" else 400,
                "not_found" if site_id == "site_nope" else "invalid_request",
            )

    def test_a_staff_action_names_one_site_only(self):
        self.line(DISABLE)
        self.assertEqual(list(SmsLineDisable.objects.values_list("site_id", flat=True)), [self.site.pk])
        self.assertTrue(sms.site_has_line(self.other))


class ReenableTests(SmsLineStaffTestCase):
    def setUp(self):
        super().setUp()
        self.staff_sign_in()

    def test_reenable_stamps_the_row_and_audits(self):
        self.line(DISABLE)
        response = self.line(REENABLE, reason="Resolved")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["sms_line"]["disabled"], False)
        row = SmsLineDisable.objects.get()
        self.assertIsNotNone(row.reenabled_at)
        self.assertEqual(len(self.audit("hq_sms_line_reenabled")), 1)
        self.assertEqual(self.audit("hq_sms_line_reenabled")[0].reason, "Resolved")
        self.assertTrue(sms.site_has_line(self.site))

    def test_reenabling_a_line_that_is_not_disabled_is_409_and_writes_nothing(self):
        self.assertError(self.line(REENABLE), 409, "not_disabled")
        self.line(DISABLE)
        self.line(REENABLE)
        self.assertError(self.line(REENABLE), 409, "not_disabled")
        self.assertEqual(SmsLineDisable.objects.count(), 1)
        self.assertEqual(len(self.audit("hq_sms_line_reenabled")), 1)

    def test_reenable_needs_a_reason_and_a_code(self):
        self.line(DISABLE)
        self.assertError(self.line(REENABLE, reason=""), 400, "invalid_request")
        self.assertError(self.line(REENABLE, code="000000"), 403, "reauth_failed")
        self.assertEqual(self.active().count(), 1)

    def test_disable_again_after_reenable_keeps_the_history(self):
        self.line(DISABLE)
        self.line(REENABLE)
        self.assertEqual(self.line(DISABLE, reason="Again").status_code, 200)
        self.assertEqual(SmsLineDisable.objects.filter(site=self.site).count(), 2)
        self.assertEqual(self.active().get().reason, "Again")


class DetailTests(SmsLineStaffTestCase):
    def test_the_detail_shows_the_line_state(self):
        self.staff_sign_in()
        make_phone("line-a", "tok-a", site=self.site)
        shown = self.call("POST", DETAIL, {"site_id": self.site.public_id}).json()["sms_line"]
        self.assertEqual(shown, {
            "has_line": True, "has_phone": True, "disabled": False, "reason": None, "disabled_at": None,
        })
        self.line(DISABLE)
        shown = self.call("POST", DETAIL, {"site_id": self.site.public_id}).json()["sms_line"]
        self.assertEqual((shown["disabled"], shown["reason"], shown["has_line"]), (True, "Abuse reported", False))
        self.assertIsNotNone(shown["disabled_at"])
        self.assertNotIn("tok-a", str(shown))

    def test_the_detail_has_no_line_state_while_the_switch_is_off(self):
        self.staff_sign_in()
        with override_settings(SMS_SITE_LINES_ENABLED=False):
            body = self.call("POST", DETAIL, {"site_id": self.site.public_id}).json()
        self.assertIsNone(body["sms_line"])


class EffectTests(SmsLineStaffTestCase):
    """A disabled Site gets no texts on its phone and its owner can no longer pair or rotate."""

    def owner(self):
        """Ana, signed in to the owner API with an email code; returns her client and a poster."""
        client = Client()

        def owner_post(path, data):
            return client.post(
                path, data=json.dumps(data), content_type="application/json", secure=True,
                HTTP_ORIGIN=ORIGIN,
            )

        email = "ana@example.test"
        asked = owner_post("/v1/auth/email-code/request/", {"email": email})
        self.assertEqual(asked.status_code, 204, asked.content)
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        verified = owner_post(
            "/v1/auth/email-code/verify/", {"email": email, "code": code, "accept_policies": True}
        )
        self.assertEqual(verified.status_code, 200, verified.content)
        return client, owner_post

    def test_disable_takes_the_phone_out_of_routing_and_the_owner_api_answers_404(self):
        owner, owner_post = self.owner()
        base = f"/v1/sites/{self.site.public_id}/sms-gateway/"
        platform = make_phone("system", "platform-token")
        paired = owner_post(base + "register/", {})
        self.assertEqual(paired.status_code, 201, paired.content)
        phone = SmsDevice.objects.get(site=self.site)
        self.assertEqual(sms.line_for(self.site), phone)

        self.staff_sign_in()
        self.assertEqual(self.line(DISABLE).status_code, 200)
        self.assertEqual(sms.line_for(self.site), platform)
        for tail in ("", "register/", "rotate/", "disable/"):
            get = owner.get(base + tail, secure=True) if tail == "" else owner_post(base + tail, {})
            self.assertEqual(get.status_code, 404, tail)

        self.assertEqual(self.line(REENABLE).status_code, 200)
        self.assertEqual(sms.line_for(self.site), phone)
        self.assertEqual(owner.get(base, secure=True).status_code, 200)

    def test_another_sites_line_is_untouched(self):
        other_phone = make_phone("line-b", "tok-b", site=self.other)
        self.staff_sign_in()
        self.line(DISABLE)
        self.assertEqual(sms.line_for(self.other), other_phone)
        self.assertEqual(Site.objects.filter(pk=self.other.pk).count(), 1)
