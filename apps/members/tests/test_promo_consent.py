"""PROMO-CONSENT (z8uq9mbreu): the wording pin, the member preference routes, the owner list
filter. Contract: docs/plans/promo-consent-be-contract-2026-11-23.md."""
from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.members import promo, sessions
from apps.members.models import Member
from apps.members.tests.base import MembersTestCase, NOT_FOUND, NOT_SIGNED_IN, OTHER_HOST
from apps.members.tests.test_owner_routes import OwnerRoutesCase, new_member


class WordingPin(MembersTestCase):
    def test_the_wording_and_version_are_pinned_together(self):
        # Any change to the wording must bump the version: change both here on purpose.
        self.assertEqual(promo.PROMO_SMS_TEXT, "Send me promotions from {store} by text. Reply STOP anytime.")
        self.assertEqual(promo.PROMO_SMS_VERSION, "2026-11-23")

    def test_the_text_names_the_store(self):
        self.assertEqual(promo.text_for(self.site),
                         f"Send me promotions from {self.site.display_name} by text. Reply STOP anytime.")


@override_settings(PROMO_SMS_CONSENT_ENABLED=True)
class PreferenceRoutes(MembersTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.token = sessions.create_session(self.site, self.member)[1]

    def read(self, **kw):
        return self.post("preferences/read/", member_token=kw.pop("member_token", self.token), **kw)

    def save(self, ok, **kw):
        return self.post("preferences/", member_token=kw.pop("member_token", self.token), promo_sms_ok=ok, **kw)

    def test_a_new_member_reads_false(self):
        response = self.read()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"promo_sms_ok": False, "promo_sms_at": None})

    def test_yes_then_no_sets_the_value_the_time_and_the_version(self):
        response = self.save(True)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["promo_sms_ok"])
        member = Member.objects.get(pk=self.member.pk)
        self.assertEqual((member.promo_sms_ok, member.promo_sms_version), (True, promo.PROMO_SMS_VERSION))
        self.assertEqual(body["promo_sms_at"], member.promo_sms_at.isoformat())
        self.assertEqual(self.read().json(), body)
        self.assertEqual(self.save(False).json()["promo_sms_ok"], False)
        member.refresh_from_db()
        self.assertFalse(member.promo_sms_ok)
        self.assertIsNotNone(member.promo_sms_at)

    def test_the_session_errors_are_the_member_routes(self):
        for call in (self.read, self.save):
            args = (True,) if call == self.save else ()
            for token in ("", "not-a-real-token"):
                response = call(*args, member_token=token)
                self.assertEqual((response.status_code, response.json()), (401, NOT_SIGNED_IN))
        self.assertEqual(self.read(host=OTHER_HOST).status_code, 401)  # this token on another store
        self.assertEqual(self.post("preferences/read/", secret=None, member_token=self.token).status_code, 401)
        self.assertFalse(Member.objects.get(pk=self.member.pk).promo_sms_ok)

    def test_another_sites_session_cannot_reach_this_member(self):
        theirs = self.new_member(site=self.other_site, email="juan@buyer.test", mobile="09170000002")
        theirs_token = sessions.create_session(self.other_site, theirs)[1]
        self.assertEqual(self.save(True, member_token=theirs_token).status_code, 401)  # their token on this host
        self.assertFalse(Member.objects.get(pk=theirs.pk).promo_sms_ok)

    @override_settings(PROMO_SMS_CONSENT_ENABLED=False)
    def test_both_routes_are_404_while_the_switch_is_off(self):
        for response in (self.read(), self.save(True), self.read(member_token="")):
            self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND))
        self.assertFalse(Member.objects.get(pk=self.member.pk).promo_sms_ok)


@override_settings(PROMO_SMS_CONSENT_ENABLED=True)
class OwnerFilter(OwnerRoutesCase):
    def agree(self, member, ago=0):
        Member.objects.filter(pk=member.pk).update(
            promo_sms_ok=True, created_at=timezone.now() - timedelta(minutes=ago))

    def fetch(self, query=""):
        response = self.get(self.url(query))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_rows_carry_promo_sms_ok(self):
        self.agree(self.member)
        other = new_member(self.site, name="Pedro Reyes")
        rows = {m["full_name"]: m for m in self.fetch("?q=Maria")["members"]}
        self.assertIs(rows["Maria Santos"]["promo_sms_ok"], True)
        self.assertIs({m["full_name"]: m for m in self.fetch("?q=Pedro")["members"]}["Pedro Reyes"]["promo_sms_ok"], False)
        self.assertNotIn("page", self.fetch("?q=Maria"))  # the plain search answer is unchanged
        self.assertIsNotNone(other)

    def test_promo_filter_with_empty_q_lists_only_agreed_members_newest_first(self):
        old = new_member(self.site, name="Old Agreed")
        new = new_member(self.site, name="New Agreed")
        new_member(self.site, name="Never Agreed")
        self.agree(old, ago=60)
        self.agree(new, ago=1)
        body = self.fetch("?promo=1")
        self.assertEqual([m["full_name"] for m in body["members"]], ["New Agreed", "Old Agreed"])
        self.assertEqual((body["page"], body["has_more"]), (1, False))
        self.assertTrue(all(m["promo_sms_ok"] for m in body["members"]))

    def test_promo_filter_narrows_a_search_to_agreed_members(self):
        agreed = new_member(self.site, name="Maria Agreed")
        new_member(self.site, name="Maria Silent")
        self.agree(agreed)
        self.assertEqual([m["full_name"] for m in self.fetch("?promo=1&q=Maria")["members"]], ["Maria Agreed"])

    def test_promo_filter_pages_by_20(self):
        for i in range(25):
            self.agree(new_member(self.site, name=f"Buyer {i:02d}"), ago=100 - i)  # Buyer 24 is newest
        first = self.fetch("?promo=1")
        self.assertEqual((len(first["members"]), first["page"], first["has_more"]), (20, 1, True))
        self.assertEqual(first["members"][0]["full_name"], "Buyer 24")
        second = self.fetch("?promo=1&page=2")
        self.assertEqual((len(second["members"]), second["page"], second["has_more"]), (5, 2, False))
        self.assertEqual(second["members"][-1]["full_name"], "Buyer 00")

    @override_settings(PROMO_SMS_CONSENT_ENABLED=False)
    def test_switch_off_the_field_is_absent_and_the_filter_is_ignored(self):
        self.agree(self.member)
        new_member(self.site, name="Maria Other")
        rows = self.fetch("?q=Maria")["members"]
        self.assertTrue(all("promo_sms_ok" not in m for m in rows))
        body = self.fetch("?promo=1&q=Maria")
        self.assertEqual(len(body["members"]), 2)  # not narrowed
        self.assertNotIn("page", body)
        self.assertEqual(self.fetch("?promo=1")["members"], [])  # an empty q still lists nobody
