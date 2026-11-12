"""T-CMD: the terminal commands that manage the phones."""
import re
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.notifications import sms
from apps.notifications.management.commands.sms_fake_phone import Command as FakePhone
from apps.notifications.models import SmsDevice
from apps.notifications.tests.base import SmsTestCase
from apps.operators.models import OperatorAuditEntry


class SmsDeviceCommandTests(SmsTestCase):
    def setUp(self):
        super().setUp()
        self.actor = get_user_model().objects.create_user("coach", password="pw", is_staff=True)

    def run_command(self, *args, logs=True, **options):
        out = StringIO()
        options.setdefault("actor", "coach")
        if logs:
            with self.assertLogs("apps.notifications", "ERROR"):
                call_command("sms_device", *args, stdout=out, **options)
        else:
            call_command("sms_device", *args, stdout=out, **options)
        return out.getvalue()

    def add(self, name="system"):
        output = self.run_command("add", name=name)
        return re.search(r"Token: (\S+)", output).group(1)

    def refused(self, message, *args, **options):
        with self.assertRaisesMessage(CommandError, message):
            self.run_command(*args, logs=False, **options)

    def audit(self):
        return [
            (e.action, e.operator, e.target_type, e.target_id, e.detail)
            for e in OperatorAuditEntry.objects.order_by("pk")
        ]

    def poll(self, token):
        return self.client.post(
            "/gateway/v1/poll", data="{}", content_type="application/json", secure=True,
            HTTP_AUTHORIZATION=f"Bearer {token}",
        ).status_code

    def test_1_add_prints_the_token_once_and_stores_only_its_hash(self):
        output = self.run_command("add", name="System")
        token = re.search(r"Token: (\S+)", output).group(1)

        device = SmsDevice.objects.get()
        self.assertEqual(output.count(token), 1)
        self.assertGreaterEqual(len(token), 40)
        self.assertEqual((device.name, device.token_hash), ("system", sms.token_hash(token)))
        self.assertTrue(device.is_active)
        self.assertFalse(device.forwards_incoming)
        for field in SmsDevice._meta.concrete_fields:
            self.assertNotIn(token, str(getattr(device, field.attname)))
        self.assertEqual(self.poll(token), 200)

    def test_1_the_token_is_in_no_log_line_and_no_audit_row(self):
        with self.assertLogs(level="DEBUG") as logs:
            out = StringIO()
            call_command("sms_device", "add", name="system", actor="coach", stdout=out)
        token = re.search(r"Token: (\S+)", out.getvalue()).group(1)

        self.assertNotIn(token, "\n".join(logs.output))
        self.assertNotIn(token, str(self.audit()))

    def test_1_a_name_is_used_once(self):
        self.add()
        self.refused("already exists", "add", name="system")

    def test_2_rotate_makes_the_old_token_fail(self):
        old = self.add()
        output = self.run_command("rotate", name="system")
        new = re.search(r"Token: (\S+)", output).group(1)

        self.assertNotEqual(old, new)
        self.assertEqual((self.poll(old), self.poll(new)), (401, 200))
        self.assertIsNotNone(SmsDevice.objects.get().rotated_at)

    def test_3_disable_stops_the_phone(self):
        token = self.add()
        self.run_command("disable", name="system")

        self.assertEqual(self.poll(token), 401)
        self.assertFalse(SmsDevice.objects.get().is_active)
        self.assertIn("Already disabled", self.run_command("disable", name="system", logs=False))
        self.assertEqual(len(self.audit()), 2)

    def test_4_list_writes_no_audit_row_and_needs_no_actor(self):
        self.add()
        before = OperatorAuditEntry.objects.count()

        output = self.run_command("list", logs=False, actor=None)

        self.assertIn("system\tactive=True\tincoming=off\tsends=True\tlast_contact=never", output)
        self.assertEqual(OperatorAuditEntry.objects.count(), before)
        self.assertNotIn(SmsDevice.objects.get().token_hash, output)

    def test_4_list_with_no_phones(self):
        self.assertIn("No phones.", self.run_command("list", logs=False))

    @override_settings(SMS_INBOX_ENABLED=True)
    def test_5_every_other_action_writes_one_audit_row_with_the_actor(self):
        self.add()
        self.run_command("rotate", name="system")
        self.run_command("incoming", "on", name="system")
        self.run_command("disable", name="system")

        pk = str(SmsDevice.objects.get().pk)
        self.assertEqual(
            self.audit(),
            [
                ("sms_device_add", self.actor, "sms_device", pk, {}),
                ("sms_device_rotate", self.actor, "sms_device", pk, {}),
                ("sms_device_incoming", self.actor, "sms_device", pk, {"forwards_incoming": True}),
                ("sms_device_disable", self.actor, "sms_device", pk, {}),
            ],
        )

    def test_5_an_action_needs_a_staff_actor_and_a_known_phone(self):
        get_user_model().objects.create_user("visitor", password="pw")
        self.refused("--actor is required", "add", name="system", actor=None)
        self.refused("active staff user", "add", name="system", actor="visitor")
        self.refused("--name is required", "add", name=None)
        self.refused("--name is required", "add", name="has space")
        self.refused("No such phone", "rotate", name="missing")
        self.refused("incoming on", "incoming", name="system")
        self.refused("incoming on", "add", "on", name="system")
        self.assertEqual(SmsDevice.objects.count(), 0)
        self.assertEqual(OperatorAuditEntry.objects.count(), 0)

    @override_settings(SMS_INBOX_ENABLED=True)
    def test_7_incoming_on_and_off_set_the_switch_and_list_shows_it(self):
        self.add()

        self.run_command("incoming", "on", name="system")
        self.assertTrue(SmsDevice.objects.get().forwards_incoming)
        self.assertIn("incoming=on", self.run_command("list", logs=False))

        self.run_command("incoming", "off", name="system")
        self.assertFalse(SmsDevice.objects.get().forwards_incoming)
        self.assertIn("incoming=off", self.run_command("list", logs=False))
        details = [
            e.detail for e in OperatorAuditEntry.objects.filter(action="sms_device_incoming").order_by("pk")
        ]
        self.assertEqual(details, [{"forwards_incoming": True}, {"forwards_incoming": False}])

    def test_8_incoming_on_is_refused_while_the_inbox_is_off(self):
        self.add()
        entries = OperatorAuditEntry.objects.count()
        self.refused(
            "SMS_INBOX_ENABLED is off. Turn it on before a phone forwards texts.",
            "incoming", "on", name="system",
        )
        self.assertFalse(SmsDevice.objects.get().forwards_incoming)
        self.assertEqual(OperatorAuditEntry.objects.count(), entries)

    def test_9_incoming_off_works_while_the_inbox_is_off(self):
        self.add()
        SmsDevice.objects.update(forwards_incoming=True)
        self.run_command("incoming", "off", name="system")
        self.assertFalse(SmsDevice.objects.get().forwards_incoming)
        entry = OperatorAuditEntry.objects.get(action="sms_device_incoming")
        self.assertEqual((entry.operator, entry.detail), (self.actor, {"forwards_incoming": False}))

    @override_settings(SMS_INBOX_ENABLED=True)
    def test_10_incoming_on_says_what_is_stored(self):
        self.add()
        output = self.run_command("incoming", "on", name="system")
        self.assertIn("Texts this phone receives are now stored in the inbox, for 365 days.", output)
        self.assertNotIn("Nothing the phone forwards is stored", output)


class FakePhoneTests(SmsTestCase):
    def run_phone(self, **options):
        out, err = StringIO(), StringIO()
        call_command("sms_fake_phone", token="t", once=True, stdout=out, stderr=err, **options)
        return out.getvalue(), err.getvalue()

    def test_6_it_refuses_to_run_without_debug(self):
        with mock.patch.object(FakePhone, "_post") as post:
            with self.assertRaisesMessage(CommandError, "local development"):
                self.run_phone()
        post.assert_not_called()

    @override_settings(DEBUG=True)
    def test_6_with_debug_it_prints_each_text_and_reports_it_sent(self):
        answer = {
            "messages": [{"id": "sms_1", "to": "+639171234567", "text": "Your code is 1.", "gap": 0}],
            "wait": 5,
        }
        with mock.patch.object(FakePhone, "_post", side_effect=[answer, {}]) as post:
            out, _ = self.run_phone()

        self.assertIn("Your code is 1.", out)
        self.assertEqual(post.call_args_list[0].args[0], "poll")
        self.assertEqual(
            post.call_args_list[1].args,
            ("report", {"reports": [{"id": "sms_1", "status": "sent"}]}),
        )

    @override_settings(DEBUG=True)
    def test_6_a_server_that_is_down_is_reported_not_raised(self):
        with mock.patch.object(FakePhone, "_post", side_effect=OSError("refused")):
            _, err = self.run_phone()
        self.assertIn("Could not reach the server", err)
