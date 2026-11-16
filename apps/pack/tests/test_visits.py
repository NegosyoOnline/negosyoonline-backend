"""The Membership Pack page visitor (ClickUp z8uq9mfw14): create or return a visit, video progress, the
checkout contact saved on blur, the link to the order, rate limits, site scope, retention and purge."""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.pack import constants as C, purge, retention
from apps.pack.models import PackOrder, PackVisit
from apps.pack.tests.base import HOST, NOT_FOUND, OTHER_HOST, PackTestCase

VISIT = "/storefront/v1/visit/"


class VisitBase(PackTestCase):
    def call(self, path, host=HOST, secret="__default__", ip="203.0.113.50", **fields):
        from apps.pack.tests.base import SECRET
        body = {"host": host, **fields}
        if ip is not None:
            body["visitor_ip"] = ip
        headers = {"HTTP_X_REAL_IP": "198.51.100.200"}
        key = SECRET if secret == "__default__" else secret
        if key is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {key}"
        return self.client.post(VISIT + path, body, content_type="application/json", secure=True, **headers)

    def begin(self, **fields):
        response = self.call("", **fields)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["ref"]

    def row(self, ref, site=None):
        return PackVisit.objects.get(site=site or self.site, visitor_ref=ref)

    def event(self, ref, event, percent=None, **kw):
        fields = {"ref": ref, "event": event}
        if percent is not None:
            fields["percent"] = percent
        return self.call("event/", **fields, **kw)


class VisitTests(VisitBase):
    def test_a_new_visit_gets_a_server_made_ref_and_a_returning_one_gets_the_same(self):
        ref = self.begin()
        self.assertGreaterEqual(len(ref), 16)
        visit = self.row(ref)
        self.assertIsNone(visit.order_id)
        self.assertEqual((visit.video_percent, visit.contact_name, visit.started_watching_at), (0, "", None))
        self.assertEqual(self.begin(ref=ref), ref)
        self.assertEqual(PackVisit.objects.count(), 1)

    def test_a_ref_the_browser_made_up_is_not_adopted(self):
        made_up = "A" * 22
        ref = self.begin(ref=made_up)
        self.assertNotEqual(ref, made_up)
        self.assertFalse(PackVisit.objects.filter(visitor_ref=made_up).exists())

    def test_a_ref_of_another_site_is_not_used(self):
        theirs = self.begin(host=OTHER_HOST)
        mine = self.begin(ref=theirs)
        self.assertNotEqual(mine, theirs)
        self.assertEqual(PackVisit.objects.filter(site=self.site).count(), 1)
        self.assertEqual(self.event(theirs, "start").status_code, 404)  # not on this host
        self.assertIsNone(self.row(theirs, self.other_site).started_watching_at)

    def test_progress_keeps_the_highest_mark_and_done_watching_comes_at_90(self):
        ref = self.begin()
        self.assertEqual(self.event(ref, "start").status_code, 200)
        self.assertIsNotNone(self.row(ref).started_watching_at)
        for mark in (50, 25, 75):
            self.assertEqual(self.event(ref, "percent", mark).status_code, 200)
        visit = self.row(ref)
        self.assertEqual(visit.video_percent, 75)
        self.assertIsNone(visit.done_watching_at)
        self.event(ref, "percent", 90)
        self.event(ref, "percent", 25)
        visit = self.row(ref)
        self.assertEqual(visit.video_percent, 90)
        self.assertIsNotNone(visit.done_watching_at)
        self.event(ref, "percent", 100)
        self.assertEqual(self.row(ref).video_percent, 100)

    def test_a_percent_mark_alone_starts_the_watching(self):
        ref = self.begin()
        self.event(ref, "percent", 25)
        self.assertIsNotNone(self.row(ref).started_watching_at)

    def test_bad_events_and_marks_are_422_and_change_nothing(self):
        ref = self.begin()
        for kw in ({"event": "nope"}, {"event": "percent"}, {"event": "percent", "percent": 33}):
            self.assertEqual(self.call("event/", ref=ref, **kw).status_code, 422, kw)
        self.assertEqual(self.row(ref).video_percent, 0)

    def test_checkout_event_marks_the_checkout_opened(self):
        ref = self.begin()
        self.event(ref, "checkout")
        self.assertIsNotNone(self.row(ref).checkout_started_at)

    def test_unknown_or_malformed_ref_is_404(self):
        for ref in ("", "short", "x" * 22, "bad ref with spaces!!"):
            self.assertEqual(self.event(ref, "start").status_code, 404, ref)
            self.assertEqual(self.call("contact/", ref=ref, full_name="A").status_code, 404, ref)

    def test_the_contact_is_saved_on_blur_in_the_visit_only(self):
        ref = self.begin()
        self.assertEqual(self.call("contact/", ref=ref, full_name="Maria  Santos", mobile="0917 123 4567").status_code, 200)
        visit = self.row(ref)
        self.assertEqual((visit.contact_name, visit.contact_mobile, visit.contact_email), ("Maria Santos", "0917 123 4567", ""))
        self.assertIsNotNone(visit.contact_saved_at)
        self.assertIsNotNone(visit.checkout_started_at)
        self.call("contact/", ref=ref, full_name="Maria Santos", mobile="09171234567", email=" Maria@Buyer.test ")
        visit = self.row(ref)
        self.assertEqual((visit.contact_mobile, visit.contact_email), ("09171234567", "maria@buyer.test"))
        from apps.members.models import Member
        from apps.pack.models import CheckoutToken
        self.assertEqual((Member.objects.count(), CheckoutToken.objects.count()), (0, 0))

    def test_a_field_that_is_not_what_it_says_is_dropped_not_an_error(self):
        ref = self.begin()
        self.call("contact/", ref=ref, full_name="Ana", mobile="<script>", email="not an email")
        visit = self.row(ref)
        self.assertEqual((visit.contact_name, visit.contact_mobile, visit.contact_email), ("Ana", "", ""))

    def test_an_empty_save_changes_nothing(self):
        ref = self.begin()
        self.call("contact/", ref=ref, full_name="Ana")
        self.call("contact/", ref=ref, full_name="", mobile="", email="")
        self.assertEqual(self.row(ref).contact_name, "Ana")

    def test_placing_an_order_with_the_ref_links_the_visit(self):
        ref = self.begin()
        token = self.start()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.place(token, visitor_ref=ref)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.row(ref).order_id, PackOrder.objects.get().pk)

    def test_a_ref_of_another_site_links_nothing_and_does_not_stop_the_order(self):
        theirs = self.begin(host=OTHER_HOST)
        token = self.start()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.place(token, visitor_ref=theirs)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(self.row(theirs, self.other_site).order_id)

    def test_an_order_without_a_ref_is_unchanged(self):
        self.placed()
        self.assertEqual(PackVisit.objects.count(), 0)


class VisitGateTests(VisitBase):
    def test_unknown_foreign_host_credential_and_switch(self):
        for host in ("nobody.negosyotest.com", "example.org"):
            response = self.call("", host=host)
            self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND), host)
        self.assertEqual(self.call("", secret=None).status_code in (401, 403), True)
        with override_settings(PACK_ENABLED=False):
            self.assertEqual(self.call("").json(), NOT_FOUND)
        self.assertEqual(PackVisit.objects.count(), 0)


class VisitRateLimitTests(VisitBase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_new_visits_per_ip_are_limited_and_other_ips_are_not(self):
        scope, limit, _ = C.VISIT_IP_LIMIT
        with mock.patch.object(C, "VISIT_IP_LIMIT", (scope, 3, 3600)):
            for _ in range(3):
                self.assertEqual(self.call("").status_code, 200)
            self.assertEqual(self.call("").status_code, 429)
            self.assertEqual(self.call("", ip="203.0.113.99").status_code, 200)

    def test_events_and_contact_saves_are_limited_per_visit(self):
        ref = self.begin()
        with mock.patch.object(C, "VISIT_REF_LIMIT", ("pack-visit-ref", 2, 3600)):
            self.assertEqual(self.event(ref, "start").status_code, 200)
            self.assertEqual(self.event(ref, "percent", 25).status_code, 200)
            self.assertEqual(self.event(ref, "percent", 50).status_code, 429)
        self.assertEqual(self.row(ref).video_percent, 25)
        with mock.patch.object(C, "VISIT_CONTACT_LIMIT", ("pack-visit-contact", 1, 3600)):
            self.assertEqual(self.call("contact/", ref=ref, full_name="Ana").status_code, 200)
            self.assertEqual(self.call("contact/", ref=ref, full_name="Bea").status_code, 429)
        self.assertEqual(self.row(ref).contact_name, "Ana")


@override_settings(RETENTION_ENABLED=True)
class VisitRetentionTests(VisitBase):
    def make(self, age_days, order=None, name="Ana"):
        ref = self.begin(ip=f"203.0.113.{PackVisit.objects.count() + 1}")
        PackVisit.objects.filter(site=self.site, visitor_ref=ref).update(
            last_activity_at=timezone.now() - timedelta(days=age_days), contact_name=name,
            contact_mobile="09171234567", contact_email="a@b.test", order=order)
        return ref

    def test_old_visits_without_an_order_are_deleted_and_with_an_order_are_blanked(self):
        self.placed()
        order = PackOrder.objects.get()
        old, linked, fresh = self.make(91), self.make(91, order=order), self.make(89)
        self.assertEqual(retention.old_visits(timezone.now(), True), 2)  # a dry run changes nothing
        self.assertEqual(PackVisit.objects.count(), 3)
        retention.old_visits(timezone.now(), False)
        self.assertFalse(PackVisit.objects.filter(visitor_ref=old).exists())
        kept = self.row(linked)
        self.assertEqual((kept.contact_name, kept.contact_mobile, kept.contact_email), ("", "", ""))
        self.assertEqual(kept.order_id, order.pk)
        self.assertEqual(self.row(fresh).contact_name, "Ana")

    def test_the_rule_reads_nothing_while_the_pack_is_off(self):
        self.make(91)
        with override_settings(PACK_ENABLED=False):
            self.assertEqual(retention.old_visits(timezone.now(), False), 0)
        self.assertEqual(PackVisit.objects.count(), 1)

    def test_disposal_of_the_account_deletes_the_visits(self):
        self.make(1)
        counts = retention.blank_packs_of(self.site.account)
        self.assertEqual(counts["pack_visits"], 1)
        self.assertEqual(PackVisit.objects.filter(site=self.site).count(), 0)

    def test_disposal_of_another_account_leaves_this_sites_visits(self):
        self.make(1)
        self.begin(host=OTHER_HOST)
        retention.blank_packs_of(self.other_site.account)
        self.assertEqual(PackVisit.objects.filter(site=self.site).count(), 1)


class VisitPurgeTests(VisitBase):
    def test_the_day_90_purge_deletes_the_sites_visits_and_only_that_site(self):
        self.begin()
        self.begin(host=OTHER_HOST)
        counts = purge._clear_details(self.site.pk, timezone.now())
        self.assertEqual(counts["pack_visits"], 1)
        self.assertEqual(PackVisit.objects.filter(site=self.site).count(), 0)
        self.assertEqual(PackVisit.objects.filter(site=self.other_site).count(), 1)
