"""create_member, the onboarding proof, the delivery address and the released-contact tombstone."""
from django.utils import timezone

from apps.common.errors import ApiError
from apps.members import hooks, otp, services, sessions
from apps.members.models import Member, MemberContactAudit, MemberOtpChallenge
from apps.members.tests.base import BINDING, EMAIL, MOBILE, MembersTestCase
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


ADDRESS = {"line": "12 Rizal St", "barangay": "San Roque", "city": "Cebu City", "province": "Cebu", "zip": "6000"}


class AddressTests(MembersTestCase):
    def test_an_address_is_optional_for_an_onboarding_member(self):
        self.assertEqual(self.member.delivery_address, {})

    def test_an_address_is_kept_with_every_key(self):
        member = services.create_member(
            self.site, full_name="Ana", mobile="09175550130", email="a@buyer.test", delivery_address=ADDRESS
        )
        self.assertEqual(member.delivery_address, {**ADDRESS, "landmark": ""})

    def test_a_bad_address_is_refused(self):
        for bad in ({**ADDRESS, "city": ""}, {**ADDRESS, "extra": "x"}, {**ADDRESS, "line": "x" * 201}, {"line": "x"}):
            with self.subTest(bad=bad), self.assertRaises(ApiError) as caught:
                services.create_member(
                    self.site, full_name="Ana", mobile="09175550131", email="b@buyer.test", delivery_address=bad
                )
            self.assertEqual(caught.exception.fields, {"delivery_address": ["Enter the delivery address."]})

    def test_address_only_drops_the_receiver_beside_the_address(self):
        self.assertEqual(services.address_only({**ADDRESS, "name": "Ana"}), ADDRESS)

    def test_an_unknown_source_or_email_state_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            services.create_member(self.site, full_name="A", mobile="09175550132", email="c@buyer.test", source="operator_test")
        with self.assertRaises(ValueError):
            services.create_member(
                self.site, full_name="A", mobile="09175550132", email="c@buyer.test", email_state="verified"
            )


class MobileProofTests(MembersTestCase):
    def proof(self, mobile="+639175550140", purpose="onboarding"):
        row, code = otp.open_challenge(
            self.site, purpose=purpose, channel="sms", target=mobile, binding_raw=BINDING
        )
        return otp.verify_challenge(self.site, purpose=purpose, binding_raw=BINDING, code=code)

    def test_a_used_onboarding_challenge_makes_the_mobile_verified_and_is_claimed(self):
        proof = self.proof()
        member = services.create_member(
            self.site, full_name="Ana", mobile="09175550140", email="d@buyer.test", mobile_proof=proof
        )
        self.assertEqual(member.mobile_state, "verified")
        self.assertEqual(member.mobile_verified_at, proof.used_at)
        self.assertEqual(MemberOtpChallenge.objects.get(pk=proof.pk).member, member)

    def test_one_proof_backs_one_member(self):
        proof = self.proof()
        services.create_member(
            self.site, full_name="Ana", mobile="09175550140", email="d@buyer.test", mobile_proof=proof
        )
        with self.assertRaises(ApiError) as caught:
            services.create_member(
                self.site, full_name="Ben", mobile="09175550140", email="e@buyer.test", mobile_proof=proof
            )
        self.assertIn(caught.exception.code, ("mobile_not_verified", "contact_in_use"))

    def test_a_proof_for_another_number_purpose_or_an_unused_one_is_refused(self):
        for proof, mobile in (
            (self.proof("+639175550141"), "09175550142"),
            (self.proof("+639175550143", purpose="signin"), "09175550143"),
        ):
            with self.subTest(mobile=mobile), self.assertRaises(ApiError) as caught:
                services.create_member(
                    self.site, full_name="Ana", mobile=mobile, email="f@buyer.test", mobile_proof=proof
                )
            self.assertEqual(caught.exception.code, "mobile_not_verified")
        unused, _ = otp.open_challenge(
            self.site, purpose="onboarding", channel="sms", target="+639175550144", binding_raw="u" * 20
        )
        with self.assertRaises(ApiError):
            services.create_member(
                self.site, full_name="Ana", mobile="09175550144", email="g@buyer.test", mobile_proof=unused
            )

    def test_a_returning_buyers_proof_is_claimed_for_the_existing_member_once(self):
        proof = self.proof("+639171234567")
        services.claim_onboarding_proof(self.site, self.member, mobile=MOBILE, proof=proof)
        self.assertEqual(MemberOtpChallenge.objects.get(pk=proof.pk).member, self.member)
        with self.assertRaises(ApiError):
            services.claim_onboarding_proof(self.site, self.member, mobile=MOBILE, proof=proof)
        with self.assertRaises(ApiError):
            services.claim_onboarding_proof(self.site, self.member, mobile="09175550199", proof=proof)

    def test_confirm_pending_mobile_replaces_a_typo_only_with_a_proven_number(self):
        proof = self.proof("+639175550145")
        member = services.confirm_pending_mobile(self.site, self.member, proof, ip="203.0.113.5")
        self.assertEqual((member.mobile, member.mobile_state), ("+639175550145", "verified"))
        audit = MemberContactAudit.objects.get()
        self.assertEqual((audit.kind, audit.field), ("self_service", "mobile"))
        self.assertEqual(audit.old_masked, "+63917***4567")
        again = services.confirm_pending_mobile(self.site, member, proof)
        self.assertEqual(MemberContactAudit.objects.count(), 1)
        self.assertEqual(again.mobile, "+639175550145")

    def test_confirming_the_number_already_on_file_is_a_first_verification(self):
        proof = self.proof("+639171234567")
        services.confirm_pending_mobile(self.site, self.member, proof)
        self.assertEqual(MemberContactAudit.objects.get().kind, "first_verification")


class ReleaseTests(MembersTestCase):
    def test_release_gives_up_both_contacts_revokes_sessions_and_audits(self):
        session, _ = sessions.create_session(self.site, self.member)
        released = services.release_contacts(self.site, self.member)
        self.assertRegex(released.email, r"^released-\d+@released\.invalid$")
        self.assertRegex(released.mobile, r"^\+639[0-9]{9}$")
        self.assertTrue(services.contacts_released(released))
        self.assertFalse(services.contacts_released(self.member.__class__(email=EMAIL)))
        session.refresh_from_db()
        self.assertEqual(session.revoke_reason, "contact_change")
        self.assertEqual(MemberContactAudit.objects.filter(actor_type="system").count(), 2)

    def test_the_real_owner_can_then_be_a_member_of_another_site(self):
        hooks.register_contact_release(services.contacts_released)
        self.addCleanup(hooks._contact_releases.clear)
        services.release_contacts(self.site, self.member)
        # the tombstone holds the contacts, not the typed ones, so they are free anyway
        self.new_member(self.other_site, email=EMAIL, mobile=MOBILE)

    def test_a_member_the_hook_calls_released_never_blocks_another_site(self):
        hooks.register_contact_release(lambda member: member.pk == self.member.pk)
        self.addCleanup(hooks._contact_releases.clear)
        self.new_member(self.other_site, email=EMAIL, mobile=MOBILE)

    def test_without_the_hook_a_held_contact_still_blocks(self):
        with self.assertRaises(ApiError):
            self.new_member(self.other_site, email=EMAIL, mobile="09175550150")

    def test_helpers_for_the_payout_slice(self):
        self.assertEqual(services.verified_contacts(self.member), [])
        self.assertEqual(services.default_channel(self.member), "mobile")
        Member.objects.filter(pk=self.member.pk).update(email_state="verified", mobile_state="verified")
        self.member.refresh_from_db()
        self.assertEqual(services.verified_contacts(self.member), [("mobile", self.member.mobile), ("email", EMAIL)])
        self.assertEqual(services.money_otp_targets(self.member)[0], ("email", EMAIL))
        self.assertEqual(services.default_channel(self.member), "email")


class HookTests(MembersTestCase):
    def test_session_issued_hooks_run_once_and_a_failing_one_never_blocks(self):
        calls = []

        def boom(**kwargs):
            raise RuntimeError("later slice broke")

        hooks.register_session_issued_hook(boom)
        hooks.register_session_issued_hook(lambda site, member: calls.append((site.pk, member.pk)))
        self.addCleanup(hooks._session_issued.clear)
        with self.assertLogs(hooks.logger, "ERROR"):
            session, raw = sessions.issue_session(self.site, self.member, ip="203.0.113.5")
        self.assertEqual(calls, [(self.site.pk, self.member.pk)])
        self.assertIsNotNone(sessions.find_session(self.site, raw))

    def test_registering_twice_runs_once(self):
        calls = []
        fn = lambda site, member: calls.append(1)  # noqa: E731
        hooks.register_session_issued_hook(fn)
        hooks.register_session_issued_hook(fn)
        self.addCleanup(hooks._session_issued.clear)
        sessions.issue_session(self.site, self.member)
        self.assertEqual(calls, [1])

    def test_recheck_sees_a_revoked_or_expired_session(self):
        session, _ = sessions.create_session(self.site, self.member)
        self.assertTrue(sessions.recheck(session.pk, self.member.pk))
        sessions.revoke_session(session, "recovery")
        self.assertFalse(sessions.recheck(session.pk, self.member.pk))
