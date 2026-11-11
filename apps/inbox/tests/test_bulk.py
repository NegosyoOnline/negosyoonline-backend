"""Tasks 4 and 5 of plan 8: the source reader, the check, the confirm. Made-up numbers only."""
import json

from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.common.errors import ApiError
from apps.inbox import bulk
from apps.inbox.models import BulkBatch, BulkRecipient, DoNotText
from apps.inbox.tests.bulk_base import TEXT, BulkCase, lines, mobile
from apps.notifications.models import SmsMessage
from apps.notifications.tests.base import set_policy


class CheckTests(BulkCase):
    def check(self, data):
        return bulk.check_answer(self.scope(), data)

    def test_1_pasted_counts_bad_lines_and_repeats(self):
        answer = self.check(self.pasted(mobile(1), "not a number", mobile(2), mobile(1), "", "0917 000 0003"))
        self.assertEqual(
            (answer["lines_read"], answer["duplicates"], answer["refused_count"], answer["size"]), (5, 1, 1, 3)
        )
        self.assertEqual(answer["refused"], [{"line": 2, "reason": bulk.NOT_A_MOBILE}])
        self.assertEqual((answer["text_length"], answer["blocked"]), (len(TEXT) + 22, ""))

    def test_2_file_reads_only_the_first_column_and_skips_a_header(self):
        csv_text = f"number,name,note\n{mobile(1)},Secret Person,call 09179999999\n{mobile(2)},Other,x\n"
        answer = self.check({"text": TEXT, "source": "file", "csv": csv_text})
        self.assertEqual((answer["lines_read"], answer["size"], answer["refused_count"]), (2, 2, 0))
        self.assertNotIn("Secret", json.dumps(answer))
        self.assertNotIn("9999999", json.dumps(answer))

    def test_3_groups_count_a_contact_in_two_groups_once(self):
        one = self.make_group("One", mobile(1), mobile(2))
        two = self.make_group("Two", mobile(2), mobile(3))
        answer = self.check({"text": TEXT, "source": "groups", "groups": [one.public_id, two.public_id]})
        self.assertEqual(answer["size"], 3)

    def test_4_unknown_group_is_404_and_groups_with_contacts_off_is_400(self):
        with self.assertRaises(ApiError) as caught:
            self.check({"text": TEXT, "source": "groups", "groups": ["grp_unknown"]})
        self.assertEqual(caught.exception.status, 404)
        group = self.make_group("One", mobile(1))
        from django.test import override_settings
        with override_settings(SMS_CONTACTS_ENABLED=False), self.assertRaises(ApiError) as caught:
            self.check({"text": TEXT, "source": "groups", "groups": [group.public_id]})
        self.assertEqual(caught.exception.status, 400)

    def test_5_a_line_that_is_not_a_mobile_shows_no_content(self):
        answer = self.check(self.pasted("secret-value-77"))
        self.assertNotIn("secret-value", json.dumps(answer))

    def test_6_201_refused_lines_show_200_and_count_201(self):
        answer = self.check(self.pasted(*(["bad"] * 201), mobile(1)))
        self.assertEqual((len(answer["refused"]), answer["refused_count"]), (200, 201))

    def test_7_a_file_over_1_mb_is_413(self):
        with self.assertRaises(ApiError) as caught:
            self.check({"text": TEXT, "source": "file", "csv": "0" * (1_048_576 + 1)})
        self.assertEqual((caught.exception.status, caught.exception.code), (413, "file_too_large"))

    def test_8_listed_numbers_are_skipped_and_counted(self):
        DoNotText.objects.create(platform="sms", mobile=mobile(2))
        answer = self.check(self.pasted(mobile(1), mobile(2), mobile(3)))
        self.assertEqual((answer["opted_out"], answer["size"]), (1, 2))

    def test_9_the_size_is_cut_to_the_room(self):
        for _ in range(214):
            SmsMessage.objects.create(
                recipient=mobile(0), category="code", purpose="mobile_verify", priority=10, status="sent",
                expires_at=__import__("django.utils.timezone", fromlist=["now"]).now(),
            )
        from django.core.cache import cache
        cache.clear()
        answer = self.check(self.pasted(*(mobile(i) for i in range(1, 2294))))
        self.assertEqual(
            (answer["used_today"], answer["left_today"], answer["size"], answer["left_out"]),
            (214, 1486, 1486, 807),
        )
        self.assertGreater(answer["seconds_max"], 36000)

    def test_10_no_room_blocks(self):
        set_policy(platform_daily_limit=300)
        answer = self.check(self.pasted(mobile(1)))
        self.assertEqual((answer["size"], answer["blocked"]), (0, "no_room"))

    def test_11_the_text_rules(self):
        for bad in ("x" * 139, "see example.com", "", "   ", None, 5):
            with self.subTest(text=bad), self.assertRaises(ApiError) as caught:
                self.check(self.pasted(mobile(1), text=bad))
            self.assertEqual(caught.exception.code, "invalid_text")
        answer = self.check(self.pasted(mobile(1), text="x" * 138))
        self.assertEqual(answer["text_length"], 160)

    def test_12_incoming_off_blocks(self):
        self.phone.forwards_incoming = False
        self.phone.save()
        self.assertEqual(self.check(self.pasted(mobile(1)))["blocked"], "incoming_off")

    def test_13_check_writes_nothing(self):
        before = len(self.entries())
        self.check(self.pasted(mobile(1)))
        self.assertEqual((BulkBatch.objects.count(), BulkRecipient.objects.count(), len(self.entries())), (0, 0, before))

    def test_14_left_out_returns_the_rest_and_is_audited_with_counts(self):
        set_policy(platform_daily_limit=302)
        body = bulk.left_out(self.scope(), self.pasted(mobile(1), mobile(2), mobile(3)))
        self.assertEqual(body, f"{mobile(3)}\n")
        entry = self.entries("sms_inbox_bulk_left_out")[0]
        self.assertEqual(entry.detail, {"platform": "sms", "source": "pasted", "count": 1})
        self.assertNothingPersonalIn([entry], mobile(2), mobile(3))
        set_policy(platform_daily_limit=2000)
        from django.core.cache import cache
        cache.clear()
        with self.assertRaises(ApiError) as caught:
            bulk.left_out(self.scope(), self.pasted(mobile(1)))
        self.assertEqual(caught.exception.code, "nothing_left_out")


class ConfirmTests(BulkCase):
    def test_1_confirm_with_the_typed_count_makes_the_batch(self):
        row, created = self.start(3, mobile(1), mobile(2), mobile(3))
        self.assertTrue(created)
        batch = BulkBatch.objects.get()
        self.assertEqual((batch.state, batch.size, row["waiting"], row["state"]), ("waiting", 3, 3, "waiting"))
        self.assertEqual(
            list(batch.recipients.order_by("position").values_list("mobile", flat=True)),
            [mobile(1), mobile(2), mobile(3)],
        )
        self.assertNotIn(mobile(1), json.dumps(row))
        self.assertNotIn(TEXT, json.dumps(row))

    def test_2_the_cut_numbers_are_not_rows(self):
        set_policy(platform_daily_limit=302)
        self.start(2, mobile(1), mobile(2), mobile(3))
        batch = BulkBatch.objects.get()
        self.assertEqual((batch.size, batch.left_out, batch.recipients.count()), (2, 1, 2))

    def test_3_a_wrong_count_or_a_changed_source_is_409_and_writes_nothing(self):
        for count, numbers in ((2, [mobile(1), mobile(2), mobile(3)]), (3, [mobile(1), mobile(2)])):
            with self.assertRaises(ApiError) as caught:
                self.start(count, *numbers)
            self.assertEqual((caught.exception.status, caught.exception.code), (409, "changed"))
        self.assertEqual((BulkBatch.objects.count(), len(self.entries("sms_inbox_bulk"))), (0, 0))

    def test_4_a_non_integer_count_is_refused(self):
        for count in ("3", True, None, 2.5):
            with self.assertRaises(ApiError) as caught:
                bulk.confirm(self.scope(), self.pasted(mobile(1), count=count, request_id=self.request_id()))
            self.assertEqual(caught.exception.status, 400)

    def test_5_a_second_batch_while_one_runs_is_409(self):
        self.start(1, mobile(1))
        with self.assertRaises(ApiError) as caught:
            self.start(1, mobile(2))
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "batch_running"))

    def test_6_the_same_request_id_answers_the_same_batch(self):
        data = self.pasted(mobile(1), count=1, request_id="request-id-repeat-0001")
        first, created = bulk.confirm(self.scope(), data)
        again, created_again = bulk.confirm(self.scope(), data)
        self.assertEqual((created, created_again, first["batch"]), (True, False, again["batch"]))
        self.assertEqual((BulkBatch.objects.count(), len(self.entries("sms_inbox_bulk"))), (1, 1))

    def test_7_blocked_cases(self):
        self.phone.forwards_incoming = False
        self.phone.save()
        with self.assertRaises(ApiError) as caught:
            self.start(1, mobile(1))
        self.assertEqual(caught.exception.code, "incoming_off")
        self.phone.forwards_incoming = True
        self.phone.save()
        set_policy(platform_daily_limit=300)
        with self.assertRaises(ApiError) as caught:
            self.start(1, mobile(1))
        self.assertEqual(caught.exception.code, "no_room")

    def test_8_the_audit_entry_holds_the_design_list_and_nothing_personal(self):
        group = self.make_group("Secret Group Name", mobile(1))
        bulk.confirm(
            self.scope(),
            {"text": TEXT, "source": "groups", "groups": [group.public_id], "count": 1, "request_id": self.request_id()},
        )
        entry = self.entries("sms_inbox_bulk")[0]
        self.assertEqual(
            sorted(entry.detail),
            sorted(["platform", "batch", "source", "groups", "lines_read", "duplicates", "refused",
                    "opted_out", "size", "left_out", "text_length"]),
        )
        self.assertEqual(entry.detail["groups"], 1)
        self.assertNothingPersonalIn([entry], TEXT, mobile(1), "Secret Group Name", group.public_id)

    def test_9_a_user_without_the_permissions_has_no_scope(self):
        from django.contrib.auth import get_user_model
        from apps.inbox.models import InboxAccess
        none = get_user_model().objects.create_user("none", password="pw", is_staff=True)
        reader = get_user_model().objects.create_user("reader", password="pw", is_staff=True)
        sender = get_user_model().objects.create_user("sender", password="pw", is_staff=True)
        self.grant(reader, can_send=False, can_bulk=False)
        self.grant(sender, can_send=True, can_bulk=False)
        for user in (none, reader, sender):
            self.assertIsNone(bulk.bulk_scope(user))

    def test_10_the_batch_belongs_to_the_manila_day(self):
        self.start(1, mobile(1))
        from django.utils import timezone
        self.assertEqual(BulkBatch.objects.get().local_day, timezone.localdate())
