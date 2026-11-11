"""The member tables: constraints, ids and the stored session hash."""
from django.db import DataError, IntegrityError, transaction
from django.test import TestCase

from apps.common.ids import is_public_id
from apps.members import sessions
from apps.members.models import Member, MemberSession
from apps.members.tests.base import EMAIL, MOBILE, MembersTestCase


class MemberModelTests(MembersTestCase):
    def make(self, **fields):
        values = {
            "site": self.site, "full_name": "Ana", "mobile": "+639181234567",
            "email": "ana@buyer.test", **fields,
        }
        return Member.objects.create(**values)

    def refused(self, **fields):
        with self.assertRaises((IntegrityError, DataError)), transaction.atomic():
            self.make(**fields)

    def test_a_member_has_a_mem_public_id(self):
        self.assertTrue(is_public_id(self.member.public_id, "mem"))

    def test_a_new_member_has_both_contacts_pending_and_the_order_source(self):
        self.assertEqual(
            (self.member.mobile_state, self.member.email_state, self.member.source),
            ("pending", "pending", "onboarding"),
        )

    def test_the_mobile_and_the_email_are_each_unique_within_a_site(self):
        self.refused(mobile=self.member.mobile)
        self.refused(email=self.member.email)

    def test_the_same_contacts_are_allowed_on_another_site_by_the_table(self):
        self.make(site=self.other_site, mobile=self.member.mobile, email=self.member.email)

    def test_the_email_must_be_lowercase(self):
        self.refused(email="Ana@Buyer.test")

    def test_the_mobile_must_be_plus_639_and_nine_digits(self):
        for bad in ("09181234567", "+63918123456", "+6391812345678", "+639abcdefghi", "+639١٢٣٤٥٦٧٨٩"):
            with self.subTest(mobile=bad):
                self.refused(mobile=bad)

    def test_a_session_stores_only_the_hash_of_its_token(self):
        session, raw = sessions.create_session(self.site, self.member)
        self.assertEqual(session.token_hash, sessions.token_hash(raw))
        self.assertNotIn(raw, session.token_hash)
        self.assertEqual(len(session.token_hash), 64)
        self.assertFalse(MemberSession.objects.filter(token_hash=raw).exists())

    def test_two_sessions_never_share_a_hash(self):
        first, _ = sessions.create_session(self.site, self.member)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MemberSession.objects.create(
                site=self.site, member=self.member, token_hash=first.token_hash,
                expires_at=first.expires_at,
            )

    def test_a_site_with_members_cannot_be_deleted(self):
        from django.db.models import ProtectedError

        with self.assertRaises(ProtectedError):
            self.site.delete()
