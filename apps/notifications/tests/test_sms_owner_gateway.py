"""SMS-LINE step 5: the owner's SMS Gateway API, built dark. Isolation first: a phone paired
through the owner API gets only its own Site's texts, and one Site can never reach another's
phone or the platform's."""
import json
import re
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts.models import AccountAuditEvent, AccountMobile
from apps.notifications import devices, sms
from apps.notifications.models import SmsDevice, SmsMessage
from apps.notifications.tests.base import MOBILE, gateway_on, make_phone
from apps.operators.models import OperatorAuditEntry
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase

S = SmsMessage.Status
LINE_EVENTS = ("sms_line_register", "sms_line_rotate", "sms_line_disable")
PLATFORM_TOKEN = "platform-phone-token"
switched_on = override_settings(
    SMS_SITE_LINES_ENABLED=True, ACCOUNT_EMAIL_CHANGE_HARDENED=True,
    SMS_GATEWAY_PUBLIC_URL="http://127.0.0.1:8000",
)


@gateway_on
@switched_on
class OwnerCase(SitesApiTestCase):
    """Ana (self.client) owns site A; Ben owns site B. Both Sites have a line."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.no_line = set()
        for patcher in (
            mock.patch("apps.operators.background.run_later"),
            mock.patch.object(
                sms, "site_has_line", side_effect=lambda site: site.pk not in self.no_line
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ben = self.other_client()
        self.site_a = Site.objects.get(public_id=self.create_site()["id"])
        made = self.create_site(client=self.ben, subdomain="bens-store", display_name="Ben's")
        self.site_b = Site.objects.get(public_id=made["id"])
        self.platform = make_phone("system", PLATFORM_TOKEN)

    # --- helpers
    def path(self, site, tail=""):
        return f"/v1/sites/{site.public_id}/sms-gateway/{tail}"

    def pair(self, site, client=None):
        response = self.send("post", self.path(site, "register/"), client=client)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["token"]

    def step_up_code(self, client=None):
        mail.outbox.clear()
        cache.clear()  # the code's resend wait
        response = self.send(
            "post", "/v1/account/step-up/", {"channel": "email", "purpose": "sms_line_rotate"},
            client=client,
        )
        self.assertEqual(response.status_code, 204, response.content)
        return self.last_code()

    def rotate(self, site, code, client=None):
        return self.send("post", self.path(site, "rotate/"), {"step_up_code": code}, client=client)

    def call(self, name, token, body=None):
        headers = {"HTTP_X_REAL_IP": "203.0.113.7"}
        if token is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return Client().post(
            f"/gateway/v1/{name}", data=json.dumps(body or {}), content_type="application/json",
            secure=True, **headers,
        )

    def poll(self, token):
        response = self.call("poll", token)
        self.assertEqual(response.status_code, 200, response.content)
        return {m["id"] for m in response.json()["messages"]}

    def queue(self, site=None, code=False):
        message = SmsMessage.objects.create(
            recipient=MOBILE, category="code" if code else "status", purpose="test", site=site,
            priority=SmsMessage.Priority.CODE if code else SmsMessage.Priority.STATUS,
            expires_at=timezone.now() + timedelta(seconds=600),
        )
        sms.hold_text(message, "Your order 1042 is on its way.", 600)
        return message

    def all_routes(self, client=None):
        path = self.path(self.site_a)
        return [
            self.get(path, client=client),
            self.send("post", path + "register/", client=client),
            self.send("post", path + "rotate/", {"step_up_code": "123456"}, client=client),
            self.send("post", path + "disable/", client=client),
        ]


class IsolationTests(OwnerCase):
    def test_1_each_paired_phone_is_handed_only_its_own_sites_texts(self):
        token_a, token_b = self.pair(self.site_a), self.pair(self.site_b, self.ben)
        plain, text_a, text_b = self.queue(), self.queue(self.site_a), self.queue(self.site_b)
        self.assertEqual(self.poll(token_a), {text_a.public_id})
        self.assertEqual(self.poll(token_b), {text_b.public_id})
        # The platform phone gets the platform text, never a text of a Site with its own phone.
        self.assertEqual(self.poll(PLATFORM_TOKEN), {plain.public_id})
        text_a.refresh_from_db()
        text_b.refresh_from_db()
        self.assertEqual(text_a.device.site_id, self.site_a.pk)
        self.assertEqual(text_b.device.site_id, self.site_b.pk)

    def test_1b_a_site_phone_is_never_handed_a_code_or_another_sites_text(self):
        token_a = self.pair(self.site_a)
        self.queue(self.site_a, code=True)
        self.queue(self.site_b)
        self.queue()
        self.assertEqual(self.poll(token_a), set())

    def test_1c_a_site_without_a_phone_uses_the_platform_phone(self):
        text_b = self.queue(self.site_b)
        self.assertEqual(self.poll(PLATFORM_TOKEN), {text_b.public_id})

    def test_2_another_account_cannot_view_register_rotate_or_disable_a_phone(self):
        self.pair(self.site_a)
        columns = ("pk", "token_hash", "is_active")
        before = list(SmsDevice.objects.order_by("pk").values_list(*columns))
        code = self.step_up_code(client=self.ben)
        for response in (
            self.get(self.path(self.site_a), client=self.ben),
            self.send("post", self.path(self.site_a, "register/"), client=self.ben),
            self.rotate(self.site_a, code, client=self.ben),
            self.send("post", self.path(self.site_a, "disable/"), client=self.ben),
        ):
            self.error(response, 404, "not_found")
        self.assertEqual(before, list(SmsDevice.objects.order_by("pk").values_list(*columns)))

    def test_2b_a_foreign_and_an_unknown_site_answer_the_same_404(self):
        foreign = self.get(self.path(self.site_a), client=self.ben)
        unknown = self.get("/v1/sites/site_doesnotexist/sms-gateway/", client=self.ben)
        self.error(foreign, 404, "not_found")
        self.error(unknown, 404, "not_found")
        self.assertEqual(foreign.json(), unknown.json())

    def test_2c_no_device_id_or_name_is_taken_from_the_body(self):
        self.pair(self.site_a)
        other = make_phone("line-b", "b-token", site=self.site_b)
        code = self.step_up_code()
        for body in (
            {"step_up_code": code, "device": other.pk}, {"step_up_code": code, "name": "line-b"},
        ):
            self.error(self.send("post", self.path(self.site_a, "rotate/"), body), 400, "invalid_request")
        # Disable ignores the body: only A's own phone is ever touched.
        self.send("post", self.path(self.site_a, "disable/"), {"device": other.pk, "name": other.name})
        other.refresh_from_db()
        self.assertTrue(other.is_active)
        self.assertEqual(other.token_hash, sms.token_hash("b-token"))
        self.assertFalse(SmsDevice.objects.get(site=self.site_a).is_active)

    def test_3_the_platform_phone_is_never_touched_or_shown_by_a_site_route(self):
        platform = self.platform.token_hash
        self.pair(self.site_a)
        self.rotate(self.site_a, self.step_up_code())
        self.send("post", self.path(self.site_a, "disable/"))
        self.platform.refresh_from_db()
        self.assertEqual((self.platform.token_hash, self.platform.is_active), (platform, True))
        self.assertIsNone(self.get(self.path(self.site_b), client=self.ben).json()["device"])

    def test_3b_the_hq_phone_functions_never_pick_up_a_site_phone(self):
        SmsDevice.objects.filter(pk=self.platform.pk).update(is_active=False)
        make_phone("line-a", "a-token", site=self.site_a)
        self.assertEqual(devices.platform_phone().pk, self.platform.pk)
        self.assertIsNone(sms.pick_device())

    def test_4_a_disabled_platform_phone_does_not_promote_a_site_phone(self):
        token_a = self.pair(self.site_a)
        SmsDevice.objects.filter(pk=self.platform.pk).update(is_active=False)
        plain = self.queue()
        self.assertEqual(self.poll(token_a), set())
        plain.refresh_from_db()
        self.assertEqual(plain.status, S.QUEUED)

    def test_5_a_site_without_a_line_gets_nothing_and_the_routes_404(self):
        token_a = self.pair(self.site_a)
        text_a = self.queue(self.site_a)
        self.no_line.add(self.site_a.pk)
        self.assertEqual(self.poll(token_a), set())
        for response in self.all_routes():
            self.error(response, 404, "not_found")
        self.no_line.clear()
        self.assertEqual(self.poll(token_a), {text_a.public_id})


class SwitchTests(OwnerCase):
    def test_13_every_route_404s_with_the_site_lines_switch_off_and_writes_nothing(self):
        with override_settings(SMS_SITE_LINES_ENABLED=False):
            for response in self.all_routes():
                self.error(response, 404, "not_found")
            self.client.cookies.clear()  # even signed out
            for response in self.all_routes():
                self.error(response, 404, "not_found")
        self.assertEqual(SmsDevice.objects.filter(site__isnull=False).count(), 0)

    def test_13b_every_route_404s_with_the_gateway_off(self):
        with override_settings(SMS_GATEWAY_ENABLED=False):
            for response in self.all_routes():
                self.error(response, 404, "not_found")

    def test_signed_out_is_401_and_a_non_sante_site_is_404(self):
        self.client.cookies.clear()
        for response in self.all_routes():
            self.error(response, 401, "unauthenticated")
        self.sign_in()
        Site.objects.filter(pk=self.site_a.pk).update(template="other")
        for response in self.all_routes():
            self.error(response, 404, "not_found")

    def test_a_billing_only_site_is_404_not_403(self):
        self.no_line.add(self.site_a.pk)
        with mock.patch("apps.sites.api.billing.position") as position:
            position.return_value.billing_only = True
            for response in self.all_routes():
                self.error(response, 404, "not_found")


class PairingTests(OwnerCase):
    def test_7_register_returns_the_token_once_and_keeps_only_its_hash(self):
        response = self.send("post", self.path(self.site_a, "register/"))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response["Cache-Control"], "no-store")
        body = response.json()
        token = body["token"]
        self.assertEqual(body["server_address"], "http://127.0.0.1:8000")
        self.assertEqual(body["device"]["connection"], "waiting")
        device = SmsDevice.objects.get(site=self.site_a)
        self.assertEqual(device.token_hash, sms.token_hash(token))
        self.assertNotIn(token, json.dumps(list(SmsDevice.objects.values()), default=str))
        later = self.get(self.path(self.site_a))
        self.assertEqual(later["Cache-Control"], "no-store")
        self.assertNotIn("token", later.json())
        self.assertNotIn(token, later.content.decode())
        self.assertNotIn(device.token_hash, later.content.decode())

    def test_a_second_register_is_409_and_a_disabled_phone_can_be_replaced(self):
        first = self.pair(self.site_a)
        self.error(self.send("post", self.path(self.site_a, "register/")), 409, "phone_exists")
        self.assertEqual(self.send("post", self.path(self.site_a, "disable/")).status_code, 200)
        second = self.pair(self.site_a)
        self.assertNotEqual(first, second)
        self.assertEqual(SmsDevice.objects.filter(site=self.site_a).count(), 2)
        self.assertEqual(SmsDevice.objects.filter(site=self.site_a, is_active=True).count(), 1)

    def test_7b_the_audit_and_the_logs_never_hold_a_token_or_a_hash(self):
        with self.assertLogs("apps.notifications.devices", level="ERROR") as logs:
            token = self.pair(self.site_a)
            rotated = self.rotate(self.site_a, self.step_up_code()).json()["token"]
            self.send("post", self.path(self.site_a, "disable/"))
        events = AccountAuditEvent.objects.filter(event__in=LINE_EVENTS).order_by("pk")
        self.assertEqual(
            [e.event for e in events], ["sms_line_register", "sms_line_rotate", "sms_line_disable"]
        )
        device = SmsDevice.objects.get(site=self.site_a)
        for event in events:
            self.assertEqual(event.detail, {"site": self.site_a.public_id, "device": device.pk})
        blob = json.dumps([e.detail for e in AccountAuditEvent.objects.all()], default=str)
        blob += "".join(logs.output)
        blob += json.dumps(list(OperatorAuditEntry.objects.values()), default=str)
        for secret in (token, rotated, sms.token_hash(token), sms.token_hash(rotated)):
            self.assertNotIn(secret, blob)

    def test_an_error_body_never_holds_the_token(self):
        token = self.pair(self.site_a)
        response = self.rotate(self.site_a, "000000")
        self.assertNotIn(token, response.content.decode())


class RotateTests(OwnerCase):
    def test_6_the_old_token_gets_the_401_everywhere_and_the_new_one_works(self):
        old = self.pair(self.site_a)
        response = self.rotate(self.site_a, self.step_up_code())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Cache-Control"], "no-store")
        new = response.json()["token"]
        wrong = self.call("poll", "a-wrong-token")
        for name, body in (("poll", {}), ("report", {"reports": []}), ("received", {"messages": []})):
            stale = self.call(name, old, body)
            self.assertEqual(stale.status_code, 401)
            self.assertEqual(stale.json(), wrong.json())
            self.assertIn(self.call(name, new, body).status_code, (200, 204))

    def test_8_rotate_needs_a_fresh_step_up_code(self):
        self.pair(self.site_a)
        for code in ("", "123456"):
            self.error(self.rotate(self.site_a, code), 400, "step_up_required")
        code = self.step_up_code()
        self.assertEqual(self.rotate(self.site_a, code).status_code, 200)
        self.error(self.rotate(self.site_a, code), 400, "step_up_required")  # used up

    def test_8b_a_code_for_another_purpose_or_account_is_refused(self):
        self.pair(self.site_a)
        mail.outbox.clear()
        self.post("/v1/account/step-up/", {"channel": "email", "purpose": "email_change"})
        self.error(self.rotate(self.site_a, self.last_code()), 400, "step_up_required")
        ben_code = self.step_up_code(client=self.ben)
        self.error(self.rotate(self.site_a, ben_code), 400, "step_up_required")

    def test_8c_a_code_from_the_verified_mobile_is_accepted(self):
        AccountMobile.objects.create(account=self.account, number=MOBILE, verified_at=timezone.now())
        self.pair(self.site_a)
        self.post("/v1/account/step-up/", {"channel": "mobile", "purpose": "sms_line_rotate"})
        text = sms.take_text(SmsMessage.objects.latest("pk"))
        code = re.search(r"\b(\d{6})\b", text).group(1)
        self.assertEqual(self.rotate(self.site_a, code).status_code, 200)

    def test_rotate_without_a_phone_or_on_a_disabled_phone_is_409_and_never_revives_it(self):
        self.error(self.rotate(self.site_a, self.step_up_code()), 409, "no_phone")
        token = self.pair(self.site_a)
        self.send("post", self.path(self.site_a, "disable/"))
        self.error(self.rotate(self.site_a, self.step_up_code()), 409, "phone_disabled")
        self.assertFalse(SmsDevice.objects.get(site=self.site_a).is_active)
        self.assertEqual(self.call("poll", token).status_code, 401)


class StatusTests(OwnerCase):
    def status(self, site=None):
        return self.get(self.path(site or self.site_a)).json()["device"]

    def test_10_status_follows_the_phone(self):
        self.assertIsNone(self.status())
        token = self.pair(self.site_a)
        self.assertEqual(self.status()["connection"], "waiting")
        self.call("poll", "wrong-token")
        self.assertEqual(self.status()["connection"], "waiting")  # a bad poll changes nothing
        self.call("received", token, {"messages": []})
        self.assertEqual(self.status()["connection"], "waiting")  # received does not count
        self.assertEqual(self.poll(token), set())
        self.assertEqual(self.status()["connection"], "connected")
        SmsDevice.objects.filter(site=self.site_a).update(
            created_at=timezone.now() - timedelta(minutes=5),
            last_contact_at=timezone.now() - timedelta(seconds=61),
        )
        self.assertEqual(self.status()["connection"], "offline")
        self.rotate(self.site_a, self.step_up_code())
        self.assertEqual(self.status()["connection"], "waiting")

    def test_9_a_disabled_phone_gets_the_401_on_all_three_calls_and_shows_disabled(self):
        token = self.pair(self.site_a)
        response = self.send("post", self.path(self.site_a, "disable/"))
        self.assertEqual(response.json()["device"]["connection"], "disabled")
        for name, body in (("poll", {}), ("report", {"reports": []}), ("received", {"messages": []})):
            self.assertEqual(self.call(name, token, body).status_code, 401)
        self.assertEqual(self.status()["connection"], "disabled")
        self.error(self.send("post", self.path(self.site_a, "disable/")), 409, "no_phone")

    def test_a_site_sees_only_its_own_phone(self):
        self.pair(self.site_a)
        self.pair(self.site_b, self.ben)
        self.assertEqual(self.status()["name"], SmsDevice.objects.get(site=self.site_a).name)
        mine = self.get(self.path(self.site_b), client=self.ben).json()["device"]["name"]
        self.assertEqual(mine, SmsDevice.objects.get(site=self.site_b).name)

    def test_11_forwarding_stays_separate_and_never_turns_on_for_a_site_phone(self):
        self.pair(self.site_a)
        self.rotate(self.site_a, self.step_up_code())
        self.assertFalse(SmsDevice.objects.get(site=self.site_a).forwards_incoming)
        self.platform.refresh_from_db()
        self.assertFalse(self.platform.forwards_incoming)
        self.assertFalse(self.status()["forwards_incoming"])
