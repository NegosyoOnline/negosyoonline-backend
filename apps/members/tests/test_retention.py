"""Account disposal, account export and the old-session rule, for the members of a site."""
import json
from datetime import timedelta

from django.test import override_settings

from apps.accounts import retention as rules
from apps.accounts.tests.disposal_base import DisposalTestCase
from apps.common import retention as registry
from apps.members import retention, services, sessions
from apps.members.models import Member, MemberSession
from apps.operators.models import OperatorAuditEntry
from apps.sites.models import Site

KEYS = ("members", "member_sessions")
NAME, EMAIL, MOBILE = "Maria Santos", "maria@buyer.test", "+639171234567"
THEIR_EMAIL = "juan@buyer.test"


@override_settings(MEMBERS_ENABLED=True, RETENTION_ENABLED=True)
class MembersDisposalTestCase(DisposalTestCase):
    def setUp(self):
        super().setUp()
        self.site = Site.objects.get(account=self.account)
        self.their_site = Site.objects.get(account=self.other)
        self.member = services.create_member(
            self.site, full_name=NAME, mobile=MOBILE, email=EMAIL
        )
        self.theirs = services.create_member(
            self.their_site, full_name="Juan Cruz", mobile="09175550199", email=THEIR_EMAIL
        )
        self.live, _ = sessions.create_session(self.site, self.member, ip="203.0.113.9")
        self.revoked, _ = sessions.create_session(self.site, self.member)
        sessions.revoke_session(self.revoked)
        self.their_session, _ = sessions.create_session(self.their_site, self.theirs)

    def dispose(self, **extra):
        return rules.dispose_account(
            self.account, now=self.now, rule=rules.REQUEST, actor=self.operator,
            request_ref="DPO-2026-010", **extra,
        )


class DisposalTests(MembersDisposalTestCase):
    def test_members_are_blanked_their_sessions_deleted_and_the_rows_kept(self):
        counts = self.dispose()

        self.assertEqual((counts["members"], counts["member_sessions"]), (1, 2))
        row = Member.objects.get(pk=self.member.pk)
        self.assertEqual(row.full_name, "")
        self.assertNotIn(EMAIL, row.email)
        self.assertRegex(row.email, r"@deleted\.invalid$")
        self.assertRegex(row.mobile, r"^\+639[0-9]{9}$")
        self.assertNotEqual(row.mobile, MOBILE)
        self.assertEqual((row.mobile_state, row.email_state), ("pending", "pending"))
        self.assertEqual(row.public_id, self.member.public_id)
        self.assertEqual(MemberSession.objects.filter(member=self.member).count(), 0)

    def test_a_disposed_member_cannot_be_signed_in_or_reached(self):
        self.dispose()
        self.assertFalse(Member.objects.filter(email=EMAIL).exists())

    def test_the_contact_is_free_for_a_new_member_afterwards(self):
        self.dispose()
        services.create_member(self.their_site, full_name="New", mobile=MOBILE, email=EMAIL)

    def test_another_accounts_members_and_sessions_are_untouched(self):
        self.dispose()
        self.theirs.refresh_from_db()
        self.assertEqual((self.theirs.full_name, self.theirs.email), ("Juan Cruz", THEIR_EMAIL))
        self.assertTrue(MemberSession.objects.filter(pk=self.their_session.pk).exists())

    def test_the_audit_entry_names_the_fields_and_never_a_value(self):
        self.dispose()
        entry = OperatorAuditEntry.objects.get(action="account_disposed")
        self.assertEqual(entry.detail["blanked"]["members"], ["full_name", "mobile", "email"])
        self.assertEqual(entry.detail["counts"]["members"], 1)
        text = json.dumps(entry.detail)
        for value in (NAME, EMAIL, MOBILE, "203.0.113.9"):
            self.assertNotIn(value, text)

    def test_the_dry_run_counts_and_changes_nothing(self):
        before = list(Member.objects.values()), list(MemberSession.objects.values())
        counts = self.dispose(dry_run=True)
        self.assertEqual((counts["members"], counts["member_sessions"]), (1, 2))
        self.assertEqual((list(Member.objects.values()), list(MemberSession.objects.values())), before)

    @override_settings(MEMBERS_ENABLED=False)
    def test_with_the_switch_off_nothing_about_members_is_read_or_reported(self):
        counts = self.dispose()
        entry = OperatorAuditEntry.objects.get(action="account_disposed")
        for key in KEYS:
            self.assertNotIn(key, counts)
            self.assertNotIn(key, entry.detail["blanked"])
        self.assertEqual(Member.objects.get(pk=self.member.pk).email, EMAIL)


class ExportTests(MembersDisposalTestCase):
    def export(self):
        return rules.export_account_data(self.account, actor=self.operator, request_ref="DPO-2")

    def test_it_holds_the_members_of_the_accounts_sites_only(self):
        data = self.export()
        self.assertEqual(data["members"], [{
            "site": self.site.public_id, "id": self.member.public_id, "full_name": NAME,
            "mobile": MOBILE, "mobile_verified_at": None, "email": EMAIL,
            "email_verified_at": None, "source": "onboarding",
            "created_at": self.member.created_at.isoformat(),
        }])
        self.assertNotIn(THEIR_EMAIL, json.dumps(data))
        self.assertEqual(len(data["member_sessions"]), 2)

    def test_it_holds_no_token_hash_no_address_and_no_row_number(self):
        text = json.dumps(self.export())
        for secret in (self.live.token_hash, self.revoked.token_hash, "203.0.113.9"):
            self.assertNotIn(secret, text)
        for row in data_rows(self.export()):
            self.assertNotIn("pk", row)
            self.assertNotIn("token_hash", row)

    def test_the_audit_entry_has_counts_only(self):
        self.export()
        entry = OperatorAuditEntry.objects.get(action="account_exported")
        self.assertEqual((entry.detail["counts"]["members"], entry.detail["counts"]["member_sessions"]), (1, 2))
        self.assertNotIn(EMAIL, json.dumps(entry.detail))

    @override_settings(MEMBERS_ENABLED=False)
    def test_with_the_switch_off_the_export_has_no_member_keys(self):
        data = self.export()
        for key in KEYS:
            self.assertNotIn(key, data)


def data_rows(data):
    return [*data["members"], *data["member_sessions"]]


class ExpiredSessionRuleTests(MembersDisposalTestCase):
    def run_rule(self, dry_run=False):
        return retention.expired_member_sessions(self.now, dry_run)

    def test_it_is_registered(self):
        self.assertIn("expired_member_sessions", registry.names())

    def test_sessions_are_deleted_thirty_days_after_they_expired_or_were_revoked(self):
        old = timedelta(days=31)
        expired, _ = sessions.create_session(self.site, self.member, now=self.now - old - old)
        MemberSession.objects.filter(pk=expired.pk).update(expires_at=self.now - old)
        long_revoked, _ = sessions.create_session(self.site, self.member)
        MemberSession.objects.filter(pk=long_revoked.pk).update(revoked_at=self.now - old)
        recent_expired, _ = sessions.create_session(self.site, self.member)
        MemberSession.objects.filter(pk=recent_expired.pk).update(expires_at=self.now - timedelta(days=5))

        self.assertEqual(self.run_rule(dry_run=True), 2)
        self.assertEqual(MemberSession.objects.count(), 6)
        self.assertEqual(self.run_rule(), 2)
        self.assertEqual(
            set(MemberSession.objects.values_list("pk", flat=True)),
            {self.live.pk, self.revoked.pk, self.their_session.pk, recent_expired.pk},
        )

    @override_settings(RETENTION_ENABLED=False)
    def test_with_retention_off_a_real_run_deletes_nothing(self):
        MemberSession.objects.update(expires_at=self.now - timedelta(days=60))
        self.assertEqual(self.run_rule(), 0)
        self.assertEqual(MemberSession.objects.count(), 3)

    @override_settings(MEMBERS_ENABLED=False)
    def test_with_members_off_it_reads_nothing(self):
        MemberSession.objects.update(expires_at=self.now - timedelta(days=60))
        self.assertEqual(self.run_rule(dry_run=True), 0)
