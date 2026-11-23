"""The buyer portal (ClickUp z8uq9menvu, plan 2026-11-13-buyer-portal-z8uq9mbk0m, C1 tests).

A paid Membership Pack buyer of a Santé site that is off (suspended, or cancelled past its service
end or with no payment method) can sign in and read their own order until the site's deletion
point. Nothing here changes what the member and pack routes answer for an unserved site.
"""
import json
import re
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.billing import services
from apps.billing.models import Subscription
from apps.billing.tests.base import (
    CYCLE_1_END, SECOND, T0, TRIAL_END, collection_is_live, days, make_active, make_attention,
    make_subscription, make_suspended, method_on_file,
)
from apps.members import sessions
from apps.pack import constants as C, portal
from apps.pack.models import CheckoutToken, PackOrder
from apps.pack.tests.base import HOST, NOT_FOUND, PackTestCase
from apps.pack.tests.owner_base import make_order
from apps.sites.models import Site
from apps.storefront import api as storefront
from apps.storefront.tests.base import SECRET

BASE = "/storefront/v1/portal/"
SUSPENDED_AT = CYCLE_1_END + days(7)
POINT = SUSPENDED_AT + days(90)  # a paid site: service ended at the suspension, data due 90 days on
ROUTES = {
    "site/", "signin/request/", "signin/resend/", "signin/accept-email/", "signin/verify/",
    "me/", "signout/", "orders/", "proof/",
}
FORBIDDEN = (
    "date_of_birth", "desired_username", "alternate_username", "delivery_address", "address",
    "receiver_mobile", "receiver_name", "full_name", "email", "mobile", "billing", "state",
    "subscription", "sante_member_id", "commission", "owner", "account_number", "buyer_name",
    "deletion", "reason", "rejection", "lock",
)


def keys_of(value):
    """Every JSON key at any depth."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from keys_of(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from keys_of(inner)


class PortalBase(PackTestCase):
    """A Santé site (HOST) with one verified order, ended by a suspension 90 days before POINT."""

    def setUp(self):
        super().setUp()
        self.order, self.proof = make_order(self.site, status=C.VERIFIED)
        self.member = self.order.member
        self.token = self.session_token(self.member)

    def session_token(self, member, site=None):
        return sessions.create_session(site or self.site, member)[1]

    def portal(self, path, host=HOST, secret=SECRET, **fields):
        headers = {"HTTP_X_REAL_IP": "198.51.100.200"}
        if secret is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {secret}"
        return self.client.post(
            BASE + path, {"host": host, **fields}, content_type="application/json", secure=True,
            **headers,
        )

    def suspend(self):
        """The site's own subscription, made suspended (the site was made by PackTestCase)."""
        subscription = Subscription.objects.get(site=self.site)
        with method_on_file():
            services.first_payment_succeeded(subscription, paid_at=TRIAL_END, reference="first-1")
        services.renewal_failed(Subscription.objects.get(pk=subscription.pk), at=CYCLE_1_END)
        services.sweep(now=SUSPENDED_AT)
        return Subscription.objects.get(pk=subscription.pk)

    def open_portal(self, at=None):
        self.suspend()
        self.at(at or SUSPENDED_AT + days(1))

    def assert_not_found(self, response):
        self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(response.json(), NOT_FOUND)
        self.assertEqual(response["Cache-Control"], "no-store")


class PortalSiteTests(PortalBase):
    def test_a_suspended_site_opens_the_portal(self):
        self.open_portal()

        response = self.portal("site/")

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["display_name"], self.site.display_name)
        self.assertEqual(body["accent_color"], self.site.accent_color)
        self.assertEqual(set(body), {"display_name", "accent_color", "closes_at", "banner"})
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_a_cancellation_past_its_service_end_opens_it(self):
        subscription = Subscription.objects.get(site=self.site)
        with method_on_file():
            services.first_payment_succeeded(subscription, paid_at=TRIAL_END, reference="first-1")
        services.cancel(Subscription.objects.get(pk=subscription.pk), now=TRIAL_END + days(3))
        self.at(CYCLE_1_END + SECOND)

        self.assertEqual(self.portal("site/").status_code, 200)

    def test_a_cancellation_inside_the_paid_period_is_still_served_so_no_portal(self):
        subscription = Subscription.objects.get(site=self.site)
        with method_on_file():
            services.first_payment_succeeded(subscription, paid_at=TRIAL_END, reference="first-1")
        services.cancel(Subscription.objects.get(pk=subscription.pk), now=TRIAL_END + days(3))
        self.at(CYCLE_1_END - SECOND)

        self.assert_not_found(self.portal("site/"))

    def test_a_trial_that_ended_with_no_payment_method_opens_it(self):
        self.at(TRIAL_END)

        self.assertEqual(self.portal("site/").status_code, 200)

    def test_a_trial_a_paid_cycle_and_attention_do_not(self):
        for moment in (T0 + days(2), TRIAL_END - SECOND):
            self.at(moment)
            self.assert_not_found(self.portal("site/"))
        subscription = Subscription.objects.get(site=self.site)
        with method_on_file():
            services.first_payment_succeeded(subscription, paid_at=TRIAL_END, reference="first-1")
        self.at(TRIAL_END + days(2))
        self.assert_not_found(self.portal("site/"))
        services.renewal_failed(Subscription.objects.get(pk=subscription.pk), at=CYCLE_1_END)
        self.at(CYCLE_1_END + days(3))
        self.assert_not_found(self.portal("site/"))

    def test_a_billing_exempt_site_never_does(self):
        Subscription.objects.filter(site=self.site).update(billing_exempt=True)
        self.at(TRIAL_END + days(300))

        self.assert_not_found(self.portal("site/"))

    def test_a_site_with_no_subscription_does_not(self):
        from apps.sites.tests.base import make_account, make_site
        make_site(make_account("solo@example.test"), name="solo-store")

        self.assert_not_found(self.portal("site/", host="solo-store.negosyotest.com"))

    @override_settings(BILLING_ENABLED=False)
    def test_billing_off_does_not(self):
        self.assert_not_found(self.portal("site/"))

    def test_an_unknown_foreign_or_malformed_host_does_not(self):
        self.open_portal()

        for host in ("nobody.negosyotest.com", "anas-store.example.com", "", "anas-store"):
            with self.subTest(host=host):
                self.assert_not_found(self.portal("site/", host=host))

    def test_a_soft_deleted_site_does_not(self):
        self.open_portal()
        Site.objects.filter(pk=self.site.pk).update(deleted_at=self.clock.now())

        self.assert_not_found(self.portal("site/"))

    def test_a_site_on_operator_hold_still_opens_it(self):
        """H3 decision C: members keep read access while HQ has the store suspended."""
        self.open_portal()
        Site.objects.filter(pk=self.site.pk).update(operator_hold=True)

        self.assertEqual(self.portal("site/").status_code, 200)

    def test_a_held_site_whose_billing_is_active_opens_it_with_the_neutral_banner(self):
        self.at(T0 + days(2))
        Site.objects.filter(pk=self.site.pk).update(operator_hold=True)

        response = self.portal("site/")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["banner"], portal.NEUTRAL_BANNER)
        self.assertIsNone(response.json()["closes_at"])

    def test_a_site_that_is_not_a_sante_site_does_not(self):
        self.open_portal()
        Site.objects.filter(pk=self.site.pk).update(template="other")

        self.assert_not_found(self.portal("site/"))

    def test_the_members_and_pack_switches_each_close_it(self):
        self.open_portal()
        for switch in ("MEMBERS_ENABLED", "PACK_ENABLED"):
            with self.subTest(switch=switch), override_settings(**{switch: False}):
                self.assert_not_found(self.portal("site/"))

    def test_a_billing_read_error_answers_the_same_404_and_logs(self):
        self.open_portal()

        with mock.patch("apps.billing.services._read_position", side_effect=RuntimeError("x")):
            with self.assertLogs("apps.billing.services", level="ERROR"):
                self.assert_not_found(self.portal("site/"))

    def test_a_deletion_point_read_error_answers_the_same_404_and_logs(self):
        self.open_portal()

        with mock.patch("apps.billing.services.deletion_point", side_effect=RuntimeError("x")):
            with self.assertLogs("apps.pack.portal", level="ERROR"):
                self.assert_not_found(self.portal("site/"))

    def test_a_wrong_or_missing_server_credential_is_refused(self):
        self.open_portal()

        self.assertEqual(self.portal("site/", secret=None).status_code, 401)
        self.assertEqual(self.portal("site/", secret="wrong").status_code, 401)

    def test_the_old_routes_still_refuse_an_unserved_site(self):
        self.open_portal()
        member_path, pack_path = "/storefront/v1/member/me/", "/storefront/v1/member/pack/"
        for path in (member_path, pack_path, "/storefront/v1/checkout/contact/"):
            response = self.client.post(
                path, {"host": HOST, "token": self.token}, content_type="application/json",
                secure=True, HTTP_AUTHORIZATION=f"Bearer {SECRET}", HTTP_X_REAL_IP="203.0.113.7",
            )
            with self.subTest(path=path):
                self.assert_not_found(response)


class DeletionPointTests(PortalBase):
    def test_a_paid_site_closes_at_90_days_to_the_second(self):
        self.suspend()
        for moment, status in (
            (SUSPENDED_AT + days(29), 200), (SUSPENDED_AT + days(30), 200),
            (SUSPENDED_AT + days(89), 200), (POINT - SECOND, 200), (POINT, 404),
            (POINT + SECOND, 404),
        ):
            self.at(moment)
            with self.subTest(moment=moment):
                self.assertEqual(self.portal("site/").status_code, status)

    def test_the_point_is_read_from_the_clock_when_the_sweep_never_ran(self):
        subscription = Subscription.objects.get(site=self.site)
        with method_on_file():
            services.first_payment_succeeded(subscription, paid_at=TRIAL_END, reference="first-1")
        services.renewal_failed(Subscription.objects.get(pk=subscription.pk), at=CYCLE_1_END)
        # no sweep: the stored state is still "payment requires attention"
        self.at(SUSPENDED_AT + days(1))
        self.assertEqual(self.portal("site/").status_code, 200)
        self.at(POINT - SECOND)
        self.assertEqual(self.portal("site/").status_code, 200)
        self.at(POINT)
        self.assert_not_found(self.portal("site/"))

    def test_deletion_point_returns_none_or_the_instant(self):
        site = self.site
        self.assertIsNone(services.deletion_point(site, at=T0 + days(2)))  # a trial: no clock
        self.suspend()
        self.assertEqual(services.deletion_point(site, at=SUSPENDED_AT + days(1)), POINT)
        self.assertEqual(services.deletion_point(site, at=POINT + days(5)), POINT)

    def test_deletion_point_writes_nothing(self):
        self.suspend()
        before = Subscription.objects.values().get(site=self.site)

        services.deletion_point(self.site, at=POINT + days(1))

        self.assertEqual(Subscription.objects.values().get(site=self.site), before)

    def test_a_never_paid_site_closes_30_days_after_service_ends(self):
        with collection_is_live():
            services.sweep(now=TRIAL_END)
        end = T0 + days(44)  # TRIAL_END + 30 days
        for moment, status in (
            (TRIAL_END + days(29), 200), (end - SECOND, 200), (end, 404), (end + days(1), 404),
        ):
            self.at(moment)
            with self.subTest(moment=moment):
                self.assertEqual(self.portal("site/").status_code, status)

    def test_a_never_paid_site_before_collection_is_live_has_no_clock_and_stays_open(self):
        services.sweep(now=TRIAL_END)

        self.assertIsNone(services.deletion_point(self.site, at=TRIAL_END + days(400)))
        self.at(TRIAL_END + days(400))
        self.assertEqual(self.portal("site/").status_code, 200)

    def test_a_cancelled_paid_site_closes_90_days_after_its_service_end(self):
        subscription = Subscription.objects.get(site=self.site)
        with method_on_file():
            services.first_payment_succeeded(subscription, paid_at=TRIAL_END, reference="first-1")
        services.cancel(Subscription.objects.get(pk=subscription.pk), now=TRIAL_END + days(3))

        self.at(CYCLE_1_END + days(90) - SECOND)
        self.assertEqual(self.portal("site/").status_code, 200)
        self.at(CYCLE_1_END + days(90))
        self.assert_not_found(self.portal("site/"))

    def test_a_reactivation_ends_the_portal_and_returns_the_site(self):
        self.suspend()
        self.at(SUSPENDED_AT + days(10))
        self.assertEqual(self.portal("site/").status_code, 200)

        services.reactivated(
            Subscription.objects.get(site=self.site), paid_at=SUSPENDED_AT + days(10), reference="pay-9"
        )

        self.assert_not_found(self.portal("site/"))
        self.assertIsNone(services.deletion_point(self.site, at=SUSPENDED_AT + days(11)))
        self.assertEqual(
            self.client.get(
                "/v1/public/sites/by-host/", {"host": HOST}, secure=True, HTTP_X_REAL_IP="203.0.113.7"
            ).status_code,
            200,
        )


class BannerTests(PortalBase):
    def test_the_closing_instant_and_a_neutral_line_before_the_last_14_days(self):
        self.suspend()
        self.at(POINT - days(14) - SECOND)

        body = self.portal("site/").json()

        self.assertEqual(body["banner"], "This store is not taking orders.")
        self.assertEqual(body["closes_at"], POINT.isoformat())

    def test_the_full_notice_in_the_last_14_days(self):
        self.suspend()
        self.at(POINT - days(14))

        banner = self.portal("site/").json()["banner"]

        self.assertTrue(banner.startswith("This store stopped taking orders on January 1, 2027."))
        self.assertIn("You can view your order here until 3:00 PM Philippine time on April 1, 2027.", banner)
        self.assertTrue(banner.endswith("After that this page closes."))

    def test_the_banner_never_says_deleted_or_names_a_billing_reason(self):
        self.suspend()
        for moment in (SUSPENDED_AT + days(1), POINT - days(2), POINT - SECOND):
            self.at(moment)
            banner = self.portal("site/").json()["banner"].lower()
            for word in ("delet", "erase", "remov", "suspend", "payment", "billing", "subscription"):
                self.assertNotIn(word, banner)

    def test_a_site_with_no_clock_has_no_closing_instant(self):
        services.sweep(now=TRIAL_END)
        self.at(TRIAL_END + days(5))

        body = self.portal("site/").json()

        self.assertIsNone(body["closes_at"])
        self.assertEqual(body["banner"], "This store is not taking orders.")


class IdentityTests(PortalBase):
    def setUp(self):
        super().setUp()
        self.open_portal()

    def test_a_signed_in_buyer_with_a_verified_order_is_known_by_first_name_only(self):
        response = self.portal("me/", token=self.token)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"first_name": "Maria"})

    def test_a_buyer_with_only_an_unverified_order_gets_the_neutral_404(self):
        for status in (C.PENDING_REVIEW, C.REJECTED):
            order, _ = make_order(self.site, status=status)
            token = self.session_token(order.member)
            for path in ("me/", "orders/"):
                with self.subTest(status=status, path=path):
                    self.assert_not_found(self.portal(path, token=token))

    def test_a_member_with_no_order_gets_the_neutral_404(self):
        member = self.new_member_for(self.site)

        self.assert_not_found(self.portal("me/", token=self.session_token(member)))

    def new_member_for(self, site):
        from apps.members import services as members
        return members.create_member(
            site, full_name="No Order", mobile="09170000001", email="none@buyer.test", email_state="pending"
        )

    def test_a_session_of_another_site_opens_nothing(self):
        other_order, _ = make_order(self.other_site, status=C.VERIFIED)
        token = self.session_token(other_order.member, site=self.other_site)

        self.assertEqual(self.portal("me/", token=token).status_code, 401)  # not a session of this site
        self.assertEqual(self.portal("orders/", token=token).status_code, 401)

    def test_a_missing_expired_or_revoked_session_answers_the_session_error(self):
        for token in ("", "nonsense", self.token):
            if token == self.token:
                sessions.revoke_sessions(self.member)
            response = self.portal("me/", token=token)
            with self.subTest(token=token[:4]):
                self.assertEqual(response.status_code, 401, response.content)
                self.assertEqual(response.json()["error"]["code"], "member_session_invalid")

    def test_an_expired_session_is_refused(self):
        from apps.members.models import MemberSession
        MemberSession.objects.filter(member=self.member).update(
            expires_at=timezone.now() - SECOND
        )

        self.assertEqual(self.portal("me/", token=self.token).status_code, 401)

    def test_a_session_issued_before_the_suspension_still_works(self):
        self.assertEqual(self.portal("me/", token=self.token).status_code, 200)

    def test_signout_revokes_and_twice_is_fine(self):
        self.assertEqual(self.portal("signout/", token=self.token).json(), {"status": "signed_out"})
        self.assertEqual(self.portal("me/", token=self.token).status_code, 401)
        self.assertEqual(self.portal("signout/", token=self.token).json(), {"status": "signed_out"})

    def test_the_portal_closes_for_a_signed_in_buyer_at_the_point(self):
        self.at(POINT)

        self.assert_not_found(self.portal("me/", token=self.token))
        self.assert_not_found(self.portal("orders/", token=self.token))
        self.assert_not_found(self.portal("proof/", token=self.token, proof_id=self.proof.public_id))


class SignInTests(PortalBase):
    BINDING = "browser-binding-0123456789abcdef"

    def setUp(self):
        super().setUp()
        self.open_portal()
        patcher = mock.patch("apps.members.signin._sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_buyer_signs_in_by_email_code_and_gets_a_token_only(self):
        mail.outbox.clear()
        asked = self.portal("signin/request/", binding=self.BINDING, contact=self.member.email)
        self.assertEqual(asked.status_code, 200, asked.content)
        self.assertEqual(set(asked.json()), {"step", "message", "offer_email"})
        code = re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)

        verified = self.portal("signin/verify/", binding=self.BINDING, code=code)

        self.assertEqual(verified.status_code, 200, verified.content)
        self.assertEqual(set(verified.json()), {"token"})
        self.assertEqual(self.portal("me/", token=verified.json()["token"]).status_code, 200)

    def test_an_unknown_contact_gets_the_same_answer_shape_as_a_member(self):
        known = self.portal("signin/request/", binding=self.BINDING, contact=self.member.email)
        unknown = self.portal(
            "signin/request/", binding="another-binding-0123456789abcd", contact="who@nobody.test"
        )

        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.json(), unknown.json())

    def test_a_short_binding_is_refused(self):
        self.assertEqual(
            self.portal("signin/request/", binding="short", contact=self.member.email).status_code, 400
        )

    def test_signin_is_closed_when_the_portal_is_not_open(self):
        self.at(POINT)

        for path in ("signin/request/", "signin/resend/", "signin/accept-email/", "signin/verify/"):
            with self.subTest(path=path):
                self.assert_not_found(
                    self.portal(path, binding=self.BINDING, contact=self.member.email, code="000000")
                )


class OrdersTests(PortalBase):
    def setUp(self):
        super().setUp()
        self.open_portal()

    def test_the_card_holds_the_buyers_own_verified_order_only(self):
        response = self.portal("orders/", token=self.token)

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(set(body), {"cards", "closes_at", "banner"})
        self.assertEqual(len(body["cards"]), 1)
        card = body["cards"][0]
        self.assertEqual(card["order_number"], self.order.order_number)
        self.assertEqual(card["total_centavos"], C.PRICE_CENTAVOS)
        self.assertEqual(card["payment_status"], "verified")
        self.assertEqual(card["payment_method_label"], "Bank or e-wallet transfer")
        self.assertEqual(len(card["steps"]), 4)
        self.assertEqual(
            [p["id"] for p in card["proofs"]], [self.proof.public_id]
        )
        self.assertEqual(
            set(card["proofs"][0]),
            {"id", "seq", "submitted_at", "amount_sent_centavos", "reference_number", "content_type"},
        )

    def test_shipment_fields_show_once_shipped(self):
        PackOrder.objects.filter(pk=self.order.pk).update(
            courier="lbc", tracking_number="LBC123", shipped_on=self.clock.now().date(),
            shipment_recorded_at=self.clock.now(),
        )

        card = self.portal("orders/", token=self.token).json()["cards"][0]

        self.assertEqual((card["courier"], card["tracking"]), ("LBC", "LBC123"))
        self.assertTrue(card["shipped_on"])

    def test_the_registration_status_is_a_label_and_a_line_with_no_action(self):
        from apps.pack.models import SanteRegistration
        SanteRegistration.objects.create(
            site=self.site, member=self.member, pack_order=self.order, status="awaiting_buyer_details",
        )

        card = self.portal("orders/", token=self.token).json()["cards"][0]

        self.assertEqual(set(card["registration"]), {"label", "line"})
        self.assertEqual(card["registration"]["label"], "Details needed")

    def test_no_card_has_a_forbidden_key_and_no_value_leaks(self):
        from apps.pack.models import SanteRegistration
        SanteRegistration.objects.create(
            site=self.site, member=self.member, pack_order=self.order, status="registered",
            date_of_birth=self.clock.now().date(), desired_username="secretuser",
        )
        PackOrder.objects.filter(pk=self.order.pk).update(
            delivery_address={"line": "12 Secret St", "receiver_mobile": "09179999999"},
            rejection_lock_active=True,
        )
        responses = [
            self.portal("site/"), self.portal("me/", token=self.token),
            self.portal("orders/", token=self.token),
        ]

        for response in responses:
            body = response.json()
            text = json.dumps(body)
            for key in set(keys_of(body)):
                for word in FORBIDDEN:
                    self.assertNotIn(word, key.lower(), f"{key} in {text}")
            for secret in (
                "Secret St", "09179999999", "secretuser", self.member.email, self.member.mobile,
                "Maria Santos",
            ):
                self.assertNotIn(secret, text)

    def test_other_buyers_orders_do_not_appear(self):
        make_order(self.site, status=C.VERIFIED)
        other_site_order, _ = make_order(self.other_site, status=C.VERIFIED)

        cards = self.portal("orders/", token=self.token).json()["cards"]

        self.assertEqual([c["order_number"] for c in cards], [self.order.order_number])
        self.assertNotIn(other_site_order.order_number, json.dumps(cards))

    def test_the_buyers_own_proof_file_streams_with_private_headers(self):
        response = self.portal("proof/", token=self.token, proof_id=self.proof.public_id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")  # the surface's own wrapper wins
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Content-Type"], self.proof.content_type)
        self.assertTrue(response.content)

    def test_another_buyers_proof_and_an_unknown_id_answer_the_same_404(self):
        _, other_proof = make_order(self.site, status=C.VERIFIED)
        _, foreign_proof = make_order(self.other_site, status=C.VERIFIED)

        for proof_id in (other_proof.public_id, foreign_proof.public_id, "pps_nothing"):
            with self.subTest(proof_id=proof_id):
                self.assert_not_found(self.portal("proof/", token=self.token, proof_id=proof_id))


class RouteTableTests(PortalBase):
    def portal_paths(self):
        return [
            str(p.pattern) for p in storefront.urlpatterns[0]
            if str(p.pattern).startswith("portal/")
        ]

    def test_the_router_has_exactly_the_planned_routes(self):
        self.assertEqual({p.removeprefix("portal/") for p in self.portal_paths()}, ROUTES)

    def test_every_portal_route_is_a_post(self):
        for pattern in storefront.urlpatterns[0]:
            if str(pattern.pattern).startswith("portal/"):
                response = self.client.get(
                    "/storefront/v1/" + str(pattern.pattern), {"host": HOST}, secure=True,
                    HTTP_AUTHORIZATION=f"Bearer {SECRET}",
                )
                with self.subTest(route=str(pattern.pattern)):
                    self.assertEqual(response.status_code, 405)

    def test_no_write_route_exists_for_a_buyer(self):
        self.open_portal()
        for path in ("checkout/", "resubmit/", "details/", "contact/", "account/", "order/"):
            with self.subTest(path=path):
                response = self.portal(path, token=self.token)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["error"]["message"], "No such endpoint.")

    def test_every_portal_route_answers_the_neutral_404_on_a_served_host(self):
        for route in sorted(ROUTES):
            with self.subTest(route=route):  # the trial runs: the site is served
                self.assert_not_found(
                    self.portal(route, token=self.token, proof_id=self.proof.public_id, binding="x" * 20)
                )

    def test_a_portal_write_changes_no_pack_row(self):
        self.open_portal()
        before = (
            PackOrder.objects.count(), self.order.submissions.count(),
            PackOrder.objects.values().get(pk=self.order.pk),
        )

        self.portal("orders/", token=self.token)
        self.portal("me/", token=self.token)
        self.portal("proof/", token=self.token, proof_id=self.proof.public_id)

        self.assertEqual(
            before,
            (
                PackOrder.objects.count(), self.order.submissions.count(),
                PackOrder.objects.values().get(pk=self.order.pk),
            ),
        )


class SaleGateTests(PortalBase):
    """L45: no Membership Pack is sold unless the buyer portal exists. Product orders are separate."""

    def test_the_constant_is_true_on_main(self):
        self.assertIs(portal.BUYER_PORTAL_READY, True)

    def test_false_blocks_the_checkout_entry_with_the_neutral_404(self):
        with mock.patch.object(portal, "BUYER_PORTAL_READY", False):
            response = self.contact()

        self.assert_not_found(response)
        self.assertFalse(CheckoutToken.objects.exists())

    def test_false_blocks_place_pack_order_before_any_file_is_stored(self):
        token = self.start()
        before = self.proof_files()
        with mock.patch.object(portal, "BUYER_PORTAL_READY", False):
            response = self.place(token)

        self.assert_not_found(response)
        self.assertEqual(self.proof_files(), before)
        self.assertEqual(PackOrder.objects.filter(site=self.site).count(), 1)  # the verified one of setUp

    def test_true_allows_the_whole_checkout(self):
        token = self.start()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.place(token)

        self.assertEqual(response.status_code, 200, response.content)

    def test_a_product_order_path_is_not_gated(self):
        import apps.shop.api as shop_api
        import inspect
        self.assertNotIn("BUYER_PORTAL_READY", inspect.getsource(shop_api))
        from apps.shop import placement as shop_placement
        self.assertNotIn("BUYER_PORTAL_READY", inspect.getsource(shop_placement))
