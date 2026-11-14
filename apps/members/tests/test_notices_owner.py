"""The Subscriber's notice editor: the gates, validation, the picture, and one owner per site."""
from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.accounts.tests.base import ORIGIN
from apps.catalog.tests.base import FakeMedia, image_file
from apps.members.models import Member, MemberNotice, MemberNotification
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase

NOT_FOUND = {"error": {"code": "not_found", "message": "Not found."}}
MISSING = "ntc_00000000000000000000"


@override_settings(MEMBERS_ENABLED=True)
class NoticeOwnerCase(SitesApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.ben = self.other_client()
        self.ben_site_id = self.create_site(client=self.ben, subdomain="bens-store", display_name="Ben's")["id"]
        self.ben_site = Site.objects.get(public_id=self.ben_site_id)

    def url(self, tail="", site_id=None):
        return f"/v1/sites/{site_id or self.site_id}/member-notices/{tail}"

    def req(self, method, path, body=None, client=None):
        if method == "get":
            return self.get(path, client=client)
        return self.send(method, path, body, client=client)

    def create(self, client=None, site_id=None, **fields):
        body = {"kind": "announcement", "title": "Hello members", **fields}
        return self.send("post", self.url(site_id=site_id), body, client=client)

    def make(self, site=None, **fields):
        return MemberNotice.objects.create(site=site or self.site, **{"kind": "promo", "title": "Fixture", **fields})

    def upload(self, notice_id, client=None, site_id=None, file=None):
        return (client or self.client).post(
            self.url(f"{notice_id}/image/", site_id), data={"file": file or image_file()}, secure=True, HTTP_ORIGIN=ORIGIN
        )


class GateTests(NoticeOwnerCase):
    def test_every_route_is_404_while_the_switch_is_off(self):
        notice = self.make()
        with override_settings(MEMBERS_ENABLED=False):
            for method, tail in (("get", ""), ("post", ""), ("patch", f"{notice.public_id}/"),
                                 ("delete", f"{notice.public_id}/"), ("delete", f"{notice.public_id}/image/")):
                with self.subTest(method=method, tail=tail):
                    response = self.req(method, self.url(tail), {"kind": "promo", "title": "x"} if method != "delete" else None)
                    self.error(response, 404, "not_found")
            self.error(self.upload(notice.public_id), 404, "not_found")

    def test_signed_out_is_401(self):
        self.client.cookies.clear()
        self.assertEqual(self.get(self.url()).status_code, 401)
        self.assertEqual(self.create().status_code, 401)

    def test_a_site_that_is_not_santa_is_404(self):
        Site.objects.filter(pk=self.site.pk).update(template="other")
        self.error(self.get(self.url()), 404, "not_found")

    def test_every_answer_is_not_cached(self):
        self.assertEqual(self.get(self.url())["Cache-Control"], "no-store")


class CrudTests(NoticeOwnerCase):
    def test_create_list_update_delete(self):
        now = timezone.now()
        response = self.create(
            kind="promo", title="  Big sale ", body="20% off", active=True,
            starts_at=(now - timedelta(days=1)).isoformat(), ends_at=(now + timedelta(days=3)).isoformat(),
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["kind"], body["title"], body["body"], body["active"], body["image_url"]),
                         ("promo", "Big sale", "20% off", True, None))
        self.assertTrue(body["id"].startswith("ntc_"))
        self.assertEqual(set(body), {"id", "kind", "title", "body", "image_url", "starts_at", "ends_at", "active",
                                     "created_at", "updated_at"})
        listed = self.get(self.url()).json()["notices"]
        self.assertEqual([n["id"] for n in listed], [body["id"]])
        patched = self.send("patch", self.url(f"{body['id']}/"), {"title": "Bigger sale", "active": False})
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual((patched.json()["title"], patched.json()["active"], patched.json()["body"]),
                         ("Bigger sale", False, "20% off"))
        self.assertEqual(self.send("delete", self.url(f"{body['id']}/")).json(), {"status": "deleted"})
        self.assertEqual(self.get(self.url()).json(), {"notices": []})

    def test_the_list_holds_inactive_and_expired_notices_too(self):
        self.make(active=False)
        self.make(ends_at=timezone.now() - timedelta(days=1))
        self.assertEqual(len(self.get(self.url()).json()["notices"]), 2)

    def test_a_patch_can_clear_a_date(self):
        notice = self.make(ends_at=timezone.now() + timedelta(days=1))
        response = self.send("patch", self.url(f"{notice.public_id}/"), {"ends_at": None})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json()["ends_at"])

    def test_creating_a_visible_notice_rings_each_members_bell_of_this_site_only(self):
        mine = Member.objects.create(site=self.site, full_name="M", mobile="+639171110001", email="m@x.test")
        Member.objects.create(site=self.ben_site, full_name="B", mobile="+639171110002", email="b@x.test")
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(list(MemberNotification.objects.values_list("member_id", flat=True)), [mine.pk])

    def test_bad_values_are_refused_with_the_field_named(self):
        cases = (
            ({"kind": "news"}, "kind"), ({"title": ""}, "title"), ({"title": "x" * 121}, "title"),
            ({"body": "x" * 5001}, "body"), ({"starts_at": "2026-12-01T10:00:00"}, "starts_at"),
            ({"starts_at": "2026-12-02T10:00:00+08:00", "ends_at": "2026-12-01T10:00:00+08:00"}, "ends_at"),
            ({"starts_at": "2026-12-01T10:00:00+08:00", "ends_at": "2026-12-01T10:00:00+08:00"}, "ends_at"),
        )
        for fields, name in cases:
            with self.subTest(fields=fields):
                response = self.create(**fields)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(name, response.json()["error"]["fields"])
        self.assertEqual(MemberNotice.objects.count(), 0)

    def test_unknown_keys_and_a_missing_kind_are_refused(self):
        self.assertEqual(self.send("post", self.url(), {"title": "x"}).status_code, 400)
        self.assertEqual(self.create(site="other").status_code, 400)  # an unknown key
        self.assertEqual(self.create(public_id="ntc_x").status_code, 400)
        notice = self.make()
        self.assertEqual(self.send("patch", self.url(f"{notice.public_id}/"), {"title": None}).status_code, 400)

    def test_a_patch_cannot_move_the_end_before_the_stored_start(self):
        now = timezone.now()
        notice = self.make(starts_at=now, ends_at=now + timedelta(days=2))
        response = self.send("patch", self.url(f"{notice.public_id}/"), {"ends_at": (now - timedelta(days=1)).isoformat()})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("ends_at", response.json()["error"]["fields"])


class IsolationTests(NoticeOwnerCase):
    def test_an_owner_sees_and_edits_only_their_own_sites_notices(self):
        mine, bens = self.make(), self.make(self.ben_site)
        listed = self.get(self.url()).json()["notices"]
        self.assertEqual([n["id"] for n in listed], [mine.public_id])
        ben_listed = self.get(self.url(site_id=self.ben_site_id), client=self.ben).json()["notices"]
        self.assertEqual([n["id"] for n in ben_listed], [bens.public_id])

    def test_another_accounts_site_is_the_same_404_as_a_missing_site_for_every_route(self):
        bens = self.make(self.ben_site)
        before = list(MemberNotice.objects.values())
        for method, tail, body in (
            ("get", "", None), ("post", "", {"kind": "promo", "title": "x"}),
            ("patch", f"{bens.public_id}/", {"title": "hacked"}), ("delete", f"{bens.public_id}/", None),
            ("delete", f"{bens.public_id}/image/", None),
        ):
            with self.subTest(method=method, tail=tail):
                foreign = self.req(method, self.url(tail, self.ben_site_id), body)
                missing = self.req(method, self.url(tail, "site_00000000000000000000"), body)
                self.assertEqual((foreign.status_code, foreign.json()), (404, NOT_FOUND))
                self.assertEqual(foreign.content, missing.content)
        with FakeMedia():
            self.assertEqual(self.upload(bens.public_id, site_id=self.ben_site_id).status_code, 404)
        self.assertEqual(list(MemberNotice.objects.values()), before)

    def test_a_notice_of_the_other_site_under_my_own_site_id_is_the_same_404_as_a_missing_one(self):
        bens = self.make(self.ben_site, title="Ben's")
        for method, body in (("patch", {"title": "hacked"}), ("delete", None)):
            with self.subTest(method=method):
                foreign = self.send(method, self.url(f"{bens.public_id}/"), body)
                missing = self.send(method, self.url(f"{MISSING}/"), body)
                self.assertEqual((foreign.status_code, foreign.json()), (404, NOT_FOUND))
                self.assertEqual(foreign.content, missing.content)
        for method in ("post", "delete"):
            with FakeMedia():
                response = self.upload(bens.public_id) if method == "post" else self.send(
                    "delete", self.url(f"{bens.public_id}/image/"))
                self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND))
        bens.refresh_from_db()
        self.assertEqual((bens.title, bens.image_key), ("Ben's", ""))

    def test_ben_cannot_reach_anas_site_either(self):
        self.make()
        self.assertEqual(self.get(self.url(), client=self.ben).status_code, 404)
        self.assertEqual(self.create(client=self.ben).status_code, 404)
        self.assertEqual(MemberNotice.objects.count(), 1)


class ImageTests(NoticeOwnerCase):
    def test_upload_replace_and_clear_the_picture(self):
        notice = self.make()
        with override_settings(PUBLIC_MEDIA_BASE_URL="https://media.example.test", PUBLIC_BLOB_READ_WRITE_TOKEN="t"), \
                FakeMedia() as media:
            response = self.upload(notice.public_id)
            self.assertEqual(response.status_code, 200, response.content)
            first = MemberNotice.objects.get(pk=notice.pk).image_key
            self.assertTrue(first.startswith("member-notices/"))
            self.assertEqual(response.json()["image_url"], f"https://media.example.test/{first}")
            self.assertEqual(list(media.files), [first])
            with self.captureOnCommitCallbacks(execute=True):
                self.upload(notice.public_id)  # replaces: the old file goes
            second = MemberNotice.objects.get(pk=notice.pk).image_key
            self.assertNotEqual(first, second)
            self.assertEqual((list(media.files), media.deleted), ([second], [first]))
            with self.captureOnCommitCallbacks(execute=True):
                cleared = self.send("delete", self.url(f"{notice.public_id}/image/"))
            self.assertEqual(cleared.status_code, 200, cleared.content)
            self.assertIsNone(cleared.json()["image_url"])
            self.assertEqual((media.files, media.deleted), ({}, [first, second]))

    def test_deleting_a_notice_removes_its_picture(self):
        notice = self.make()
        with FakeMedia() as media:
            self.upload(notice.public_id)
            key = MemberNotice.objects.get(pk=notice.pk).image_key
            with self.captureOnCommitCallbacks(execute=True):
                self.send("delete", self.url(f"{notice.public_id}/"))
            self.assertEqual(media.deleted, [key])

    def test_a_wrong_type_stores_nothing(self):
        notice = self.make()
        with FakeMedia() as media:
            response = self.upload(notice.public_id, file=image_file(b"plain text", "text/plain", "x.txt"))
            self.assertEqual(response.status_code, 400, response.content)
            self.assertIn("file", response.json()["error"]["fields"])
            self.assertEqual(media.files, {})

    def test_no_store_configured_is_a_503_and_nothing_changes(self):
        notice = self.make()
        response = self.upload(notice.public_id)
        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(response.json()["error"]["code"], "image_store_not_configured")
        self.assertEqual(MemberNotice.objects.get(pk=notice.pk).image_key, "")
