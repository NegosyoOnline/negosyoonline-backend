"""create_member and change_contacts."""
from django.utils import timezone

from apps.common.errors import ApiError
from apps.members import services, sessions
from apps.members.models import Member, MemberSession
from apps.members.tests.base import EMAIL, MOBILE, MembersTestCase
from apps.sites.models import Site


class CreateMemberTests(MembersTestCase):
    def test_contacts_are_normalised(self):
        member = self.new_member(email=" Ben@Buyer.TEST ", mobile="+63 917 555 0100")
        self.assertEqual((member.email, member.mobile), ("ben@buyer.test", "+639175550100"))

    def test_a_name_is_trimmed_and_required(self):
        self.assertEqual(self.new_member(email="x@buyer.test", mobile="09175550101", name="  Xi ").full_name, "Xi")
        for bad in ("", "   ", "x" * 121):
            with self.subTest(name=bad), self.assertRaises(ApiError) as caught:
                self.new_member(email="y@buyer.test", mobile="09175550102", name=bad)
            self.assertEqual(caught.exception.fields, {"full_name": ["Enter the member's full name."]})

    def test_a_bad_email_or_mobile_is_refused_with_its_field(self):
        for fields, field in (
            ({"email": "not-an-email"}, "email"), ({"mobile": "12345"}, "mobile"),
            ({"mobile": "０９１７５５５０１０３"}, "mobile"),
        ):
            with self.subTest(fields=fields), self.assertRaises(ApiError) as caught:
                self.new_member(**{"email": "z@buyer.test", "mobile": "09175550103", **fields})
            self.assertEqual(caught.exception.code, "invalid_request")
            self.assertIn(field, caught.exception.fields)

    def test_a_contact_already_a_member_here_is_refused(self):
        for fields, field in (
            ({"email": EMAIL, "mobile": "09175550104"}, "email"),
            ({"email": "w@buyer.test", "mobile": MOBILE}, "mobile"),
        ):
            with self.subTest(field=field), self.assertRaises(ApiError) as caught:
                self.new_member(**fields)
            self.assertEqual((caught.exception.code, caught.exception.status), ("contact_in_use", 409))
            self.assertIn(field, caught.exception.fields)

    def test_a_contact_held_by_a_member_of_another_live_site_is_refused(self):
        with self.assertRaises(ApiError) as caught:
            self.new_member(self.other_site, email="v@buyer.test", mobile=MOBILE)
        self.assertEqual(caught.exception.code, "contact_in_use")
        self.assertNotIn("bens-store", caught.exception.message + str(caught.exception.fields))

    def test_a_contact_of_a_deleted_site_is_free_again(self):
        Site.objects.filter(pk=self.site.pk).update(deleted_at=timezone.now())
        self.new_member(self.other_site, email=EMAIL, mobile=MOBILE)

    def test_a_deleted_site_gets_no_member(self):
        Site.objects.filter(pk=self.other_site.pk).update(deleted_at=timezone.now())
        with self.assertRaises(ApiError) as caught:
            self.new_member(self.other_site, email="u@buyer.test", mobile="09175550105")
        self.assertEqual(caught.exception.status, 404)

    def test_nothing_is_written_when_it_is_refused(self):
        before = Member.objects.count()
        with self.assertRaises(ApiError):
            self.new_member(email=EMAIL, mobile="09175550106")
        self.assertEqual(Member.objects.count(), before)


class ChangeContactsTests(MembersTestCase):
    def test_a_new_email_is_pending_and_every_session_is_revoked(self):
        self.member.email_state, self.member.email_verified_at = "verified", timezone.now()
        self.member.save()
        first, _ = sessions.create_session(self.site, self.member)
        second, _ = sessions.create_session(self.site, self.member)

        changed = services.change_contacts(self.member, email="New@Buyer.test")

        self.assertEqual((changed.email, changed.email_state, changed.email_verified_at), ("new@buyer.test", "pending", None))
        for session in (first, second):
            session.refresh_from_db()
            self.assertIsNotNone(session.revoked_at)
            self.assertEqual(session.revoke_reason, "contact_change")

    def test_no_change_revokes_nothing(self):
        session, _ = sessions.create_session(self.site, self.member)
        services.change_contacts(self.member, email=EMAIL, mobile=MOBILE)
        session.refresh_from_db()
        self.assertIsNone(session.revoked_at)

    def test_a_new_mobile_is_pending_and_revokes(self):
        session, _ = sessions.create_session(self.site, self.member)
        changed = services.change_contacts(self.member, mobile="09175550107")
        self.assertEqual((changed.mobile, changed.mobile_state), ("+639175550107", "pending"))
        session.refresh_from_db()
        self.assertIsNotNone(session.revoked_at)

    def test_a_taken_contact_is_refused_and_changes_nothing(self):
        other = self.new_member(email="o@buyer.test", mobile="09175550108")
        session, _ = sessions.create_session(self.site, other)
        with self.assertRaises(ApiError):
            services.change_contacts(other, email=EMAIL)
        other.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(other.email, "o@buyer.test")
        self.assertIsNone(session.revoked_at)

    def test_a_session_is_revoked_only_for_its_own_member(self):
        other = self.new_member(email="o@buyer.test", mobile="09175550108")
        session, _ = sessions.create_session(self.site, other)
        services.change_contacts(self.member, email="n@buyer.test")
        session.refresh_from_db()
        self.assertIsNone(session.revoked_at)
        self.assertEqual(MemberSession.objects.filter(revoked_at__isnull=False).count(), 0)
