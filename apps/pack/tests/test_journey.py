"""The buyer journey read (ClickUp z8uq9meyqk): counts on fixtures, the period filter, fail-closed
routes, no leak across sites, no personal fields."""
from datetime import timedelta

from django.test import Client, override_settings
from django.utils import timezone

from apps.members.models import MemberSession
from apps.meta_ads.models import MetaDelivery, MetaSettings
from apps.pack import constants as C
from apps.pack.models import CheckoutToken, PackOrder, PackProofSubmission, SanteRegistration
from apps.pack.tests.owner_base import OwnerTestCase, make_order
from apps.sites.models import Site

_n = [0]


def token(site, at=None):
    _n[0] += 1
    row = CheckoutToken.objects.create(site=site, token_hash=f"{_n[0]:064d}", expires_at=timezone.now() + timedelta(days=1))
    if at:
        CheckoutToken.objects.filter(pk=row.pk).update(created_at=at)


def delivery(site, name, outcome, at=None):
    _n[0] += 1
    row = MetaDelivery.objects.create(site=site, event_name=name, event_id=f"e{_n[0]}", outcome=outcome)
    if at:
        MetaDelivery.objects.filter(pk=row.pk).update(created_at=at)


def session(site, member):
    _n[0] += 1
    return MemberSession.objects.create(
        site=site, member=member, token_hash=f"{_n[0]:064d}", expires_at=timezone.now() + timedelta(days=1))


def age(order, days):
    when = timezone.now() - timedelta(days=days)
    PackOrder.objects.filter(pk=order.pk).update(created_at=when)
    return when


class JourneyTests(OwnerTestCase):
    def path(self, query="", site_id=None):
        return f"/v1/sites/{site_id or self.site_id}/pack/journey/{query}"

    def stage(self, body, key):
        return next(s for s in body["stages"] if s["key"] == key)

    def seed(self, site):
        token(site)
        token(site)
        order, _ = make_order(site, status=C.VERIFIED)
        PackOrder.objects.filter(pk=order.pk).update(verified_at=timezone.now())
        make_order(site)  # pending, no registration
        SanteRegistration.objects.create(site=site, member=order.member, pack_order=order, status="ready_to_register")
        session(site, order.member)
        session(site, order.member)  # same person twice counts once
        return order

    def test_counts_and_shape(self):
        order = self.seed(self.site)
        PackProofSubmission.objects.filter(order=order).first()  # make_order made one proof per order
        body = self.get(self.path()).json()
        self.assertEqual(
            sorted(body), ["attribution", "generated_at", "meta", "period", "recent", "since", "stages"])
        self.assertEqual(body["period"], "30d")
        self.assertEqual(body["attribution"], {"per_person": "not_recorded"})
        self.assertEqual([s["key"] for s in body["stages"]], [
            "checkout_started", "order_placed", "proof_submitted", "payment_verified", "onboarding",
            "dashboard_access"])
        counts = {s["key"]: s["count"] for s in body["stages"]}
        self.assertEqual(counts, {
            "checkout_started": 2, "order_placed": 2, "proof_submitted": 2, "payment_verified": 1,
            "onboarding": 1, "dashboard_access": 1})
        self.assertEqual(self.stage(body, "onboarding")["breakdown"], [{"status": "ready_to_register", "count": 1}])
        self.assertEqual(len(body["recent"]), 2)
        mine = next(r for r in body["recent"] if r["order_id"] == order.public_id)
        self.assertEqual(mine["registration_status"], "ready_to_register")
        self.assertTrue(mine["dashboard_access"])
        self.assertEqual(mine["payment_status"], C.VERIFIED)
        other = next(r for r in body["recent"] if r["order_id"] != order.public_id)
        self.assertIsNone(other["registration_status"])
        self.assertFalse(other["dashboard_access"])

    def test_meta_events(self):
        for _ in range(2):
            delivery(self.site, "PageView", "sent")
        delivery(self.site, "PageView", "failed")
        delivery(self.site, "PageView", "queued")
        delivery(self.site, "Purchase", "expired")
        delivery(self.site, "Zeta", "sent")
        delivery(self.site, "ViewContent", "sent")
        events = self.get(self.path()).json()["meta"]["events"]
        self.assertEqual(events, [
            {"name": "PageView", "sent": 2, "failed": 1}, {"name": "ViewContent", "sent": 1, "failed": 0},
            {"name": "Purchase", "sent": 0, "failed": 1}, {"name": "Zeta", "sent": 1, "failed": 0}])

    def test_connected_needs_switch_pixel_and_token(self):
        meta = lambda: self.get(self.path()).json()["meta"]["connected"]  # noqa: E731
        self.assertFalse(meta())
        MetaSettings.objects.create(site=self.site, pixel_id="12345678", token_encrypted="x")
        with override_settings(META_EVENTS_ENABLED=True):
            self.assertTrue(meta())
            MetaSettings.objects.update(token_encrypted="")
            self.assertFalse(meta())
            MetaSettings.objects.update(token_encrypted="x", pixel_id="")
            self.assertFalse(meta())
        MetaSettings.objects.update(pixel_id="12345678")
        self.assertFalse(meta())  # switch off

    def test_period_filter(self):
        old, _ = make_order(self.site, status=C.REJECTED)
        age(old, 20)
        ancient, _ = make_order(self.site, status=C.REJECTED)
        age(ancient, 60)
        make_order(self.site)
        token(self.site, timezone.now() - timedelta(days=20))
        delivery(self.site, "PageView", "sent", timezone.now() - timedelta(days=20))
        for period, placed, recent, tokens, since_set in [("7d", 1, 1, 0, True), ("30d", 2, 2, 1, True), ("all", 3, 3, 1, False)]:
            body = self.get(self.path(f"?period={period}")).json()
            self.assertEqual(body["period"], period)
            self.assertEqual(self.stage(body, "order_placed")["count"], placed, period)
            self.assertEqual(self.stage(body, "checkout_started")["count"], tokens, period)
            self.assertEqual(len(body["recent"]), recent, period)
            self.assertEqual(bool(body["since"]), since_set)
            self.assertEqual(sum(e["sent"] for e in body["meta"]["events"]), 0 if period == "7d" else 1)

    def test_all_time_counts_only_verified_orders(self):
        make_order(self.site)  # pending
        make_order(self.site, status=C.REJECTED)
        paid, _ = make_order(self.site, status=C.VERIFIED)
        PackOrder.objects.filter(pk=paid.pk).update(verified_at=timezone.now())
        body = self.get(self.path("?period=all")).json()
        self.assertEqual(self.stage(body, "order_placed")["count"], 3)
        self.assertEqual(self.stage(body, "payment_verified")["count"], 1)

    def test_each_step_is_counted_on_its_own(self):
        for _ in range(2):
            order, _ = make_order(self.site, status=C.VERIFIED)
            age(order, 10)
            PackOrder.objects.filter(pk=order.pk).update(verified_at=timezone.now() - timedelta(days=2))
        body = self.get(self.path("?period=7d")).json()
        self.assertEqual(self.stage(body, "order_placed")["count"], 0)
        self.assertEqual(self.stage(body, "payment_verified")["count"], 2)

    def test_bad_period_is_422(self):
        for value in ("90d", "", "ALL"):
            self.error(self.get(self.path(f"?period={value}")), 422, "invalid_period")

    def test_recent_is_capped_at_20_newest_first(self):
        orders = [make_order(self.site)[0] for _ in range(22)]
        for i, o in enumerate(orders):
            PackOrder.objects.filter(pk=o.pk).update(created_at=timezone.now() - timedelta(minutes=30 - i))
        body = self.get(self.path()).json()
        self.assertEqual(len(body["recent"]), 20)
        self.assertEqual(body["recent"][0]["order_id"], orders[-1].public_id)
        self.assertEqual(self.stage(body, "order_placed")["count"], 22)

    def test_no_personal_fields(self):
        order = self.seed(self.site)
        delivery(self.site, "PageView", "sent")
        text = self.get(self.path()).content.decode()
        for value in (order.member.mobile, order.member.email, order.member.full_name, order.buyer_name,
                      "REF123", "Maria", "BDO"):
            self.assertNotIn(value, text)
        row = self.get(self.path()).json()["recent"][0]
        self.assertEqual(sorted(row), ["dashboard_access", "order_id", "order_number", "payment_status",
                                       "placed_at", "registration_status"])

    def test_two_sites_never_leak(self):
        ben, ben_site, ben_order, _ = self.other_site()
        self.seed(ben_site)
        delivery(ben_site, "PageView", "sent")
        mine, _ = make_order(self.site)
        body = self.get(self.path()).json()
        self.assertEqual([r["order_id"] for r in body["recent"]], [mine.public_id])
        self.assertEqual({s["key"]: s["count"] for s in body["stages"]}, {
            "checkout_started": 0, "order_placed": 1, "proof_submitted": 1, "payment_verified": 0,
            "onboarding": 0, "dashboard_access": 0})
        self.assertEqual(self.stage(body, "onboarding")["breakdown"], [])
        self.assertEqual(body["meta"]["events"], [])
        ben_body = ben.get(self.path(site_id=ben_site.public_id)).json()
        self.assertEqual(self.stage(ben_body, "order_placed")["count"], 3)


class FailClosedTests(OwnerTestCase):
    def path(self, site_id=None):
        return f"/v1/sites/{site_id or self.site_id}/pack/journey/"

    def test_another_owners_site_is_404(self):
        _, ben_site, _, _ = self.other_site()
        self.error(self.get(self.path(ben_site.public_id)), 404, "not_found")

    def test_a_non_sante_site_is_404(self):
        Site.objects.filter(pk=self.site.pk).update(template="other")
        self.error(self.get(self.path()), 404, "not_found")

    def test_signed_out_is_401(self):
        self.assertEqual(self.get(self.path(), client=Client()).status_code, 401)

    @override_settings(PACK_ENABLED=False)
    def test_switch_off_is_404_signed_in_or_not(self):
        self.error(self.get(self.path()), 404, "not_found")
        self.error(self.get(self.path(), client=Client()), 404, "not_found")
