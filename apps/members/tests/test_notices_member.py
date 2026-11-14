"""The member's dashboard reads: announcements and promos, and the bell. Everything is read under
the session's own site and member."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from apps.catalog.tests.base import MEDIA
from apps.members import notices
from apps.members.models import MemberNotice, MemberNotification
from apps.members.tests.base import BASE, NOT_FOUND, NOT_SIGNED_IN, OTHER_HOST, MembersTestCase

BEN_EMAIL, BEN_MOBILE = "ben@buyer.test", "09175550120"
CARLA_EMAIL, CARLA_MOBILE = "carla@buyer.test", "09175550121"


def notice(site, title="Hello", **fields):
    values = {"kind": "announcement", "body": "Body", **fields}
    return MemberNotice.objects.create(site=site, title=title, **values)


def bell(member, title="Ping", **fields):
    return MemberNotification.objects.create(
        site=member.site, member=member, kind=fields.pop("kind", "notice"), title=title, url="/dashboard/", **fields
    )


class NoticeReadTests(MembersTestCase):
    def read(self, token, host=None, **extra):
        body = {"token": token, **extra}
        return self.post("notices/", **({"host": host} if host else {}), **body)

    def test_a_member_sees_the_visible_notices_of_their_own_site_only(self):
        mine = notice(self.site, "Mine", kind="promo")
        notice(self.other_site, "Theirs")
        token = self.sign_in()
        response = self.read(token)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([n["id"] for n in response.json()["notices"]], [mine.public_id])
        self.assertEqual(response.json()["notices"][0]["kind"], "promo")
        self.assertNotIn(b"Theirs", response.content)

    def test_inactive_expired_and_not_yet_started_notices_are_left_out(self):
        now = timezone.now()
        shown = notice(self.site, "Shown", starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=1))
        open_ended = notice(self.site, "Open")
        notice(self.site, "Off", active=False)
        notice(self.site, "Over", ends_at=now - timedelta(minutes=1))
        notice(self.site, "Later", starts_at=now + timedelta(hours=1))
        found = self.read(self.sign_in()).json()["notices"]
        self.assertEqual({n["id"] for n in found}, {shown.public_id, open_ended.public_id})

    def test_the_answer_carries_no_flag_or_row_number(self):
        notice(self.site)
        row = self.read(self.sign_in()).json()["notices"][0]
        self.assertEqual(set(row), {"id", "kind", "title", "body", "image_url", "starts_at", "ends_at"})

    @override_settings(**MEDIA)
    def test_the_picture_is_a_public_url_or_null(self):
        with_image = notice(self.site, "Pic", image_key="member-notices/" + "a" * 32 + ".png")
        plain = notice(self.site, "Plain")
        rows = {n["id"]: n for n in self.read(self.sign_in()).json()["notices"]}
        self.assertEqual(rows[with_image.public_id]["image_url"], f"https://media.example.test/{with_image.image_key}")
        self.assertIsNone(rows[plain.public_id]["image_url"])

    def test_a_session_of_another_site_is_refused_on_this_host(self):
        self.new_member(self.other_site, email=BEN_EMAIL, mobile=BEN_MOBILE, name="Ben")
        notice(self.site)
        token = self.sign_in(BEN_EMAIL, host=OTHER_HOST)
        response = self.read(token)  # Ben's token, Ana's host
        self.assertEqual((response.status_code, response.json()), (401, NOT_SIGNED_IN))

    def test_no_session_a_wrong_token_an_unknown_host_and_the_switch_off(self):
        notice(self.site)
        self.assertEqual(self.read("").status_code, 401)
        self.assertEqual(self.read("not-a-real-token-0123456789").status_code, 401)
        token = self.sign_in()
        self.assertEqual(self.read(token, host="nobody.negosyotest.com").json(), NOT_FOUND)
        with override_settings(MEMBERS_ENABLED=False):
            response = self.read(token)
        self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND))

    def test_the_credential_is_required(self):
        response = self.post("notices/", secret=None, token="x")
        self.assertEqual(response.status_code, 401)


class NotificationTests(MembersTestCase):
    def setUp(self):
        super().setUp()
        self.ben = self.new_member(self.other_site, email=BEN_EMAIL, mobile=BEN_MOBILE, name="Ben")
        self.carla = self.new_member(self.site, email=CARLA_EMAIL, mobile=CARLA_MOBILE, name="Carla")
        self.token = self.sign_in()

    def list(self, **extra):
        return self.post("notifications/", token=self.token, **extra)

    def test_a_member_sees_only_their_own_entries_newest_first_with_the_unread_count(self):
        now = timezone.now()
        older = bell(self.member, "Older", created_at=now - timedelta(hours=2))
        newer = bell(self.member, "Newer", created_at=now - timedelta(hours=1))
        read = bell(self.member, "Read", created_at=now - timedelta(hours=3), read_at=now)
        bell(self.carla, "Carla's")
        bell(self.ben, "Ben's")
        body = self.list().json()
        self.assertEqual([n["id"] for n in body["notifications"]], [newer.public_id, older.public_id, read.public_id])
        self.assertEqual(body["unread_count"], 2)
        self.assertEqual(set(body["notifications"][0]), {"id", "kind", "title", "url", "created_at", "read_at"})
        self.assertNotIn(b"Carla's", self.list().content)
        self.assertNotIn(b"Ben's", self.list().content)

    def test_the_limit_is_kept_between_1_and_50_and_defaults_to_20(self):
        for n in range(25):
            bell(self.member, f"n{n}")
        self.assertEqual(len(self.list().json()["notifications"]), 20)
        self.assertEqual(len(self.list(limit=3).json()["notifications"]), 3)
        self.assertEqual(len(self.list(limit=0).json()["notifications"]), 1)
        self.assertEqual(len(self.list(limit=500).json()["notifications"]), 25)
        self.assertEqual(self.list(limit=500).json()["unread_count"], 25)

    def test_marking_one_read_sets_read_at_once_and_updates_the_count(self):
        first, second = bell(self.member, "A"), bell(self.member, "B")
        response = self.post(f"notifications/{first.public_id}/read/", token=self.token)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["unread_count"], 1)
        stamp = MemberNotification.objects.get(pk=first.pk).read_at
        self.assertIsNotNone(stamp)
        self.assertEqual(response.json()["notification"]["id"], first.public_id)
        again = self.post(f"notifications/{first.public_id}/read/", token=self.token)  # twice is not an error
        self.assertEqual(again.status_code, 200)
        self.assertEqual(MemberNotification.objects.get(pk=first.pk).read_at, stamp)
        self.assertIsNone(MemberNotification.objects.get(pk=second.pk).read_at)

    def test_another_members_entry_and_another_sites_entry_answer_the_same_404_as_a_missing_one(self):
        theirs, bens = bell(self.carla, "Carla's"), bell(self.ben, "Ben's")
        missing = self.post("notifications/ntf_00000000000000000000/read/", token=self.token)
        self.assertEqual((missing.status_code, missing.json()), (404, NOT_FOUND))
        for row in (theirs, bens):
            response = self.post(f"notifications/{row.public_id}/read/", token=self.token)
            self.assertEqual((response.status_code, response.content), (404, missing.content))
            self.assertIsNone(MemberNotification.objects.get(pk=row.pk).read_at)

    def test_read_all_marks_only_my_own_entries(self):
        bell(self.member, "A"), bell(self.member, "B")
        theirs, bens = bell(self.carla, "C"), bell(self.ben, "D")
        response = self.post("notifications/read-all/", token=self.token)
        self.assertEqual(response.json(), {"marked": 2, "unread_count": 0})
        self.assertEqual(MemberNotification.objects.filter(member=self.member, read_at__isnull=True).count(), 0)
        for row in (theirs, bens):
            self.assertIsNone(MemberNotification.objects.get(pk=row.pk).read_at)

    def test_a_session_is_needed_and_a_foreign_host_does_not_reach_the_entries(self):
        row = bell(self.member)
        for path in ("notifications/", "notifications/read-all/", f"notifications/{row.public_id}/read/"):
            with self.subTest(path=path):
                response = self.post(path, token="")
                self.assertEqual((response.status_code, response.json()), (401, NOT_SIGNED_IN))
                wrong_host = self.post(path, host=OTHER_HOST, token=self.token)
                self.assertEqual(wrong_host.status_code, 401)
        self.assertIsNone(MemberNotification.objects.get(pk=row.pk).read_at)

    def test_the_routes_sit_under_the_member_prefix(self):
        self.assertEqual(self.list().status_code, 200)
        self.assertTrue(BASE.endswith("/member/"))


class FanOutTests(MembersTestCase):
    def test_a_visible_notice_puts_one_entry_in_each_members_bell_of_that_site_only(self):
        ben = self.new_member(self.other_site, email=BEN_EMAIL, mobile=BEN_MOBILE, name="Ben")
        carla = self.new_member(self.site, email=CARLA_EMAIL, mobile=CARLA_MOBILE, name="Carla")
        notices.create_notice(self.site, {"kind": "promo", "title": "Sale"})
        self.assertEqual(MemberNotification.objects.filter(member__in=[self.member, carla]).count(), 2)
        self.assertEqual(MemberNotification.objects.filter(member=ben).count(), 0)
        row = MemberNotification.objects.get(member=self.member)
        self.assertEqual((row.site, row.kind, row.title, row.url, row.read_at), (self.site, "notice", "Sale", "/dashboard/", None))

    def test_an_inactive_or_not_yet_started_notice_notifies_nobody(self):
        notices.create_notice(self.site, {"kind": "promo", "title": "Off", "active": False})
        notices.create_notice(self.site, {"kind": "promo", "title": "Later",
                                          "starts_at": timezone.now() + timedelta(days=1)})
        self.assertEqual(MemberNotification.objects.count(), 0)
