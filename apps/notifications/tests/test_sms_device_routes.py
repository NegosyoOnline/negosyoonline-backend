"""The HQ routes under /staff/v1/sms/device/: the platform phone's register, rotate, disable and
status. The shared functions in apps.notifications.devices are what the CLI calls too."""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, override_settings
from django.utils import timezone

from apps.notifications import devices, sms
from apps.notifications.models import SmsDevice
from apps.notifications.tests.base import gateway_on
from apps.operators import two_step
from apps.operators.models import OperatorAuditEntry
from apps.operators.tests.test_staff_routes import StaffTestCase, on_staff

BASE = "/staff/v1/sms/device/"
PATHS = ("get/", "register/", "rotate/", "disable/")
URL = "https://api.example.test"
mgmt_on = override_settings(SMS_GATEWAY_MGMT_ENABLED=True, SMS_GATEWAY_PUBLIC_URL=URL)


def poll(token):
    return Client().post(
        "/gateway/v1/poll", data="{}", content_type="application/json", secure=True,
        HTTP_AUTHORIZATION=f"Bearer {token}",
    ).status_code


@on_staff
@gateway_on
@mgmt_on
class SmsDeviceRouteTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.identity = self.enrolled()

    def dev(self, path, data=None, **kwargs):
        return self.call("POST", BASE + path, data, **kwargs)

    def signed_in(self):
        self.staff_sign_in()

    def fresh(self):
        """An authenticator code the second step has not used yet."""
        self.identity.refresh_from_db()
        self.identity.totp_last_step = 0
        self.identity.save(update_fields=["totp_last_step"])
        return self.code()

    def register(self, name="system"):
        response = self.dev("register/", {"name": name, "confirm": True})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["token"]

    def audit(self, action):
        return list(OperatorAuditEntry.objects.filter(action=action).order_by("pk"))

    # Switches, session, headers

    def test_every_route_is_404_with_either_switch_off(self):
        self.signed_in()
        for flags in ({"SMS_GATEWAY_MGMT_ENABLED": False}, {"SMS_GATEWAY_ENABLED": False}):
            with override_settings(**flags):
                for path in PATHS:
                    self.assertError(
                        self.dev(path, {"name": "system", "confirm": True}), 404, "not_found"
                    )
        self.assertFalse(SmsDevice.objects.exists())
        self.assertEqual(OperatorAuditEntry.objects.filter(action__startswith="sms_device").count(), 0)

    def test_no_session_and_a_session_before_the_second_step_are_refused(self):
        for path in PATHS:
            self.assertError(self.dev(path, {}), 401, "unauthenticated")
        self.staff_email_step()
        for path in PATHS:
            self.assertError(self.dev(path, {}), 401, "second_step_required")
        self.assertFalse(SmsDevice.objects.exists())

    def test_another_origin_is_refused(self):
        for path in PATHS:
            self.assertError(self.dev(path, {}, origin="https://evil.example"), 403, "origin_not_allowed")

    def test_every_answer_is_no_store(self):
        self.signed_in()
        for path in PATHS:
            self.assertEqual(self.dev(path, {})["Cache-Control"], "no-store")
        token_answer = self.dev("register/", {"name": "system", "confirm": True})
        self.assertEqual(token_answer["Cache-Control"], "no-store")

    # Get and register

    def test_get_with_no_phone_and_the_server_address(self):
        self.signed_in()
        self.assertEqual(self.dev("get/").json(), {"server_address": URL, "device": None})
        with override_settings(SMS_GATEWAY_PUBLIC_URL=""):
            self.assertEqual(self.dev("get/").json()["server_address"], "")

    def test_register_needs_confirm_and_a_good_name(self):
        self.signed_in()
        self.assertError(self.dev("register/", {"name": "system"}), 400, "confirm_required")
        self.assertError(self.dev("register/", {"name": "Bad Name!", "confirm": True}), 400, "invalid_name")
        self.assertError(self.dev("register/", {"name": 5, "confirm": True}), 400, "invalid_name")
        self.assertFalse(SmsDevice.objects.exists())

    def test_7_register_shows_the_token_once_and_keeps_only_the_hash(self):
        self.signed_in()
        with self.assertLogs(level="DEBUG") as logs:
            response = self.dev("register/", {"name": "system", "confirm": True})
        token = response.json()["token"]
        device = SmsDevice.objects.get()
        self.assertEqual(device.token_hash, sms.token_hash(token))
        self.assertEqual(response.content.decode().count(token), 1)
        self.assertEqual(response.json()["device"]["connection"], "waiting")
        self.assertNotIn(token, "\n".join(logs.output))
        self.assertNotIn(device.token_hash, response.content.decode())
        entry = self.audit("sms_device_add")[0]
        self.assertEqual(entry.detail, {"device": device.pk, "name": "system", "via": "hq"})
        self.assertNotIn(token, str(entry.detail))
        later = self.dev("get/")
        self.assertNotIn(token, later.content.decode())
        self.assertNotIn("token", later.json()["device"])
        self.assertNotIn(device.token_hash, later.content.decode())
        self.assertEqual(poll(token), 200)

    def test_register_is_409_while_a_platform_phone_exists(self):
        self.signed_in()
        self.register()
        self.assertError(self.dev("register/", {"name": "other", "confirm": True}), 409, "phone_exists")
        self.assertEqual(SmsDevice.objects.count(), 1)

    def test_11_register_and_rotate_never_turn_on_forwarding(self):
        self.signed_in()
        self.register()
        self.dev("rotate/", {"name": "system", "confirm": True, "code": self.fresh()})
        self.assertFalse(SmsDevice.objects.get().forwards_incoming)
        self.assertFalse(self.dev("get/").json()["device"]["forwards_incoming"])

    # Status

    def test_10_status_follows_the_polls(self):
        self.signed_in()
        self.assertIsNone(devices.status())
        self.assertEqual(devices.connection(None), "no_phone")
        token = self.register()
        status = lambda: self.dev("get/").json()["device"]["connection"]
        self.assertEqual(status(), "waiting")
        self.assertEqual(poll("not-the-token"), 401)
        self.assertEqual(status(), "waiting")
        self.assertEqual(poll(token), 200)
        self.assertEqual(status(), "connected")
        long_ago = timezone.now() - timedelta(minutes=5)
        SmsDevice.objects.update(created_at=long_ago - timedelta(minutes=1), last_contact_at=long_ago)
        self.assertEqual(status(), "offline")
        self.assertEqual(poll(token), 200)
        self.assertEqual(status(), "connected")
        response = self.dev("rotate/", {"name": "system", "confirm": True, "code": self.fresh()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(status(), "waiting")
        self.assertEqual(poll(token), 401)
        self.assertEqual(status(), "waiting")
        self.assertEqual(poll(response.json()["token"]), 200)
        self.assertEqual(status(), "connected")

    def test_10_a_received_call_does_not_count_as_a_poll(self):
        self.signed_in()
        token = self.register()
        Client().post(
            "/gateway/v1/received", data="{}", content_type="application/json", secure=True,
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(self.dev("get/").json()["device"]["connection"], "waiting")

    # Rotate

    def test_6_rotate_makes_the_old_token_fail_and_the_new_one_work(self):
        self.signed_in()
        old = self.register()
        response = self.dev("rotate/", {"name": "system", "confirm": True, "code": self.fresh()})
        new = response.json()["token"]
        device = SmsDevice.objects.get()
        self.assertNotEqual(old, new)
        self.assertEqual(device.token_hash, sms.token_hash(new))
        self.assertIsNotNone(device.rotated_at)
        self.assertEqual(poll(old), 401)
        self.assertEqual(poll(new), 200)
        entry = self.audit("sms_device_rotate")[0]
        self.assertEqual(entry.detail, {"device": device.pk, "name": "system", "via": "hq"})
        self.assertNotIn(new, str(entry.detail) + str(entry.personal))
        self.assertNotIn(new, self.dev("get/").content.decode())

    def test_8_rotate_and_disable_refuse_every_bad_code_the_same_way(self):
        self.signed_in()
        self.register()
        before = SmsDevice.objects.get().token_hash
        used = self.fresh()
        self.assertEqual(self.dev("rotate/", {"name": "system", "confirm": True, "code": used}).status_code, 200)
        after = SmsDevice.objects.get().token_hash
        bad = {
            "none": None,
            "wrong": self.wrong_code(),
            "reused": used,
            "recovery": self.recovery_codes[0],
            "short": "123",
            "number": 123456,
        }
        messages = set()
        for path in ("rotate/", "disable/"):
            for code in bad.values():
                body = {"name": "system", "confirm": True}
                if code is not None:
                    body["code"] = code
                response = self.dev(path, body)
                self.assertError(response, 403, "reauth_failed")
                messages.add(response.json()["error"]["message"])
        self.assertEqual(len(messages), 1)
        device = SmsDevice.objects.get()
        self.assertEqual(device.token_hash, after)
        self.assertNotEqual(before, after)
        self.assertTrue(device.is_active)
        self.assertEqual(len(self.audit("sms_device_reauth_failed")), 2 * len(bad))
        for entry in self.audit("sms_device_reauth_failed"):
            self.assertEqual(set(entry.detail), {"failed_attempts", "locked"})
        self.assertTrue(all(c.used_at is None for c in self.identity.recovery_codes.all()))

    def test_8_five_wrong_codes_lock_and_a_right_code_is_then_refused_the_same(self):
        self.signed_in()
        self.register()
        wrong = self.wrong_code()
        for _ in range(5):
            self.assertError(
                self.dev("rotate/", {"name": "system", "confirm": True, "code": wrong}),
                403, "reauth_failed",
            )
        self.assertTrue(self.audit("sms_device_reauth_failed")[-1].detail["locked"])
        self.identity.refresh_from_db()
        self.assertIsNotNone(self.identity.locked_until)
        self.assertFalse(two_step.verify_fresh_totp(self.identity.pk, self.fresh(), surface="hq").ok)

    def test_rotate_and_disable_need_confirm_and_the_platform_phone_name(self):
        self.signed_in()
        self.register()
        code = self.fresh()
        for path in ("rotate/", "disable/"):
            self.assertError(self.dev(path, {"name": "system", "code": code}), 400, "confirm_required")
            self.assertError(
                self.dev(path, {"name": "other", "confirm": True, "code": code}), 404, "not_found"
            )
            self.assertError(self.dev(path, {"confirm": True, "code": code}), 400, "invalid_name")
        self.assertEqual(self.audit("sms_device_reauth_failed"), [])

    # Disable

    def test_9_disable_stops_the_phone_and_rotate_does_not_re_enable_it(self):
        self.signed_in()
        token = self.register()
        response = self.dev("disable/", {"name": "system", "confirm": True, "code": self.fresh()})
        self.assertEqual(response.json()["device"]["connection"], "disabled")
        self.assertFalse(response.json()["device"]["enabled"])
        self.assertNotIn("token", response.json())
        self.assertEqual(poll(token), 401)
        entry = self.audit("sms_device_disable")[0]
        self.assertEqual(entry.detail["via"], "hq")
        self.assertEqual(self.dev("get/").json()["device"]["connection"], "disabled")
        self.assertError(
            self.dev("rotate/", {"name": "system", "confirm": True, "code": self.fresh()}),
            409, "device_disabled",
        )
        self.assertFalse(SmsDevice.objects.get().is_active)
        # Disabling again changes nothing and writes no second row.
        again = self.dev("disable/", {"name": "system", "confirm": True, "code": self.fresh()})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(len(self.audit("sms_device_disable")), 1)
        # A new phone can then be registered.
        self.register("system-2")
        self.assertEqual(self.dev("get/").json()["device"]["name"], "system-2")

    def test_the_wrapper_refuses_a_recovery_code_without_counting_it(self):
        self.signed_in()
        result = two_step.verify_fresh_totp(self.identity.pk, self.recovery_codes[0], surface="hq")
        self.assertFalse(result.ok)
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.failed_attempts, 0)
        self.assertTrue(two_step.verify_fresh_totp(self.identity.pk, self.fresh(), surface="hq").ok)
