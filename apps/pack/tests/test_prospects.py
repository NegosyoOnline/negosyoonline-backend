"""The prospect list (ClickUp z8uq9mfw14): one furthest stage per order, inquiry rows from stored
rows only, search and stage filter, paging, fail closed, no extra personal fields, no provider call."""
from datetime import timedelta
from unittest import mock

from django.test import Client
from django.utils import timezone

from apps.inquiries.models import InquiryConversation
from apps.members.models import Member, MemberSession
from apps.pack import constants as C
from apps.pack import prospects
from apps.pack.models import CheckoutToken, PackOrder, PackProofSubmission, PackVisit, SanteRegistration
from apps.pack.tests.owner_base import OwnerTestCase, make_order
from apps.sites.models import Site

_n = [0]


def session(site, member, at=None):
    _n[0] += 1
    row = MemberSession.objects.create(
        site=site, member=member, token_hash=f"{_n[0]:064d}", expires_at=timezone.now() + timedelta(days=1))
    if at:
        MemberSession.objects.filter(pk=row.pk).update(created_at=at)


def inquiry(site, provider, name="", at=None, **extra):
    _n[0] += 1
    return InquiryConversation.objects.create(
        site=site, provider=provider, external_id=f"x{_n[0]}", stores_history=True,
        last_message_at=at, contact_name=name, **extra)


class ProspectTests(OwnerTestCase):
    def path(self, query="", site_id=None):
        return f"/v1/sites/{site_id or self.site_id}/pack/journey/prospects/{query}"

    def rows(self, query=""):
        return self.get(self.path(query)).json()["results"]

    def by_number(self, order):
        return next(r for r in self.rows() if r["order_number"] == order.order_number)

    def bare_order(self, number="SMP90001"):
        """An order with no proof: make_order always files one, and proofs are append-only."""
        member = Member.objects.create(
            site=self.site, full_name="Bare Buyer", mobile=f"+6391700{number[-5:]}", mobile_state="verified",
            email=f"bare{number.lower()}@buyer.test")
        return PackOrder.objects.create(
            site=self.site, member=member, order_number=number, price_centavos=C.PRICE_CENTAVOS,
            buyer_name="Bare Buyer", payment_status=C.PENDING_REVIEW, idempotency_key=f"bare-{number}")

    def verified_order(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        PackOrder.objects.filter(pk=order.pk).update(verified_at=timezone.now())
        return order

    def test_each_stage_is_the_furthest_step(self):
        placed = self.bare_order()  # an order, no payment details yet
        proofed, _ = make_order(self.site)  # bank order with a proof (any review outcome)
        rejected, _ = make_order(self.site, status=C.REJECTED)
        verified = self.verified_order()
        reg = self.verified_order()
        SanteRegistration.objects.create(site=self.site, member=reg.member, pack_order=reg, status="ready_to_register")
        dash = self.verified_order()
        SanteRegistration.objects.create(site=self.site, member=dash.member, pack_order=dash, status="registered")
        session(self.site, dash.member)
        # A sign-in before any payment details is not a step.
        early = self.bare_order("SMP90002")
        session(self.site, early.member)

        self.assertEqual(self.by_number(placed)["stage"], "checked_out")
        self.assertEqual(self.by_number(early)["stage"], "checked_out")
        self.assertEqual(self.by_number(proofed)["stage"], "purchased")
        self.assertEqual(self.by_number(rejected)["stage"], "purchased")
        row = self.by_number(verified)
        self.assertEqual((row["stage"], row["registration_status"]), ("purchased", None))
        row = self.by_number(reg)
        self.assertEqual((row["stage"], row["registration_status"]), ("purchased", "ready_to_register"))
        row = self.by_number(dash)
        self.assertEqual((row["stage"], row["registration_status"]), ("activated", "registered"))
        counts = self.get(self.path()).json()["stage_counts"]
        self.assertEqual(counts, {"all": 7, "landing_page": 0, "start_watching": 0, "done_watching": 0,
                                  "checked_out": 2, "purchased": 4, "activated": 1})

    def test_the_checkout_token_marks_activated_when_no_session_row_is_there(self):
        order = self.verified_order()
        CheckoutToken.objects.create(
            site=self.site, token_hash="t" * 64, expires_at=timezone.now() + timedelta(days=1),
            order_ref=order.pk, session_issued_at=timezone.now())
        self.assertEqual(self.by_number(order)["stage"], "activated")

    def test_last_activity_is_the_latest_event(self):
        # The newest of placed, proof, approval, registration change and sign-in.
        order, proof = make_order(self.site, status=C.VERIFIED)
        old = timezone.now() - timedelta(days=9)
        PackOrder.objects.filter(pk=order.pk).update(created_at=old, verified_at=old + timedelta(days=1))
        SanteRegistration.objects.create(site=self.site, member=order.member, pack_order=order, status="registered",
                                         status_entered_at=old + timedelta(days=2))
        session(self.site, order.member, old + timedelta(days=3))
        self.assertEqual(self.by_number(order)["last_activity"], proof.submitted_at.isoformat())  # proof is newest
        bare = self.bare_order()
        PackOrder.objects.filter(pk=bare.pk).update(created_at=old, verified_at=old + timedelta(days=1),
                                                    payment_status=C.VERIFIED)
        SanteRegistration.objects.create(site=self.site, member=bare.member, pack_order=bare, status="registered",
                                         status_entered_at=old + timedelta(days=2))
        self.assertEqual(self.by_number(bare)["last_activity"], (old + timedelta(days=2)).isoformat())
        session(self.site, bare.member, old + timedelta(days=3))
        row = self.by_number(bare)
        self.assertEqual((row["last_activity"], row["stage"]), ((old + timedelta(days=3)).isoformat(), "activated"))

    def test_inquiry_rows(self):
        t = timezone.now() - timedelta(days=1)
        inquiry(self.site, "webchat", "Chat Person", t)
        inquiry(self.site, "email", "Mail Person", t - timedelta(hours=1))
        blank = inquiry(self.site, "webchat", "Gone Name", t - timedelta(hours=3))
        InquiryConversation.objects.filter(pk=blank.pk).update(contact_name="", display_name="Old Name")
        rows = [r for r in self.rows() if r["kind"] == "inquiry"]
        self.assertEqual([(r["name"], r["source"]) for r in rows], [
            ("Chat Person", "webchat"), ("Mail Person", "email"), ("", "webchat")])
        for r in rows:
            self.assertEqual((r["stage"], r["order_id"], r["order_number"]), ("landing_page", None, None))

    def test_webchat_email_and_messenger_inquiries_are_listed_newest_first(self):
        t = timezone.now() - timedelta(days=1)
        inquiry(self.site, InquiryConversation.WEBCHAT, "Chat Person", t)
        inquiry(self.site, InquiryConversation.EMAIL, "Mail Person", t - timedelta(hours=1))
        inquiry(self.site, InquiryConversation.MESSENGER, "Messenger Person", t - timedelta(hours=2))
        response = self.get(self.path())
        self.assertEqual(response.status_code, 200)
        rows = [r for r in response.json()["results"] if r["kind"] == "inquiry"]
        self.assertEqual([(r["name"], r["source"]) for r in rows], [
            ("Chat Person", "webchat"), ("Mail Person", "email"), ("Messenger Person", "messenger")])
        self.assertEqual(rows[2]["stage"], "landing_page")

    def test_inquiry_provider_absent_from_channel_keys_is_skipped(self):
        t = timezone.now() - timedelta(days=1)
        inquiry(self.site, InquiryConversation.WEBCHAT, "Chat Person", t)
        inquiry(self.site, InquiryConversation.MESSENGER, "Messenger Person", t - timedelta(hours=1))
        keys = {k: v for k, v in prospects.CHANNEL_KEYS.items() if k != InquiryConversation.MESSENGER}
        with mock.patch.dict(prospects.CHANNEL_KEYS, keys, clear=True):
            response = self.get(self.path())
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["name"] for r in response.json()["results"] if r["kind"] == "inquiry"],
                         ["Chat Person"])

    def test_inquiry_and_order_are_never_matched_by_name(self):
        order, _ = make_order(self.site)
        inquiry(self.site, "webchat", order.buyer_name, timezone.now())
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(r["kind"] for r in rows), ["inquiry", "order"])
        self.assertEqual(self.get(self.path()).json()["stage_counts"]["purchased"], 1)

    def test_search_and_stage_filter(self):
        a, _ = make_order(self.site)
        b, _ = make_order(self.site, status=C.VERIFIED)
        PackOrder.objects.filter(pk=b.pk).update(verified_at=timezone.now(), buyer_name="Juan Dela Cruz")
        inquiry(self.site, "webchat", "Juana Reyes", timezone.now())
        self.assertEqual([r["name"] for r in self.rows("?q=dela")], ["Juan Dela Cruz"])
        self.assertEqual([r["order_number"] for r in self.rows(f"?q={a.order_number.lower()}")], [a.order_number])
        self.assertEqual(sorted(r["name"] for r in self.rows("?q=juan")), ["Juan Dela Cruz", "Juana Reyes"])
        self.assertEqual([r["name"] for r in self.rows("?stage=landing_page")], ["Juana Reyes"])
        self.assertEqual([r["order_number"] for r in self.rows("?stage=purchased")], [b.order_number, a.order_number])
        body = self.get(self.path("?stage=landing_page&q=zzz")).json()
        self.assertEqual((body["results"], body["total"]), ([], 0))
        self.assertEqual(body["stage_counts"]["all"], 3)  # counts ignore search and stage

    def test_paging_newest_first(self):
        base = timezone.now()
        for i in range(27):
            inquiry(self.site, "webchat", f"P{i:02d}", base - timedelta(minutes=i))
        one = self.get(self.path()).json()
        self.assertEqual((one["page"], one["pages"], one["total"], len(one["results"])), (1, 2, 27, 25))
        self.assertEqual(one["results"][0]["name"], "P00")
        two = self.get(self.path("?page=2")).json()
        self.assertEqual([r["name"] for r in two["results"]], ["P25", "P26"])
        self.assertEqual(self.get(self.path("?page=9")).json()["page"], 2)

    def test_bad_stage_and_page_are_422(self):
        self.error(self.get(self.path("?stage=nope")), 422, "invalid_stage")
        for bad in ("0", "-1", "x", "1.5"):
            self.error(self.get(self.path(f"?page={bad}")), 422, "invalid_page")

    def test_only_a_name_mobile_email_and_the_listed_fields(self):
        order, _ = make_order(self.site)
        SanteRegistration.objects.create(site=self.site, member=order.member, pack_order=order, status="registered",
                                         date_of_birth="1990-01-01", desired_username="secretuser")
        inquiry(self.site, "email", "Mail Person", timezone.now(), contact_email="mail@buyer.test")
        resp = self.get(self.path())
        text = resp.content.decode()
        # No address, proof, date of birth or Meta data; the member's mobile and email are the approved contact.
        for value in ("REF123", "BDO", "1990", "secretuser"):
            self.assertNotIn(value, text)
        keys = ["email", "kind", "last_activity", "mobile", "name", "order_id", "order_number", "payment_status",
                "placed_at", "price_centavos", "currency", "registration_status", "source", "stage", "video_percent"]
        for r in resp.json()["results"]:
            self.assertEqual(sorted(r), sorted(keys))

    def test_no_provider_call(self):
        inquiry(self.site, "email", "Mail Person", timezone.now())
        inquiry(self.site, "webchat", "Chat Person", timezone.now())
        with mock.patch("socket.socket.connect", side_effect=AssertionError("network used")), \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            self.assertEqual(self.get(self.path()).json()["total"], 2)

    def test_two_sites_never_leak(self):
        ben, ben_site, ben_order, _ = self.other_site()
        inquiry(ben_site, "webchat", "Ben Chat", timezone.now())
        mine, _ = make_order(self.site)
        body = self.get(self.path()).json()
        self.assertEqual([r["order_number"] for r in body["results"]], [mine.order_number])
        self.assertEqual(body["stage_counts"]["all"], 1)
        self.assertEqual(ben.get(self.path(site_id=ben_site.public_id), secure=True).json()["total"], 2)


class ProspectFailClosedTests(OwnerTestCase):
    def path(self, site_id=None):
        return f"/v1/sites/{site_id or self.site_id}/pack/journey/prospects/"

    def test_another_owners_site_is_404(self):
        _, ben_site, _, _ = self.other_site()
        self.error(self.get(self.path(ben_site.public_id)), 404, "not_found")

    def test_a_non_sante_site_is_404(self):
        Site.objects.filter(pk=self.site.pk).update(template="other")
        self.error(self.get(self.path()), 404, "not_found")

    def test_signed_out_is_401(self):
        self.assertEqual(self.get(self.path(), client=Client()).status_code, 401)


class ProspectVisitTests(OwnerTestCase):
    def path(self, query="", site_id=None):
        return f"/v1/sites/{site_id or self.site_id}/pack/journey/prospects/{query}"

    def visit(self, site=None, **fields):
        _n[0] += 1
        return PackVisit.objects.create(site=site or self.site, visitor_ref=f"v{_n[0]:020d}", **fields)

    def rows(self, query=""):
        return self.get(self.path(query)).json()["results"]

    def test_each_visit_stage_is_the_furthest_step(self):
        now = timezone.now()
        self.visit(contact_name="Landed")
        self.visit(contact_name="Started", started_watching_at=now, video_percent=25)
        self.visit(contact_name="Done", started_watching_at=now, done_watching_at=now, video_percent=90)
        self.visit(contact_name="Cart", done_watching_at=now, video_percent=100, checkout_started_at=now)
        self.visit(contact_name="Typed", contact_saved_at=now)
        got = {r["name"]: (r["stage"], r["video_percent"]) for r in self.rows()}
        self.assertEqual(got, {"Landed": ("landing_page", 0), "Started": ("start_watching", 25),
                               "Done": ("done_watching", 90), "Cart": ("checked_out", 100),
                               "Typed": ("landing_page", 0)})
        counts = self.get(self.path()).json()["stage_counts"]
        self.assertEqual((counts["landing_page"], counts["start_watching"], counts["done_watching"],
                          counts["checked_out"]), (2, 1, 1, 1))

    def test_a_visit_that_opened_checkout_is_checked_out_even_with_no_video(self):
        self.visit(contact_name="Opened", checkout_started_at=timezone.now(), video_percent=0)
        row = self.rows()[0]
        self.assertEqual((row["stage"], row["video_percent"]), ("checked_out", 0))
        self.assertEqual(self.get(self.path()).json()["stage_counts"]["checked_out"], 1)

    def test_a_visit_whose_contact_was_saved_is_checked_out(self):
        from apps.pack import visits
        v = self.visit()
        self.assertTrue(visits.save_contact(self.site, v.visitor_ref, "Saved Person", "09171234567", "", ip=""))
        row = self.rows()[0]
        self.assertEqual((row["name"], row["stage"]), ("Saved Person", "checked_out"))
        self.assertEqual(self.get(self.path()).json()["stage_counts"]["checked_out"], 1)

    def test_an_order_row_is_unaffected_by_its_visits_checkout_time(self):
        order, _ = make_order(self.site)
        self.visit(order=order, checkout_started_at=timezone.now())
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "order")
        self.assertEqual(rows[0]["stage"], prospects.order_rows(self.site)[0]["stage"])

    def test_every_row_shows_the_contact_it_already_has(self):
        self.visit(contact_name="Maria Cart", contact_mobile="09171234567", contact_email="maria@buyer.test",
                   contact_saved_at=timezone.now())
        order, _ = make_order(self.site)
        inquiry(self.site, "email", "Mail Person", timezone.now(), contact_email="mail@buyer.test")
        inquiry(self.site, "webchat", "Chat Person", timezone.now())
        by_name = {r["name"]: r for r in self.rows()}
        cart = by_name["Maria Cart"]
        self.assertEqual((cart["kind"], cart["mobile"], cart["email"]), ("visit", "09171234567", "maria@buyer.test"))
        row = next(r for r in self.rows() if r["kind"] == "order")
        self.assertEqual((row["mobile"], row["email"]), (order.member.mobile, order.member.email))
        self.assertEqual((row["payment_status"], row["price_centavos"]), (order.payment_status, C.PRICE_CENTAVOS))
        self.assertEqual((by_name["Mail Person"]["mobile"], by_name["Mail Person"]["email"]), ("", "mail@buyer.test"))
        self.assertEqual((by_name["Chat Person"]["mobile"], by_name["Chat Person"]["email"]), ("", ""))

    def test_a_visit_with_an_order_is_not_a_second_row_and_lends_its_video_progress(self):
        order, _ = make_order(self.site)
        self.visit(order=order, video_percent=75, started_watching_at=timezone.now(), contact_name="Typed Name")
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["kind"], rows[0]["video_percent"], rows[0]["name"]), ("order", 75, order.buyer_name))

    def test_an_order_with_no_linked_visit_has_unknown_video_progress_not_zero(self):
        bare, _ = make_order(self.site)
        self.assertIsNone(self.rows()[0]["video_percent"])
        self.visit(order=bare, video_percent=0)
        self.assertEqual(self.rows()[0]["video_percent"], 0)

    def test_search_finds_a_row_by_its_mobile_or_email(self):
        self.visit(contact_name="Maria Cart", contact_mobile="09171234567", contact_email="maria@buyer.test")
        order, _ = make_order(self.site)
        self.assertEqual([r["name"] for r in self.rows("?q=0917123")], ["Maria Cart"])
        self.assertEqual([r["name"] for r in self.rows("?q=maria@buyer")], ["Maria Cart"])
        self.assertEqual([r["kind"] for r in self.rows(f"?q={order.member.email}")], ["order"])

    def test_another_sites_visits_never_show(self):
        ben, ben_site, _, _ = self.other_site()
        self.visit(site=ben_site, contact_name="Ben Visitor", contact_mobile="09170000000")
        self.visit(contact_name="Mine")
        self.assertEqual([r["name"] for r in self.rows()], ["Mine"])
        self.assertNotIn("09170000000", self.get(self.path()).content.decode())
