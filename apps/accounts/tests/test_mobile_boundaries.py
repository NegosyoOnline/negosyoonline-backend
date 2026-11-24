"""T-BND: no table can hold a text, and accounts sends texts only through send_sms."""
from pathlib import Path

from django.apps import apps
from django.db import models
from django.test import SimpleTestCase

from apps.accounts.models import AccountMobile, MobileOtpChallenge

ACCOUNTS = Path(__file__).resolve().parent.parent
# The one place the notifications app keeps an announcement: a store's bulk batch holds the text it
# sent to its own members, as a CharField of at most one SMS, for 180 days (Coach Ed's plan,
# docs/plans/store-bulk-sms-plan-2026-11-23.md section 7; as Inbox SMS keeps a store's texts). The
# send records and every other table still hold no text.
ALLOWED_TEXT = {("StoreBulkBatch", "text")}


class BoundaryTests(SimpleTestCase):
    def test_1_no_model_has_a_field_that_could_hold_a_text_body(self):
        for label in ("notifications", "accounts"):
            for model in apps.get_app_config(label).get_models():
                for field in model._meta.concrete_fields:
                    with self.subTest(model=model.__name__, field=field.name):
                        self.assertNotIsInstance(field, models.TextField)
                        if (model.__name__, field.name) in ALLOWED_TEXT:
                            self.assertEqual((field.max_length, type(field)), (160, models.CharField))
                            continue
                        self.assertNotIn(field.name, ("text", "body", "message", "content"))

    def test_1_the_mobile_tables_hold_only_short_values(self):
        for model in (AccountMobile, MobileOtpChallenge):
            for field in model._meta.concrete_fields:
                with self.subTest(model=model.__name__, field=field.name):
                    self.assertNotIsInstance(field, models.JSONField)
                    if isinstance(field, models.CharField):
                        self.assertLessEqual(field.max_length, 64)  # a hash at most

    def test_2_accounts_writes_send_records_only_through_send_sms(self):
        sources = [
            path for path in ACCOUNTS.rglob("*.py")
            if "tests" not in path.parts and "migrations" not in path.parts
        ]
        self.assertTrue(sources)
        for path in sources:
            source = path.read_text()
            with self.subTest(file=path.name):
                for forbidden in ("SmsMessage", "SmsDevice", "SmsPolicy", "notifications.models",
                                  "notifications.sms", "notifications import sms", "hold_text"):
                    self.assertNotIn(forbidden, source)
        self.assertIn("send_sms(", (ACCOUNTS / "mobile.py").read_text())
