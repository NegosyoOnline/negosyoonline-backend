"""The member address edit history in an erasure (plan z8uq9mek5v, manager condition): the rows are
counted by the dry run, blanked (old, new and ip_advisory) with the member in the same run, and an
active address_change hold never blocks or skips the erasure."""
from datetime import timedelta

from django.utils import timezone

from apps.lifecycle.dry_run import erasure_dry_run
from apps.lifecycle.tests.base import NOW
from apps.lifecycle.tests.run_base import NAME, RunTestCase
from apps.members import holds
from apps.members import retention as member_retention
from apps.members.models import MemberAddressEvent, MemberContactAudit, MemberSecurityHold


class AddressEventErasureTests(RunTestCase):
    def event(self):
        return MemberAddressEvent.objects.get(site=self.site)

    def test_the_dry_run_counts_the_event_rows(self):
        counts = erasure_dry_run(NOW)["eligible"][0]["counts"]
        self.assertEqual(counts["member_address_events"], 1)
        self.assertEqual(MemberAddressEvent.objects.get(site=self.site).ip_advisory, "203.0.113.7")  # read only

    def test_the_run_blanks_old_new_and_ip_and_keeps_the_row(self):
        batch = self.approved()
        self.assertEqual(self.event().old, {"line": f"Old {NAME}"})
        self.run_batch(batch)
        event = self.event()
        self.assertEqual((event.old, event.new, event.ip_advisory), ({}, {}, ""))
        self.assertEqual(event.member_id, self.planted["member"].pk)
        item = self.item(batch)
        self.assertEqual(item.counts["member_address_events"], 1)
        for row in MemberAddressEvent.objects.filter(member=self.planted["member"]):
            self.assertNotIn(NAME, str(row.old) + str(row.new))

    def test_an_active_recovery_hold_does_not_block_or_skip_the_erasure(self):
        member = self.planted["member"]
        audit = MemberContactAudit.objects.create(
            site=self.site, member=member, kind="recovery", field="email", actor_type="owner",
        )
        MemberSecurityHold.objects.create(
            site=self.site, member=member, kind="recovery", starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(hours=20), audit=audit,
        )
        self.assertIsNotNone(holds.active_hold(member, "address_change"))
        batch = self.approved()
        result = self.run_batch(batch)
        self.assertEqual(result["erased"], 1)
        event = self.event()
        self.assertEqual((event.old, event.new, event.ip_advisory), ({}, {}, ""))
        member.refresh_from_db()
        self.assertEqual((member.full_name, member.delivery_address), ("", {}))


class AddressEventRetentionTests(RunTestCase):
    """The account-level paths (member blanking and the export) cover the event rows too."""

    @property
    def account(self):
        return self.site.account

    def test_blank_members_counts_then_blanks_old_new_and_ip(self):
        with self.settings(MEMBERS_ENABLED=True):
            counts = member_retention.blank_members_of(self.account, True)
            self.assertEqual(counts["member_address_events"], 1)
            self.assertEqual(MemberAddressEvent.objects.get(site=self.site).ip_advisory, "203.0.113.7")
            member_retention.blank_members_of(self.account)
            self.assertIn("member_address_events", member_retention.blanked())
        event = MemberAddressEvent.objects.get(site=self.site)
        self.assertEqual((event.old, event.new, event.ip_advisory), ({}, {}, ""))

    def test_the_export_includes_the_events_without_the_ip(self):
        with self.settings(MEMBERS_ENABLED=True):
            exported = member_retention.export_members_of(self.account)
        rows = exported["member_address_events"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["old"], {"line": f"Old {NAME}"})
        self.assertEqual(rows[0]["new"], {"line": f"New {NAME}"})
        self.assertNotIn("ip_advisory", rows[0])
