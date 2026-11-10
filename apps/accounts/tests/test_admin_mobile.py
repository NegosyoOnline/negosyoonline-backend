"""The admin lists of the two mobile tables: numbers and emails masked, nothing changeable."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Account, AccountMobile, MobileOtpChallenge
from apps.notifications.tests.base import gateway_on
from apps.operators.tests.base import HqAdminMixin, on_hq

EMAIL = "ana.reyes@example.test"
MASKED_EMAIL = "a***@example.test"
MOBILE = "+639171234567"
MASKED = "+63917***4567"
CODE_HASH = "c0de" * 16
MODELS = ("accountmobile", "mobileotpchallenge")


@on_hq
@gateway_on
class MobileAdminTests(HqAdminMixin, TestCase):
    def setUp(self):
        operator = get_user_model().objects.create_superuser("op", "op@operators.test", "pw")
        self.open_hq_session(operator)
        now = timezone.now()
        self.account = Account.objects.create(email=EMAIL, name="Ana")
        self.rows = {
            "accountmobile": AccountMobile.objects.create(
                account=self.account, number=MOBILE, verified_at=now
            ),
            "mobileotpchallenge": MobileOtpChallenge.objects.create(
                account=self.account, number=MOBILE, code_hash=CODE_HASH,
                expires_at=now + timedelta(minutes=10), sms_public_id="sms_abc",
            ),
        }

    def url(self, model, page="changelist", *args):
        return self.admin_url(f"admin:accounts_{model}_{page}", *args)

    def assert_masked(self, response):
        self.assertContains(response, MASKED)
        self.assertContains(response, MASKED_EMAIL)
        for private in (MOBILE, "9171234567", EMAIL, CODE_HASH):
            self.assertNotContains(response, private)

    def test_each_list_shows_the_number_and_the_account_masked(self):
        for model in MODELS:
            with self.subTest(model=model):
                self.assert_masked(self.hq_get(self.url(model)))

    def test_each_detail_page_shows_them_masked_and_no_code_hash(self):
        for model in MODELS:
            with self.subTest(model=model):
                response = self.hq_get(self.url(model, "change", self.rows[model].pk))
                self.assertEqual(response.status_code, 200)
                self.assert_masked(response)

    def test_search_finds_a_full_number_only(self):
        found = self.hq_get(self.url("accountmobile") + "?q=%2B639171234567")
        self.assertContains(found, f">{MASKED}<")
        for fragment in ("4567", "9171234567"):
            with self.subTest(fragment=fragment):
                response = self.hq_get(self.url("accountmobile") + f"?q={fragment}")
                self.assertNotContains(response, MASKED)

    def test_nothing_can_be_added_changed_or_deleted(self):
        for model in MODELS:
            with self.subTest(model=model):
                pk = self.rows[model].pk
                self.assertEqual(self.hq_get(self.url(model, "add")).status_code, 403)
                self.assertEqual(self.hq_post(self.url(model, "change", pk)).status_code, 403)
                self.assertEqual(self.hq_post(self.url(model, "delete", pk)).status_code, 403)
        self.assertEqual(AccountMobile.objects.count(), 1)
        self.assertEqual(MobileOtpChallenge.objects.count(), 1)

    def test_the_lists_are_closed_while_the_gateway_is_off(self):
        with override_settings(SMS_GATEWAY_ENABLED=False):
            for model in MODELS:
                with self.subTest(model=model):
                    self.assertEqual(self.hq_get(self.url(model)).status_code, 403)
            index = self.hq_get(self.admin_url("admin:index"))
        self.assertNotContains(index, "Account mobiles")
        self.assertNotContains(index, "Mobile verification challenges")
