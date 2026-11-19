"""Onboarding after payment: the code steps, the delivery and Santé details, the Completed session,
the secure access link and the registration edges up to the member signing in."""
import re
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.utils import timezone

from apps.members import hooks, otp
from apps.members.models import Member, MemberContactAudit, MemberOtpChallenge, MemberSession
from apps.members.tests.base import BINDING
from apps.pack import access, constants as C, registration, registration_rules as rules
from apps.pack.models import CheckoutToken, PackAgreementAcceptance, PackOrder, SanteRegistration, SanteRegistrationEvent
from apps.pack.tests.base import HOST, MOBILE, NOT_FOUND, OTHER_HOST, VISITOR, PackTestCase
from apps.storefront.tests.base import SECRET

ONBOARDING = "/storefront/v1/"
ADDRESS = {
    "receiver_name": "Maria Santos", "receiver_mobile": "09171234567", "line": "12 Rizal St",
    "province": "Camarines Sur", "city": "Naga City", "barangay": "Triangulo", "landmark": "Barangay hall",
}
DETAILS = {
    "dob_year": "1990", "dob_month": "5", "dob_day": "17", "desired_username": "mariasantos1",
    "alternate_username": "mariasantos2", "terms": True,
}


class OnboardingCase(PackTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.codes = []
        hooks._sms_transport.clear()
        hooks.register_sms_transport(
            available=lambda: True, deliver_code=self._deliver, precheck=None,
        )
        self.addCleanup(hooks._sms_transport.clear)

    def _deliver(self, challenge, code, category):
        self.codes.append((challenge.target, code))
        return "queued"

    def call(self, path, host=HOST, secret=SECRET, ip=VISITOR, **fields):
        body = {"host": host, **fields}
        if ip is not None:
            body["visitor_ip"] = ip
        headers = {"HTTP_X_REAL_IP": "198.51.100.200"}
        if secret is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {secret}"
        return self.client.post(
            ONBOARDING + path, body, content_type="application/json", secure=True, **headers
        )

    def placed_token(self):
        token, _ = self.placed()
        return token

    def state(self, token):
        response = self.call("onboarding/state/", token=token)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def email_code(self):
        return re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)

    def age_codes(self, seconds=61):
        for row in MemberOtpChallenge.objects.all():
            MemberOtpChallenge.objects.filter(pk=row.pk).update(created_at=row.created_at - timedelta(seconds=seconds))

    def verify_both(self, token):
        self.assertEqual(self.call("onboarding/mobile/send/", token=token).status_code, 200)
        self.assertEqual(self.call("onboarding/mobile/verify/", token=token, code=self.codes[-1][1]).status_code, 200)
        self.assertEqual(self.call("onboarding/email/send/", token=token).status_code, 200)
        response = self.call("onboarding/email/verify/", token=token, code=self.email_code())
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def finish(self, token):
        self.verify_both(token)
        self.assertEqual(self.call("onboarding/address/", token=token, **ADDRESS).status_code, 200)
        response = self.call("onboarding/details/", token=token, **DETAILS)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()


class RegistrationEdgeTests(OnboardingCase):
    def test_placing_opens_the_registration_at_awaiting_details_with_edge_t1(self):
        self.placed_token()
        reg = SanteRegistration.objects.get()
        self.assertEqual(reg.status, "awaiting_buyer_details")
        event = SanteRegistrationEvent.objects.get()
        self.assertEqual((event.edge, event.from_status, event.to_status, event.actor_type), ("T1", "", "awaiting_buyer_details", "system"))

    def test_activate_is_idempotent(self):
        self.placed_token()
        registration.activate(PackOrder.objects.get())
        self.assertEqual(SanteRegistration.objects.count(), 1)

    def test_details_while_the_payment_is_under_review_is_t3_and_waits_for_accept(self):
        token = self.placed_token()
        self.finish(token)
        reg = SanteRegistration.objects.get()
        self.assertEqual(reg.status, "waiting_for_accept")
        self.assertEqual((reg.desired_username, reg.alternate_username), ("mariasantos1", "mariasantos2"))
        self.assertEqual(reg.date_of_birth.isoformat(), "1990-05-17")
        edges = list(SanteRegistrationEvent.objects.values_list("edge", flat=True))
        self.assertEqual(edges, ["T1", "T3"])

    def test_details_after_the_payment_is_verified_is_t4(self):
        token = self.placed_token()
        PackOrder.objects.update(payment_status=C.VERIFIED)
        self.finish(token)
        self.assertEqual(SanteRegistration.objects.get().status, "ready_to_register")
        self.assertEqual(list(SanteRegistrationEvent.objects.values_list("edge", flat=True)), ["T1", "T4"])

    def test_approval_moves_a_waiting_registration_to_ready_t8(self):
        token = self.placed_token()
        self.finish(token)
        order = PackOrder.objects.get()
        order.payment_status = C.VERIFIED
        order.save()
        registration.on_payment_verified(order)
        self.assertEqual(SanteRegistration.objects.get().status, "ready_to_register")
        self.assertEqual(SanteRegistrationEvent.objects.last().edge, "T8")

    def test_the_event_note_names_the_terms_version_the_buyer_agreed_to(self):
        token = self.placed_token()
        self.finish(token)
        note = SanteRegistrationEvent.objects.get(edge="T3").note
        self.assertTrue(note.startswith(f"Santé Membership Terms {self.version.version_label} accepted on "))
        self.assertIn(self.version.sha256[:12], note)

    def test_a_second_submission_is_refused_as_the_wrong_status(self):
        token = self.placed_token()
        self.finish(token)
        reg = SanteRegistration.objects.get()
        with self.assertRaises(Exception) as caught:
            registration.submit_details(
                self.site, Member.objects.get(), date_of_birth="1990-05-17", desired_username="mariasantos1",
                alternate_username="mariasantos2", terms_accepted=True,
            )
        self.assertEqual(caught.exception.code, "wrong_status")
        self.assertEqual(SanteRegistration.objects.get().state_version, reg.state_version)

    def test_registrations_and_events_are_never_deleted_or_edited(self):
        self.placed_token()
        with self.assertRaises(Exception):
            SanteRegistration.objects.get().delete()
        with self.assertRaises(Exception):
            SanteRegistrationEvent.objects.update(note="x")


class DetailsGateTests(OnboardingCase):
    def ready(self):
        token = self.placed_token()
        self.verify_both(token)
        self.call("onboarding/address/", token=token, **ADDRESS)
        return token

    def test_the_mobile_is_confirmed_first(self):
        token = self.placed_token()
        response = self.call("onboarding/details/", token=token, **DETAILS)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["message"], registration.MOBILE_FIRST)

    def test_every_refusal_has_twcincs_wording(self):
        token = self.ready()
        response = self.call("onboarding/details/", token=token, **{**DETAILS, "dob_year": "2015", "desired_username": "ab",
                                                                    "alternate_username": "", "terms": False})
        self.assertEqual(response.status_code, 422)
        fields = response.json()["error"]["fields"]
        self.assertEqual(fields["date_of_birth"], [rules.DOB_MINOR])
        self.assertEqual(fields["desired_username"], [rules.USERNAME_FORMAT_ERROR])
        self.assertEqual(fields["alternate_username"], [rules.ALTERNATE_REQUIRED_ERROR])
        self.assertEqual(fields["terms"], ["Please agree to the Santé Membership Terms to continue."])

    def test_the_two_usernames_must_differ_and_reserved_names_are_refused(self):
        token = self.ready()
        response = self.call("onboarding/details/", token=token, **{**DETAILS, "alternate_username": "MariaSantos1"})
        self.assertEqual(response.json()["error"]["fields"]["alternate_username"], [rules.USERNAME_DUPLICATE_ERROR])

    def test_a_missing_date_part_is_a_required_error_not_a_crash(self):
        token = self.ready()
        response = self.call("onboarding/details/", token=token, **{**DETAILS, "dob_year": "²", "dob_day": ""})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["fields"]["date_of_birth"], [rules.DOB_REQUIRED])

    def test_a_refused_form_changes_nothing(self):
        token = self.ready()
        self.call("onboarding/details/", token=token, **{**DETAILS, "terms": False})
        self.assertEqual(SanteRegistration.objects.get().status, "awaiting_buyer_details")

    def test_no_acceptance_record_and_no_version_id_asks_for_the_terms(self):
        token = self.ready()
        PackAgreementAcceptance.objects.all()._raw_delete("default")  # the evidence table refuses a normal delete
        response = self.call("onboarding/details/", token=token, **DETAILS)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (422, "agreement_required"))
        self.assertEqual(SanteRegistration.objects.get().status, "awaiting_buyer_details")


class TermsAtDetailsTests(OnboardingCase):
    """Santé Membership Terms accepted at the account-details step (checkout no longer asks)."""

    def ready(self):
        response = self.post("contact/", **self.contact_fields_without_agreement())
        token = response.json()["token"]
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.place(token).status_code, 200)
        self.give_member("SMP00001")  # the Subscriber approved the proof: the member exists now
        self.verify_both(token)
        self.call("onboarding/address/", token=token, **ADDRESS)
        self.assertEqual(PackAgreementAcceptance.objects.count(), 0)
        return token

    def details(self, token, **changes):
        return self.call("onboarding/details/", token=token, **{**DETAILS, "agreement_version_id": str(self.version.pk), **changes})

    def test_the_tick_and_the_current_version_write_one_acceptance_with_the_checksum(self):
        token = self.ready()
        response = self.details(token)
        self.assertEqual(response.status_code, 200, response.content)
        row = PackAgreementAcceptance.objects.get()
        self.assertEqual((row.version_id, row.sha256, row.site_id), (self.version.pk, self.version.sha256, self.site.pk))
        self.assertEqual(row.order_id, PackOrder.objects.get().pk)
        note = SanteRegistrationEvent.objects.get(edge="T3").note
        self.assertTrue(note.startswith(f"Santé Membership Terms {self.version.version_label} accepted on "))
        self.assertIn(self.version.sha256[:12], note)

    def test_a_missing_tick_is_a_terms_field_error_and_writes_nothing(self):
        token = self.ready()
        response = self.details(token, terms=False)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["fields"]["terms"], [registration.TERMS_ERROR])
        self.assertEqual(PackAgreementAcceptance.objects.count(), 0)

    def test_a_missing_version_id_is_refused_with_the_terms_field(self):
        token = self.ready()
        response = self.details(token, agreement_version_id="")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (422, "agreement_required"))
        self.assertEqual(response.json()["error"]["fields"]["terms"], [C.MSG_AGREEMENT_REQUIRED])
        self.assertEqual(PackAgreementAcceptance.objects.count(), 0)
        self.assertEqual(SanteRegistration.objects.get().status, "awaiting_buyer_details")

    def test_an_unknown_version_is_required(self):
        token = self.ready()
        response = self.details(token, agreement_version_id="999999")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (422, "agreement_required"))
        self.assertEqual(PackAgreementAcceptance.objects.count(), 0)

    def test_a_stale_version_is_changed_and_a_retry_with_the_new_one_is_accepted(self):
        from apps.sante import agreement
        from apps.sante.tests.agreement_helpers import make_operator, published
        token = self.ready()
        published(make_operator("op2"), label="v2")
        response = self.details(token)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "agreement_changed"))
        self.assertEqual(PackAgreementAcceptance.objects.count(), 0)
        retry = self.details(token, agreement_version_id=str(agreement.active_version().pk))
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertEqual(PackAgreementAcceptance.objects.get().version.version_label, "v2")

    def test_another_sites_buyer_token_cannot_reach_the_step(self):
        token = self.ready()
        response = self.call("onboarding/details/", token=token, host=OTHER_HOST, agreement_version_id=str(self.version.pk), **DETAILS)
        self.assertEqual(response.status_code, 409)  # the token is not this host's: refused like any wrong-site call
        self.assertEqual(PackAgreementAcceptance.objects.count(), 0)

    def test_an_order_already_accepted_at_checkout_writes_no_second_row(self):
        token = self.placed_token()  # an older storefront posted the agreement at checkout
        before = PackAgreementAcceptance.objects.get()
        self.verify_both(token)
        self.call("onboarding/address/", token=token, **ADDRESS)
        response = self.call("onboarding/details/", token=token, **{**DETAILS, "agreement_version_id": "999999"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(PackAgreementAcceptance.objects.get().pk, before.pk)


class FunnelCodeTests(OnboardingCase):
    def test_the_state_starts_at_verify_and_slides_the_token(self):
        token = self.placed_token()
        row = self.token_row(token)
        CheckoutToken.objects.filter(pk=row.pk).update(expires_at=timezone.now() + timedelta(minutes=5))
        state = self.state(token)
        self.assertEqual(state["step"], "verify")
        self.assertEqual(state["order_number"], "SMP00001")
        self.assertEqual(state["buyer_first_name"], "Maria")
        self.assertFalse(state["email"]["verified"])
        self.assertTrue(state["mobile"]["pending"])
        row.refresh_from_db()
        self.assertGreater(row.expires_at, timezone.now() + timedelta(minutes=110))

    def test_no_placed_order_or_an_unknown_token_is_closed(self):
        token = self.start()  # a token with no order
        self.assertEqual(self.call("onboarding/state/", token=token).status_code, 409)
        self.assertEqual(self.call("onboarding/state/", token="nope").json()["error"]["code"], "checkout_closed")

    def test_foreign_host_and_missing_credential_are_refused(self):
        token = self.placed_token()
        self.assertEqual(self.call("onboarding/state/", host=OTHER_HOST, token=token).status_code, 409)
        self.assertEqual(self.call("onboarding/state/", secret=None, token=token).status_code, 401)

    def test_the_mobile_code_confirms_the_number_and_audits_the_first_verification(self):
        token = self.placed_token()
        self.call("onboarding/mobile/send/", token=token)
        self.assertEqual(self.codes[-1][0], "+639171234567")
        wrong = self.call("onboarding/mobile/verify/", token=token, code="000000")
        self.assertEqual(wrong.status_code, 400)
        state = self.call("onboarding/mobile/verify/", token=token, code=self.codes[-1][1]).json()
        self.assertFalse(state["mobile"]["pending"])
        member = Member.objects.get()
        self.assertEqual(member.mobile_state, "verified")
        self.assertTrue(MemberContactAudit.objects.filter(member=member, kind="first_verification", field="mobile").exists())

    def test_a_typo_fix_replaces_the_number_only_once_the_new_one_is_proved(self):
        token = self.placed_token()
        self.call("onboarding/mobile/send/", token=token, mobile="09181234567")
        self.assertEqual(Member.objects.get().mobile, "+639171234567")
        self.call("onboarding/mobile/verify/", token=token, code=self.codes[-1][1])
        self.assertEqual(Member.objects.get().mobile, "+639181234567")

    def test_the_email_code_confirms_the_email(self):
        token = self.placed_token()
        self.call("onboarding/email/send/", token=token)
        state = self.call("onboarding/email/verify/", token=token, code=self.email_code()).json()
        self.assertTrue(state["email"]["verified"])
        self.assertTrue(MemberContactAudit.objects.filter(kind="first_verification", field="email").exists())

    def test_sends_are_throttled_per_token(self):
        token = self.placed_token()
        for _ in range(10):
            response = self.call("onboarding/email/send/", token=token)
            if response.status_code == 429:
                break
        self.assertEqual(response.status_code, 429)

    def test_the_step_advances_verify_address_details_done(self):
        token = self.placed_token()
        self.assertEqual(self.verify_both(token)["step"], "address")
        self.assertEqual(self.call("onboarding/address/", token=token, **ADDRESS).json()["step"], "details")
        self.assertEqual(self.call("onboarding/details/", token=token, **DETAILS).json()["step"], "done")

    def test_the_address_is_kept_on_the_order_with_the_server_derived_zip_never_on_the_member(self):
        token = self.placed_token()
        self.verify_both(token)
        self.call("onboarding/address/", token=token, **ADDRESS)
        order = PackOrder.objects.get()
        self.assertEqual(order.delivery_address["zip"], "4400")
        self.assertEqual(order.delivery_address["country"], "Philippines")
        self.assertEqual(Member.objects.get().delivery_address, {})

    def test_an_address_missing_its_fields_gets_the_twcinc_messages(self):
        token = self.placed_token()
        self.verify_both(token)
        response = self.call("onboarding/address/", token=token, receiver_name="", receiver_mobile="x")
        fields = response.json()["error"]["fields"]
        self.assertEqual(fields["line"], ["Enter the address."])
        self.assertEqual(fields["province"], ["Select the province."])
        self.assertEqual(fields["receiver_name"], ["Enter the receiver's full name."])

    def test_an_unknown_province_is_refused(self):
        token = self.placed_token()
        self.verify_both(token)
        response = self.call("onboarding/address/", token=token, **{**ADDRESS, "province": "Nowhere"})
        self.assertEqual(response.json()["error"]["fields"]["province"], ["Select a province from the list."])

    def test_zip_lookup_answers_from_the_vendored_dataset(self):
        body = self.call("address/zip/", province="Camarines Sur", city="Naga City", barangay="Triangulo").json()
        self.assertEqual((body["ok"], body["zip_code"], body["zip_precision"]), (True, "4400", "city_unique"))
        body = self.call("address/zip/", province="Nowhere", city="x", barangay="y").json()
        self.assertEqual((body["ok"], body["error_field"]), (False, "province"))


class CompletedSessionTests(OnboardingCase):
    def test_completed_is_open_until_every_step_is_done(self):
        token = self.placed_token()
        self.assertEqual(self.call("onboarding/completed/", token=token).json(), {"result": "open", "session_token": ""})
        self.assertEqual(MemberSession.objects.count(), 0)

    def test_the_session_is_issued_once_and_works_for_the_member(self):
        token = self.placed_token()
        self.finish(token)
        first = self.call("onboarding/completed/", token=token).json()
        self.assertEqual(first["result"], "issued")
        me = self.client.post(
            ONBOARDING + "member/me/", {"host": HOST, "token": first["session_token"]},
            content_type="application/json", secure=True, HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["member"]["email"], "maria@buyer.test")
        self.assertTrue(CheckoutToken.objects.get().session_issued_at)
        second = self.call("onboarding/completed/", token=token).json()
        self.assertEqual(second, {"result": "spent", "session_token": ""})
        self.assertEqual(MemberSession.objects.count(), 1)

    def test_a_recovery_after_the_checkout_began_makes_it_stale(self):
        token = self.placed_token()
        self.finish(token)
        member = Member.objects.get()
        MemberContactAudit.objects.create(
            site=self.site, member=member, kind="subscriber_recovery", field="mobile", actor_type="subscriber",
        )
        MemberContactAudit.objects.filter(member=member, kind="subscriber_recovery").update(
            created_at=timezone.now() + timedelta(minutes=1)
        )
        self.assertEqual(self.call("onboarding/state/", token=token).json()["error"]["code"], "checkout_stale")


class AccessLinkTests(OnboardingCase):
    def link(self):
        member = Member.objects.get()
        return access.signed_id(access.open_link(member) or access._issue(member, timezone.now()))

    def test_the_received_email_carries_the_link_while_the_mobile_is_unconfirmed(self):
        self.placed_token()
        received = [m for m in mail.outbox if m.subject.startswith("We received your payment proof")][0]
        self.assertIn("/funnel/membership-pack/access/", received.body)
        self.assertIn("(This secure link works once and expires in 7 days.)", received.body)

    def test_read_changes_nothing_and_redeem_spends_it_once(self):
        self.placed_token()
        link = self.link()
        read = self.call("access/read/", link=link).json()
        self.assertEqual(read, {
            "link_open": True, "renewable": True, "kind": "member", "payment_status": "", "payment_method": "",
        })
        self.assertEqual(self.call("access/read/", link=link).json()["link_open"], True)
        first = self.call("access/redeem/", link=link).json()
        self.assertTrue(first["redeemed"])
        self.assertEqual(self.state(first["token"])["step"], "verify")
        self.assertTrue(Member.objects.get().email_state == "verified")
        again = self.call("access/redeem/", link=link).json()
        self.assertEqual((again["redeemed"], again["token"], again["expires_at"]), (False, "", ""))
        self.assertEqual(MemberSession.objects.count(), 0)

    def test_redeeming_never_issues_a_session_and_marks_only_the_email(self):
        self.placed_token()
        self.call("access/redeem/", link=self.link())
        member = Member.objects.get()
        self.assertEqual((member.email_state, member.mobile_state), ("verified", "pending"))
        self.assertEqual(MemberSession.objects.count(), 0)

    def test_an_unknown_or_foreign_link_is_404(self):
        self.placed_token()
        link = self.link()
        self.assertEqual(self.call("access/read/", link="garbage").json(), NOT_FOUND)
        self.assertEqual(self.call("access/read/", host=OTHER_HOST, link=link).status_code, 404)

    def test_renew_replaces_the_link_and_is_limited_to_three_an_hour(self):
        self.placed_token()
        link = self.link()
        sent = self.call("access/renew/", link=link).json()
        self.assertTrue(sent["sent"])
        self.assertFalse(self.call("access/read/", link=link).json()["link_open"])
        statuses = [self.call("access/renew/", link=link).status_code for _ in range(4)]
        self.assertIn(429, statuses)

    def test_the_link_stops_working_once_the_mobile_is_confirmed(self):
        token = self.placed_token()
        link = self.link()
        self.call("onboarding/mobile/send/", token=token)
        self.call("onboarding/mobile/verify/", token=token, code=self.codes[-1][1])
        read = self.call("access/read/", link=link).json()
        self.assertEqual((read["link_open"], read["renewable"], read["kind"]), (False, False, "member"))

    def test_a_link_expires_after_seven_days(self):
        self.placed_token()
        link = self.link()
        MemberOtpChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(self.call("access/read/", link=link).json()["link_open"])


class CheckoutCodeTests(OnboardingCase):
    def test_a_first_time_buyer_needs_no_code_before_payment(self):
        token = self.start()
        state = self.call("checkout/state/", token=token).json()
        self.assertEqual((state["needs_code"], state["verified"]), (False, False))

    def test_a_returning_buyer_proves_the_number_before_payment_and_is_signed_in_as_that_member(self):
        from apps.members import services as members
        existing = members.create_member(
            self.site, full_name="Maria Santos", mobile=MOBILE, email="maria@buyer.test", email_state="pending",
        )
        Member.objects.filter(pk=existing.pk).update(mobile_state="verified")
        token = self.start()
        self.assertTrue(self.call("checkout/state/", token=token).json()["needs_code"])
        refused = self.place(token)
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json()["error"]["message"], C.MSG_VERIFY_FIRST)
        self.assertEqual(self.call("checkout/code/send/", token=token).status_code, 200)
        self.assertEqual(self.call("checkout/code/verify/", token=token, code="000000").status_code, 400)
        self.assertEqual(self.call("checkout/code/verify/", token=token, code=self.codes[-1][1]).json(), {"status": "verified"})
        self.assertTrue(self.call("checkout/state/", token=token).json()["verified"])
        self.assertEqual(self.place(token).status_code, 200)
        self.assertIsNone(PackOrder.objects.get().member_id)  # no member until the owner approves
        self.give_member("SMP00001")
        self.assertEqual(PackOrder.objects.get().member_id, existing.pk)

    def test_send_needs_contact_details_first(self):
        response = self.call("checkout/code/send/", token="x")
        self.assertEqual(response.status_code, 409)
