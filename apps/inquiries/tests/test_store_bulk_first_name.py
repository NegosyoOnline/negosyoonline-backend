"""BULK-SMS-FIRSTNAME (z8uq9mjj07): the {first_name} merge field in store Bulk SMS and Schedule SMS.
Every name and number is made up."""
from unittest import mock

from django.test import SimpleTestCase

from apps.accounts.models import AccountAuditEvent
from apps.inbox.models import Contact
from apps.inquiries import store_bulk
from apps.inquiries.tests.test_store_bulk import BulkCase
from apps.inquiries.tests.test_store_bulk_pick import PickCase
from apps.inquiries.tests.test_store_bulk_schedule import AFTER, ScheduleCase
from apps.members.models import Member
from apps.notifications import services
from apps.notifications.models import StoreBulkBatch, StoreBulkRecipient

HI = "Hi {first_name}, sale today!"
BRACES = "Only {first_name} can go inside { }."


def sent_texts(spy):
    return [call.kwargs["text"] for call in spy.call_args_list]


class SpyMixin:
    def spy_on_sends(self):
        patcher = mock.patch.object(services, "send_sms", wraps=services.send_sms)
        spy = patcher.start()
        self.addCleanup(patcher.stop)
        return spy


class FillUnitTests(SimpleTestCase):
    def test_fills_every_repeat(self):
        self.assertEqual(store_bulk.fill_text("{first_name}, hi {first_name}.", "Ana"), "Ana, hi Ana.")

    def test_no_placeholder_is_unchanged(self):
        self.assertEqual(store_bulk.fill_text("Sale  today ,ok", "Ana"), "Sale  today ,ok")

    def test_fallbacks(self):
        for name in ("", None, "   "):
            self.assertEqual(store_bulk.fill_text(HI, name), "Hi, sale today!")

    def test_punctuation_and_spacing(self):
        cases = {
            "Hi {first_name}, sale": "Hi, sale",
            "Hi {first_name} !": "Hi!",
            "Hello {first_name} ; ok : yes ? no .": "Hello; ok: yes? no.",
            "Hi  {first_name}  there": "Hi there",
            "{first_name} sale": "sale",
            "Sale for {first_name}": "Sale for",
            "A {first_name} {first_name} B": "A B",
        }
        for template, expected in cases.items():
            with self.subTest(template):
                self.assertEqual(store_bulk.fill_text(template, ""), expected)

    def test_over_length_uses_fallback_and_never_cuts_the_name(self):
        template = "x" * 140 + " {first_name}"
        self.assertEqual(store_bulk.fill_text(template, "B" * 30), "x" * 140)
        self.assertEqual(store_bulk.fill_text(template, "Ben"), "x" * 140 + " Ben")

    def test_text_check_failure_uses_fallback(self):
        self.assertEqual(store_bulk.fill_text(HI, "Ana☃"), "Hi, sale today!")  # not a plain character
        self.assertEqual(store_bulk.fill_text("See {first_name}", "x.com"), "See")  # would make a web link

    def test_first_word(self):
        self.assertEqual(store_bulk._first_word("  Ben  Cruz "), "Ben")
        self.assertEqual(store_bulk._first_word(" "), "")


class RefusalTests(BulkCase):
    def assert_braces(self, response):
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(response.json()["error"]["fields"]["text"], [BRACES])

    def test_placeholder_is_accepted_everywhere(self):
        self.assertEqual(self.preview(HI).status_code, 200)
        self.assertEqual(self.preview("{first_name}{first_name} hi").status_code, 200)
        self.assertEqual(self.test(HI).status_code, 200)
        self.assertEqual(self.confirm(text=HI).status_code, 201)

    def test_other_braces_are_refused_on_preview_test_and_confirm(self):
        for bad in ("Hi {name}", "Hi {first_name", "Hi first_name}", "Hi {{first_name}}", "Hi {First_name}", "Hi { }", "}"):
            with self.subTest(bad):
                self.assert_braces(self.preview(bad))
                self.assert_braces(self.test(bad, key="key-test-bad1"))
                self.assert_braces(self.confirm(text=bad, key="key-batch-bad1"))
        self.assertFalse(StoreBulkBatch.objects.exists())

    def test_length_counts_the_placeholder_as_twelve(self):
        ok = "x" * 148 + "{first_name}"  # 160
        self.assertEqual(self.preview(ok).json()["length"], 160)
        self.assertEqual(self.preview(ok).json()["text"], ok)
        response = self.preview("x" * 149 + "{first_name}")  # 161
        self.assertEqual(response.status_code, 422)
        self.assertIn("at most 160", response.json()["error"]["fields"]["text"][0])

    def test_link_and_plain_rules_apply_with_the_placeholder_removed(self):
        self.assertEqual(self.preview("Hi {first_name} see example.com").status_code, 422)
        self.assertEqual(self.preview("Hi {first_name} ☃").status_code, 422)
        self.assertEqual(self.preview("{first_name}").status_code, 422)  # nothing left once it is removed


class ScheduleRefusalTests(ScheduleCase):
    def test_create_and_edit(self):
        self.assertEqual(self.schedule(text=HI).status_code, 201)
        for bad in ("Hi {name}", "Hi {first_name"):
            response = self.schedule(text=bad, key="key-sched-bad1")
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["error"]["fields"]["text"], [BRACES])
        batch = StoreBulkBatch.objects.get()
        url = f"{self.sroot}{batch.public_id}/"
        self.assertEqual(self.send("patch", url, {"text": "Bye {first_name}."}).status_code, 200)
        batch.refresh_from_db()
        self.assertEqual(batch.text, "Bye {first_name}.")
        response = self.send("patch", url, {"text": "Bye {nope}"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["fields"]["text"], [BRACES])


class MemberFillTests(SpyMixin, BulkCase):
    def setUp(self):
        super().setUp()
        self.spy = self.spy_on_sends()
        for member, name in zip(self.members, ("Ana Reyes", "  Ben   Cruz ", "")):
            Member.objects.filter(pk=member.pk).update(full_name=name)

    def test_first_word_and_fallback_for_a_blank_name(self):
        batch = self.start(text=HI)
        self.pump(3)
        self.assertEqual(sent_texts(self.spy), ["Hi Ana, sale today!", "Hi Ben, sale today!", "Hi, sale today!"])
        batch.refresh_from_db()
        self.assertEqual((batch.text, batch.sent), (HI, 3))
        self.assertEqual(self.get(f"{self.root}batches/{batch.public_id}/").json()["text"], HI)

    def test_name_is_read_at_send_time(self):
        self.start(text=HI)
        Member.objects.filter(pk=self.members[0].pk).update(full_name="Cy Dela")
        self.pump(1)
        self.assertEqual(sent_texts(self.spy)[0], "Hi Cy, sale today!")

    def test_a_text_without_the_placeholder_is_sent_as_is(self):
        self.start(text="Sale  today ,ok")
        self.pump(1)
        self.assertEqual(sent_texts(self.spy)[0], "Sale  today ,ok")

    def test_repeats(self):
        self.start(text="{first_name}, hi {first_name}")
        self.pump(1)
        self.assertEqual(sent_texts(self.spy)[0], "Ana, hi Ana")

    def test_over_length_falls_back_and_is_never_refused(self):
        Member.objects.filter(pk=self.members[0].pk).update(full_name="B" * 100)
        batch = self.start(text="x" * 100 + " {first_name}")
        self.pump(1)
        self.assertEqual(sent_texts(self.spy)[0], "x" * 100)
        self.assertEqual(StoreBulkRecipient.objects.get(batch=batch, position=0).state, StoreBulkRecipient.SENT)

    def test_text_check_failure_falls_back(self):
        Member.objects.filter(pk=self.members[0].pk).update(full_name="a.com")
        self.start(text="See {first_name}")
        self.pump(1)
        self.assertEqual(sent_texts(self.spy)[0], "See")

    def test_a_removed_member_is_skipped_as_before(self):
        batch = self.start(text=HI)
        self.members[0].delete()
        self.pump(1)
        self.assertEqual(StoreBulkRecipient.objects.get(batch=batch, position=0).reason, "member_removed")
        self.assertEqual(sent_texts(self.spy)[0], "Hi Ben, sale today!")

    def test_no_name_or_text_in_the_audit(self):
        self.start(text=HI)
        self.pump(3)
        blob = str(list(AccountAuditEvent.objects.values()))
        for secret in ("Ana", "Ben", "sale today"):
            self.assertNotIn(secret, blob)


class ContactFillTests(SpyMixin, PickCase):
    def setUp(self):
        super().setUp()
        self.spy = self.spy_on_sends()

    def test_contact_first_name_read_at_send_time(self):
        a = self.add("Ana", "+639172000001")
        b = self.add("Ben", "+639172000002")
        batch = self.start(audience="pick", contacts=[a, b], text=HI)
        Contact.objects.filter(public_id=a).update(first_name="Anita")
        self.pump(2)
        self.assertEqual(sent_texts(self.spy), ["Hi Anita, sale today!", "Hi Ben, sale today!"])
        self.assertEqual(StoreBulkBatch.objects.get(pk=batch.pk).text, HI)

    def test_blank_contact_name_uses_the_fallback(self):
        a = self.add("Ana", "+639172000001")
        self.start(audience="pick", contacts=[a], text=HI)
        Contact.objects.filter(public_id=a).update(first_name="")
        self.pump(1)
        self.assertEqual(sent_texts(self.spy), ["Hi, sale today!"])

    def test_contact_gone_is_skipped_as_before(self):
        a = self.add("Ana", "+639172000001")
        b = self.add("Ben", "+639172000002")
        batch = self.start(audience="pick", contacts=[a, b], text=HI)
        Contact.objects.filter(public_id=a).delete()
        self.pump(2)
        self.assertEqual(StoreBulkRecipient.objects.get(batch=batch, position=0).reason, "contact_removed")
        self.assertEqual(sent_texts(self.spy), ["Hi Ben, sale today!"])


class ScheduleStartTests(SpyMixin, ScheduleCase):
    def test_a_batch_started_from_a_schedule_is_filled(self):
        spy = self.spy_on_sends()
        Member.objects.filter(pk=self.members[0].pk).update(full_name="Ana Reyes")
        batch = self.make(text=HI)
        self.assertEqual(self.run_due(), 1)
        batch.refresh_from_db()
        self.assertEqual(batch.text, HI)
        self.feed(AFTER)
        self.assertEqual(sent_texts(spy), ["Hi Ana, sale today!"])


class TestSmsTests(SpyMixin, BulkCase):
    def setUp(self):
        super().setUp()
        self.spy = self.spy_on_sends()

    def test_filled_with_the_owners_first_name(self):
        self.account.name = "Dan Lim"
        self.account.save(update_fields=["name"])
        self.assertEqual(self.test(HI).status_code, 200)
        self.assertEqual(sent_texts(self.spy), ["Hi Dan, sale today!"])

    def test_no_name_uses_the_fallback(self):
        self.account.name = ""
        self.account.save(update_fields=["name"])
        self.assertEqual(self.test(HI).status_code, 200)
        self.assertEqual(sent_texts(self.spy), ["Hi, sale today!"])
