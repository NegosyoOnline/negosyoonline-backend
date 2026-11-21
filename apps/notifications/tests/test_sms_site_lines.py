"""SMS-LINE P2: `site_has_line` follows billing and HQ's Disable; a store text to an offline
own phone is refused; a phone gets only its own Site's texts, and a code always goes from the
NegosyoOnline phone."""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone

from apps.billing import services as billing
from apps.billing.tests import base as billing_base
from apps.billing.tests.base import CYCLE_1_END, days
from apps.inquiries import sms_inbox
from apps.notifications import sms
from apps.notifications.models import SmsDevice, SmsLineDisable, SmsMessage
from apps.notifications.services import send_sms
from apps.notifications.tests import test_sms_gateway as base
from apps.notifications.tests.base import CODE_TEXT, MOBILE, make_phone
from apps.notifications.tests.test_sms_store import store_text
from apps.sites.tests.base import make_account, make_site

S = SmsMessage.Status
TOKENS = {"a": "token-line-a", "b": "token-line-b", "c": "token-line-c", "d": "token-line-d"}
NOW_TRIAL = billing_base.T0 + days(1)
NOW_ACTIVE = billing_base.TRIAL_END + days(1)
NOW_ATTENTION = CYCLE_1_END + days(2)
NOW_ENDED = CYCLE_1_END + days(8)

lines_on = override_settings(SMS_SITE_LINES_ENABLED=True, BILLING_ENABLED=True)


class LineCase(base.GatewayCase):
    """Four stores, each with its own paired phone: A active, B billing-ended, C active (HQ
    disables it in the tests that need it), D in its trial. Billing is read at a fixed instant
    per store."""

    def setUp(self):
        super().setUp()
        self.staff = get_user_model().objects.create_user("coach", password="pw", is_staff=True)
        with override_settings(BILLING_ENABLED=True):
            # B first: making a suspended store runs a billing sweep over every subscription.
            sub_b = billing_base.make_suspended(email="b@example.test", name="store-b")
            subs = (
                billing_base.make_active(email="a@example.test", name="store-a"),
                sub_b,
                billing_base.make_active(email="c@example.test", name="store-c"),
                billing_base.make_subscription(email="d@example.test", name="store-d"),
                billing_base.make_attention(email="e@example.test", name="store-e"),
            )
        self.sites = {key: sub.site for key, sub in zip("abcde", subs)}
        self.at = {
            "a": NOW_ACTIVE, "b": NOW_ENDED, "c": NOW_ACTIVE, "d": NOW_TRIAL, "e": NOW_ATTENTION,
        }
        self.phones = {
            key: make_phone(f"line-{key}", TOKENS[key], site=self.sites[key]) for key in "abcd"
        }
        real = billing.position
        times = {self.sites[key].pk: key for key in self.sites}
        patcher = mock.patch.object(
            billing, "position",
            side_effect=lambda site, at=None: real(site, at=self.at[times[site.pk]]),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def disable(self, key, **fields):
        return SmsLineDisable.objects.create(
            site=self.sites[key], disabled_by=self.staff, reason="Misuse reported", **fields
        )

    def handed(self, token):
        return {m["id"] for m in self.poll(token=token)["messages"]}

    def state(self, message):
        message.refresh_from_db()
        return message.status, message.reason

    def offline(self, key):
        SmsDevice.objects.filter(pk=self.phones[key].pk).update(
            last_contact_at=timezone.now() - timedelta(seconds=300)
        )


@lines_on
class SiteHasLineTests(LineCase):
    def test_serving_positions_have_a_line_and_ended_ones_do_not(self):
        self.assertTrue(sms.site_has_line(self.sites["a"]))  # active
        self.assertTrue(sms.site_has_line(self.sites["d"]))  # trial
        self.assertFalse(sms.site_has_line(self.sites["b"]))  # suspended
        # payment_requires_attention is still served
        self.assertEqual(billing.position(self.sites["e"]).state, "payment_requires_attention")
        self.assertTrue(sms.site_has_line(self.sites["e"]))

    def test_reactivated_it_works_again_with_the_same_phone(self):
        self.at["a"] = NOW_ENDED
        self.assertFalse(sms.site_has_line(self.sites["a"]))
        self.assertEqual(sms.line_for(self.sites["a"]), self.phone)
        self.at["a"] = NOW_ACTIVE
        self.assertEqual(sms.line_for(self.sites["a"]), self.phones["a"])

    def test_an_active_disable_removes_the_line_and_a_reenabled_one_does_not(self):
        row = self.disable("c")
        self.assertFalse(sms.site_has_line(self.sites["c"]))
        SmsLineDisable.objects.filter(pk=row.pk).update(reenabled_at=timezone.now())
        self.assertTrue(sms.site_has_line(self.sites["c"]))
        self.disable("c")  # disabled again later: history is kept, a new row
        self.assertFalse(sms.site_has_line(self.sites["c"]))
        self.assertEqual(SmsLineDisable.objects.filter(site=self.sites["c"]).count(), 2)

    def test_it_fails_closed_with_billing_off_or_no_subscription(self):
        with override_settings(BILLING_ENABLED=False):
            self.assertFalse(sms.site_has_line(self.sites["a"]))  # billing off: no position
        site = make_site(make_account("none@example.test"), "no-subscription")
        with mock.patch.object(billing, "position", return_value=billing.NOT_SERVED):
            self.assertFalse(sms.site_has_line(site))

    def test_a_disable_of_one_site_touches_no_other(self):
        self.disable("c")
        self.assertTrue(sms.site_has_line(self.sites["a"]))
        self.assertTrue(sms.site_has_line(self.sites["d"]))

    def test_only_one_active_disable_per_site(self):
        self.disable("c")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.disable("c")


@lines_on
class IsolationEndedAndDisabledTests(LineCase):
    """No stub: `site_has_line` is the real one, over real billing positions."""

    def status(self, key):
        """A waiting order-status text of the store: what a Site phone carries."""
        return self.queue(priority=base.P.STATUS, site=self.sites[key])

    def queue_all(self):
        self.platform = self.queue()
        self.text = {key: self.status(key) for key in "abcd"}
        self.disable("c")

    def test_each_phone_gets_only_its_own_texts_and_ended_or_disabled_sites_fall_back(self):
        self.queue_all()
        self.assertEqual(self.handed(TOKENS["a"]), {self.text["a"].public_id})
        self.assertEqual(self.handed(TOKENS["d"]), {self.text["d"].public_id})
        self.assertEqual(self.handed(TOKENS["b"]), set())  # billing ended: its phone gets nothing
        self.assertEqual(self.handed(TOKENS["c"]), set())  # disabled: nothing
        # the NegosyoOnline phone: the platform text and the two sites with no line, no other
        self.assertEqual(
            self.handed(base.TOKEN),
            {self.platform.public_id, self.text["b"].public_id, self.text["c"].public_id},
        )
        self.assertEqual(SmsMessage.objects.get(pk=self.text["a"].pk).device, self.phones["a"])
        self.assertEqual(SmsMessage.objects.get(pk=self.text["d"].pk).device, self.phones["d"])

    def test_the_platform_phone_never_takes_a_lined_sites_text(self):
        self.queue_all()
        platform_ids = self.handed(base.TOKEN)
        self.assertNotIn(self.text["a"].public_id, platform_ids)
        self.assertNotIn(self.text["d"].public_id, platform_ids)

    def test_a_site_that_ends_while_its_text_waits_is_not_handed_to_its_own_phone(self):
        waiting = self.status("a")
        self.at["a"] = NOW_ENDED
        self.assertEqual(self.handed(TOKENS["a"]), set())
        self.assertEqual(self.handed(base.TOKEN), {waiting.public_id})

    def test_a_disabled_sites_phone_takes_nothing_after_the_disable(self):
        waiting = self.status("c")
        self.disable("c")
        self.assertEqual(self.handed(TOKENS["c"]), set())
        self.assertFalse(sms.may_be_handed_texts(self.phones["c"]))
        self.assertEqual(self.state(waiting), (S.QUEUED, ""))

    def test_a_reenabled_site_is_handed_its_texts_again(self):
        row = self.disable("c")
        waiting = self.status("c")
        SmsLineDisable.objects.filter(pk=row.pk).update(reenabled_at=timezone.now())
        self.assertEqual(self.handed(TOKENS["c"]), {waiting.public_id})

    def test_the_switch_off_changes_nothing_even_for_serving_sites(self):
        text_a = self.status("a")
        with override_settings(SMS_SITE_LINES_ENABLED=False):
            self.assertEqual(self.handed(TOKENS["a"]), set())
            self.assertEqual(self.handed(base.TOKEN), {text_a.public_id})


@lines_on
class CodesStayOnTheNegosyoOnlinePhoneTests(LineCase):
    """D1: a member's sign-in code names its Site but is never carried by the Site's phone."""

    def test_a_code_of_a_lined_site_goes_to_the_platform_phone_only(self):
        result = send_sms(
            to=MOBILE, purpose="member_signin_code", text=CODE_TEXT, site=self.sites["a"]
        )
        self.assertEqual(result.outcome, sms.QUEUED)
        self.assertEqual(self.handed(TOKENS["a"]), set())
        self.assertEqual(self.handed(base.TOKEN), {result.message.public_id})
        self.assertEqual(SmsMessage.objects.get(pk=result.message.pk).device, self.phone)

    def test_the_onboarding_code_too(self):
        result = send_sms(
            to=MOBILE, purpose="member_mobile_verify", text=CODE_TEXT, site=self.sites["d"]
        )
        self.assertEqual(self.handed(TOKENS["d"]), set())
        self.assertEqual(self.handed(base.TOKEN), {result.message.public_id})

    def test_a_code_is_not_held_back_by_the_sites_own_phone_being_offline(self):
        self.offline("a")
        result = send_sms(
            to=MOBILE, purpose="member_signin_code", text=CODE_TEXT, site=self.sites["a"]
        )
        self.assertEqual(result.outcome, sms.QUEUED)

    def test_a_code_of_a_billing_ended_or_disabled_site_still_goes_from_the_platform_phone(self):
        self.disable("c")
        for key in ("b", "c"):
            result = send_sms(
                to=MOBILE, purpose="member_signin_code", text=CODE_TEXT, site=self.sites[key]
            )
            self.assertEqual(result.outcome, sms.QUEUED)
        self.assertEqual(len(self.handed(base.TOKEN)), 2)

    def test_line_for_a_code_is_the_platform_phone(self):
        self.assertEqual(sms.line_for(self.sites["a"], sms.CODE), self.phone)
        self.assertEqual(sms.line_for(self.sites["a"]), self.phones["a"])

    def test_a_code_queued_under_a_lined_site_is_not_claimable_by_that_sites_phone(self):
        waiting = self.queue(site=self.sites["a"])  # category code
        self.assertEqual(self.handed(TOKENS["a"]), set())
        self.assertEqual(self.state(waiting), (S.QUEUED, ""))


@lines_on
class StorePhoneOfflineTests(LineCase):
    def test_a_store_text_with_its_own_phone_offline_is_refused_site_phone_offline(self):
        self.offline("a")
        with self.assertLogs("apps.notifications", level="ERROR"):
            result = store_text(self.sites["a"])
        self.assertEqual((result.outcome, result.reason), (sms.REFUSED, "site_phone_offline"))
        self.assertEqual(self.state(result.message), (S.REFUSED, "site_phone_offline"))
        self.assertFalse(SmsMessage.objects.filter(status=S.QUEUED).exists())

    def test_it_is_not_sent_from_the_platform_phone_even_though_that_is_online(self):
        self.offline("a")
        self.assertTrue(sms.gateway_online())
        with self.assertLogs("apps.notifications", level="ERROR"):
            store_text(self.sites["a"])
        self.assertEqual(self.handed(base.TOKEN), set())

    def test_a_refusal_gives_back_the_stores_slots(self):
        self.offline("a")
        with self.assertLogs("apps.notifications", level="ERROR"):
            store_text(self.sites["a"])
        self.assertEqual(sms.store_used_today(self.sites["a"]), 0)
        SmsDevice.objects.filter(pk=self.phones["a"].pk).update(last_contact_at=timezone.now())
        self.assertEqual(store_text(self.sites["a"]).outcome, sms.QUEUED)

    def test_the_phone_online_queues_it_for_that_phone_only(self):
        result = store_text(self.sites["a"])
        self.assertEqual(result.outcome, sms.QUEUED)
        self.assertEqual(self.handed(TOKENS["b"]), set())
        self.assertEqual(self.handed(base.TOKEN), set())
        self.assertEqual(self.handed(TOKENS["a"]), {result.message.public_id})

    def test_a_billing_ended_store_follows_the_shared_phone_not_its_own(self):
        self.offline("b")  # its own phone is down, but it is no longer its line
        result = store_text(self.sites["b"])
        self.assertEqual(result.outcome, sms.QUEUED)
        self.assertEqual(self.handed(TOKENS["b"]), set())
        self.assertEqual(self.handed(base.TOKEN), {result.message.public_id})

    def test_a_disabled_store_follows_the_shared_phone(self):
        self.disable("c")
        result = store_text(self.sites["c"])
        self.assertEqual(result.outcome, sms.QUEUED)
        self.assertEqual(self.handed(TOKENS["c"]), set())
        self.assertEqual(self.handed(base.TOKEN), {result.message.public_id})

    def test_a_store_with_the_shared_phone_offline_still_says_gateway_offline(self):
        self.disable("c")
        SmsDevice.objects.filter(pk=self.phone.pk).update(last_contact_at=None)
        with self.assertLogs("apps.notifications", level="ERROR"):
            result = store_text(self.sites["c"])
        self.assertEqual(result.reason, "gateway_offline")

    def test_a_store_with_no_active_phone_of_its_own_meets_the_shared_one(self):
        SmsDevice.objects.filter(pk=self.phones["a"].pk).update(is_active=False)
        self.assertEqual(store_text(self.sites["a"]).outcome, sms.QUEUED)

    def test_the_switch_off_the_old_reason_stays(self):
        self.offline("a")
        SmsDevice.objects.filter(pk=self.phone.pk).update(last_contact_at=None)
        with override_settings(SMS_SITE_LINES_ENABLED=False), self.assertLogs(
            "apps.notifications", level="ERROR"
        ):
            result = store_text(self.sites["a"])
        self.assertEqual(result.reason, "gateway_offline")

    def test_the_owner_is_told_the_phone_is_offline(self):
        self.assertIn("phone is offline", sms_inbox.REASON_TEXTS["site_phone_offline"])
        self.assertIn("site_phone_offline", sms_inbox.AUDIT_CODE)
