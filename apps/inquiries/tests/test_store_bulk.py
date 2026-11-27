"""STORE-BULK BE: a store's bulk text to its own members from its own paired phone. The switches and
their prerequisites, the audience and what is left out, the text rules (no link, no footer), the test
text, confirm, the paced one-at-a-time feed (window, cap, per-member re-checks), stop, a hold or
Billing-only end, the history's masking, tenancy, retention and the audit entries."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import AccountAuditEvent, AccountMobile
from apps.common import retention
from apps.inbox.models import Contact, ContactGroup
from apps.inbox.tests.base import inbox_on
from apps.inquiries import retention as inquiry_retention
from apps.inquiries import store_bulk
from apps.inquiries.models import InquirySmsOptOut, InquirySmsSettings
from apps.inquiries.tests.test_sms_inbox import MOBILE, OTHER_MOBILE, make_member
from apps.inquiries.tests.test_stored import StoredOwnerTestCase
from apps.members.models import Member
from apps.notifications import services, sms
from apps.notifications.models import SmsMessage, SmsPolicy, StoreBulkBatch, StoreBulkRecipient
from apps.notifications.tests.base import gateway_on, make_phone, online_phone, set_policy
from apps.sites.models import Site

MANILA = ZoneInfo("Asia/Manila")
TOKEN = "token-store-bulk-phone"
OWN_MOBILE = "+639175550000"
TEXT = "Sale this weekend, 20 percent off."
FULL = TEXT  # nothing is added in front of the owner's text
DAY = datetime(2026, 11, 24, 10, 0, tzinfo=MANILA)  # inside the window
NIGHT = datetime(2026, 11, 24, 22, 30, tzinfo=MANILA)  # outside it
NUMBERS = ["+639171000001", "+639171000002", "+639171000003"]


def at(hour, minute=0):
    return datetime(2026, 11, 24, hour, minute, tzinfo=MANILA)


@inbox_on
@gateway_on
@override_settings(INQUIRY_SMS_ENABLED=True, SMS_SITE_LINES_ENABLED=True, STORE_BULK_SMS_ENABLED=True, STORE_CONTACTS_ENABLED=True)
class BulkCase(StoredOwnerTestCase):
    mirror_members = True  # each member's number is also a contact in one group; the pick tests switch it off

    def setUp(self):
        super().setUp()
        cache.clear()
        for target in ("apps.operators.background.run_later",):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        line = mock.patch.object(sms, "site_has_line", return_value=True)
        self.has_line = line.start()
        self.addCleanup(line.stop)
        self.platform = online_phone(forwards_incoming=True)
        self.line = make_phone("store-line", TOKEN, site=self.site)
        InquirySmsSettings.objects.create(site=self.site, enabled=True)
        InquirySmsSettings.objects.create(site=self.ben_site, enabled=True)
        AccountMobile.objects.create(account=self.account, number=OWN_MOBILE, verified_at=timezone.now())
        self.members = [make_member(self.site, mobile, name=f"Member {i}") for i, mobile in enumerate(NUMBERS)]
        self.root = f"{self.base}sms/bulk/"
        # "all" is All groups (ALL-GROUPS-OPTION, 2026-11-25): each member's number is also a contact in one group
        self.everyone = ContactGroup.objects.create(owner_kind="site", site=self.site, name="Everyone")
        self.contacts = []
        if self.mirror_members:
            self.contacts = [self.group_contact(self.site, m.mobile, f"Member {i}") for i, m in enumerate(self.members)]

    # -- helpers
    def group_contact(self, site, mobile, first, last="", group=None):
        contact = Contact.objects.create(
            owner_kind="site", site=site, first_name=first, last_name=last, mobile=mobile, created_by_account=self.account)
        group = group or (self.everyone if site.pk == self.site.pk else ContactGroup.objects.get_or_create(
            owner_kind="site", site=site, name="Everyone")[0])
        contact.groups.add(group)
        return contact

    def status(self, **kw):
        return self.get(self.root + "status/", **kw)

    def audience(self, audience="all"):
        return self.send("post", self.root + "audience/", {"audience": audience})

    def preview(self, text=TEXT):
        return self.send("post", self.root + "preview/", {"text": text})

    def test(self, text=TEXT, key="key-test-0001"):
        return self.send("post", self.root + "test/", {"text": text, "idempotency_key": key})

    def confirm(self, key="key-batch-0001", audience="all", text=TEXT, **extra):
        body = {"text": text, "audience": audience, "idempotency_key": key, "confirm": True, **extra}
        return self.send("post", self.root + "batches/", body)

    def start(self, **kw):
        response = self.confirm(**kw)
        self.assertEqual(response.status_code, 201, response.content)
        return StoreBulkBatch.objects.get(public_id=response.json()["id"])

    def feed(self, now=DAY):
        store_bulk.feed(Site.objects.get(pk=self.site.pk), now)

    def hand_in(self, state=SmsMessage.Status.SENT):
        """The phone reports every text of the store that is waiting for it."""
        return SmsMessage.objects.filter(category="storebulk", status=SmsMessage.Status.QUEUED).update(status=state)

    def pump(self, times, now=DAY):
        for _ in range(times):
            self.feed(now)
            self.hand_in()
        self.feed(now)

    def records(self):
        return SmsMessage.objects.filter(category="storebulk").order_by("pk")


class SwitchTests(BulkCase):
    def test_on_the_routes_answer(self):
        self.assertEqual(self.status().status_code, 200)

    def test_each_switch_off_makes_every_route_404(self):
        for name in ("STORE_BULK_SMS_ENABLED", "INQUIRY_SMS_ENABLED", "SMS_GATEWAY_ENABLED", "SMS_SITE_LINES_ENABLED"):
            with self.subTest(name), override_settings(**{name: False}):
                for response in (
                    self.status(), self.audience(), self.preview(), self.test(), self.confirm(),
                    self.get(self.root + "batches/"),
                ):
                    self.assertEqual(response.status_code, 404, (name, response.content))

    def test_another_signed_in_store_owner_cannot_use_my_site(self):
        self.assertEqual(self.get(self.root + "status/", client=self.ben).status_code, 404)

    def test_the_deploy_check_wants_the_prerequisites(self):
        from django.core.checks import run_checks
        with override_settings(SMS_SITE_LINES_ENABLED=False):
            self.assertIn("inquiries.E009", [e.id for e in run_checks(include_deployment_checks=True)])
        self.assertNotIn("inquiries.E009", [e.id for e in run_checks(include_deployment_checks=True)])


class AudienceTests(BulkCase):
    def setUp(self):
        super().setUp()
        # Members are not read for "all": this one is in no group and is never counted or texted
        make_member(self.site, "+639171000004", name="Member in no group")
        self.group_contact(self.site, "+639001000005", "Test number")
        self.group_contact(self.site, "+639171000006", "Stopped")
        InquirySmsOptOut.objects.create(site=self.site, mobile="+639171000006")
        Contact.objects.create(owner_kind="site", site=self.site, first_name="No group", mobile="+639171000007", created_by_account=self.account)
        self.group_contact(self.ben_site, "+639181000009", "Ben's contact")

    def test_an_explicit_all_counts_each_left_out_group(self):
        body = self.audience("all").json()
        self.assertEqual(body["audience"], "all")
        self.assertEqual((body["total_members"], body["will_send"]), (5, 3))
        self.assertEqual(body["left_out"], {"unverified": 0, "invalid_number": 1, "opted_out": 1, "duplicates": 0})

    def test_any_other_audience_is_refused(self):
        for value in ("promo", "everyone", "", "ALL"):
            response = self.audience(value)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], "invalid_audience")

    def test_a_missing_null_or_blank_audience_is_refused_and_nothing_is_sent(self):
        for body in ({}, {"audience": None}, {"audience": ""}, {"audience": "   "}):
            response = self.send("post", self.root + "audience/", body)
            self.assertEqual(response.status_code, 400, body)
            error = response.json()["error"]
            self.assertEqual(error["code"], "invalid_audience", body)
            self.assertIn("audience", error["fields"], body)
            confirm = {"text": TEXT, "idempotency_key": "key-missing-01", "confirm": True, **body}
            response = self.send("post", self.root + "batches/", confirm)
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "invalid_audience"), body)
            self.assertIn("audience", response.json()["error"]["fields"], body)
        self.assertFalse(StoreBulkBatch.objects.exists())
        self.assertFalse(self.records().exists())

    def test_a_promo_agreement_changes_nothing(self):
        Member.objects.filter(site=self.site).update(promo_sms_ok=False)
        self.assertEqual(self.audience().json()["will_send"], 3)

    def test_another_stores_members_are_never_counted(self):
        self.assertEqual(self.audience().json()["total_members"], 5)
        self.assertEqual(self.send("post", f"{self.ben_base}sms/bulk/audience/", {"audience": "all"}, client=self.ben).json()["total_members"], 1)

    def test_no_number_is_ever_in_the_answer(self):
        text = self.audience().content.decode()
        self.assertNotIn("+639", text)


class TextTests(BulkCase):
    def test_preview_is_the_exact_text_typed_and_no_footer(self):
        body = self.preview().json()
        self.assertEqual(body["text"], FULL)
        self.assertEqual((body["length"], body["remaining"]), (len(FULL), 160 - len(FULL)))
        self.assertNotIn("STOP", body["text"])

    def test_a_link_is_refused_everywhere(self):
        for call in (self.preview, self.test, self.confirm):
            with self.subTest(call.__name__):
                response = call(text="Shop at https://example.com now")
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "invalid")
        self.assertEqual(self.preview(text="see www.shop.ph").status_code, 422)

    def test_160_characters_are_accepted_as_typed_and_161_refused_for_preview_test_and_batch(self):
        full, over = "x" * 160, "x" * 161
        self.assertEqual(self.preview(text=full).json()["text"], full)
        self.assertEqual(self.test(text=full).status_code, 200)
        self.assertEqual(sms.take_text(self.records().get()), full)
        self.assertEqual(self.confirm(text=full).status_code, 201)
        self.assertEqual(StoreBulkBatch.objects.get().text, full)
        self.assertEqual(self.preview(text=over).status_code, 422)
        self.assertEqual(self.test(text=over, key="key-over-0001").status_code, 422)
        self.assertEqual(self.confirm(text=over, key="key-over-0002").status_code, 422)

    def test_too_long_and_empty_texts_are_refused(self):
        self.assertEqual(self.preview(text="x" * 161).status_code, 422)
        self.assertEqual(self.preview(text="   ").status_code, 422)

    def test_the_status_carries_the_empty_prefix_cap_window_and_cost_line(self):
        body = self.status().json()
        self.assertEqual((body["prefix"], body["max_length"], body["daily_limit"], body["left_today"]), ("", 160, 2000, 2000))
        self.assertEqual((body["blocked"], body["phone_online"], body["active_batch"]), ("", True, None))
        self.assertEqual(body["window"], {"start": "06:00", "end": "22:00", "timezone": "Asia/Manila"})
        self.assertEqual(body["seconds_between"], {"min": 18, "max": 25})
        self.assertIn("carrier", body["cost_note"])

    def test_cost_note_is_empty_for_a_store_with_no_phone_of_its_own(self):  # ONE-PHONE, 2026-11-26
        self.assertIn("carrier", self.status().json()["cost_note"])
        self.line.delete()
        self.assertEqual(self.status().json()["cost_note"], "")


class TestSendTests(BulkCase):
    def test_a_test_goes_to_the_owners_own_mobile_from_the_store_phone_and_counts_as_one(self):
        self.assertEqual(self.test().json(), {"state": "queued", "repeated": False})
        record = self.records().get()
        self.assertEqual((record.recipient, record.site_id, record.purpose, record.status), (OWN_MOBILE, self.site.pk, "store_bulk", "queued"))
        self.assertEqual(sms.take_text(record), FULL)
        self.assertEqual(self.status().json()["used_today"], 1)

    def test_the_same_key_sends_once(self):
        self.test()
        self.assertTrue(self.test().json()["repeated"])
        self.assertEqual(self.records().count(), 1)

    def test_no_account_mobile_is_a_409(self):
        AccountMobile.objects.all().delete()
        self.assertEqual(self.test().json()["error"]["code"], "no_mobile")

    def test_the_cap_applies_to_a_test(self):
        set_policy(store_bulk_daily_limit=1)
        self.assertEqual(self.test(key="key-test-0001").status_code, 200)
        self.assertEqual(self.test(key="key-test-0002").json()["error"]["code"], "bulk_limit")

    def test_the_other_caps_are_not_used(self):
        set_policy(platform_daily_limit=0, store_daily_limit=0, all_stores_daily_limit=0, site_daily_limit=0)
        self.assertEqual(self.test().status_code, 200)

    def test_a_test_from_a_store_with_no_line_is_refused_and_nothing_is_queued(self):
        # ONE-PHONE (Coach Ed, 2026-11-26): a store with no phone of its own but entitled to a line
        # uses the platform phone; a store that is not entitled is still refused no_line.
        SmsMessage.objects.all().delete()
        self.line.delete()
        self.has_line.return_value = False
        self.assertEqual(self.test().json()["error"]["code"], "no_line")
        self.assertFalse(self.records().exists())

    def test_a_test_from_a_store_with_no_phone_of_its_own_goes_from_the_platform_phone(self):
        SmsMessage.objects.all().delete()
        self.line.delete()
        self.assertEqual(self.test().status_code, 200)
        self.assertEqual(self.records().get().purpose, "store_bulk")


class ConfirmTests(BulkCase):
    def test_confirm_makes_a_batch_of_the_stores_verified_members(self):
        response = self.confirm()
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual((body["state"], body["size"], body["waiting"], body["audience"], body["text"]), ("waiting", 3, 3, "all", FULL))
        self.assertEqual(StoreBulkRecipient.objects.filter(batch__public_id=body["id"]).count(), 3)
        self.assertEqual(self.status().json()["active_batch"]["id"], body["id"])

    def test_the_box_must_be_ticked(self):
        response = self.send("post", self.root + "batches/", {"text": TEXT, "idempotency_key": "key-batch-0001"})
        self.assertEqual(response.status_code, 422)
        self.assertFalse(StoreBulkBatch.objects.exists())

    def test_the_same_key_is_the_same_batch(self):
        first = self.confirm().json()
        again = self.confirm()
        self.assertEqual((again.status_code, again.json()["id"]), (200, first["id"]))
        self.assertEqual(StoreBulkBatch.objects.count(), 1)

    def test_one_active_batch_per_store(self):
        self.start()
        self.assertEqual(self.confirm(key="key-batch-0002").json()["error"]["code"], "batch_running")
        # ... and each store has its own
        self.group_contact(self.ben_site, "+639181000009", "Ben")
        make_phone("ben-line", "token-ben-line", site=self.ben_site)
        response = self.send("post", f"{self.ben_base}sms/bulk/batches/", {"text": TEXT, "audience": "all", "idempotency_key": "key-batch-0003", "confirm": True}, client=self.ben)
        self.assertEqual(response.status_code, 201, response.content)

    def test_a_changed_count_is_409_with_the_new_count(self):
        response = self.confirm(expected_count=2)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], {"code": "audience_changed", "message": mock.ANY, "will_send": 3})
        self.assertEqual(self.confirm(expected_count=3).status_code, 201)

    def test_nobody_to_send_to_is_409(self):
        Contact.objects.filter(site=self.site).delete()
        self.assertEqual(self.confirm().json()["error"]["code"], "empty_audience")

    def test_a_held_store_is_refused_site_held(self):
        Site.objects.filter(pk=self.site.pk).update(operator_hold=True)
        body = self.confirm().json()["error"]
        self.assertEqual(body["code"], "site_held")
        self.assertEqual(self.status().json()["blocked"], "site_held")

    def test_a_store_not_entitled_to_a_line_is_refused_no_line_with_the_pairing_hint(self):
        # ONE-PHONE (2026-11-26): no own phone is no longer enough to be refused; no entitlement is.
        self.line.delete()
        self.has_line.return_value = False
        body = self.confirm().json()["error"]
        self.assertEqual(body["code"], "no_line")
        self.assertIn("Settings", body["message"])

    def test_texts_off_for_the_store_is_refused(self):
        InquirySmsSettings.objects.filter(site=self.site).update(enabled=False)
        self.assertEqual(self.confirm().json()["error"]["code"], "sms_off")

    def test_a_billing_only_store_is_403(self):
        with mock.patch("apps.billing.services.position", return_value=SimpleNamespace(billing_only=True, storefront_served=False)):
            for response in (self.status(), self.confirm(), self.audience()):
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error"]["code"], "billing_only")

    def test_the_audit_entry_has_counts_and_no_text_or_number(self):
        batch = self.start()
        event = AccountAuditEvent.objects.get(event="store_bulk_confirmed")
        self.assertEqual(event.detail["batch"], batch.public_id)
        self.assertEqual(event.detail["size"], 3)
        dump = str(event.detail)
        for secret in (TEXT, *NUMBERS, "+639"):
            self.assertNotIn(secret, dump)


class FeedTests(BulkCase):
    def test_one_text_at_a_time_from_the_stores_phone_in_order(self):
        batch = self.start()
        self.feed()
        (first,) = self.records()
        self.assertEqual((first.recipient, first.site_id, first.status), (NUMBERS[0], self.site.pk, "queued"))
        self.assertEqual(sms.take_text(first), FULL)  # nothing added: no footer
        self.feed()
        self.assertEqual(self.records().count(), 1, "the next text waits until the last one is reported")
        batch.refresh_from_db()
        self.assertEqual(batch.state, "sending")
        self.hand_in()
        self.feed()
        self.assertEqual([r.recipient for r in self.records()], NUMBERS[:2])

    def test_the_batch_finishes_and_the_numbers_are_masked(self):
        batch = self.start()
        self.pump(4)
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.sent, batch.not_sent), ("finished", 3, 0))
        self.assertIsNotNone(batch.finished_at)
        self.assertEqual(sorted(batch.recipients.values_list("mobile", flat=True)), ["+63917***0001", "+63917***0002", "+63917***0003"])
        event = AccountAuditEvent.objects.get(event="store_bulk_ended")
        self.assertEqual((event.detail["reason"], event.detail["sent"]), ("finished", 3))

    def test_a_failed_text_is_counted_and_the_batch_goes_on(self):
        batch = self.start()
        self.feed()
        self.hand_in(SmsMessage.Status.FAILED)
        self.pump(3)
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.sent, batch.failed), ("finished", 2, 1))

    def test_nothing_goes_outside_06_to_22_manila_and_the_batch_waits(self):
        batch = self.start()
        for hour in (0, 5, 22, 23):
            self.feed(at(hour, 59 if hour == 5 else 0))
        self.assertFalse(self.records().exists())
        batch.refresh_from_db()
        self.assertEqual(batch.state, "waiting")
        self.feed(at(6))
        self.assertEqual(self.records().count(), 1)
        self.hand_in()
        self.feed(at(21, 59))
        self.assertEqual(self.records().count(), 2)
        self.hand_in()
        self.feed(at(22))
        self.assertEqual(self.records().count(), 2)
        self.assertTrue(store_bulk.in_window(at(6)) and store_bulk.in_window(at(21, 59)))
        self.assertFalse(store_bulk.in_window(at(5, 59)) or store_bulk.in_window(at(22)))

    def test_window_edges(self):
        self.assertFalse(store_bulk.in_window(at(5, 59)))
        self.assertTrue(store_bulk.in_window(at(6)))
        self.assertTrue(store_bulk.in_window(at(7, 59)))  # was outside before the 06:00 ruling
        self.assertTrue(store_bulk.in_window(at(8)))
        self.assertTrue(store_bulk.in_window(at(21, 59)))
        self.assertFalse(store_bulk.in_window(at(22)))
        self.assertTrue(store_bulk.in_window(at(20, 30)))  # was outside before the 22:00 ruling
        self.assertFalse(store_bulk.in_window(NIGHT))

    def test_the_paused_reason_says_why_nothing_is_going(self):
        batch = self.start()
        with mock.patch.object(store_bulk, "in_window", return_value=False):
            self.assertEqual(self.status().json()["active_batch"]["paused_reason"], "outside_window")
        with mock.patch.object(store_bulk, "in_window", return_value=True):
            self.assertEqual(self.status().json()["active_batch"]["paused_reason"], "")
            set_policy(store_bulk_daily_limit=0)
            self.assertEqual(self.status().json()["active_batch"]["paused_reason"], "daily_cap")
            set_policy(store_bulk_daily_limit=2000)
            self.line.last_contact_at = timezone.now() - timedelta(minutes=5)
            self.line.save()
            self.assertEqual(self.status().json()["active_batch"]["paused_reason"], "phone_offline")
        self.assertEqual(batch.state, "waiting")

    def test_the_daily_cap_pauses_without_ending_and_leaves_no_refused_records(self):
        set_policy(store_bulk_daily_limit=2)
        batch = self.start()
        self.pump(5)
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.sent, batch.size - batch.sent - batch.failed - batch.not_sent), ("sending", 2, 1))
        self.assertEqual(SmsMessage.objects.filter(status="refused").count(), 0)
        # tomorrow the count starts again
        tomorrow = timezone.now() + timedelta(days=1)
        with mock.patch.object(sms, "_today", return_value=tomorrow.astimezone(MANILA).date()):
            with mock.patch.object(sms, "_day_start", return_value=timezone.make_aware(datetime.combine(tomorrow.astimezone(MANILA).date(), datetime.min.time()))):
                self.feed()
        self.assertEqual(self.records().count(), 3)

    def test_the_cap_is_per_store_and_hq_can_change_it(self):
        self.assertEqual(SmsPolicy.objects.get_or_create(pk=1)[0].store_bulk_daily_limit, 2000)
        self.assertIn("store_bulk_daily_limit", services.policy_fields())
        set_policy(store_bulk_daily_limit=5000)
        self.assertEqual(self.status().json()["daily_limit"], 5000)
        # another store's bulk texts do not use this store's room
        make_phone("ben-line", "token-ben-line", site=self.ben_site)
        make_member(self.ben_site, "+639181000009")
        services.send_sms(to="+639181000009", purpose="store_bulk", text="Ben's: hi", site=self.ben_site)
        self.assertEqual(self.status().json()["used_today"], 0)

    def test_a_member_who_asked_for_stop_after_confirm_is_skipped(self):
        batch = self.start()
        InquirySmsOptOut.objects.create(site=self.site, mobile=NUMBERS[0])
        self.pump(3)
        batch.refresh_from_db()
        first = batch.recipients.get(position=0)
        self.assertEqual((first.state, first.reason), ("not_sent", "opted_out"))
        self.assertEqual((batch.sent, batch.not_sent, batch.state), (2, 1, "finished"))
        self.assertNotIn(NUMBERS[0], [r.recipient for r in self.records()])

    def test_in_all_mode_promo_is_not_asked(self):
        batch = self.start()
        Member.objects.filter(site=self.site).update(promo_sms_ok=False)
        self.pump(4)
        batch.refresh_from_db()
        self.assertEqual(batch.sent, 3)

    def test_a_removed_member_or_a_changed_number_is_skipped(self):
        batch = self.start()
        self.contacts[0].delete()
        Contact.objects.filter(pk=self.contacts[1].pk).update(mobile="+639171999999")
        self.pump(3)
        reasons = dict(batch.recipients.values_list("position", "reason"))
        self.assertEqual((reasons[0], reasons[1], reasons[2]), ("contact_removed", "number_changed", ""))
        self.assertEqual([r.recipient for r in self.records()], [NUMBERS[2]])

    def test_another_stores_members_are_never_sent_to(self):
        make_member(self.ben_site, "+639181000009")
        self.start()
        self.pump(4)
        self.assertNotIn("+639181000009", [r.recipient for r in self.records()])
        self.assertEqual({r.site_id for r in self.records()}, {self.site.pk})

    def test_off_the_poll_queues_nothing(self):
        self.start()
        with override_settings(STORE_BULK_SMS_ENABLED=False):
            self.client.post("/gateway/v1/poll", data="{}", content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {TOKEN}", secure=True)
        self.assertFalse(self.records().exists())


class PollTests(BulkCase):
    def poll(self, token=TOKEN):
        return self.client.post(
            "/gateway/v1/poll", data="{}", content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}", secure=True,
        )

    def test_the_stores_phone_gets_one_text_with_the_18_to_25_second_gap(self):
        self.start()
        with mock.patch.object(store_bulk, "in_window", return_value=True):
            body = self.poll().json()
        (message,) = body["messages"]
        self.assertEqual((message["to"], message["text"]), (NUMBERS[0], FULL))
        self.assertTrue(18 <= message["gap"] <= 25)
        with mock.patch.object(store_bulk, "in_window", return_value=True):
            self.assertEqual(self.poll().json()["messages"], [], "one at a time until it is reported")

    def test_the_shared_phone_never_gets_a_text_of_a_store_that_has_its_own_phone(self):  # ONE-PHONE, 2026-11-26
        self.platform.token_hash = sms.token_hash("token-shared")
        self.platform.save()
        self.start()
        with mock.patch.object(store_bulk, "in_window", return_value=True):
            self.feed()  # queued, still waiting for a phone
            self.assertEqual(self.poll(token="token-shared").json()["messages"], [])
            self.assertEqual(self.records().get().status, "queued")
            self.assertEqual(len(self.poll().json()["messages"]), 1)  # only the store's own phone takes it
        self.assertEqual(self.records().get().device_id, self.line.pk)


class StopAndEndTests(BulkCase):
    def test_stop_ends_the_rest_and_takes_back_the_waiting_text(self):
        batch = self.start()
        self.feed()
        self.hand_in()
        self.feed()  # the second is queued, not yet with the phone
        response = self.send("post", f"{self.root}batches/{batch.public_id}/stop/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["state"], body["end_reason"], body["sent"], body["not_sent"], body["waiting"]), ("stopped", "stopped", 1, 2, 0))
        batch.refresh_from_db()
        self.assertIsNotNone(batch.stopped_at)
        self.assertEqual(set(batch.recipients.exclude(state="sent").values_list("reason", flat=True)), {"stopped"})
        self.feed()
        self.assertEqual(self.records().count(), 2)
        self.assertEqual(self.records().last().status, "expired")  # taken back before the phone took it
        self.assertEqual(self.send("post", f"{self.root}batches/{batch.public_id}/stop/").json()["error"]["code"], "not_running")
        event = AccountAuditEvent.objects.get(event="store_bulk_stopped")
        self.assertEqual((event.detail["batch"], event.detail["sent"]), (batch.public_id, 1))
        for secret in (TEXT, "+639"):
            self.assertNotIn(secret, str(event.detail))

    def test_another_stores_owner_cannot_stop_or_open_my_batch(self):
        batch = self.start()
        self.assertEqual(self.send("post", f"{self.ben_base}sms/bulk/batches/{batch.public_id}/stop/", client=self.ben).status_code, 404)
        self.assertEqual(self.get(f"{self.ben_base}sms/bulk/batches/{batch.public_id}/", client=self.ben).status_code, 404)
        self.assertEqual(self.get(self.root + "batches/", client=self.ben).status_code, 404)  # wrong site for Ben
        self.assertEqual(self.get(f"{self.ben_base}sms/bulk/batches/", client=self.ben).json()["items"], [])
        batch.refresh_from_db()
        self.assertEqual(batch.state, "waiting")

    def test_a_hold_ends_the_batch_with_that_reason(self):
        batch = self.start()
        self.feed()
        self.hand_in()
        Site.objects.filter(pk=self.site.pk).update(operator_hold=True)
        self.feed()
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.end_reason, batch.sent, batch.not_sent), ("ended", "site_held", 1, 2))
        self.assertEqual(self.records().count(), 1)
        self.assertEqual(set(batch.recipients.exclude(state="sent").values_list("reason", flat=True)), {"site_held"})
        event = AccountAuditEvent.objects.get(event="store_bulk_ended")
        self.assertEqual((event.detail["reason"], event.detail["not_sent"]), ("site_held", 2))
        self.assertNotIn("+639", str(event.detail))
        self.assertTrue(all("*" in m for m in batch.recipients.values_list("mobile", flat=True)))

    def test_billing_only_ends_the_batch(self):
        batch = self.start()
        with mock.patch("apps.billing.services.position", return_value=SimpleNamespace(billing_only=True, storefront_served=False)):
            self.feed()
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.end_reason, batch.not_sent), ("ended", "billing_only", 3))

    def test_a_line_switched_off_ends_the_batch(self):
        batch = self.start()
        self.has_line.return_value = False
        self.feed()
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.end_reason), ("ended", "line_disabled"))

    def test_after_an_end_a_new_batch_may_start(self):
        batch = self.start()
        Site.objects.filter(pk=self.site.pk).update(operator_hold=True)
        self.feed()
        Site.objects.filter(pk=self.site.pk).update(operator_hold=False)
        self.assertEqual(self.confirm(key="key-batch-0002").status_code, 201)
        batch.refresh_from_db()
        self.assertEqual(batch.state, "ended")


class HistoryTests(BulkCase):
    def test_the_list_and_the_batch_show_masked_numbers_and_names(self):
        batch = self.start()
        self.pump(2)
        listing = self.get(self.root + "batches/").json()
        self.assertEqual([b["id"] for b in listing["items"]], [batch.public_id])
        self.assertIsNone(listing["next_cursor"])
        opened = self.get(f"{self.root}batches/{batch.public_id}/")
        body = opened.json()
        self.assertEqual([(r["name"], r["mobile"], r["state"]) for r in body["results"]][:2],
                         [("Member 0", "+63917***0001", "sent"), ("Member 1", "+63917***0002", "sent")])
        for text in (opened.content.decode(), str(listing)):
            for number in NUMBERS:
                self.assertNotIn(number, text)

    def test_paging_of_batches_and_recipients(self):
        batch = self.start()
        page = self.get(f"{self.root}batches/{batch.public_id}/?limit=2").json()
        self.assertEqual((len(page["results"]), page["next"]), (2, "1"))
        rest = self.get(f"{self.root}batches/{batch.public_id}/?limit=2&cursor=1").json()
        self.assertEqual((len(rest["results"]), rest["next"]), (1, None))
        self.assertEqual(self.get(f"{self.root}batches/{batch.public_id}/?cursor=x").status_code, 422)
        self.assertEqual(self.get(self.root + "batches/?cursor=sbk_nope").status_code, 422)

    def test_a_batch_that_is_not_found_is_404(self):
        self.assertEqual(self.get(self.root + "batches/sbk_" + "0" * 20 + "/").status_code, 404)
        self.assertEqual(self.get(self.root + "batches/nope/").status_code, 404)


class RetentionTests(BulkCase):
    def make(self, state, age, site=None):
        batch = StoreBulkBatch.objects.create(
            site=site or self.site, created_by=self.account, request_id=f"k-{state}-{age}", text=FULL, state=state, size=1,
        )
        StoreBulkRecipient.objects.create(batch=batch, position=0, name="X", mobile="+63917***0001")
        StoreBulkBatch.objects.filter(pk=batch.pk).update(created_at=timezone.now() - timedelta(days=age))
        return batch

    def test_old_finished_batches_go_after_180_days_with_their_recipients(self):
        old, recent = self.make("finished", 181), self.make("stopped", 179)
        self.make("sending", 200)
        self.assertEqual(inquiry_retention.store_bulk_batches(timezone.now(), True), 1)
        self.assertEqual(inquiry_retention.store_bulk_batches(timezone.now(), False), 1)
        self.assertFalse(StoreBulkBatch.objects.filter(pk=old.pk).exists())
        self.assertTrue(StoreBulkBatch.objects.filter(pk=recent.pk).exists())
        self.assertEqual(StoreBulkBatch.objects.count(), 2, "an active batch is never deleted")
        self.assertEqual(StoreBulkRecipient.objects.count(), 2)

    def test_the_rule_runs_with_the_daily_ones(self):
        from apps.notifications.retention import DAILY_RULES
        self.assertIn("store_bulk_batches", DAILY_RULES)
        self.make("finished", 181)
        report = retention.run_all(only=("store_bulk_batches",))
        self.assertEqual(report.counts, {"store_bulk_batches": 1})
        self.assertFalse(StoreBulkBatch.objects.exists())

    def test_site_erasure_deletes_the_sites_batches_only(self):
        self.make("finished", 1)
        other = self.make("finished", 1, site=self.ben_site)
        counts = inquiry_retention.blank_inquiries(Site.objects.filter(pk=self.site.pk))
        self.assertEqual(counts["store_bulk_batches"], 1)
        self.assertEqual(list(StoreBulkBatch.objects.all()), [other])
        self.assertIn("store_bulk_batches", inquiry_retention.blanked())


class BeforeTheMigrationTests(BulkCase):
    def test_before_0008_the_policy_column_is_not_read_or_saved_and_the_default_stands(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from apps.notifications import models as notification_models
        sms.policy()
        with mock.patch.object(notification_models, "store_bulk_limit_column_exists", return_value=False):
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(sms.policy().store_bulk_daily_limit, 2000)
            self.assertFalse([q for q in queries.captured_queries if "store_bulk_daily_limit" in q["sql"]])
        with mock.patch.object(services, "store_bulk_limit_column_exists", return_value=False):
            self.assertNotIn("store_bulk_daily_limit", services.policy_fields({"store_bulk_daily_limit": 5}))

    def test_the_platform_phone_leaves_out_the_bulk_of_a_store_with_its_own_phone(self):  # ONE-PHONE, 2026-11-26
        from apps.notifications.models import SmsDevice
        claim = sms.claimable(SmsDevice.objects.get(pk=self.platform.pk))
        self.assertIn("site_id__in", str(claim))
