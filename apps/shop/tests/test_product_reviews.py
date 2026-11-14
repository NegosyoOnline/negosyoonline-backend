"""CAT-1 r5b: product reviews. Only a buyer's own delivered, non-cancelled order line; one per line;
one change within 7 days; isolation between stores and orders; hidden reviews are not public; the
Subscriber hides and shows and never edits."""
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import Client, override_settings
from django.utils import timezone

from apps.shop import constants as C, placement
from apps.shop.models import ImmutableRecordError, ProductReview, ShopOrder, ShopOrderEvent
from apps.shop.tests.base import HOST, NOT_FOUND, ShopTestCase
from apps.shop.tests.owner_base import OwnerTestCase, make_order

BEN_HOST = "bens-store.negosyotest.com"


def deliver(order):
    """What the Subscriber's actions leave behind: paid, shipped, delivered, with its events."""
    today = timezone.localdate()
    ShopOrder.objects.filter(pk=order.pk).update(
        payment_status=C.VERIFIED, verified_at=timezone.now(), shipped_on=today, delivered_on=today
    )
    ShopOrderEvent.objects.create(order=order, kind=C.EVENT_DELIVERED, actor_type="subscriber")


class ReviewTestCase(ShopTestCase):
    def line_path(self, order_id, position=1):
        return f"shop/orders/{order_id}/lines/{position}/review/"

    def send(self, method, order_id, position=1, host=HOST, **fields):
        body = {"host": host, "visitor_ip": "203.0.113.50", **fields}
        return getattr(self.client, method)(
            "/storefront/v1/" + self.line_path(order_id, position), body,
            content_type="application/json", secure=True, **self.headers(),
        )

    def review(self, order_id, position=1, **fields):
        return self.send("post", order_id, position, **fields)

    def change(self, order_id, position=1, **fields):
        return self.send("patch", order_id, position, **fields)

    def delivered(self, **fields):
        body = self.placed(**fields)
        deliver(ShopOrder.objects.get(public_id=body["order_id"]))
        return body

    def page(self, product=None, host=HOST, **query):
        return self.client.get(
            f"/storefront/v1/shop/products/{(product or self.a).public_id}/reviews/",
            {"host": host, **query}, secure=True, **self.headers(),
        )

    def ben_order(self, product=None, *, delivered=True, token="ben-token"):
        order, _ = make_order(self.other_site, status=C.VERIFIED, shipped=True, delivered=delivered)
        ShopOrder.objects.filter(pk=order.pk).update(view_token_hash=placement.token_hash(token))
        if delivered:
            ShopOrderEvent.objects.create(order=order, kind=C.EVENT_DELIVERED, actor_type="subscriber")
        return order


class WritingTests(ReviewTestCase):
    def test_a_guest_reviews_a_delivered_line_with_the_default_name(self):
        body = self.delivered()
        response = self.review(body["order_id"], view_token=body["view_token"], rating=5, body="  Good.  ")
        self.assertEqual(response.status_code, 200, response.content)
        out = response.json()
        self.assertEqual((out["rating"], out["body"], out["display_name"]), (5, "Good.", "Maria S."))
        self.assertEqual((out["edited"], out["can_edit"]), (False, True))
        row = ProductReview.objects.get()
        self.assertEqual((row.site, row.product_id, row.is_hidden), (self.site, self.a.public_id, False))

    def test_a_member_reviews_with_their_own_signed_in_order(self):
        member = self.member()
        token = self.token(member)
        body = self.delivered(member_token=token)
        response = self.review(body["order_id"], member_token=token, rating=4, display_name="Mia")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["display_name"], "Mia")

    def test_another_member_or_a_bad_token_cannot(self):
        mine = self.member()
        theirs = self.member(email="juan@buyer.test", mobile="09170000002", name="Juan Cruz")
        body = self.delivered(member_token=self.token(mine))
        for fields in ({"member_token": self.token(theirs)}, {"member_token": "nope"}, {"view_token": "nope"}, {}):
            with self.subTest(fields=fields):
                response = self.review(body["order_id"], rating=5, **fields)
                self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND))
        self.assertEqual(ProductReview.objects.count(), 0)

    def test_a_token_for_one_order_cannot_review_another_orders_line(self):
        first, second = self.delivered(), self.delivered()
        response = self.review(second["order_id"], view_token=first["view_token"], rating=5)
        self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND))
        self.assertEqual(ProductReview.objects.count(), 0)

    def test_an_unknown_line_position_is_the_same_404(self):
        body = self.delivered()
        for position in (2, 0, 99):
            self.assertEqual(self.review(body["order_id"], position, view_token=body["view_token"], rating=5).status_code, 404)

    def test_only_a_delivered_line_can_be_reviewed(self):
        body = self.placed()  # placed, not delivered
        response = self.review(body["order_id"], view_token=body["view_token"], rating=5)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "not_delivered"))
        order = ShopOrder.objects.get(public_id=body["order_id"])
        ShopOrder.objects.filter(pk=order.pk).update(
            payment_status=C.VERIFIED, shipped_on=timezone.localdate(), delivered_on=timezone.localdate()
        )  # dates alone, no Delivered event
        self.assertEqual(self.review(body["order_id"], view_token=body["view_token"], rating=5).status_code, 409)
        self.assertEqual(ProductReview.objects.count(), 0)

    def test_a_cancelled_order_cannot_be_reviewed(self):
        body = self.placed()  # the database already forbids cancelling a shipped order
        ShopOrder.objects.filter(public_id=body["order_id"]).update(payment_status=C.CANCELLED, cancel_note="x")
        ShopOrderEvent.objects.create(
            order=ShopOrder.objects.get(public_id=body["order_id"]), kind=C.EVENT_DELIVERED, actor_type="system"
        )
        response = self.review(body["order_id"], view_token=body["view_token"], rating=5)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "not_delivered"))

    def test_one_review_per_line(self):
        body = self.delivered(lines=self.cart(1, 1))
        first = self.review(body["order_id"], 1, view_token=body["view_token"], rating=5)
        again = self.review(body["order_id"], 1, view_token=body["view_token"], rating=1)
        other = self.review(body["order_id"], 2, view_token=body["view_token"], rating=3)
        self.assertEqual((first.status_code, again.status_code, other.status_code), (200, 409, 200))
        self.assertEqual(again.json()["error"]["code"], "already_reviewed")
        self.assertEqual(ProductReview.objects.count(), 2)
        line = ProductReview.objects.first().order_line
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductReview.objects.create(
                site=self.site, order=line.order, order_line=line, product_id="x", rating=3, display_name="A"
            )

    def test_the_rating_is_bounded_and_whole(self):
        body = self.delivered()
        for rating in (0, 6, -1, 3.5, "5", True, None):
            with self.subTest(rating=rating):
                response = self.review(body["order_id"], view_token=body["view_token"], rating=rating)
                self.assertEqual(response.status_code, 422, response.content)
                self.assertIn("rating", response.json()["error"]["fields"])
        response = self.review(body["order_id"], view_token=body["view_token"])
        self.assertEqual(response.status_code, 422)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductReview.objects.filter().model.objects.create(
                site=self.site, order=ShopOrder.objects.get(), order_line=ShopOrder.objects.get().lines.get(),
                product_id="x", rating=6, display_name="A",
            )
        self.assertEqual(ProductReview.objects.count(), 0)

    def test_text_limits(self):
        body = self.delivered()
        token = body["view_token"]
        for fields in ({"body": "x" * 1001}, {"body": 5}, {"display_name": "n" * 41}, {"display_name": "  "}, {"display_name": 7}):
            with self.subTest(fields=fields):
                self.assertEqual(self.review(body["order_id"], view_token=token, rating=5, **fields).status_code, 422)
        ok = self.review(body["order_id"], view_token=token, rating=5, body="x" * 1000, display_name="n" * 40)
        self.assertEqual(ok.status_code, 200, ok.content)

    def test_the_words_are_stored_and_returned_as_text(self):
        body = self.delivered()
        text = '<script>alert(1)</script> & "quotes" <b>bold</b>'
        self.review(body["order_id"], view_token=body["view_token"], rating=5, body=text, display_name="<i>Mia</i>")
        self.assertEqual(ProductReview.objects.get().body, text)
        response = self.page()
        self.assertEqual(response["Content-Type"].split(";")[0], "application/json")
        review = response.json()["reviews"][0]
        self.assertEqual((review["body"], review["display_name"]), (text, "<i>Mia</i>"))

    def test_the_switch_and_a_foreign_host(self):
        body = self.delivered()
        with override_settings(SHOP_ENABLED=False):
            self.assertEqual(self.review(body["order_id"], view_token=body["view_token"], rating=5).status_code, 404)
            self.assertEqual(self.page().status_code, 404)
        self.assertEqual(self.review(body["order_id"], host=BEN_HOST, view_token=body["view_token"], rating=5).status_code, 404)
        self.assertEqual(self.review(body["order_id"], host="nowhere.example.test", view_token=body["view_token"], rating=5).status_code, 404)


class EditingTests(ReviewTestCase):
    def setUp(self):
        super().setUp()
        self.body = self.delivered()
        self.token = self.body["view_token"]
        self.review(self.body["order_id"], view_token=self.token, rating=3, body="Okay.")

    def test_one_change_is_allowed_then_it_is_locked(self):
        first = self.change(self.body["order_id"], view_token=self.token, rating=5, body="Better.")
        self.assertEqual(first.status_code, 200, first.content)
        out = first.json()
        self.assertEqual((out["rating"], out["body"], out["edited"], out["can_edit"]), (5, "Better.", True, False))
        second = self.change(self.body["order_id"], view_token=self.token, rating=1)
        self.assertEqual((second.status_code, second.json()["error"]["code"]), (409, "review_locked"))
        self.assertEqual(ProductReview.objects.get().rating, 5)

    def test_a_change_after_seven_days_is_refused(self):
        ProductReview.objects.update(created_at=timezone.now() - timedelta(days=7, minutes=1))
        response = self.change(self.body["order_id"], view_token=self.token, rating=1)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "review_locked"))
        ProductReview.objects.update(created_at=timezone.now() - timedelta(days=6, hours=23))
        self.assertEqual(self.change(self.body["order_id"], view_token=self.token, rating=1).status_code, 200)

    def test_a_change_that_changes_nothing_keeps_the_one_edit(self):
        same = self.change(self.body["order_id"], view_token=self.token, rating=3, body="Okay.")
        self.assertEqual((same.status_code, same.json()["edited"]), (200, False))
        self.assertEqual(self.change(self.body["order_id"], view_token=self.token, rating=4).status_code, 200)

    def test_invalid_values_do_not_use_the_edit(self):
        self.assertEqual(self.change(self.body["order_id"], view_token=self.token, rating=9).status_code, 422)
        self.assertEqual(self.change(self.body["order_id"], view_token=self.token, body="x" * 1001).status_code, 422)
        self.assertIsNone(ProductReview.objects.get().edited_at)

    def test_only_the_owner_of_the_order_edits_it(self):
        other = self.delivered()
        response = self.change(self.body["order_id"], view_token=other["view_token"], rating=1)
        self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND))
        self.assertEqual(ProductReview.objects.get().rating, 3)

    def test_a_line_with_no_review_has_nothing_to_change(self):
        fresh = self.delivered()
        self.assertEqual(self.change(fresh["order_id"], view_token=fresh["view_token"], rating=4).status_code, 404)

    def test_a_review_is_never_deleted(self):
        with self.assertRaises(ImmutableRecordError):
            ProductReview.objects.get().delete()


class BuyerViewTests(ReviewTestCase):
    def test_the_order_view_says_what_can_be_reviewed(self):
        body = self.placed()
        view = lambda: self.post(f"shop/orders/{body['order_id']}/view/", view_token=body["view_token"]).json()["lines"][0]  # noqa: E731
        self.assertEqual((view()["can_review"], view()["review"], view()["position"]), (False, None, 1))
        deliver(ShopOrder.objects.get(public_id=body["order_id"]))
        self.assertEqual((view()["can_review"], view()["product_id"]), (True, self.a.public_id))
        self.review(body["order_id"], view_token=body["view_token"], rating=4)
        line = view()
        self.assertEqual((line["can_review"], line["review"]["rating"]), (False, 4))

    def test_my_orders_carries_the_review_state(self):
        token = self.token(self.member())
        body = self.delivered(member_token=token)
        self.review(body["order_id"], member_token=token, rating=2)
        card = self.post("member/shop-orders/", token=token).json()["cards"][0]
        self.assertEqual(card["lines"][0]["review"]["rating"], 2)


class StorefrontTests(ReviewTestCase):
    def reviewed(self, rating, **extra):
        body = self.delivered()
        response = self.review(body["order_id"], view_token=body["view_token"], rating=rating, **extra)
        self.assertEqual(response.status_code, 200, response.content)
        return ProductReview.objects.get(public_id=response.json()["id"])

    def test_no_reviews_gives_zero_and_no_average(self):
        out = self.page().json()
        self.assertEqual((out["rating_count"], out["rating_average"], out["reviews"], out["has_more"]), (0, None, [], False))

    def test_average_count_latest_first_and_verified(self):
        self.reviewed(5, body="One")
        self.reviewed(4, body="Two")
        self.reviewed(4, body="Three")
        out = self.page().json()
        self.assertEqual((out["rating_count"], out["rating_average"]), (3, "4.3"))
        self.assertEqual([r["body"] for r in out["reviews"]], ["Three", "Two", "One"])
        self.assertTrue(all(r["verified_buyer"] for r in out["reviews"]))
        self.assertEqual(set(out["reviews"][0]), {"id", "rating", "body", "display_name", "created_at", "edited", "verified_buyer"})
        self.assertEqual(self.page(self.b).json()["rating_count"], 0)

    def test_pages(self):
        for n in range(C.REVIEW_PAGE_SIZE + 2):
            self.reviewed(5, body=f"r{n}")
        first, second = self.page().json(), self.page(page=2).json()
        self.assertEqual((len(first["reviews"]), first["has_more"], first["rating_count"]), (10, True, 12))
        self.assertEqual((len(second["reviews"]), second["has_more"]), (2, False))
        self.assertEqual(self.page(page=0).json()["page"], 1)

    def test_hidden_reviews_are_not_public_or_counted(self):
        shown, hidden = self.reviewed(5, body="Shown"), self.reviewed(1, body="Hidden")
        ProductReview.objects.filter(pk=hidden.pk).update(is_hidden=True, hidden_at=timezone.now())
        out = self.page().json()
        self.assertEqual((out["rating_count"], out["rating_average"], [r["id"] for r in out["reviews"]]), (1, "5.0", [shown.public_id]))
        self.assertNotIn("Hidden", self.page().content.decode())

    def test_a_store_shows_only_its_own_reviews_both_ways(self):
        mine = self.reviewed(5, body="Mine")
        ben_order = self.ben_order()
        ben_line = ben_order.lines.get()
        ProductReview.objects.create(
            site=self.other_site, order=ben_order, order_line=ben_line, product_id=self.a.public_id,
            rating=1, body="Bens", display_name="Ben B.",
        )
        ours = self.page().json()
        bens = self.page(host=BEN_HOST).json()
        self.assertEqual([r["body"] for r in ours["reviews"]], ["Mine"])
        self.assertEqual([r["body"] for r in bens["reviews"]], ["Bens"])
        self.assertEqual((ours["rating_count"], bens["rating_count"]), (1, 1))
        self.assertEqual(mine.site, self.site)

    def test_an_unknown_or_inactive_product_is_a_404(self):
        self.assertEqual(self.client.get(
            "/storefront/v1/shop/products/prod_missing/reviews/", {"host": HOST}, secure=True, **self.headers()
        ).status_code, 404)
        self.a.__class__.objects.filter(pk=self.a.pk).update(is_active=False)
        self.assertEqual(self.page().status_code, 404)

    def test_the_server_credential_is_needed(self):
        response = self.client.get(
            f"/storefront/v1/shop/products/{self.a.public_id}/reviews/", {"host": HOST}, secure=True
        )
        self.assertEqual(response.status_code, 401)


class OwnerReviewTests(OwnerTestCase):
    def make_review(self, site, *, body="Nice", rating=5, product="prod_x", hidden=False):
        order, _ = make_order(site, status=C.VERIFIED, shipped=True, delivered=True)
        return ProductReview.objects.create(
            site=site, order=order, order_line=order.lines.get(), product_id=product, rating=rating, body=body,
            display_name="Maria S.", is_hidden=hidden, hidden_at=timezone.now() if hidden else None,
        )

    def url(self, tail="", site_id=None):
        return f"/v1/sites/{site_id or self.site_id}/shop/reviews/{tail}"

    def test_the_owner_lists_the_stores_reviews_with_filters(self):
        a = self.make_review(self.site, body="A")
        b = self.make_review(self.site, body="B", hidden=True, product="prod_y")
        rows = self.get(self.url()).json()["reviews"]
        self.assertEqual([r["id"] for r in rows], [b.public_id, a.public_id])
        self.assertEqual(
            {"id", "rating", "body", "display_name", "created_at", "edited", "verified_buyer", "product_id",
             "product_code", "product_name", "order_number", "is_hidden", "hidden_at"},
            set(rows[0]),
        )
        self.assertEqual([r["id"] for r in self.get(self.url("?product=prod_y")).json()["reviews"]], [b.public_id])
        self.assertEqual([r["id"] for r in self.get(self.url("?hidden=true")).json()["reviews"]], [b.public_id])
        self.assertEqual([r["id"] for r in self.get(self.url("?hidden=false")).json()["reviews"]], [a.public_id])

    def test_hide_and_show_leave_the_words_alone(self):
        review = self.make_review(self.site, body="<b>Words</b>")
        hidden = self.post(self.url(f"{review.public_id}/hide/"), {})
        self.assertEqual(hidden.status_code, 200, hidden.content)
        self.assertEqual((hidden.json()["is_hidden"], hidden.json()["body"]), (True, "<b>Words</b>"))
        review.refresh_from_db()
        self.assertTrue(review.is_hidden and review.hidden_at is not None)
        self.assertEqual(self.post(self.url(f"{review.public_id}/hide/"), {}).status_code, 200)  # idempotent
        shown = self.post(self.url(f"{review.public_id}/show/"), {})
        self.assertEqual(shown.json()["is_hidden"], False)
        review.refresh_from_db()
        self.assertEqual((review.is_hidden, review.hidden_at, review.body, review.rating), (False, None, "<b>Words</b>", 5))

    def test_the_owner_cannot_edit_the_words(self):
        review = self.make_review(self.site)
        for method in ("put", "patch"):
            self.assertEqual(self.send(method, self.url(f"{review.public_id}/"), {"body": "x"}).status_code, 404)
        self.post(self.url(f"{review.public_id}/hide/"), {"body": "changed", "rating": 1})
        review.refresh_from_db()
        self.assertEqual((review.body, review.rating), ("Nice", 5))

    def test_another_stores_reviews_are_invisible_both_ways(self):
        ben, ben_site, *_ = self.other_site()
        mine = self.make_review(self.site, body="Mine")
        theirs = self.make_review(ben_site, body="Theirs")
        self.assertEqual([r["body"] for r in self.get(self.url()).json()["reviews"]], ["Mine"])
        self.assertEqual([r["body"] for r in self.get(self.url(site_id=ben_site.public_id), client=ben).json()["reviews"]], ["Theirs"])
        # not mine to hide, whichever site id is in the path
        for site_id, client in ((self.site_id, None), (ben_site.public_id, ben)):
            target = theirs if client is None else mine
            response = self.post(self.url(f"{target.public_id}/hide/", site_id=site_id), {}, client=client)
            self.assertEqual(response.status_code, 404)
        # and Ben cannot reach Ana's site at all
        self.assertIn(self.get(self.url(), client=ben).status_code, (403, 404))
        for review in (mine, theirs):
            review.refresh_from_db()
            self.assertFalse(review.is_hidden)

    def test_signed_out_and_switch_off(self):
        review = self.make_review(self.site)
        self.assertEqual(self.get(self.url(), client=Client()).status_code, 401)
        with override_settings(SHOP_ENABLED=False):
            self.assertEqual(self.get(self.url()).status_code, 404)
            self.assertEqual(self.post(self.url(f"{review.public_id}/hide/"), {}).status_code, 404)
