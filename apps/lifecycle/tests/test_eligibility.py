"""Which sites are due for erasure (plan z8uq9mb22v, sections 4 and 5, tests of section 9)."""
from django.contrib.auth import get_user_model
from django.test import override_settings

from apps.billing.models import BankTransferPayment, BillingNotice, Subscription
from apps.lifecycle import eligibility
from apps.lifecycle.dry_run import erasure_dry_run
from apps.lifecycle.models import ErasureBatch, ErasureHold, ErasureItem
from apps.lifecycle.tests.base import (
    DUE, ENDED, NOW, LifecycleTestCase, State, days, ended_site, open_charge, second_site,
    submitted_proof,
)


def classify(at=NOW):
    return eligibility.classify(at)


class EligibleTests(LifecycleTestCase):
    def test_every_ended_state_is_eligible_after_its_date(self):
        for index, state in enumerate(
            (State.SUSPENDED, State.CANCELLED, State.CANCELLED_NO_PAYMENT_METHOD)
        ):
            ended_site(f"p{index}@example.test", f"store-{index}", state=state)
        self.assertEqual(len(classify().eligible), 3)

    def test_states_that_are_still_served_are_never_listed(self):
        for index, state in enumerate(
            (State.TRIAL_ACTIVE, State.ACTIVE, State.PAYMENT_REQUIRES_ATTENTION)
        ):
            ended_site(f"p{index}@example.test", f"store-{index}", state=state)
        found = classify()
        self.assertEqual((found.eligible, found.near, found.refused), ([], [], []))

    def test_before_at_and_after_the_deletion_date(self):
        sub = ended_site()
        self.assertEqual(classify(DUE - days(15)).near, [])
        self.assertEqual([r.pk for r in classify(DUE - days(10)).near], [sub.pk])
        self.assertEqual(classify(DUE - days(10)).eligible, [])
        self.assertEqual(classify(DUE - days(1)).eligible, [])
        self.assertEqual(len(classify(DUE).eligible), 1)  # at the instant
        self.assertEqual(len(classify(DUE + days(5)).eligible), 1)

    def test_a_cancellation_inside_its_paid_term_is_never_listed(self):
        ended_site(ended=NOW + days(3), due=NOW - days(1))
        found = classify()
        self.assertEqual((found.eligible, found.near, found.refused), ([], [], []))

    def test_a_site_whose_dates_never_started_is_not_listed(self):
        sub = ended_site()
        Subscription.objects.filter(pk=sub.pk).update(deletion_due_at=None, download_until=None)
        self.assertEqual(classify().eligible, [])

    def test_a_billing_exempt_site_is_never_listed(self):
        ended_site(billing_exempt=True)
        self.assertEqual(classify().eligible, [])

    def test_a_site_reactivated_by_payment_has_no_date_and_drops_out(self):
        sub = ended_site()
        self.assertEqual(len(classify().eligible), 1)
        Subscription.objects.filter(pk=sub.pk).update(
            state=State.ACTIVE, service_ended_at=None, download_until=None, deletion_due_at=None
        )
        self.assertEqual(classify().eligible, [])


class RefusedTests(LifecycleTestCase):
    def test_a_proof_under_review_refuses_and_a_decided_one_does_not(self):
        sub = ended_site()
        proof = submitted_proof(sub)
        found = classify()
        self.assertEqual(found.eligible, [])
        self.assertEqual([r.reason_code for r in found.refused], ["payment_under_review"])
        BankTransferPayment.objects.filter(pk=proof.pk).update(status="rejected")
        self.assertEqual(len(classify().eligible), 1)

    def test_an_open_charge_refuses_a_cancelled_site_but_not_a_suspended_one(self):
        cancelled = ended_site("c@example.test", "c-store", state=State.CANCELLED)
        suspended = ended_site("s@example.test", "s-store", state=State.SUSPENDED)
        open_charge(cancelled)
        open_charge(suspended)
        found = classify()
        self.assertEqual([r.reason_code for r in found.refused], ["unpaid_usage"])
        self.assertEqual(found.refused[0].subscription.pk, cancelled.pk)
        self.assertEqual([c.subscription.pk for c in found.eligible], [suspended.pk])
        self.assertTrue(found.eligible[0].usage_to_write_off)

    def test_a_written_off_charge_does_not_refuse(self):
        cancelled = ended_site(state=State.CANCELLED)
        open_charge(cancelled, status="written_off")
        self.assertEqual(len(classify().eligible), 1)

    def test_a_hold_refuses_and_a_released_hold_does_not(self):
        sub = ended_site()
        user = get_user_model().objects.create_user(username="staff", password="x", is_staff=True)
        hold = ErasureHold.objects.create(
            site=sub.site, reason_code="dispute", reference="T-1", placed_by=user, placed_at=NOW,
        )
        with override_settings(SITE_ERASURE_ENABLED=True):
            found = classify()
            self.assertEqual(found.eligible, [])
            self.assertEqual(
                [(r.reason_code, r.hold_reason) for r in found.refused], [("held", "dispute")]
            )
            ErasureHold.objects.filter(pk=hold.pk).update(released_at=NOW, released_by=user)
            self.assertEqual(len(classify().eligible), 1)

    def test_an_erased_site_is_refused_as_already_erased(self):
        sub = ended_site()
        user = get_user_model().objects.create_user(username="staff", password="x", is_staff=True)
        batch = ErasureBatch.objects.create(
            proposed_by=user, proposed_at=NOW, list_digest="x", expires_at=NOW + days(7),
            status="done",
        )
        ErasureItem.objects.create(
            batch=batch, site=sub.site, subscription_id=sub.pk, deletion_due_at=DUE, status="erased",
        )
        with override_settings(SITE_ERASURE_ENABLED=True):
            found = classify()
        self.assertEqual(found.eligible, [])
        self.assertEqual([r.reason_code for r in found.refused], ["already_erased"])

    def test_with_the_switch_off_no_lifecycle_table_is_read(self):
        ended_site()
        with self.assertNumQueries(4):  # subscriptions, proofs, charges, notices, and nothing more
            found = classify()
        self.assertFalse(found.lifecycle_checked)
        self.assertEqual(len(found.eligible), 1)


class NoticeFlagTests(LifecycleTestCase):
    def test_the_notice_status_is_shown_and_blocks_nothing(self):
        sent = ended_site("a@example.test", "a-store", state=State.CANCELLED)
        unsent = ended_site("b@example.test", "b-store", state=State.CANCELLED_NO_PAYMENT_METHOD)
        paused = ended_site("c@example.test", "c-store", state=State.SUSPENDED)
        BillingNotice.objects.create(
            subscription=sent, code="export_notice", sent_at=NOW,
            idempotency_key=eligibility.export_notice_key(sent),
        )
        result = {c.subscription.pk: c.notice for c in classify().eligible}
        self.assertEqual(result, {
            sent.pk: "sent", unsent.pk: "not_sent", paused.pk: "no_notice_for_state",
        })
        entries = {e["site"]: e for e in erasure_dry_run(NOW)["eligible"]}
        self.assertTrue(entries[sent.site.public_id]["notice_sent"])
        self.assertFalse(entries[unsent.site.public_id]["notice_sent"])
        self.assertEqual(entries[paused.site.public_id]["notice"], "no_notice_for_state")


class AccountStepTests(LifecycleTestCase):
    def step(self, sub, **settings_):
        with override_settings(**settings_):
            entries = {e["site"]: e for e in erasure_dry_run(NOW)["eligible"]}
        return entries[sub.site.public_id]

    def test_the_account_is_disposed_with_its_last_site(self):
        sub = ended_site()
        entry = self.step(sub, RETENTION_ENABLED=True)
        self.assertEqual((entry["account_action"], entry["account_reason"]), ("dispose", ""))

    def test_another_live_site_keeps_the_account(self):
        sub = ended_site()
        second_site(sub)
        entry = self.step(sub, RETENTION_ENABLED=True)
        self.assertEqual(
            (entry["account_action"], entry["account_reason"]), ("keep", "other_live_site")
        )

    def test_two_due_sites_of_one_account_both_dispose_it(self):
        first = ended_site()
        other = second_site(first)
        from apps.billing import services

        Subscription.objects.filter(pk=services.start_subscription(
            other, other.account, now=ENDED - days(30)).pk
        ).update(
            state=State.CANCELLED_NO_PAYMENT_METHOD, service_ended_at=ENDED,
            download_until=ENDED + days(30), deletion_due_at=DUE,
        )
        with override_settings(RETENTION_ENABLED=True):
            entries = erasure_dry_run(NOW)["eligible"]
        self.assertEqual([e["account_action"] for e in entries], ["dispose", "dispose"])

    def test_the_catalog_owner_account_is_kept(self):
        from apps.sante.models import SanteCatalogOwner

        sub = ended_site()
        SanteCatalogOwner.objects.update_or_create(pk=1, defaults={"site": sub.site})
        entry = self.step(sub, RETENTION_ENABLED=True)
        self.assertEqual(
            (entry["account_action"], entry["account_reason"]), ("keep", "catalog_owner")
        )

    def test_retention_off_refuses_the_account_step_at_the_dry_run(self):
        sub = ended_site()
        entry = self.step(sub, RETENTION_ENABLED=False)
        self.assertEqual(
            (entry["account_action"], entry["account_reason"]), ("keep", "retention_switched_off")
        )
