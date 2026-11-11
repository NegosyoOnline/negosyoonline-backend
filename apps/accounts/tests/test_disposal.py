import json
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.accounts import retention as rules
from apps.accounts.models import (
    Account,
    AccountAuditEvent,
    AccountMobile,
    ExternalIdentity,
    MobileOtpChallenge,
    PolicyAcceptance,
    Session,
)
from apps.accounts.tests.disposal_base import (
    EMAIL,
    MOBILE,
    OTHER_EMAIL,
    DisposalTestCase,
    full_account,
    snapshot,
)
from apps.common import retention
from apps.notifications.models import EmailMessage, SmsMessage
from apps.operators.models import OperatorAuditEntry
from apps.sites.models import Onboarding, Site, SiteBusinessProfile, Subdomain

RULE = "never_verified_accounts"
COUNTS = {
    "sessions": 2,
    "sign_in_identities": 1,
    "policy_acceptances": 1,
    "account_audit_events": 1,
    "mobile_numbers": 1,
    "mobile_challenges": 2,
    "sites": 1,
    "business_profiles": 1,
    "web_addresses_retired": 1,
    "email_send_records": 1,
    "earlier_address_send_records": 0,
}
KEPT_ACCOUNT = ("public_id", "email_verified_at", "created_at", "referral_code", "referred_by_id")


def disposed(**filters):
    return OperatorAuditEntry.objects.filter(action="account_disposed", **filters)


@override_settings(RETENTION_ENABLED=True)
class DisposeAccountTests(DisposalTestCase):
    def dispose(self, **extra):
        return rules.dispose_account(
            self.account, now=self.now, rule="request", actor=self.operator,
            request_ref="DPO-2026-001", **extra,
        )

    def test_the_account_row_is_kept_with_its_personal_fields_blanked(self):
        before = Account.objects.filter(pk=self.account.pk).values(*KEPT_ACCOUNT).get()
        self.assertEqual(self.dispose(), COUNTS)
        account = Account.objects.get(pk=self.account.pk)
        self.assertEqual(account.email, f"deleted-{account.pk}@deleted.invalid")
        self.assertEqual(account.name, "")
        self.assertEqual(account.closed_at, self.now)
        self.assertEqual(
            Account.objects.filter(pk=account.pk).values(*KEPT_ACCOUNT).get(), before
        )
        self.assertTrue(rules.is_disposed(account))

    def test_a_closing_date_already_on_the_account_is_kept(self):
        earlier = self.now - timedelta(days=3)
        Account.objects.filter(pk=self.account.pk).update(closed_at=earlier)
        self.dispose()
        self.assertEqual(Account.objects.get(pk=self.account.pk).closed_at, earlier)

    def test_sessions_and_sign_in_identities_are_removed(self):
        self.dispose()
        self.assertFalse(Session.objects.filter(account=self.account).exists())
        self.assertFalse(ExternalIdentity.objects.filter(account=self.account).exists())

    def test_its_mobile_number_and_verification_challenges_are_removed(self):
        self.dispose()
        self.assertFalse(AccountMobile.objects.filter(account=self.account).exists())
        self.assertFalse(MobileOtpChallenge.objects.filter(account=self.account).exists())

    def test_another_account_holding_the_same_number_keeps_its_own_rows(self):
        self.dispose()
        self.assertEqual(AccountMobile.objects.get(account=self.other).number, MOBILE)
        self.assertEqual(MobileOtpChallenge.objects.filter(account=self.other).count(), 2)

    def test_an_account_without_a_mobile_number_is_disposed_of(self):
        AccountMobile.objects.filter(account=self.account).delete()
        MobileOtpChallenge.objects.filter(account=self.account).delete()
        counts = self.dispose()
        self.assertEqual((counts["mobile_numbers"], counts["mobile_challenges"]), (0, 0))

    def test_sms_send_records_are_not_touched(self):
        SmsMessage.objects.create(
            recipient=MOBILE, category="code", purpose="mobile_verify", priority=10,
            expires_at=self.now,
        )
        before = list(SmsMessage.objects.values())
        self.dispose()
        self.assertEqual(list(SmsMessage.objects.values()), before)

    def test_the_mobile_audit_events_hold_no_number_and_are_kept(self):
        self.dispose()
        event = AccountAuditEvent.objects.get(account=self.account, event="mobile_verified")
        self.assertEqual(event.detail, {})

    def test_the_policy_acceptance_stays_without_its_ip(self):
        before = PolicyAcceptance.objects.filter(account=self.account).values(
            "policy_version", "policies", "accepted_at"
        ).get()
        self.dispose()
        row = PolicyAcceptance.objects.get(account=self.account)
        self.assertIsNone(row.ip)
        self.assertEqual(
            {"policy_version": row.policy_version, "policies": row.policies,
             "accepted_at": row.accepted_at},
            before,
        )

    def test_audit_events_stay_and_only_the_email_change_loses_its_detail(self):
        before = list(
            AccountAuditEvent.objects.filter(account=self.account)
            .order_by("pk").values("pk", "event", "created_at")
        )
        self.dispose()
        events = AccountAuditEvent.objects.filter(account=self.account).order_by("pk")
        self.assertEqual(list(events.values("pk", "event", "created_at")), before)
        self.assertEqual(events.get(event="email_changed").detail, {})
        self.assertEqual(
            events.get(event="signed_in").detail, {"method": "email_code", "session": "ses_1"}
        )

    def test_the_site_is_blanked_closed_and_its_web_address_retired(self):
        site_before = Site.objects.filter(account=self.account).values(
            "pk", "public_id", "template", "accent_color", "operator_hold", "created_at"
        ).get()
        self.dispose()
        site = Site.objects.get(account=self.account)
        for field in ("contact_email", "contact_mobile", "facebook_url", "instagram_url", "tiktok_url"):
            self.assertEqual(getattr(site, field), "", field)
        self.assertEqual(site.display_name, f"Deleted site #{site.pk}")
        self.assertEqual(site.deleted_at, self.now)
        self.assertEqual(
            Site.objects.filter(pk=site.pk).values(*site_before).get(), site_before
        )
        profile = SiteBusinessProfile.objects.get(site=site)
        for field in (
            "business_name", "registered_name", "tin", "dti_business_number", "bir_rsn",
            "business_address", "complaints_contact_name", "privacy_contact_email",
        ):
            self.assertEqual(getattr(profile, field), "", field)
        self.assertEqual(profile.shipping_luzon_estimate, "2-5 business days")
        self.assertTrue(profile.shipping_estimates_confirmed)
        name = Subdomain.objects.get(name="anas-store")
        self.assertEqual((name.site, name.state, name.retired_at), (site, "retired", self.now))
        self.assertTrue(Onboarding.objects.filter(account=self.account, first_site=site).exists())

    def test_its_send_records_are_deleted_except_the_admin_sign_in_ones(self):
        self.dispose()
        self.assertEqual(
            list(EmailMessage.objects.filter(recipient=EMAIL).values_list("category", flat=True)),
            ["admin_signin_code"],
        )

    def email_changes(self, *changes):
        """Replace the account's email changes with these (old, new, days ago) ones."""
        AccountAuditEvent.objects.filter(account=self.account, event="email_changed").delete()
        for old, new, days_ago in changes:
            event = AccountAuditEvent.objects.create(
                account=self.account, event="email_changed", detail={"old": old, "new": new}
            )
            AccountAuditEvent.objects.filter(pk=event.pk).update(
                created_at=self.now - timedelta(days=days_ago)
            )

    def sent(self, recipient, category, days_ago, minutes_later=0):
        row = EmailMessage.objects.create(recipient=recipient, category=category)
        EmailMessage.objects.filter(pk=row.pk).update(
            created_at=self.now - timedelta(days=days_ago) + timedelta(minutes=minutes_later)
        )
        return row.pk

    def test_send_records_of_every_earlier_address_are_deleted(self):
        """The account went first@ -> second@ -> its address today."""
        first, second = "first@example.test", "second@example.test"
        self.email_changes((first, second, 20), (second, EMAIL, 10))
        gone = [
            self.sent(first, "signin_code", 30),
            self.sent("First@Example.test", "signin_code", 25),
            self.sent(first, "email_changed_notice", 20, minutes_later=1),
            self.sent(second, "email_change_code", 20, minutes_later=-1),
            self.sent(second, "signin_code", 15),
            self.sent(second, "email_changed_notice", 10, minutes_later=1),
        ]
        stay = [
            self.sent(first, "admin_signin_code", 30),
            self.sent(second, "admin_lockout", 15),
            # Someone else signed up with the first address after the account left it.
            self.sent(first, "signin_code", 5),
            self.sent(first, "email_changed_notice", 3),
        ]
        newcomer = Account.objects.create(email=first)
        before_other = self.rows_of_other()

        counts = self.dispose()

        self.assertEqual(counts, {**COUNTS, "account_audit_events": 2,
                                  "earlier_address_send_records": 6})
        self.assertFalse(EmailMessage.objects.filter(pk__in=gone).exists())
        self.assertEqual(EmailMessage.objects.filter(pk__in=stay).count(), 4)
        self.assertEqual(Account.objects.get(pk=newcomer.pk).email, first)
        self.assertEqual(self.rows_of_other(), before_other)
        entry = disposed().get()
        self.assertEqual(entry.detail["counts"]["earlier_address_send_records"], 6)
        text = json.dumps([entry.reason, entry.detail])
        self.assertNotIn("first@", text)
        self.assertNotIn("second@", text)

    def test_the_dry_run_counts_the_earlier_addresses_and_changes_nothing(self):
        self.email_changes(("first@example.test", EMAIL, 10))
        self.sent("first@example.test", "signin_code", 30)
        before = snapshot()
        expected = {**COUNTS, "earlier_address_send_records": 1}
        self.assertEqual(self.dispose(dry_run=True), expected)
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.dispose(), expected)

    def test_an_address_the_account_came_back_to_is_counted_once(self):
        """ana@ -> away@ -> ana@: the records of ana@ are the current address's, not earlier."""
        self.email_changes((EMAIL, "away@example.test", 20), ("away@example.test", EMAIL, 10))
        self.sent("away@example.test", "signin_code", 15)
        expected = {**COUNTS, "account_audit_events": 2, "earlier_address_send_records": 1}
        self.assertEqual(self.dispose(dry_run=True), expected)
        self.assertEqual(self.dispose(), expected)
        self.assertEqual(
            list(EmailMessage.objects.exclude(recipient=OTHER_EMAIL)
                 .values_list("recipient", "category")),
            [(EMAIL, "admin_signin_code")],
        )

    def test_an_email_change_with_no_detail_is_skipped(self):
        AccountAuditEvent.objects.filter(account=self.account, event="email_changed").update(
            detail={}
        )
        self.assertEqual(self.dispose(), COUNTS)

    def test_no_personal_value_of_the_account_is_left_anywhere(self):
        # The other account holds the same test values, and the admin records are not ours.
        EmailMessage.objects.filter(category="admin_signin_code").delete()
        self.dispose()
        rules.dispose_account(self.other, now=self.now, rule="request", actor=self.operator)
        self.assert_no_personal_value(json.dumps(snapshot(), default=str))

    def test_the_audit_entry_holds_counts_and_field_names_only(self):
        self.dispose()
        entry = disposed().get()
        self.assertEqual(entry.operator, self.operator)
        self.assertEqual((entry.target_type, entry.target_id), ("account", self.account.public_id))
        self.assertEqual(entry.reason, "request: DPO-2026-001")
        self.assertEqual(entry.detail["counts"], COUNTS)
        self.assertEqual(entry.detail["blanked"]["account"], ["email", "name"])
        self.assertIn("tin", entry.detail["blanked"]["site_business_profile"])
        self.assertEqual(entry.personal, {})
        self.assertNotIn("account_mobile", entry.detail["blanked"])  # deleted, not blanked
        self.assert_no_personal_value(json.dumps([entry.reason, entry.detail]))

    def test_another_account_is_untouched(self):
        before = self.rows_of_other()
        self.dispose()
        self.assertEqual(self.rows_of_other(), before)

    def test_a_second_run_changes_nothing_and_writes_nothing(self):
        self.dispose()
        before = snapshot()
        self.assertIsNone(self.dispose())
        self.assertEqual(snapshot(), before)
        self.assertEqual(disposed().count(), 1)

    def test_an_account_without_a_site_is_disposed_of(self):
        bare = full_account("cora@example.test", "cora", now=self.now, with_site=False)
        counts = rules.dispose_account(bare, now=self.now, rule="request", actor=self.operator)
        self.assertEqual((counts["sites"], counts["web_addresses_retired"]), (0, 0))
        self.assertTrue(rules.is_disposed(Account.objects.get(pk=bare.pk)))
        self.assertFalse(Onboarding.objects.filter(account=bare).exists())

    def test_the_dry_run_reports_the_counts_and_changes_nothing(self):
        before = snapshot()
        self.assertEqual(self.dispose(dry_run=True), COUNTS)
        self.assertEqual(snapshot(), before)

    @override_settings(RETENTION_ENABLED=False)
    def test_switched_off_it_refuses_and_only_the_dry_run_works(self):
        before = snapshot()
        with self.assertRaises(rules.SwitchedOff):
            self.dispose()
        self.assertEqual(self.dispose(dry_run=True), COUNTS)
        self.assertEqual(snapshot(), before)

    def test_a_request_reference_that_looks_like_an_address_is_refused(self):
        before = snapshot()
        with self.assertRaises(ValueError):
            rules.dispose_account(
                self.account, now=self.now, rule="request", actor=self.operator,
                request_ref=EMAIL,
            )
        self.assertEqual(snapshot(), before)

    def test_sign_in_is_refused_afterwards(self):
        from apps.accounts.services import _refuse_closed
        from apps.common.errors import ApiError

        self.dispose()
        with self.assertRaises(ApiError) as raised:
            _refuse_closed(Account.objects.get(pk=self.account.pk))
        self.assertEqual(raised.exception.code, "account_closed")


@override_settings(RETENTION_ENABLED=True)
class NeverVerifiedRuleTests(DisposalTestCase):
    def unverified(self, slug, age):
        account = full_account(
            f"{slug}@example.test", slug, now=self.now, verified=False, with_site=False
        )
        Account.objects.filter(pk=account.pk).update(created_at=self.now - age)
        return account

    def build(self):
        self.old = [self.unverified(f"old{n}", timedelta(days=30 + n)) for n in range(3)]
        self.young = self.unverified("young", timedelta(days=30, minutes=-1))
        Account.objects.filter(pk=self.account.pk).update(created_at=self.now - timedelta(days=90))

    def test_it_is_registered_behind_the_switch(self):
        self.assertIn(RULE, retention.names())
        self.assertTrue(rules.never_verified_accounts.behind_switch)

    def test_disposes_of_unverified_accounts_of_30_days_and_no_others(self):
        self.build()
        report = retention.run_all(now=self.now, only=[RULE])
        self.assertEqual(report.counts, {RULE: 3})
        for account in self.old:
            self.assertTrue(rules.is_disposed(Account.objects.get(pk=account.pk)))
        for account in (self.young, self.account, self.other):
            fresh = Account.objects.get(pk=account.pk)
            self.assertFalse(rules.is_disposed(fresh))
            self.assertEqual(fresh.email, account.email)
            self.assertEqual(Session.objects.filter(account=fresh).count(), 2)

    def test_each_disposal_is_logged_and_the_run_log_has_the_count(self):
        self.build()
        retention.run_all(now=self.now, only=[RULE])
        entries = disposed()
        self.assertEqual(
            sorted(entries.values_list("target_id", flat=True)),
            sorted(account.public_id for account in self.old),
        )
        for entry in entries:
            self.assertIsNone(entry.operator)
            self.assertEqual(entry.reason, "never_verified")
        run = OperatorAuditEntry.objects.get(action="retention_run")
        self.assertIsNone(run.operator)
        self.assertEqual(run.detail["counts"], {RULE: 3})
        self.assertNotIn("example.test", json.dumps(run.detail))

    def test_a_second_run_takes_nothing(self):
        self.build()
        retention.run_all(now=self.now, only=[RULE])
        report = retention.run_all(now=self.now, only=[RULE])
        self.assertEqual(report.counts, {RULE: 0})
        self.assertEqual(disposed().count(), 3)

    def test_the_dry_run_counts_the_same_and_changes_nothing(self):
        self.build()
        before = snapshot()
        report = retention.run_all(now=self.now, dry_run=True, only=[RULE])
        self.assertEqual(report.counts, {RULE: 3})
        self.assertEqual(snapshot(), before)

    @override_settings(RETENTION_ENABLED=False)
    def test_switched_off_nothing_is_blanked_whoever_calls(self):
        self.build()
        before = snapshot()
        self.assertEqual(rules.never_verified_accounts(self.now, False), 0)
        report = retention.run_all(now=self.now, only=[RULE])  # as an automatic start would
        self.assertEqual(report.switched_off, [RULE])
        before["operators.OperatorAuditEntry"] = snapshot()["operators.OperatorAuditEntry"]
        self.assertEqual(snapshot(), before)
        self.assertFalse(disposed().exists())

    def test_the_other_rules_leave_accounts_alone(self):
        self.build()
        others = [name for name in retention.names() if name != RULE]
        retention.run_all(now=self.now, only=others)
        self.assertFalse(disposed().exists())
        self.assertEqual(Account.objects.filter(closed_at__isnull=False).count(), 0)


@override_settings(RETENTION_ENABLED=True)
class DisposeAccountCommandTests(DisposalTestCase):
    def run_command(self, *args, account=None, **options):
        out = StringIO()
        options.setdefault("actor", "coach")
        options.setdefault("request_ref", "DPO-2026-001")
        call_command(
            "dispose_account", account or self.account.public_id, *args, stdout=out, **options
        )
        return out.getvalue()

    def test_the_dry_run_prints_the_counts_and_changes_nothing(self):
        before = snapshot()
        out = self.run_command("--dry-run")
        self.assertIn("Dry run: nothing was changed.", out)
        self.assertIn(self.account.public_id, out)
        for name, count in COUNTS.items():
            self.assertIn(f"{name}: {count}", out)
        self.assert_no_personal_value(out)
        self.assertEqual(snapshot(), before)

    def test_it_disposes_by_public_id_and_names_the_operator(self):
        out = self.run_command()
        self.assertIn(f"Disposed of account {self.account.public_id}.", out)
        self.assertIn("sessions: 2", out)
        self.assert_no_personal_value(out)
        self.assertEqual(disposed().get().operator, self.operator)
        self.assertEqual(Account.objects.get(pk=self.other.pk).email, OTHER_EMAIL)

    def test_it_finds_the_account_by_email_in_any_case(self):
        out = self.run_command(account="  ANA@Example.Test ")
        self.assertIn(f"Disposed of account {self.account.public_id}.", out)
        self.assert_no_personal_value(out)

    def test_a_second_run_says_so_and_writes_nothing(self):
        self.run_command()
        before = snapshot()
        self.assertIn("already disposed of", self.run_command())
        self.assertEqual(snapshot(), before)

    @override_settings(RETENTION_ENABLED=False)
    def test_switched_off_only_the_dry_run_works(self):
        before = snapshot()
        with self.assertRaisesMessage(CommandError, "RETENTION_ENABLED"):
            self.run_command()
        self.assertIn("sessions: 2", self.run_command("--dry-run"))
        self.assertEqual(snapshot(), before)

    def test_an_unknown_account_or_actor_is_refused(self):
        before = snapshot()
        with self.assertRaisesMessage(CommandError, "No such account."):
            self.run_command(account="acc_missing")
        with self.assertRaisesMessage(CommandError, "--actor must be an active staff user."):
            self.run_command(actor="nobody")
        self.assertEqual(snapshot(), before)
