"""The member's media library read: access, site isolation, hidden assets, filters, paging."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from apps.catalog.tests.base import MEDIA
from apps.media_library.tests.base import make_asset
from apps.members.tests.base import NOT_FOUND, NOT_SIGNED_IN, OTHER_HOST, MembersTestCase


@override_settings(**MEDIA)
class MediaTestCase(MembersTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.sign_in()

    def list(self, token=None, **extra):
        return self.post("media/", token=self.token if token is None else token, **extra)

    def titles(self, **extra):
        response = self.list(**extra)
        self.assertEqual(response.status_code, 200, response.content)
        return [x["title"] for x in response.json()["items"]]


class AccessTests(MediaTestCase):
    def test_a_member_reads_the_library_and_gets_urls_and_labels(self):
        make_asset(self.site, "Barley promo", product_category="barley", caption="Buy now", width=1080, height=1350,
                   file_size=1234, thumb_key=f"media-library/{self.site.public_id}/t.png")
        item = self.list().json()["items"][0]
        self.assertEqual(item["file_url"], f"https://media.example.test/media-library/{self.site.public_id}/barleypromo.png")
        self.assertEqual(item["thumb_url"], f"https://media.example.test/media-library/{self.site.public_id}/t.png")
        self.assertIsNone(item["poster_url"])
        self.assertEqual(
            (item["category"], item["category_label"], item["product_category_label"], item["caption"]),
            ("promo", "Product Promo", "Barley", "Buy now"),
        )
        self.assertEqual((item["width"], item["height"], item["file_size"], item["is_new"]), (1080, 1350, 1234, True))
        for hidden in ("active", "order", "file_key", "id_pk", "site", "template_key"):
            self.assertNotIn(hidden, item)

    def test_a_video_card_uses_its_poster_as_the_thumbnail(self):
        make_asset(self.site, "Clip", asset_type="video", file_key="media-library/c.mp4", poster_key="media-library/p.png",
                   duration_seconds=30)
        item = self.list().json()["items"][0]
        self.assertEqual(item["thumb_url"], "https://media.example.test/media-library/p.png")
        self.assertEqual((item["file_url"].endswith(".mp4"), item["duration_seconds"]), (True, 30))

    def test_no_session_a_wrong_secret_and_a_session_of_another_site_are_refused(self):
        for token in ("", "nonsense"):
            self.assertEqual(self.list(token=token).json(), NOT_SIGNED_IN)
        self.assertEqual(self.list(secret="wrong").status_code, 401)
        self.assertEqual(self.list(secret=None).status_code, 401)
        self.new_member(self.other_site, email="ben@buyer.test", mobile="09175550120", name="Ben")
        ben = self.sign_in("ben@buyer.test", host=OTHER_HOST)
        self.assertEqual(self.list(token=ben).status_code, 401)

    def test_a_site_of_another_template_gets_nothing(self):
        self.site.template = "basic"
        self.site.save()
        self.assertEqual(self.list().json(), NOT_FOUND)

    @override_settings(MEMBERS_ENABLED=False)
    def test_the_switch_off_is_not_found(self):
        self.assertEqual(self.list().status_code, 404)

    def test_the_library_is_read_only_for_a_member(self):
        make_asset(self.site)
        for path in ("media/create/", "media/delete/", "media/upload/", "media/update/"):
            self.assertEqual(self.post(path, token=self.token, id="x").status_code, 404, path)


    def test_the_owner_session_alone_does_not_read_the_member_route(self):
        # the member list needs the storefronts server's secret and a member session; the owner's cookie is neither
        response = self.client.post(
            "/storefront/v1/member/media/", {"host": "x.test", "token": "t"},
            content_type="application/json", secure=True,
        )
        self.assertEqual(response.status_code, 401)

    def test_a_second_site_never_sees_the_first_sites_media_and_the_other_way_round(self):
        make_asset(self.site, "Anas")
        make_asset(self.other_site, "Bens")
        self.assertEqual(self.titles(), ["Anas"])
        self.new_member(self.other_site, email="ben@buyer.test", mobile="09175550120", name="Ben")
        ben = self.sign_in("ben@buyer.test", host=OTHER_HOST)
        body = self.list(token=ben, host=OTHER_HOST).json()
        self.assertEqual([x["title"] for x in body["items"]], ["Bens"])
        self.assertEqual(body["count"], 1)
        self.assertNotIn(self.site.public_id, str(body))


class HiddenAssetTests(MediaTestCase):
    def test_inactive_unpublished_future_and_other_sites_assets_are_not_listed(self):
        make_asset(self.site, "Shown")
        make_asset(self.site, "Inactive", active=False)
        make_asset(self.site, "Draft", published_at=None)
        make_asset(self.site, "Future", published_at=timezone.now() + timedelta(days=1))
        make_asset(self.other_site, "Elsewhere")
        self.assertEqual(self.titles(), ["Shown"])
        body = self.list().json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(sum(o["count"] for o in body["options"]["type"]), 1)


class FilterTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        make_asset(self.site, "Barley ad", category="ad", product_category="barley", days_ago=3, caption="Healthy")
        make_asset(self.site, "Drink post", category="page", product_category="drinks", days_ago=2, caption="Cold barley")
        make_asset(self.site, "Beauty clip", asset_type="video", file_key="media-library/c.mp4", poster_key="media-library/p.png",
                   category="promo", product_category="beauty", days_ago=1)
        make_asset(self.site, "No product", days_ago=0)

    def test_newest_is_the_default_and_oldest_reverses_it(self):
        expected = ["No product", "Beauty clip", "Drink post", "Barley ad"]
        self.assertEqual(self.titles(), expected)
        self.assertEqual(self.titles(sort="newest"), expected)
        self.assertEqual(self.titles(sort="oldest"), expected[::-1])

    def test_each_filter_and_their_combination(self):
        self.assertEqual(self.titles(type="video"), ["Beauty clip"])
        self.assertEqual(self.titles(category="ad"), ["Barley ad"])
        self.assertEqual(self.titles(product_category="drinks"), ["Drink post"])
        self.assertEqual(self.titles(type="image", category="promo"), ["No product"])
        self.assertEqual(self.titles(type="video", category="ad"), [])

    def test_search_matches_title_and_caption_ignoring_case(self):
        self.assertEqual(self.titles(search="barley"), ["Drink post", "Barley ad"])
        self.assertEqual(self.titles(search="HEALTHY"), ["Barley ad"])
        self.assertEqual(self.titles(search="  "), ["No product", "Beauty clip", "Drink post", "Barley ad"])
        self.assertEqual(self.titles(search="zzz"), [])

    def test_count_is_the_number_of_matches_and_options_are_unfiltered(self):
        body = self.list(search="barley").json()
        self.assertEqual(body["count"], 2)
        by = {k: {o["value"]: o["count"] for o in v} for k, v in body["options"].items()}
        self.assertEqual(by["type"], {"image": 3, "video": 1})
        self.assertEqual(by["category"], {"promo": 2, "ad": 1, "page": 1})
        self.assertEqual(by["product_category"], {"barley": 1, "drinks": 1, "essentials": 0, "beauty": 1})
        self.assertEqual(body["options"]["category"][0]["label"], "Product Promo")

    def test_a_value_outside_the_lists_is_refused(self):
        for field, value in (("type", "audio"), ("category", "spons"), ("product_category", "x"),
                             ("sort", "random"), ("offset", -1), ("search", "x" * 101)):
            response = self.list(**{field: value})
            self.assertEqual(response.status_code, 400, field)
            self.assertIn(field, response.json()["error"]["fields"])


class PagingTests(MediaTestCase):
    def test_twelve_at_a_time_with_a_next_offset(self):
        for n in range(30):
            make_asset(self.site, f"Item {n:02d}", days_ago=30 - n)  # Item 29 is the newest
        first = self.list().json()
        self.assertEqual((len(first["items"]), first["count"], first["limit"], first["next_offset"]), (12, 30, 12, 12))
        self.assertEqual(first["items"][0]["title"], "Item 29")
        second = self.list(offset=first["next_offset"]).json()
        self.assertEqual((len(second["items"]), second["next_offset"]), (12, 24))
        last = self.list(offset=24).json()
        self.assertEqual((len(last["items"]), last["next_offset"]), (6, None))
        seen = [x["id"] for page in (first, second, last) for x in page["items"]]
        self.assertEqual(len(set(seen)), 30)
        self.assertEqual(self.list(offset=99).json()["items"], [])

    def test_an_empty_library_answers_cleanly(self):
        body = self.list().json()
        self.assertEqual((body["items"], body["count"], body["next_offset"]), ([], 0, None))
