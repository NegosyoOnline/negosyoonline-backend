"""T-ADM: the SMS lists in the admin show numbers masked and change nothing but the settings."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.notifications import sms
from apps.notifications.models import SmsDevice, SmsMessage, SmsPolicy
from apps.notifications.tests.base import MOBILE, gateway_on
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.base import HqAdminMixin, on_hq

MASKED = "+63917***4567"
TOKEN_HASH = "ab12" * 16
MODELS = ("smsmessage", "smsdevice", "smspolicy")


class SmsAdminCase(HqAdminMixin, TestCase):
    def setUp(self):
        self.operator = get_user_model().objects.create_superuser("op", "op@operators.test", "pw")
        self.open_hq_session(self.operator)
        self.device = SmsDevice.objects.create(
            name="system", token_hash=TOKEN_HASH, last_contact_at=timezone.now(),
            app_version="1.0.0", battery_percent=88,
        )
        self.message = SmsMessage.objects.create(
            recipient=MOBILE, category="code", purpose="mobile_verify",
            priority=SmsMessage.Priority.CODE, expires_at=timezone.now(), device=self.device,
        )
        self.policy = sms.policy()

    def url(self, model, page="changelist", *args):
        return self.admin_url(f"admin:notifications_{model}_{page}", *args)


@on_hq
@gateway_on
class SmsAdminTests(SmsAdminCase):
    def test_1_the_list_shows_the_number_masked(self):
        response = self.hq_get(self.url("smsmessage"))

        self.assertContains(response, MASKED)
        self.assertNotContains(response, MOBILE)
        self.assertNotContains(response, "9171234567")
        for shown in ("mobile_verify", "queued"):
            self.assertContains(response, shown)

    def test_1_search_finds_a_full_number(self):
        found = self.hq_get(self.url("smsmessage") + "?q=%2B639171234567")
        self.assertContains(found, f">{MASKED}<")
        # The page repeats what the admin typed in its search box, and shows it nowhere else.
        self.assertContains(found, MOBILE, count=1)
        self.assertContains(found, f'name="q" value="{MOBILE}"')
        missing = self.hq_get(self.url("smsmessage") + "?q=%2B639990000000")
        self.assertNotContains(missing, MASKED)

    def test_2_the_detail_page_shows_the_number_masked(self):
        response = self.hq_get(self.url("smsmessage", "change", self.message.pk))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, MASKED)
        self.assertNotContains(response, MOBILE)
        self.assertNotContains(response, "9171234567")
        self.assertNotIn(MOBILE, str(self.message))

    def test_3_nothing_can_be_added_changed_or_deleted_except_the_settings(self):
        for model, row in (("smsmessage", self.message), ("smsdevice", self.device)):
            with self.subTest(model=model):
                self.assertEqual(self.hq_get(self.url(model, "add")).status_code, 403)
                change = self.hq_post(self.url(model, "change", row.pk), {"name": "changed"})
                self.assertEqual(change.status_code, 403)
                delete = self.hq_post(self.url(model, "delete", row.pk), {"post": "yes"})
                self.assertEqual(delete.status_code, 403)
                self.assertTrue(type(row).objects.filter(pk=row.pk).exists())
        self.device.refresh_from_db()
        self.assertEqual(self.device.name, "system")

    def test_3_the_settings_row_cannot_be_added_or_deleted(self):
        self.assertEqual(self.hq_get(self.url("smspolicy", "add")).status_code, 403)
        delete = self.hq_post(self.url("smspolicy", "delete", self.policy.pk), {"post": "yes"})
        self.assertEqual(delete.status_code, 403)
        self.assertEqual(SmsPolicy.objects.count(), 1)

    def test_3_the_settings_list_opens_before_the_row_exists(self):
        SmsPolicy.objects.all().delete()
        response = self.hq_get(self.url("smspolicy"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SmsPolicy.objects.count(), 1)

    def test_4_changing_the_settings_writes_an_audit_row_with_before_and_after(self):
        response = self.hq_post(
            self.url("smspolicy", "change", self.policy.pk),
            {"sending_stopped": "on", "platform_daily_limit": "4000", "site_daily_limit": "150"},
        )

        self.assertEqual(response.status_code, 302)
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.sending_stopped)
        self.assertEqual(self.policy.site_daily_limit, 150)
        entry = OperatorAuditEntry.objects.get(action="sms_policy_change")
        self.assertEqual(entry.operator, self.operator)
        self.assertEqual(entry.target_type, "sms_policy")
        self.assertEqual(
            entry.detail,
            {
                "before": {"sending_stopped": False, "site_daily_limit": 4000},
                "after": {"sending_stopped": True, "site_daily_limit": 150},
            },
        )
        self.assertEqual(entry.personal, {})

    def test_4_saving_the_settings_unchanged_writes_no_audit_row(self):
        self.hq_post(
            self.url("smspolicy", "change", self.policy.pk),
            {"platform_daily_limit": "4000", "site_daily_limit": "4000"},
        )
        self.assertFalse(OperatorAuditEntry.objects.filter(action="sms_policy_change").exists())

    def test_5_no_token_hash_is_shown(self):
        for page in (self.url("smsdevice"), self.url("smsdevice", "change", self.device.pk)):
            with self.subTest(page=page):
                response = self.hq_get(page)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "system")
                self.assertNotContains(response, TOKEN_HASH)
                self.assertNotContains(response, "oken hash")
        detail = self.hq_get(self.url("smsmessage", "change", self.message.pk))
        self.assertNotContains(detail, TOKEN_HASH)


@on_hq
@override_settings(SMS_GATEWAY_ENABLED=False)
class SmsAdminGatewayOffTests(SmsAdminCase):
    """With the switch off the tables may not exist yet, so the admin does not read them."""

    def test_the_lists_are_closed(self):
        for model in MODELS:
            with self.subTest(model=model):
                self.assertEqual(self.hq_get(self.url(model)).status_code, 403)
        change = self.hq_get(self.url("smspolicy", "change", self.policy.pk))
        self.assertEqual(change.status_code, 403)

    def test_the_admin_home_does_not_list_them(self):
        response = self.hq_get(self.admin_url("admin:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email messages")
        self.assertNotContains(response, "SMS ")


@on_hq
@gateway_on
class SmsAdminHomeTests(SmsAdminCase):
    def test_the_admin_home_lists_them(self):
        response = self.hq_get(self.admin_url("admin:index"))
        for name in ("SMS messages", "SMS phones", "SMS settings"):
            self.assertContains(response, name)
