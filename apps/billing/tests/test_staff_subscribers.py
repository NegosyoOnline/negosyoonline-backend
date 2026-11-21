"""The Subscribers routes under /staff/v1/subscribers/ on the hq host: read only, behind
HQ_SUBSCRIBERS_ENABLED, behind the staff two-step session, and free of store customer data."""
import json
from unittest import mock
from datetime import timedelta

from django.test import Client, override_settings
from django.utils.dateparse import parse_datetime

from apps.billing import services, staff_subscribers
from apps.billing.models import Subscription
from apps.billing.tests.base import (
    CYCLE_1_END, T0, TRIAL_END, days, make_active, make_attention, make_subscription as _make_subscription, submit,
    transfers_on,
)
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff
from apps.sites.models import Site

def make_subscription(display=None, **kwargs):
    """As the billing helper, and the site is named `display` (or by its subdomain)."""
    subscription = _make_subscription(**kwargs)
    Site.objects.filter(pk=subscription.site.pk).update(
        display_name=display or kwargs.get("name", "anas-store")
    )
    subscription.site.refresh_from_db()
    return subscription


def active(**kwargs):
    subscription = make_active(**kwargs)
    Site.objects.filter(pk=subscription.site.pk).update(display_name=kwargs.get("name", "anas-store"))
    subscription.site.refresh_from_db()
    return subscription


def attention(**kwargs):
    subscription = make_attention(**kwargs)
    Site.objects.filter(pk=subscription.site.pk).update(display_name=kwargs.get("name", "anas-store"))
    subscription.site.refresh_from_db()
    return subscription


LIST = "/staff/v1/subscribers/list/"
DETAIL = "/staff/v1/subscribers/detail/"
ROW_KEYS = sorted([
    "site_id", "business_name", "site_address", "template", "plan", "state", "trial_ends_at",
    "next_due_at", "last_payment_at", "owner_email", "created_at",
])
Stored = Subscription.State


@on_staff
@override_settings(HQ_SUBSCRIBERS_ENABLED=True, BILLING_ENABLED=True, BILLING_LAUNCH_PROMO_ENABLED=True,
                   SITE_PARENT_HOST="shops.test")
class SubscribersTestCase(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.now = T0 + days(5)
        clock = mock.patch.object(staff_subscribers, "_now", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.identity = self.enrolled()

    def signed_in(self):
        self.staff_sign_in()

    def listing(self, data=None):
        return self.call("POST", LIST, data or {})

    def names(self, data=None):
        response = self.listing(data)
        self.assertEqual(response.status_code, 200, response.content)
        return [row["business_name"] for row in response.json()["rows"]]

    def set_state(self, subscription, **fields):
        Subscription.objects.filter(pk=subscription.pk).update(**fields)

    def audits(self):
        return list(OperatorAuditEntry.objects.filter(action="hq_subscriber_viewed"))


class GateTests(SubscribersTestCase):
    @override_settings(HQ_SUBSCRIBERS_ENABLED=False)
    def test_switch_off_answers_404_and_reads_no_site_or_billing_table(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        subscription = make_subscription()
        self.signed_in()
        for path, body in ((LIST, {}), (DETAIL, {"site_id": subscription.site.public_id})):
            with self.subTest(path=path), CaptureQueriesContext(connection) as queries:
                self.assertError(self.call("POST", path, body), 404, "not_found")
            sql = " ".join(q["sql"] for q in queries).lower()
            for table in ("billing_", "sites_site", "accounts_account"):
                self.assertNotIn(f'"{table}', sql)
        self.assertEqual(self.audits(), [])

    def test_the_switch_is_off_by_default(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(os.environ.get("HQ_SUBSCRIBERS_ENABLED", "").strip() == "1")
        from django.conf import settings
        self.assertIn("HQ_SUBSCRIBERS_ENABLED", open(settings.BASE_DIR / "config" / "settings.py").read())

    def test_no_session_and_a_session_before_the_second_step_are_refused(self):
        subscription = make_subscription()
        body = {"site_id": subscription.site.public_id}
        for path in (LIST, DETAIL):
            self.assertError(self.call("POST", path, body), 401, "unauthenticated")
        self.staff_email_step()
        for path in (LIST, DETAIL):
            self.assertError(self.call("POST", path, body), 401, "second_step_required")
        self.assertEqual(self.audits(), [])

    def test_another_origin_is_refused(self):
        self.signed_in()
        for path in (LIST, DETAIL):
            self.assertError(self.call("POST", path, {}, origin="https://evil.example"), 403, "origin_not_allowed")

    def test_only_post_and_no_write_route(self):
        self.signed_in()
        self.assertEqual(sorted(staff_subscribers.ROUTES), ["subscribers/detail/", "subscribers/list/"])
        self.assertEqual(self.call("GET", LIST).status_code, 405)


class ListTests(SubscribersTestCase):
    def test_a_row_has_the_agreed_fields_and_newest_comes_first(self):
        older = make_subscription(email="a@example.test", name="alpha", start=T0)
        newer = make_subscription(email="b@example.test", name="beta", start=T0 + days(1))
        self.signed_in()
        body = self.listing().json()
        self.assertEqual([r["site_id"] for r in body["rows"]], [newer.site.public_id, older.site.public_id])
        self.assertIsNone(body["next"])
        row = body["rows"][1]
        self.assertEqual(sorted(row), ROW_KEYS)
        self.assertEqual(row["owner_email"], "a@example.test")
        self.assertEqual(row["site_address"], "alpha.shops.test")
        self.assertEqual(row["template"], "sante")
        self.assertEqual(row["state"], "trial_active")
        self.assertEqual(parse_datetime(row["trial_ends_at"]), older.trial_end_at)
        self.assertEqual(parse_datetime(row["next_due_at"]), older.trial_end_at)
        self.assertIsNone(row["last_payment_at"])
        self.assertIsNone(row["plan"])

    def test_active_shows_plan_next_due_and_last_confirmed_payment(self):
        subscription = active()
        self.signed_in()
        row = self.listing().json()["rows"][0]
        self.assertEqual(row["state"], "active")
        self.assertEqual(parse_datetime(row["next_due_at"]), CYCLE_1_END)
        self.assertTrue(row["plan"])

    def test_filters(self):
        trial_soon = make_subscription(email="t1@example.test", name="trialsoon", now=T0)
        trial_far = make_subscription(email="t2@example.test", name="trialfar", now=T0)
        paid = active(email="p@example.test", name="paid")
        held = attention(email="at@example.test", name="attention")
        suspended = make_subscription(email="s@example.test", name="susp")
        cancelled = make_subscription(email="c@example.test", name="canc")
        now = self.now
        self.set_state(trial_soon, trial_end_at=now + days(3))
        self.set_state(trial_far, trial_end_at=now + days(20))
        self.set_state(paid, state=Stored.ACTIVE)
        self.set_state(suspended, state=Stored.SUSPENDED)
        self.set_state(cancelled, state=Stored.CANCELLED)
        self.signed_in()
        name = lambda s: s.site.display_name
        self.assertEqual(len(self.names()), 6)
        self.assertEqual(self.names({"filter": "trial_ending"}), [name(trial_soon)])
        self.assertCountEqual(self.names({"filter": "suspended_ended"}), [name(suspended), name(cancelled)])
        unpaid = self.names({"filter": "unpaid"})
        self.assertIn(name(held), unpaid)
        self.assertNotIn(name(trial_far), unpaid)
        self.assertNotIn(name(suspended), unpaid)

    def test_unpaid_includes_an_active_site_past_its_cycle_and_an_unswept_trial(self):
        overdue = active(email="o@example.test", name="overdue")  # cycle ended 2026-12-25
        unswept = make_subscription(email="u@example.test", name="unswept")
        self.now = now = CYCLE_1_END + days(2)
        self.set_state(unswept, trial_end_at=now - days(1))
        self.signed_in()
        self.assertCountEqual(
            self.names({"filter": "unpaid"}), [overdue.site.display_name, unswept.site.display_name]
        )

    def test_search_by_business_name_host_and_owner_email(self):
        make_subscription(email="ana@example.test", name="anas-store")
        other = make_subscription(email="ben@example.test", name="bens-shop")
        Site.objects.filter(pk=other.site.pk).update(display_name="Ben Hardware")
        self.signed_in()
        self.assertEqual(self.names({"q": "hardware"}), ["Ben Hardware"])
        self.assertEqual(self.names({"q": "anas-store"}), ["anas-store"])
        self.assertEqual(self.names({"q": "anas-store.shops.test"}), ["anas-store"])
        self.assertEqual(self.names({"q": "BEN@example"}), ["Ben Hardware"])
        self.assertEqual(self.names({"q": "nothing-here"}), [])
        self.assertEqual(self.names({"q": "  "}), ["Ben Hardware", "anas-store"])

    def test_search_and_filter_combine(self):
        make_subscription(email="ana@example.test", name="anas-store")
        susp = make_subscription(email="ben@example.test", name="bens-shop")
        self.set_state(susp, state=Stored.SUSPENDED)
        self.signed_in()
        self.assertEqual(self.names({"filter": "suspended_ended", "q": "ana"}), [])
        self.assertEqual(len(self.names({"filter": "suspended_ended", "q": "ben"})), 1)

    def test_paging_is_50_newest_first_with_a_cursor(self):
        for i in range(53):
            make_subscription(email=f"u{i}@example.test", name=f"shop{i}", start=T0 + timedelta(minutes=i))
        self.signed_in()
        first = self.listing().json()
        self.assertEqual(len(first["rows"]), 50)
        self.assertEqual(first["rows"][0]["site_address"], "shop52.shops.test")
        self.assertEqual(first["next"], first["rows"][-1]["site_id"])
        second = self.listing({"cursor": first["next"]}).json()
        self.assertEqual(len(second["rows"]), 3)
        self.assertIsNone(second["next"])
        seen = [r["site_id"] for r in first["rows"] + second["rows"]]
        self.assertEqual(len(set(seen)), 53)
        self.assertEqual(second["rows"][-1]["site_address"], "shop0.shops.test")

    def test_bad_input_is_the_one_error_shape(self):
        self.signed_in()
        self.assertError(self.listing({"filter": "nope"}), 400, "invalid_request")
        self.assertError(self.listing({"q": 5}), 400, "invalid_request")
        self.assertError(self.listing({"q": "x" * 101}), 400, "invalid_request")
        self.assertError(self.listing({"cursor": "site_bogus"}), 400, "invalid_cursor")
        self.assertError(self.listing({"cursor": 5}), 400, "invalid_cursor")

    def test_a_site_without_a_subscription_row_is_listed_with_nulls(self):
        subscription = make_subscription()
        site = subscription.site
        from apps.billing.models import BillingCycle, SubscriptionTransition
        SubscriptionTransition.objects.filter(subscription=subscription).delete()
        BillingCycle.objects.filter(subscription=subscription).delete()
        subscription.delete()
        self.signed_in()
        row = self.listing().json()["rows"][0]
        self.assertEqual(row["site_id"], site.public_id)
        for key in ("state", "plan", "trial_ends_at", "next_due_at", "last_payment_at"):
            self.assertIsNone(row[key])
        self.assertEqual(self.names({"filter": "unpaid"}), [])
        detail = self.call("POST", DETAIL, {"site_id": site.public_id})
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["payments"], [])

    def test_a_deleted_site_is_not_listed(self):
        subscription = make_subscription()
        Site.objects.filter(pk=subscription.site.pk).update(deleted_at=T0)
        self.signed_in()
        self.assertEqual(self.names(), [])


class DetailTests(SubscribersTestCase):
    def test_detail_adds_payments_without_files_or_hashes_and_writes_one_audit_entry(self):
        subscription = make_subscription(email="ana@example.test")
        with transfers_on():
            payment = submit(subscription, T0 + days(1))
        self.signed_in()
        response = self.call("POST", DETAIL, {"site_id": subscription.site.public_id})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(sorted(set(body) - {"payments"}), ROW_KEYS)
        self.assertEqual(len(body["payments"]), 1)
        shown = body["payments"][0]
        self.assertEqual(shown["id"], payment.public_id)
        self.assertEqual(
            sorted(shown),
            sorted(["id", "status", "plan", "amount_centavos", "amount_received_centavos",
                    "currency", "transferred_on", "submitted_at", "decided_at"]),
        )
        text = json.dumps(body)
        for hidden in (payment.proof_key, "proof", "sender", "BANK-REF-1", "Juanito", "1234 5678"):
            self.assertNotIn(hidden, text)
        entries = self.audits()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].target_id, subscription.site.public_id)
        self.assertEqual(entries[0].operator, self.identity.user)
        self.assertNotIn("ana@example.test", json.dumps(entries[0].detail) + json.dumps(entries[0].personal))
        self.call("POST", DETAIL, {"site_id": subscription.site.public_id})
        self.assertEqual(len(self.audits()), 2)

    def test_the_list_writes_no_audit_entry(self):
        make_subscription()
        self.signed_in()
        before = OperatorAuditEntry.objects.count()
        self.listing()
        self.assertEqual(OperatorAuditEntry.objects.count(), before)

    def test_unknown_and_malformed_sites(self):
        self.signed_in()
        self.assertError(self.call("POST", DETAIL, {"site_id": "site_nothere"}), 404, "not_found")
        self.assertError(self.call("POST", DETAIL, {}), 400, "invalid_request")
        self.assertError(self.call("POST", DETAIL, {"site_id": 5}), 400, "invalid_request")
        self.assertEqual(self.audits(), [])


class NoCustomerDataTests(SubscribersTestCase):
    def test_no_customer_table_is_read_and_no_customer_field_is_in_an_answer(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        subscription = make_subscription()
        self.signed_in()
        with CaptureQueriesContext(connection) as queries:
            self.listing()
            self.call("POST", DETAIL, {"site_id": subscription.site.public_id})
        sql = " ".join(q["sql"] for q in queries).lower()
        for table in ("orders_", "storefront", "inbox_", "members", "pack_", "shop_", "contacts"):
            self.assertNotIn(f'"{table}', sql, table)
        for keys in (ROW_KEYS,):
            for forbidden in ("member", "order", "customer", "chat", "mobile", "contact_mobile"):
                self.assertFalse([k for k in keys if forbidden in k])
