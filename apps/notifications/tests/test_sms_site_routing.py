"""T-SITE: a Site's own phone, built dark. Which phone may take which text."""
import json
import time
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.inbox import bulk, storing
from apps.notifications import gateway, models as models_module, sms
from apps.notifications.models import SmsDevice, SmsMessage
from apps.notifications.services import send_sms
from apps.notifications.tests import test_sms_gateway as base
from apps.notifications.tests.base import (
    CODE_TEXT, MOBILE, STATUS_TEXT, fallback, gateway_on, make_phone,
)
from apps.sites.tests.base import make_account, make_site

S = SmsMessage.Status
SITE_TOKEN_A, SITE_TOKEN_B = "token-of-site-a", "token-of-site-b"

lines_on = override_settings(SMS_SITE_LINES_ENABLED=True)


def entitled(value=True):
    return mock.patch.object(sms, "site_has_line", return_value=value)


class RoutingCase(base.GatewayCase):
    def setUp(self):
        super().setUp()
        self.site_a = make_site(make_account("a@example.test"), "store-a", "Store A")
        self.site_b = make_site(make_account("b@example.test"), "store-b", "Store B")
        self.site_c = make_site(make_account("c@example.test"), "store-c", "Store C")
        self.phone_a = make_phone("line-a", SITE_TOKEN_A, site=self.site_a)
        self.phone_b = make_phone("line-b", SITE_TOKEN_B, site=self.site_b)

    def queue(self, **fields):
        """A text of a Site is an order-status text here: a code is never a Site phone's to
        carry (D1), so a Site's waiting text must not be one."""
        if fields.get("site") is not None:
            fields.setdefault("priority", base.P.STATUS)
        return super().queue(**fields)

    def state(self, message):
        message.refresh_from_db()
        return message.status, message.reason

    def handed(self, token):
        return {m["id"] for m in self.poll(token=token)["messages"]}

    def queue_all(self):
        self.platform = self.queue()
        self.text_a = self.queue(site=self.site_a)
        self.text_b = self.queue(site=self.site_b)
        self.text_c = self.queue(site=self.site_c)


class SwitchOffTodayUnchangedTests(RoutingCase):
    """The proof: with the switch off nothing behaves differently from before."""

    def test_the_system_phone_claims_exactly_as_now_including_a_sites_texts(self):
        self.queue_all()
        ids = self.handed(base.TOKEN)
        self.assertEqual(
            ids, {m.public_id for m in (self.platform, self.text_a, self.text_b, self.text_c)}
        )

    def test_site_phones_are_handed_nothing(self):
        self.queue_all()
        self.assertEqual(self.handed(SITE_TOKEN_A), set())
        self.assertEqual(self.handed(SITE_TOKEN_B), set())
        self.assertEqual(SmsMessage.objects.filter(status=S.QUEUED).count(), 4)

    def test_the_switch_on_but_nobody_entitled_is_the_same(self):
        self.queue_all()
        with lines_on:  # site_has_line is the stub: False
            self.assertEqual(self.handed(SITE_TOKEN_A), set())
            self.assertEqual(len(self.handed(base.TOKEN)), 4)

    def test_an_existing_phone_with_no_site_is_untouched(self):
        self.assertIsNone(self.phone.site_id)
        self.assertEqual(sms.pick_device(), self.phone)
        self.assertEqual(sms.line_for(self.site_a), self.phone)
        self.assertEqual(sms.line_for(None), self.phone)

    def test_a_second_platform_phone_is_still_handed_nothing(self):
        make_phone("spare", "token-of-the-spare-phone")
        message = self.queue()
        self.assertEqual(self.poll(token="token-of-the-spare-phone")["messages"], [])
        self.assertEqual(self.state(message), (S.QUEUED, ""))


@lines_on
class IsolationTests(RoutingCase):
    def test_1_each_phone_gets_only_its_own_sites_texts(self):
        self.queue_all()
        with entitled():
            self.assertEqual(self.handed(SITE_TOKEN_A), {self.text_a.public_id})
            self.assertEqual(self.handed(SITE_TOKEN_B), {self.text_b.public_id})
            # the platform phone: a platform text and the Site that has no phone, not A or B
            self.assertEqual(
                self.handed(base.TOKEN), {self.platform.public_id, self.text_c.public_id}
            )
        self.assertEqual(self.state(self.text_a)[0], S.DISPATCHED)
        self.assertEqual(SmsMessage.objects.get(pk=self.text_a.pk).device, self.phone_a)

    def test_a_platform_text_never_goes_to_a_site_phone(self):
        platform = self.queue()
        with entitled():
            self.assertEqual(self.handed(SITE_TOKEN_A), set())
        self.assertEqual(self.state(platform), (S.QUEUED, ""))

    def test_5_not_entitled_the_site_phone_gets_nothing_and_line_for_is_the_platform(self):
        text = self.queue(site=self.site_a)
        with entitled(False):
            self.assertEqual(self.handed(SITE_TOKEN_A), set())
            self.assertEqual(sms.line_for(self.site_a), self.phone)
            self.assertEqual(self.handed(base.TOKEN), {text.public_id})

    def test_line_for_gives_the_sites_own_active_phone(self):
        with entitled():
            self.assertEqual(sms.line_for(self.site_a), self.phone_a)
            self.assertEqual(sms.line_for(self.site_c), self.phone)
            self.assertEqual(sms.line_for(None), self.phone)
        SmsDevice.objects.filter(pk=self.phone_a.pk).update(is_active=False)
        with entitled():
            self.assertEqual(sms.line_for(self.site_a), self.phone)

    def test_the_waiting_query_alone_skips_other_sites_texts(self):
        self.queue_all()
        now = timezone.now()
        with entitled():
            own = gateway._waiting(self.phone_a, now)
            platform = gateway._waiting(self.phone, now)
        self.assertEqual([m.pk for m in own], [self.text_a.pk])
        self.assertEqual({m.pk for m in platform}, {self.platform.pk, self.text_c.pk})

    def test_the_claim_update_refuses_a_text_of_another_site(self):
        text_b = self.queue(site=self.site_b)
        with entitled(), mock.patch.object(gateway, "_waiting", return_value=[text_b]):
            handed = gateway._hand_out(self.phone_a, timezone.now())
        self.assertEqual(handed, [])
        self.assertEqual(self.state(text_b), (S.QUEUED, ""))

    def test_the_claim_update_refuses_a_lined_sites_text_to_the_platform_phone(self):
        text_a = self.queue(site=self.site_a)
        with entitled(), mock.patch.object(gateway, "_waiting", return_value=[text_a]):
            handed = gateway._hand_out(self.phone, timezone.now())
        self.assertEqual(handed, [])
        self.assertEqual(self.state(text_a), (S.QUEUED, ""))

    def test_a_site_phone_report_is_limited_to_its_own_texts(self):
        self.queue(site=self.site_a)
        with entitled():
            handed = self.poll(token=SITE_TOKEN_A)["messages"]
            other = self.queue(site=self.site_b)
            self.assertEqual(self.handed(base.TOKEN), set())
            response = self.report({"id": other.public_id, "status": "sent"}, token=SITE_TOKEN_A)
        self.assertEqual(len(handed), 1)
        self.assertEqual(response.status_code, 403)


@lines_on
class PlatformPhoneDownTests(RoutingCase):
    def test_4_a_disabled_platform_phone_does_not_promote_a_site_phone(self):
        SmsDevice.objects.filter(pk=self.phone.pk).update(is_active=False)
        platform = self.queue()
        site_text = self.queue(site=self.site_c)
        with entitled():
            self.assertIsNone(sms.pick_device())
            self.assertEqual(self.call("poll", token=base.TOKEN).status_code, 401)
            self.assertEqual(self.handed(SITE_TOKEN_A), set())
            self.assertEqual(self.handed(SITE_TOKEN_B), set())
            with self.assertLogs("apps.notifications", level="ERROR"):
                result = send_sms(to=MOBILE, purpose="mobile_verify", text=CODE_TEXT)
        self.assertEqual(result.outcome, sms.REFUSED)
        self.assertEqual(result.reason, "gateway_offline")
        self.assertEqual(self.state(platform), (S.QUEUED, ""))
        self.assertEqual(self.state(site_text), (S.QUEUED, ""))

    def test_a_site_text_with_its_phone_offline_is_refused_not_sent_by_the_platform_phone(self):
        SmsDevice.objects.filter(pk=self.phone_a.pk).update(
            last_contact_at=timezone.now() - timedelta(seconds=120)
        )
        with entitled():
            self.assertTrue(sms.gateway_online())  # the platform phone is up
            self.assertFalse(sms.gateway_online(self.site_a))
            result = send_sms(
                to=MOBILE, purpose="order_status", text=STATUS_TEXT, site=self.site_a,
                fallback=fallback(),
            )
        self.assertEqual(result.outcome, sms.EMAILED)
        self.assertEqual(result.reason, "gateway_offline")
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(SmsMessage.objects.filter(status=S.QUEUED).exists())

    def test_a_site_text_with_its_phone_online_is_queued_for_that_phone(self):
        with entitled():
            result = send_sms(
                to=MOBILE, purpose="order_status", text=STATUS_TEXT, site=self.site_a,
                fallback=fallback(),
            )
            self.assertEqual(result.outcome, sms.QUEUED)
            self.assertEqual(self.handed(base.TOKEN), set())
            self.assertEqual(self.handed(SITE_TOKEN_A), {result.message.public_id})


class SiteFieldTests(RoutingCase):
    def test_at_most_one_active_phone_per_site(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_phone("line-a2", "token-a2", site=self.site_a)
        SmsDevice.objects.filter(pk=self.phone_a.pk).update(is_active=False)
        make_phone("line-a2", "token-a2", site=self.site_a)  # the old one is disabled
        make_phone("spare-platform", "token-sp")  # platform phones are not limited this way

    def test_a_site_with_a_phone_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError):
            self.site_a.delete()


@override_settings(SMS_INBOX_ENABLED=True)
class IncomingTests(RoutingCase):
    def setUp(self):
        super().setUp()
        get_user_model().objects.create_user("coach", password="pw", is_staff=True)

    def test_11_a_site_phone_never_forwards_incoming(self):
        SmsDevice.objects.filter(pk=self.phone_a.pk).update(forwards_incoming=True)
        self.phone_a.refresh_from_db()
        self.assertFalse(storing.wanted(self.phone_a))
        SmsDevice.objects.filter(pk=self.phone.pk).update(forwards_incoming=True)
        self.phone.refresh_from_db()
        self.assertTrue(storing.wanted(self.phone))

    def test_11_a_site_phone_received_call_stores_nothing(self):
        SmsDevice.objects.filter(pk=self.phone_a.pk).update(forwards_incoming=True)
        with mock.patch.object(storing, "store") as store:
            response = self.call(
                "received", {"messages": [{"id": "1", "from": MOBILE, "text": "hi"}]},
                token=SITE_TOKEN_A,
            )
        self.assertEqual(response.status_code, 204)
        store.assert_not_called()

    def test_11_the_command_refuses_incoming_on_for_a_site_phone(self):
        with self.assertRaisesMessage(CommandError, "is not switched here"):
            call_command(
                "sms_device", "incoming", "on", name="line-a", actor="coach", stdout=StringIO()
            )
        self.phone_a.refresh_from_db()
        self.assertFalse(self.phone_a.forwards_incoming)

    def test_11_the_poll_answer_says_incoming_off_and_bulk_uses_the_platform_phone(self):
        SmsDevice.objects.filter(pk=self.phone_a.pk).update(forwards_incoming=True)
        self.assertFalse(self.poll(token=SITE_TOKEN_A)["incoming"])
        self.assertFalse(bulk._incoming_on())  # the platform phone is the one asked
        SmsDevice.objects.filter(pk=self.phone.pk).update(forwards_incoming=True)
        self.assertTrue(bulk._incoming_on())


class SiteColumnCheckTests(TestCase):
    def tearDown(self):
        models_module.forget_site_column_check()

    def test_a_found_column_is_remembered_and_a_missing_one_is_rechecked_later(self):
        models_module.forget_site_column_check()
        self.assertTrue(models_module.site_column_exists())  # the test database has it
        with mock.patch.object(models_module.connection, "cursor", side_effect=AssertionError):
            self.assertTrue(models_module.site_column_exists())  # remembered: no query
        models_module._SITE_COLUMN.update(found=False, checked=time.monotonic())
        with mock.patch.object(models_module.connection, "cursor", side_effect=AssertionError):
            self.assertFalse(models_module.site_column_exists())  # not looked up again yet
        models_module._SITE_COLUMN["checked"] = time.monotonic() - 31
        self.assertTrue(models_module.site_column_exists())  # looked up again, found


# --- Before notifications.0005 is applied ------------------------------------------------


@gateway_on
class BeforeTheMigrationTests(TransactionTestCase):
    """The code is deployed, the column is not there yet (Railway never migrates on deploy).
    The column is really dropped from the test database, and put back afterwards."""

    TOKEN = "token-of-the-system-phone"

    def setUp(self):
        cache.clear()
        declared = {"order_status": sms.Purpose(category=sms.STATUS, priority=sms.Priority.STATUS)}
        for patcher in (
            mock.patch.dict(sms.PURPOSES, declared),
            mock.patch("apps.operators.background.run_later"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.phone = make_phone("system", self.TOKEN)
        self.site = make_site(make_account("pre@example.test"), "store-pre", "Store Pre")
        self.field = SmsDevice._meta.get_field("site")
        self.constraint = SmsDevice._meta.constraints[0]
        SmsDevice._meta.constraints = []  # SQLite rebuilds the table from the model
        with connection.schema_editor() as editor:
            editor.remove_field(SmsDevice, self.field)
        self.addCleanup(self.restore_column)
        models_module.forget_site_column_check()

    def restore_column(self):
        with connection.schema_editor() as editor:
            editor.add_field(SmsDevice, self.field)
            SmsDevice._meta.constraints = [self.constraint]
            editor.add_constraint(SmsDevice, self.constraint)
        models_module.forget_site_column_check()

    def call(self, name, body=None):
        return self.client.post(
            f"/gateway/v1/{name}", data=json.dumps(body or {}), content_type="application/json",
            secure=True, HTTP_AUTHORIZATION=f"Bearer {self.TOKEN}", HTTP_X_REAL_IP="203.0.113.7",
        )

    def test_the_column_is_really_missing(self):
        self.assertFalse(models_module.site_column_exists())

    def test_every_phone_query_works_and_the_phone_has_no_site(self):
        self.assertEqual(sms.pick_device(), self.phone)
        self.assertIsNone(sms.pick_device().site_id)
        self.assertEqual(sms.line_for(self.site), self.phone)
        self.assertTrue(sms.gateway_online(self.site))
        self.assertEqual(str(SmsDevice.objects.get()), "system")

    def test_a_poll_hands_out_a_sites_text_exactly_as_today(self):
        message = SmsMessage.objects.create(
            recipient=MOBILE, category="code", purpose="test", priority=SmsMessage.Priority.CODE,
            expires_at=timezone.now() + timedelta(seconds=600), site=self.site,
        )
        sms.hold_text(message, CODE_TEXT, 600)
        response = self.call("poll", {"battery": 80})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([m["id"] for m in response.json()["messages"]], [message.public_id])
        self.phone.refresh_from_db()
        self.assertEqual(self.phone.battery_percent, 80)

    def test_the_switch_on_changes_nothing_either(self):
        with lines_on, entitled():
            self.assertEqual(sms.line_for(self.site), self.phone)
            self.assertEqual(sms.claimable(self.phone).children, [])
            self.assertEqual(self.call("poll").status_code, 200)

    def test_send_sms_queues_for_the_platform_phone(self):
        result = send_sms(to=MOBILE, purpose="mobile_verify", text=CODE_TEXT)
        self.assertEqual(result.outcome, sms.QUEUED)

    def test_the_received_call_and_wanted_work(self):
        self.assertTrue(storing.wanted(self.phone) in (True, False))
        self.assertEqual(self.call("received", {"messages": []}).status_code, 204)
