"""The dry run reads and counts and writes nothing (plan z8uq9mb22v, sections 6.1 and 9)."""
import json
from io import StringIO
from unittest import mock

from django.apps import apps
from django.core.management import call_command
from django.test import override_settings

from apps.common import private_files
from apps.lifecycle.dry_run import erasure_dry_run
from apps.lifecycle.tests.base import NOW, LifecycleTestCase, State, days, ended_site
from apps.members.models import Member
from apps.payments.models import SitePaymentAccount
from apps.sites.models import Site, SiteBusinessProfile

# Distinctive personal values, planted on the tables an erasure would blank.
NAME, EMAIL, NUMBER = "Zuleika Planted-Name", "planted.person@example.test", "09998887776"
ACCOUNT_NUMBER = "5550001234567"
VALUES = (NAME, EMAIL, NUMBER, ACCOUNT_NUMBER, "Planted Trading", "+639998887776", "QRKEY-planted")


def plant(subscription):
    site = subscription.site
    Site.objects.filter(pk=site.pk).update(
        contact_email=EMAIL, contact_mobile="+639998887776", display_name=NAME,
    )
    SiteBusinessProfile.objects.filter(site=site).update(
        business_name="Planted Trading", seal_key="seal/QRKEY-planted", tin=NUMBER,
    )
    SitePaymentAccount.objects.create(
        site=site, kind="bank", account_type="savings", provider_name="BDO",
        account_name=NAME, account_number=ACCOUNT_NUMBER, qr_file_key="qr/QRKEY-planted",
    )
    Member.objects.create(
        site=site, full_name=NAME, mobile="+639998887776", email=EMAIL,
        delivery_address={"line": NAME},
    )


def everything():
    """Every row of every table, to show that a call changed nothing."""
    return {
        model._meta.label: list(model._base_manager.order_by("pk").values())
        for model in apps.get_models()
    }


class DryRunTests(LifecycleTestCase):
    def setUp(self):
        self.sub = ended_site()
        plant(self.sub)

    def test_it_lists_the_site_with_counts_and_files(self):
        entry = erasure_dry_run(NOW)["eligible"][0]
        self.assertEqual(entry["site"], self.sub.site.public_id)
        self.assertEqual(entry["days_overdue"], 5)
        counts = entry["counts"]
        self.assertEqual(counts["sites"], 1)
        self.assertEqual(counts["site_payment_accounts"], 1)
        self.assertEqual(counts["members"], 1)
        # The seal and the QR image; no pack proof file is ever counted.
        self.assertEqual(entry["private_files"], 2)
        self.assertEqual(counts["site_seal_files"], 1)

    def test_it_counts_whatever_the_feature_switches_say(self):
        with override_settings(
            PAYMENT_METHODS_ENABLED=False, MEMBERS_ENABLED=False, PACK_ENABLED=False,
            SHOP_ENABLED=False, SANTE_SETTINGS_ENABLED=False,
        ):
            counts = erasure_dry_run(NOW)["eligible"][0]["counts"]
        self.assertEqual((counts["site_payment_accounts"], counts["members"]), (1, 1))

    def test_it_changes_no_row_of_any_table_and_audits_nothing(self):
        for switch in (False, True):
            with override_settings(SITE_ERASURE_ENABLED=switch, RETENTION_ENABLED=switch):
                before = everything()
                erasure_dry_run(NOW)
                call_command("erasure_dry_run", "--as-of", "2026-12-30", stdout=StringIO())
                self.assertEqual(everything(), before)

    def test_it_deletes_no_file_and_takes_no_lock(self):
        with mock.patch.object(private_files, "delete") as delete, \
                mock.patch("django.db.models.query.QuerySet.select_for_update") as lock:
            erasure_dry_run(NOW)
        delete.assert_not_called()
        lock.assert_not_called()

    def test_its_output_holds_no_personal_value(self):
        for switch in (False, True):
            with override_settings(SITE_ERASURE_ENABLED=switch):
                text = json.dumps(erasure_dry_run(NOW))
            for value in VALUES:
                self.assertNotIn(value, text)
        out = StringIO()
        call_command("erasure_dry_run", "--as-of", "2026-12-30", stdout=out)
        for value in VALUES:
            self.assertNotIn(value, out.getvalue())
        self.assertIn(self.sub.site.public_id, out.getvalue())

    def test_the_command_refuses_a_bad_date(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("erasure_dry_run", "--as-of", "soon", stdout=StringIO())

    def test_the_near_and_refused_lists(self):
        ended_site("n@example.test", "n-store", state=State.CANCELLED, due=NOW + days(3))
        result = erasure_dry_run(NOW)
        self.assertEqual(len(result["near"]), 1)
        self.assertEqual(result["near"][0]["days_until"], 3)
        self.assertEqual(result["refused"], [])
