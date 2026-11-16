"""The prospect list (ClickUp z8uq9mfw14): one furthest stage per order, inquiry rows from stored
rows only, search and stage filter, paging, fail closed, no extra personal fields, no provider call."""
from datetime import timedelta
from unittest import mock

from django.test import Client
from django.utils import timezone

from apps.inquiries.models import InquiryConversation
from apps.members.models import Member, MemberSession
from apps.pack import constants as C
from apps.pack.models import PackOrder, PackProofSubmission, PackVisit, SanteRegistration
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
    field = "contact_name" if provider in InquiryConversation.STORED_PROVIDERS else "display_name"
    return InquiryConversation.objects.create(
        site=site, provider=provider, external_id=f"x{_n[0]}", stores_history=provider != "pancake",
        last_message_at=at, **{field: name}, **extra)


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
            site=self.site, full_name="Bare Buyer", mobile="+639170000099", mobile_state="verified")
        return PackOrder.objects.create(
            site=self.site, member=member, order_number=number, price_centavos=C.PRICE_CENTAVOS,
            buyer_name="Bare Buyer", payment_status=C.PENDING_REVIEW, idempotency_key=f"bare-{number}")

    def verified_order(self):
        order, _ = make_order(self.site, status=C.VERIFIED)
        PackOrder.objects.filter(pk=order.pk).update(verified_at=timezone.now())
        return order

    def test_each_stage_is_the_furthest_step(self):
        placed = self.bare_order()
        proofed, _ = make_order(self.site)
        verified = self.verified_order()
        reg = self.verified_order()
        SanteRegistration.objects.create(site=self.site, member=reg.member, pack_order=reg, status="ready_to_register")
        dash = self.verified_order()
        SanteRegistration.objects.create(site=self.site, member=dash.member, pack_order=dash, status="registered")
        session(self.site, dash.member)
        # A registration row and a sign-in before the payment is approved are not steps.
        early, _ = make_order(self.site)
        SanteRegistration.objects.create(
            site=self.site, member=early.member, pack_order=early, status="awaiting_buyer_details")
        session(self.site, early.member)

        self.assertEqual(self.by_number(placed)["stage"], "checked_out")  # an order, no proof yet
        self.assertEqual(self.by_number(proofed)["stage"], "checked_out")
        row = self.by_number(verified)
        self.assertEqual((row["stage"], row["registration_status"]), ("account_setup", None))
        row = self.by_number(reg)
        self.assertEqual((row["stage"], row["registration_status"]), ("account_setup", "ready_to_register"))
        row = self.by_number(dash)
        self.assertEqual((row["stage"], row["registration_status"]), ("activated", "registered"))
        self.assertEqual(self.by_number(early)["stage"], "checked_out")
        counts = self.get(self.path()).json()["stage_counts"]
        self.assertEqual(counts, {"all": 6, "new_lead": 0, "landing_page": 0, "start_watching": 0, "done_watching": 0,
                                  "added_to_cart": 0, "checked_out": 3, "account_setup": 2, "activated": 1})

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
        inquiry(self.site, "pancake", "Fb Person", t - timedelta(hours=2))
        blank = inquiry(self.site, "webchat", "Gone Name", t - timedelta(hours=3))
        InquiryConversation.objects.filter(pk=blank.pk).update(contact_name="", display_name="Old Name")
        rows = [r for r in self.rows() if r["kind"] == "inquiry"]
        self.assertEqual([(r["name"], r["source"]) for r in rows], [
            ("Chat Person", "webchat"), ("Mail Person", "email"), ("Fb Person", "facebook"), ("", "webchat")])
        for r in rows:
            self.assertEqual((r["stage"], r["order_id"], r["order_number"]), ("new_lead", None, None))

    def test_inquiry_and_order_are_never_matched_by_name(self):
        order, _ = make_order(self.site)
        inquiry(self.site, "webchat", order.buyer_name, timezone.now())
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(r["kind"] for r in rows), ["inquiry", "order"])
        self.assertEqual(self.get(self.path()).json()["stage_counts"]["checked_out"], 1)

    def test_search_and_stage_filter(self):
        a, _ = make_order(self.site)
        b, _ = make_order(self.site, status=C.VERIFIED)
        PackOrder.objects.filter(pk=b.pk).update(verified_at=timezone.now(), buyer_name="Juan Dela Cruz")
        inquiry(self.site, "webchat", "Juana Reyes", timezone.now())
        self.assertEqual([r["name"] for r in self.rows("?q=dela")], ["Juan Dela Cruz"])
        self.assertEqual([r["order_number"] for r in self.rows(f"?q={a.order_number.lower()}")], [a.order_number])
        self.assertEqual(sorted(r["name"] for r in self.rows("?q=juan")), ["Juan Dela Cruz", "Juana Reyes"])
        self.assertEqual([r["name"] for r in self.rows("?stage=new_lead")], ["Juana Reyes"])
        self.assertEqual([r["order_number"] for r in self.rows("?stage=account_setup")], [b.order_number])
        body = self.get(self.path("?stage=new_lead&q=zzz")).json()
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

    def test_only_a_name_and_the_listed_fields(self):
        order, _ = make_order(self.site)
        SanteRegistration.objects.create(site=self.site, member=order.member, pack_order=order, status="registered",
                                         date_of_birth="1990-01-01", desired_username="secretuser")
        inquiry(self.site, "email", "Mail Person", timezone.now(), contact_email="leak@buyer.test")
        resp = self.get(self.path())
        text = resp.content.decode()
        for value in (order.member.mobile, order.member.email, "REF123", "BDO", "1990", "secretuser", "leak@buyer.test"):
            self.assertNotIn(value, text)
        keys = ["email", "kind", "last_activity", "mobile", "name", "order_id", "order_number", "registration_status",
                "source", "stage", "video_percent"]
        for r in resp.json()["results"]:
            self.assertEqual(sorted(r), keys)

    def test_no_provider_call(self):
        inquiry(self.site, "pancake", "Fb Person", timezone.now())
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
        self.assertEqual(ben.get(self.path(site_id=ben_site.public_id)).json()["total"], 2)


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
                               "Done": ("done_watching", 90), "Cart": ("added_to_cart", 100),
                               "Typed": ("added_to_cart", 0)})
        counts = self.get(self.path()).json()["stage_counts"]
        self.assertEqual((counts["landing_page"], counts["start_watching"], counts["done_watching"],
                          counts["added_to_cart"]), (1, 1, 1, 2))

    def test_only_an_unfinished_checkout_shows_mobile_and_email_in_full(self):
        self.visit(contact_name="Maria Cart", contact_mobile="09171234567", contact_email="maria@buyer.test",
                   contact_saved_at=timezone.now())
        order, _ = make_order(self.site)
        by_name = {r["name"]: r for r in self.rows()}
        cart = by_name["Maria Cart"]
        self.assertEqual((cart["kind"], cart["mobile"], cart["email"]), ("visit", "09171234567", "maria@buyer.test"))
        row = next(r for r in self.rows() if r["kind"] == "order")
        inquiry(self.site, "email", "Mail Person", timezone.now(), contact_email="leak@buyer.test")
        self.assertNotIn(order.member.mobile, str(row))
        self.assertEqual((row["mobile"], row["email"]), ("", ""))
        for r in self.rows():
            if r["kind"] in ("order", "inquiry"):
                self.assertEqual((r["mobile"], r["email"]), ("", ""), r["kind"])
        self.assertNotIn(order.member.email, self.get(self.path()).content.decode())

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

    def test_search_finds_a_visit_by_its_contact_and_an_order_row_by_no_contact(self):
        self.visit(contact_name="Maria Cart", contact_mobile="09171234567", contact_email="maria@buyer.test")
        order, _ = make_order(self.site)
        self.assertEqual([r["name"] for r in self.rows("?q=0917123")], ["Maria Cart"])
        self.assertEqual([r["name"] for r in self.rows("?q=maria@buyer")], ["Maria Cart"])
        self.assertEqual(self.rows(f"?q={order.member.email}"), [])

    def test_another_sites_visits_never_show(self):
        ben, ben_site, _, _ = self.other_site()
        self.visit(site=ben_site, contact_name="Ben Visitor", contact_mobile="09170000000")
        self.visit(contact_name="Mine")
        self.assertEqual([r["name"] for r in self.rows()], ["Mine"])
        self.assertNotIn("09170000000", self.get(self.path()).content.decode())
