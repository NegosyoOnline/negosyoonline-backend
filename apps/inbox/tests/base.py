"""Helpers shared by the inbox tests."""
from django.contrib.auth import get_user_model
from django.test import override_settings

from apps.notifications.tests.base import SmsTestCase, online_phone

inbox_on = override_settings(SMS_INBOX_ENABLED=True)
SENDER = "+639189990000"


@inbox_on
class InboxTestCase(SmsTestCase):
    """The gateway and the inbox on, one phone that forwards, one staff user."""

    def setUp(self):
        super().setUp()
        self.phone = online_phone(forwards_incoming=True)
        self.staff = get_user_model().objects.create_user("ana", password="pw", is_staff=True)
