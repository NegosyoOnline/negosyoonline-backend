"""HQ's Suspend / Resume of a store under /staff/v1/subscribers/suspend/ and /resume/ (H3): behind
HQ_SUBSCRIBERS_ENABLED, a reason, `confirm: true` and a fresh authenticator code, one audit entry
each, `Site.operator_hold` only (billing and the deletion clock untouched), and the effect on the
public host, the owner API, the owner's texts, the buyer portal and the owner's email."""
from django.core import mail
from django.core.cache import cache
from django.test import override_settings

from apps.accounts.tests.base import ORIGIN
from apps.billing.models import BillingNotice, Subscription
from apps.billing.tests.base import T0, days, make_subscription as _make_subscription
from apps.inquiries import sms_inbox
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff
from apps.sites.models import Site

SUSPEND = "/staff/v1/subscribers/suspend/"
RESUME = "/staff/v1/subscribers/resume/"
LIST = "/staff/v1/subscribers/list/"
DETAIL = "/staff/v1/subscribers/detail/"
ACTIONS = ("hq_site_suspended", "hq_site_resumed", "hq_site_reauth_failed")
REASON = "Spam listings reported by several buyers"


@on_staff
@override_settings(
    HQ_SUBSCRIBERS_ENABLED=True, BILLING_ENABLED=True, BILLING_LAUNCH_PROMO_ENABLED=True,
    SITE_PARENT_HOST="shops.test", API_ALLOWED_ORIGINS=[ORIGIN],
)
class HoldTestCase(StaffTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.identity = self.enrolled()
        self.sub = _make_subscription(email="ana@example.test", name="anas-store", now=T0 + days(1))
        self.site = self.sub.site
        self.other = _make_subscription(email="ben@example.test", name="bens-store", now=T0 + days(1)).site
        self.staff_sign_in()

    def fresh(self):
        self.identity.refresh_from_db()
        self.identity.totp_last_step = 0
        self.identity.save(update_fields=["totp_last_step"])
        return self.code()

    def hold(self, path, site=None, **fields):
        body = {"site_id": (site or self.site).public_id, "reason": REASON, "confirm": True, **fields}
        if "code" not in body:
            body["code"] = self.fresh()
        return self.call("POST", path, body)

    def audit(self, *actions):
        return list(OperatorAuditEntry.objects.filter(action__in=actions or ACTIONS).order_by("pk"))

    def held(self, site=None):
        return Site.objects.get(pk=(site or self.site).pk).operator_hold


class SwitchAndAccessTests(HoldTestCase):
    def test_switch_off_answers_404_and_changes_nothing(self):
        with override_settings(HQ_SUBSCRIBERS_ENABLED=False):
            for path in (SUSPEND, RESUME):
                self.assertError(self.hold(path), 404, "not_found")
        self.assertFalse(self.held())
        self.assertEqual(self.audit(), [])

    def test_no_session_and_another_origin_are_refused(self):
        body = {"site_id": self.site.public_id, "reason": REASON, "confirm": True, "code": "123456"}
        self.assertError(
            self.call("POST", SUSPEND, body, origin="https://evil.example"), 403, "origin_not_allowed"
        )
        self.assertFalse(self.held())

    def test_a_signed_out_client_gets_401(self):
        self.client.cookies.clear()
        body = {"site_id": self.site.public_id, "reason": REASON, "confirm": True, "code": "123456"}
        for path in (SUSPEND, RESUME):
            self.assertError(self.call("POST", path, body), 401, "unauthenticated")
        self.assertFalse(self.held())


class SuspendTests(HoldTestCase):
    def test_suspend_sets_the_hold_audits_with_the_reason_and_answers_held(self):
        response = self.hold(SUSPEND, reason=f"  {REASON}  ")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"site_id": self.site.public_id, "held": True})
        self.assertTrue(self.held())
        self.assertFalse(self.held(self.other))
        entries = self.audit("hq_site_suspended")
        self.assertEqual(len(entries), 1)
        self.assertEqual(
            (entries[0].target_id, entries[0].reason, entries[0].operator),
            (self.site.public_id, REASON, self.identity.user),
        )

    def test_billing_and_the_deletion_clock_are_untouched(self):
        before = Subscription.objects.filter(pk=self.sub.pk).values().get()
        self.hold(SUSPEND)
        self.assertEqual(Subscription.objects.filter(pk=self.sub.pk).values().get(), before)
        self.hold(RESUME)
        self.assertEqual(Subscription.objects.filter(pk=self.sub.pk).values().get(), before)

    def test_reason_is_required_trimmed_and_bounded(self):
        for reason in ("", "   ", None, 5, "too short", "x" * 501):
            with self.subTest(reason=reason):
                self.assertError(self.hold(SUSPEND, reason=reason), 400, "invalid_request")
        self.assertFalse(self.held())
        self.assertEqual(self.hold(SUSPEND, reason="y" * 500).status_code, 200)

    def test_confirm_must_be_true(self):
        for confirm in (None, False, "true", 1):
            with self.subTest(confirm=confirm):
                self.assertError(self.hold(SUSPEND, confirm=confirm), 400, "confirm_required")
        self.assertFalse(self.held())

    def test_a_fresh_authenticator_code_is_required_and_recovery_codes_are_refused(self):
        for code in (None, "", "12345", self.wrong_code(), self.recovery_codes[0]):
            with self.subTest(code=code):
                self.assertError(self.hold(SUSPEND, code=code), 403, "reauth_failed")
        self.assertFalse(self.held())
        failures = self.audit("hq_site_reauth_failed")
        self.assertEqual(len(failures), 5)
        self.assertNotIn("code", str(failures[0].detail).replace("failed_attempts", ""))
        used = self.fresh()
        self.assertEqual(self.hold(SUSPEND, code=used).status_code, 200)
        self.assertError(self.hold(RESUME, code=used), 403, "reauth_failed")  # a used code
        self.assertTrue(self.held())

    def test_suspending_twice_is_409_and_changes_nothing(self):
        self.hold(SUSPEND)
        self.assertError(self.hold(SUSPEND), 409, "already_suspended")
        self.assertEqual(len(self.audit("hq_site_suspended")), 1)
        self.assertEqual(len([m for m in mail.outbox if "paused" in m.subject]), 1)

    def test_an_unknown_site_is_404_and_a_bad_id_is_400(self):
        self.assertError(
            self.call("POST", SUSPEND, {
                "site_id": "site_nope", "reason": REASON, "confirm": True, "code": self.fresh()}),
            404, "not_found",
        )
        self.assertError(
            self.call("POST", SUSPEND, {
                "site_id": 5, "reason": REASON, "confirm": True, "code": self.fresh()}),
            400, "invalid_request",
        )

    def test_a_removed_site_is_404(self):
        from django.utils import timezone

        Site.objects.filter(pk=self.site.pk).update(deleted_at=timezone.now())
        self.assertError(self.hold(SUSPEND), 404, "not_found")


class ResumeTests(HoldTestCase):
    def test_resume_clears_the_hold_and_audits(self):
        self.hold(SUSPEND)
        response = self.hold(RESUME, reason="Cleared after review")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"site_id": self.site.public_id, "held": False})
        self.assertFalse(self.held())
        entries = self.audit("hq_site_resumed")
        self.assertEqual((len(entries), entries[0].reason), (1, "Cleared after review"))

    def test_resuming_a_store_that_is_not_suspended_is_409(self):
        self.assertError(self.hold(RESUME), 409, "not_suspended")
        self.hold(SUSPEND)
        self.hold(RESUME)
        self.assertError(self.hold(RESUME), 409, "not_suspended")
        self.assertEqual(len(self.audit("hq_site_resumed")), 1)

    def test_resume_needs_a_reason_confirm_and_a_code(self):
        self.hold(SUSPEND)
        self.assertError(self.hold(RESUME, reason=""), 400, "invalid_request")
        self.assertError(self.hold(RESUME, confirm=None), 400, "confirm_required")
        self.assertError(self.hold(RESUME, code="000000"), 403, "reauth_failed")
        self.assertTrue(self.held())

    def test_suspend_again_after_resume_works_and_each_change_has_its_own_entry(self):
        self.hold(SUSPEND)
        self.hold(RESUME)
        self.assertEqual(self.hold(SUSPEND).status_code, 200)
        self.assertEqual(len(self.audit("hq_site_suspended", "hq_site_resumed")), 3)
        self.assertEqual(BillingNotice.objects.filter(code="hq_site_paused").count(), 2)


class OwnerEmailTests(HoldTestCase):
    def setUp(self):
        super().setUp()
        mail.outbox.clear()

    def test_one_email_on_suspend_and_one_on_resume_without_the_reason(self):
        self.hold(SUSPEND)
        self.assertEqual(len(mail.outbox), 1)
        paused = mail.outbox[0]
        self.assertEqual(paused.to, ["ana@example.test"])
        self.assertIn(self.site.display_name, paused.body)
        self.assertIn("paused by NegosyoOnline under our Terms of Service", paused.body)
        self.assertNotIn(REASON, paused.body + paused.subject)
        self.hold(RESUME)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("is back online", mail.outbox[1].body)
        self.assertNotIn(REASON, mail.outbox[1].body)

    def test_a_refused_call_sends_nothing(self):
        self.hold(SUSPEND, code="000000")
        self.hold(RESUME)
        self.assertEqual(mail.outbox, [])


class ListAndDetailTests(HoldTestCase):
    def rows(self, **body):
        return {r["site_id"]: r for r in self.call("POST", LIST, body).json()["rows"]}

    def test_held_shows_in_the_list_the_detail_and_the_suspended_ended_filter(self):
        rows = self.rows()
        self.assertEqual((rows[self.site.public_id]["held"], rows[self.other.public_id]["held"]), (False, False))
        self.assertEqual(self.rows(filter="suspended_ended"), {})
        self.hold(SUSPEND)
        rows = self.rows()
        self.assertEqual((rows[self.site.public_id]["held"], rows[self.other.public_id]["held"]), (True, False))
        self.assertEqual(list(self.rows(filter="suspended_ended")), [self.site.public_id])
        detail = self.call("POST", DETAIL, {"site_id": self.site.public_id}).json()
        self.assertIs(detail["held"], True)
        self.assertEqual(detail["held_label"], "Suspended by HQ")
        self.assertIsNone(rows[self.other.public_id]["held_label"])
        self.hold(RESUME)
        self.assertEqual(self.rows(filter="suspended_ended"), {})
        self.assertIs(self.call("POST", DETAIL, {"site_id": self.site.public_id}).json()["held"], False)


class EffectTests(HoldTestCase):
    def test_a_held_store_answers_404_on_the_public_host_and_comes_back_on_resume(self):
        url = "/v1/public/sites/by-host/?host=anas-store.shops.test"
        self.assertEqual(self.client.get(url, secure=True).status_code, 200)
        self.hold(SUSPEND)
        self.assertEqual(self.client.get(url, secure=True).status_code, 404)
        self.hold(RESUME)
        self.assertEqual(self.client.get(url, secure=True).status_code, 200)

    def test_the_owner_texts_stop_and_start_again(self):
        self.assertNotEqual(sms_inbox.unavailable(self.site), sms_inbox.SITE_HELD)
        self.hold(SUSPEND)
        self.assertEqual(sms_inbox.unavailable(Site.objects.get(pk=self.site.pk)), sms_inbox.SITE_HELD)
        self.assertNotEqual(sms_inbox.unavailable(Site.objects.get(pk=self.other.pk)), sms_inbox.SITE_HELD)
        self.hold(RESUME)
        self.assertNotEqual(sms_inbox.unavailable(Site.objects.get(pk=self.site.pk)), sms_inbox.SITE_HELD)
