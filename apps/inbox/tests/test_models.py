from django.apps import apps
from django.contrib import admin
from django.test import SimpleTestCase, override_settings

from apps.inbox import switch


class SwitchTests(SimpleTestCase):
    def test_1_the_switch_is_off_unless_set(self):
        self.assertIs(switch.inbox_on(), False)
        with override_settings(SMS_INBOX_ENABLED=True):
            self.assertIs(switch.inbox_on(), True)

    def test_2_the_app_is_installed(self):
        self.assertTrue(apps.is_installed("apps.inbox"))

    def test_3_no_inbox_record_has_a_django_admin_screen(self):
        registered = [m._meta.label for m in admin.site._registry if m._meta.app_label == "inbox"]
        self.assertEqual(registered, [])
