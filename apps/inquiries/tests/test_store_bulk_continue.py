"""STORE-BULK-CONTINUE (Coach Ed, 2026-11-27): send the rest of an ENDED batch. Only the texts not sent
because the batch ended go back to waiting; their numbers are read again from the contact behind them."""
from unittest import mock

from apps.accounts.models import AccountAuditEvent
from apps.inbox.models import Contact
from apps.inquiries import store_bulk
from apps.inquiries.models import InquirySmsOptOut
from apps.inquiries.tests.test_store_bulk import DAY, NUMBERS, BulkCase, at
from apps.notifications.models import StoreBulkBatch, StoreBulkRecipient
from apps.notifications.tests.base import set_policy
from apps.sites.models import Site


class ContinueCase(BulkCase):
    def hold(self, on=True):
        Site.objects.filter(pk=self.site.pk).update(operator_hold=on)

    def url(self, batch, what="continue"):
        return f"{self.root}batches/{batch.public_id}/{what}/"

    def go(self, batch):
        return self.send("post", self.url(batch))

    def ended(self):
        """One text sent, then the store put on hold: two are not sent because the batch ended."""
        batch = self.start()
        self.feed()
        self.hand_in()
        self.hold()
        self.feed()
        self.hold(False)
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.end_reason, batch.sent, batch.not_sent), ("ended", "site_held", 1, 2))
        return batch

    def code(self, response):
        return response.status_code, response.json().get("error", {}).get("code")


class ContinueTests(ContinueCase):
    def test_happy_path_sends_the_rest_on_the_same_row(self):
        batch = self.ended()
        shown = self.get(f"{self.root}batches/{batch.public_id}/").json()
        self.assertEqual((shown["can_continue"], shown["continue_count"]), (True, 2))
        response = self.go(batch)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["id"], body["state"], body["end_reason"], body["finished_at"]), (batch.public_id, "waiting", "", None))
        self.assertEqual((body["sent"], body["not_sent"], body["waiting"], body["continue_count"], body["can_continue"]), (1, 0, 2, 0, False))
        self.assertEqual(StoreBulkBatch.objects.count(), 1)
        self.pump(3)
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.sent, batch.not_sent), ("finished", 3, 0))
        self.assertEqual([r.recipient for r in self.records()], NUMBERS)  # each exactly once, in order
        self.assertTrue(all("*" in m for m in batch.recipients.values_list("mobile", flat=True)))  # masked again

    def test_the_numbers_are_full_again_while_active_and_the_text_is_filled(self):
        batch = self.ended()
        self.go(batch)
        self.assertEqual(sorted(batch.recipients.filter(state="waiting").values_list("mobile", flat=True)), NUMBERS[1:])

    def test_audit_row_has_actor_and_count_and_no_number_or_text(self):
        batch = self.ended()
        self.go(batch)
        event = AccountAuditEvent.objects.get(event="store_bulk_continued")
        self.assertEqual((event.detail["batch"], event.detail["count"], event.account_id), (batch.public_id, 2, self.account.pk))
        self.assertNotIn("+639", str(event.detail))

    def test_refused_while_the_store_is_on_hold(self):
        batch = self.ended()
        self.hold()
        self.assertEqual(self.code(self.go(batch)), (409, "site_held"))
        self.assertFalse(self.get(f"{self.root}batches/{batch.public_id}/").json()["can_continue"])
        batch.refresh_from_db()
        self.assertEqual(batch.state, "ended")

    def test_refused_when_the_line_is_off(self):
        batch = self.ended()
        self.has_line.return_value = False
        self.assertEqual(self.code(self.go(batch)), (409, "no_line"))

    def test_refused_for_stopped_and_finished_and_active_batches(self):
        stopped = self.start()
        self.send("post", self.url(stopped, "stop"))
        self.assertEqual(self.code(self.go(stopped)), (409, "not_ended"))
        self.assertEqual(self.get(f"{self.root}batches/{stopped.public_id}/").json()["continue_count"], 0)
        done = self.start(key="key-batch-0002")
        self.pump(4)
        done.refresh_from_db()
        self.assertEqual(done.state, "finished")
        self.assertEqual(self.code(self.go(done)), (409, "not_ended"))
        live = self.start(key="key-batch-0003")
        self.assertEqual(self.code(self.go(live)), (409, "not_ended"))

    def test_refused_when_another_batch_is_active(self):
        batch = self.ended()
        other = self.start(key="key-batch-0002")
        self.assertEqual(self.code(self.go(batch)), (409, "batch_running"))
        self.assertFalse(self.get(f"{self.root}batches/{batch.public_id}/").json()["can_continue"])
        other.refresh_from_db()
        self.assertEqual(other.state, "waiting")

    def test_a_second_call_is_refused(self):
        batch = self.ended()
        self.assertEqual(self.go(batch).status_code, 200)
        self.assertEqual(self.code(self.go(batch)), (409, "not_ended"))
        self.assertEqual(batch.recipients.filter(state="waiting").count(), 2)

    def test_nothing_remaining_is_refused(self):
        batch = self.ended()
        batch.recipients.filter(state="not_sent").update(reason="opted_out")
        self.assertEqual(self.code(self.go(batch)), (409, "nothing_to_continue"))
        self.assertEqual(self.get(f"{self.root}batches/{batch.public_id}/").json()["continue_count"], 0)

    def test_skipped_recipients_are_never_resent(self):
        batch = self.start()
        InquirySmsOptOut.objects.create(site=self.site, mobile=NUMBERS[0])
        self.feed()  # the first is skipped for STOP, the second queued
        self.hand_in()
        self.hold()
        self.feed()
        self.hold(False)
        batch.refresh_from_db()
        self.assertEqual(batch.state, "ended")
        InquirySmsOptOut.objects.filter(site=self.site).delete()  # even if the STOP is lifted, it was left out
        for ref, reason in ((self.contacts[0].pk, "opted_out"),):
            self.assertEqual(batch.recipients.get(contact_ref=ref).reason, reason)
        body = self.go(batch).json()
        self.assertEqual(body["waiting"], 1)
        self.assertEqual(batch.recipients.get(contact_ref=self.contacts[0].pk).state, "not_sent")
        self.pump(3)
        self.assertNotIn(NUMBERS[0], [r.recipient for r in self.records()])

    def test_invalid_duplicate_removed_and_changed_are_not_restored(self):
        batch = self.ended()
        rows = list(batch.recipients.filter(state="not_sent").order_by("position"))
        Contact.objects.filter(pk=rows[0].contact_ref).delete()  # the person is gone
        Contact.objects.filter(pk=rows[1].contact_ref).update(mobile="+639179990000")  # a different number now
        self.assertEqual(self.code(self.go(batch)), (409, "nothing_to_continue"))
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.not_sent), ("ended", 2))

    def test_one_that_cannot_be_restored_stays_not_sent_and_the_rest_go(self):
        batch = self.ended()
        rows = list(batch.recipients.filter(state="not_sent").order_by("position"))
        Contact.objects.filter(pk=rows[0].contact_ref).delete()
        body = self.go(batch).json()
        self.assertEqual((body["waiting"], body["not_sent"]), (1, 1))
        rows[0].refresh_from_db()
        self.assertEqual((rows[0].state, "*" in rows[0].mobile), ("not_sent", True))

    def test_another_store_cannot_continue_my_batch(self):
        batch = self.ended()
        response = self.send("post", f"{self.ben_base}sms/bulk/batches/{batch.public_id}/continue/", client=self.ben)
        self.assertEqual(response.status_code, 404)
        batch.refresh_from_db()
        self.assertEqual(batch.state, "ended")

    def test_a_contact_of_another_store_with_the_same_pk_is_not_read(self):
        batch = self.ended()
        row = batch.recipients.filter(state="not_sent").first()
        Contact.objects.filter(pk=row.contact_ref).update(site=self.ben_site)
        self.assertEqual(self.go(batch).json()["waiting"], 1)


class ContinueSendTests(ContinueCase):
    def test_opt_out_after_the_end_is_skipped_at_send(self):
        batch = self.ended()
        self.go(batch)
        InquirySmsOptOut.objects.create(site=self.site, mobile=NUMBERS[1])
        self.pump(3)
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.sent, batch.not_sent), ("finished", 2, 1))
        self.assertNotIn(NUMBERS[1], [r.recipient for r in self.records()])

    def test_a_hold_after_continue_ends_it_again_and_it_can_continue_again(self):
        batch = self.ended()
        self.go(batch)
        self.hold()
        self.feed()
        self.hold(False)
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.end_reason, batch.not_sent), ("ended", "site_held", 2))
        self.assertEqual(self.go(batch).status_code, 200)

    def test_the_window_applies(self):
        batch = self.ended()
        self.go(batch)
        before = self.records().count()
        self.feed(at(5, 59))
        self.feed(at(22))
        self.assertEqual(self.records().count(), before)
        self.feed(at(6))
        self.assertEqual(self.records().count(), before + 1)

    def test_the_daily_cap_applies(self):
        batch = self.ended()
        set_policy(store_bulk_daily_limit=1)  # the one already sent used it
        self.go(batch)
        self.pump(3)
        self.assertEqual(self.records().count(), 1)
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.sent), ("waiting", 1))
        with mock.patch.object(store_bulk, "in_window", return_value=True):
            self.assertEqual(self.get(f"{self.root}batches/{batch.public_id}/").json()["paused_reason"], "daily_cap")


class ContinueUnmaskedTests(ContinueCase):
    def test_an_ended_batch_with_a_text_still_queued_keeps_full_numbers_and_continues(self):
        batch = self.start()
        self.feed()  # the first text is queued, the phone has not reported it
        self.hold()
        with mock.patch("apps.inquiries.store_bulk.services.cancel_sms", return_value=False):
            self.feed()
        self.hold(False)
        batch.refresh_from_db()
        self.assertEqual((batch.state, batch.end_reason), ("ended", "site_held"))
        self.assertEqual(batch.recipients.filter(state="queued").count(), 1)
        left = list(batch.recipients.filter(state="not_sent"))
        self.assertEqual(len(left), 2)
        self.assertTrue(all("*" not in r.mobile for r in left), "not over yet, so not masked")
        body = self.go(batch).json()
        self.assertEqual((body["state"], body["waiting"]), ("waiting", 3))  # 2 waiting + the 1 still queued
        self.assertEqual(batch.recipients.filter(state="waiting").count(), 2)
