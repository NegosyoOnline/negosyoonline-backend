"""PROMO-CONSENT (z8uq9mbreu): the checkout reads the flag (contact answer) and can only opt in (place),
for a bank-transfer order (member at placement) and a Xendit order (member at payment)."""
from django.test import override_settings
from django.utils import timezone

from apps.members import promo
from apps.members.models import Member
from apps.pack.models import PackOrder
from apps.pack.tests.xendit_base import XenditPackCase


@override_settings(PROMO_SMS_CONSENT_ENABLED=True)
class ContactAnswer(XenditPackCase):
    def test_on_the_answer_carries_the_switch_the_version_and_the_exact_text(self):
        body = self.contact().json()
        self.assertIs(body["promo_sms_consent_enabled"], True)
        self.assertEqual(body["promo_sms_version"], promo.PROMO_SMS_VERSION)
        self.assertEqual(body["promo_sms_text"], promo.PROMO_SMS_TEXT.format(store=self.site.display_name))

    @override_settings(PROMO_SMS_CONSENT_ENABLED=False)
    def test_off_the_fields_are_absent(self):
        self.assertEqual(set(self.contact().json()), {"token", "expires_at"})


@override_settings(PROMO_SMS_CONSENT_ENABLED=True)
class Place(XenditPackCase):
    def place_flag(self, token, value="1", **fields):
        extra = {} if value is None else {"promo_sms_ok": value}
        return self.place(token, **extra, **fields)

    def test_yes_opts_the_member_in_with_time_and_version(self):
        response = self.place_flag(self.start())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(set(response.json()), {"order_number", "replay", "pay_url"})  # answer unchanged
        member = Member.objects.get()
        self.assertEqual((member.promo_sms_ok, member.promo_sms_version), (True, promo.PROMO_SMS_VERSION))
        self.assertIsNotNone(member.promo_sms_at)

    def test_omitted_or_other_values_mean_no(self):
        for i, value in enumerate((None, "", "0", "true", "yes")):
            token = self.start(email=f"b{i}@buyer.test", mobile=f"0917000001{i}")
            self.assertEqual(self.place_flag(token, value, submit_key=f"k{i}").status_code, 200)
        self.assertEqual(Member.objects.count(), 5)
        self.assertFalse(Member.objects.filter(promo_sms_ok=True).exists())

    def test_replay_changes_nothing(self):
        token = self.start()
        self.place_flag(token, None)  # first placed without the yes
        again = self.place_flag(token, "1")  # same submit_key: a replay
        self.assertTrue(again.json()["replay"])
        self.assertFalse(Member.objects.get().promo_sms_ok)

    def test_an_order_without_the_box_never_clears_a_yes(self):
        self.place_flag(self.start(), None)
        member, order = Member.objects.get(), PackOrder.objects.get()
        then = timezone.now()
        promo.set_preference(member, True, at=then)
        promo.opt_in_from_order(order, member)  # this order carries no yes
        member.refresh_from_db()
        self.assertEqual((member.promo_sms_ok, member.promo_sms_at), (True, then))

    @override_settings(PROMO_SMS_CONSENT_ENABLED=False)
    def test_switch_off_the_flag_is_ignored(self):
        self.assertEqual(self.place_flag(self.start()).status_code, 200)
        member = Member.objects.get()
        self.assertEqual((member.promo_sms_ok, member.promo_sms_at, member.promo_sms_version), (False, None, ""))
        self.assertNotIn("promo_sms", PackOrder.objects.get().events.first().new)

    def test_a_xendit_order_applies_the_yes_when_its_member_is_made_at_payment(self):
        response = self.place_xendit(self.start(), promo_sms_ok="1")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Member.objects.count(), 0)
        with self.captureOnCommitCallbacks(execute=True):
            paid = self.webhook(PackOrder.objects.get())
        self.assertEqual(paid.status_code, 200, paid.content)
        member = Member.objects.get()
        self.assertEqual((member.promo_sms_ok, member.promo_sms_version), (True, promo.PROMO_SMS_VERSION))
        self.assertIsNotNone(member.promo_sms_at)
