"""Account disposal and export with Santé rows. Restates TWCinc's test_refuses_disposal (the
catalog owner half; the agreement master store ports with the pack)."""
import json
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.accounts import retention as rules
from apps.accounts.models import Account
from apps.accounts.tests.disposal_base import EMAIL, DisposalTestCase, snapshot
from apps.billing.models import Subscription
from apps.billing.tests.base import T0, days, make_subscription
from apps.operators.models import OperatorAuditEntry
from apps.sante import retention, services
from apps.sante.models import SanteCatalogOwner, SanteSiteSettings
from apps.sante.tests.base import retemplate, switched_on
from apps.sites.models import Site
from apps.sites.tests.base import make_account, make_site

SPONSOR = "ANA-SPONSOR-77"


def sante_rows():
    return (
        list(SanteSiteSettings.objects.order_by("pk").values()),
        list(SanteCatalogOwner.objects.order_by("pk").values()),
    )


@switched_on
@override_settings(RETENTION_ENABLED=True)
class SanteDisposalTestCase(DisposalTestCase):
    def setUp(self):
        super().setUp()
        self.site = Site.objects.get(account=self.account)
        self.their_site = Site.objects.get(account=self.other)
        SanteSiteSettings.objects.create(site=self.site, sponsor_id=SPONSOR)
        SanteSiteSettings.objects.create(site=self.their_site, sponsor_id="BEN-1")

    def dispose(self, account=None, **extra):
        return rules.dispose_account(
            account or self.account, now=self.now, rule=rules.REQUEST, actor=self.operator,
            request_ref="DPO-2026-009", **extra,
        )

    def own(self, site):
        services.set_catalog_owner(site, operator=self.operator)


class RefusesDisposalTests(SanteDisposalTestCase):
    def test_no_reason_by_default_and_the_reason_for_the_owner(self):
        other_template = retemplate(make_site(make_account("cora@example.test"), "site-c"))
        self.assertIsNone(services.refuses_disposal(other_template))
        self.assertIsNone(services.refuses_disposal(self.site))
        self.own(self.site)
        self.assertEqual(services.refuses_disposal(self.site), "catalog_owner")
        self.assertIsNone(services.refuses_disposal(self.their_site))
        self.own(self.their_site)
        self.assertIsNone(services.refuses_disposal(self.site))
        self.assertEqual(services.refuses_disposal(self.their_site), "catalog_owner")

    def test_the_owner_account_is_not_disposed_of_and_nothing_changes(self):
        self.own(self.site)
        before = snapshot(), sante_rows()
        with self.assertRaises(rules.NotDisposable) as refused:
            self.dispose()
        self.assertEqual(
            refused.exception.reasons, ({"reason": "catalog_owner", "site": self.site.public_id},)
        )
        self.assertEqual(refused.exception.cancelled, ())
        self.assertEqual((snapshot(), sante_rows()), before)
        self.assertEqual(Account.objects.get(pk=self.account.pk).email, EMAIL)

    @override_settings(BILLING_ENABLED=True)
    def test_a_refused_owner_keeps_its_subscription_uncancelled(self):
        subscription = make_subscription("cora@example.test", "coras-store")
        self.own(subscription.site)
        with self.assertRaises(rules.NotDisposable):
            rules.dispose_account(
                subscription.site.account, now=T0 + days(1), rule=rules.REQUEST,
                actor=self.operator,
            )
        subscription.refresh_from_db()
        self.assertEqual(subscription.state, Subscription.State.TRIAL_ACTIVE)
        self.assertEqual(subscription.transitions.filter(code="cancelled").count(), 0)

    def test_the_dry_run_says_it_would_be_refused_and_changes_nothing(self):
        self.own(self.site)
        before = snapshot(), sante_rows()
        counts = self.dispose(dry_run=True)
        self.assertEqual(
            counts["refused_for_now"], [{"reason": "catalog_owner", "site": self.site.public_id}]
        )
        self.assertEqual((snapshot(), sante_rows()), before)

    def test_once_another_owner_is_named_the_account_is_disposed_of(self):
        self.own(self.site)
        self.own(self.their_site)
        self.assertIsNotNone(self.dispose())
        self.assertIsNotNone(Site.objects.get(pk=self.site.pk).deleted_at)

    def test_another_account_is_disposed_of_while_this_one_owns_the_catalog(self):
        self.own(self.site)
        self.assertIsNotNone(self.dispose(self.other))
        self.assertEqual(services.catalog_owner_site(), self.site)

    def test_the_command_says_why_and_exits_with_an_error(self):
        self.own(self.site)
        out = StringIO()
        with self.assertRaises(CommandError) as refused:
            call_command(
                "dispose_account", self.account.public_id, actor="coach",
                request_ref="DPO-2026-009", stdout=out,
            )
        message = str(refused.exception)
        self.assertIn("was not disposed of and nothing was blanked", message)
        self.assertIn(f"Site {self.site.public_id} is the Santé catalog owner", message)
        self.assertNotIn("after that date", message)
        self.assert_no_personal_value(message + out.getvalue())

    def test_the_command_dry_run_reports_the_refusal(self):
        self.own(self.site)
        out = StringIO()
        call_command(
            "dispose_account", self.account.public_id, "--dry-run", actor="coach",
            request_ref="DPO-2026-009", stdout=out,
        )
        self.assertIn("A real run would be refused for now.", out.getvalue())
        self.assertIn("is the Santé catalog owner", out.getvalue())

    def test_the_never_verified_rule_skips_an_owner_account(self):
        Account.objects.filter(pk=self.account.pk).update(
            email_verified_at=None, created_at=self.now - rules.NEVER_VERIFIED_AFTER
        )
        self.own(self.site)
        self.assertEqual(rules.never_verified_accounts(self.now, False), 0)
        self.assertEqual(Account.objects.get(pk=self.account.pk).email, EMAIL)


class BlankingTests(SanteDisposalTestCase):
    def test_the_sponsor_id_is_blanked_and_the_row_kept(self):
        counts = self.dispose()
        self.assertEqual(counts["sante_site_settings"], 1)
        row = SanteSiteSettings.objects.get(site=self.site)
        self.assertEqual(row.sponsor_id, "")
        self.assertEqual(SanteSiteSettings.objects.get(site=self.their_site).sponsor_id, "BEN-1")

    def test_the_audit_entry_names_the_field_and_never_the_value(self):
        self.dispose()
        entry = OperatorAuditEntry.objects.get(action="account_disposed")
        self.assertEqual(entry.detail["blanked"]["sante_site_settings"], ["sponsor_id"])
        self.assertEqual(entry.detail["counts"]["sante_site_settings"], 1)
        self.assertNotIn(SPONSOR, json.dumps(entry.detail))

    def test_the_dry_run_counts_and_changes_nothing(self):
        before = snapshot(), sante_rows()
        self.assertEqual(self.dispose(dry_run=True)["sante_site_settings"], 1)
        self.assertEqual((snapshot(), sante_rows()), before)

    def test_a_site_with_no_settings_row_counts_zero(self):
        SanteSiteSettings.objects.filter(site=self.site).delete()
        self.assertEqual(self.dispose()["sante_site_settings"], 0)


class ExportTests(SanteDisposalTestCase):
    def export(self):
        return rules.export_account_data(self.account, actor=self.operator, request_ref="DPO-2")

    def test_it_holds_the_sponsor_id_of_each_of_the_accounts_sante_sites(self):
        row = SanteSiteSettings.objects.get(site=self.site)
        self.assertEqual(
            self.export()["sante_site_settings"],
            [{
                "site": self.site.public_id, "sponsor_id": SPONSOR,
                "updated_at": row.updated_at.isoformat(), "catalog_owner": False,
            }],
        )
        entry = OperatorAuditEntry.objects.get(action="account_exported")
        self.assertEqual(entry.detail["counts"]["sante_site_settings"], 1)

    def test_it_says_when_the_site_is_the_catalog_owner_and_reads_blank_without_a_row(self):
        SanteSiteSettings.objects.filter(site=self.site).delete()
        self.own(self.site)
        self.assertEqual(
            self.export()["sante_site_settings"],
            [{"site": self.site.public_id, "sponsor_id": "", "updated_at": None, "catalog_owner": True}],
        )

    def test_it_holds_nothing_of_another_account_and_changes_nothing(self):
        before = sante_rows()
        text = json.dumps(self.export())
        self.assertNotIn("BEN-1", text)
        self.assertNotIn(self.their_site.public_id, text)
        self.assertEqual(sante_rows(), before)


@override_settings(SANTE_SETTINGS_ENABLED=False)
class SwitchedOffTests(SanteDisposalTestCase):
    """Off, disposal and export behave exactly as before this app and read no Santé table."""

    def test_the_helpers_answer_empty_without_a_query(self):
        with self.assertNumQueries(0):
            self.assertEqual(retention.disposal_refusals(self.account), [])
            self.assertEqual(retention.blank_settings_of(self.account), {})
            self.assertEqual(retention.export_settings_of(self.account), {})
            self.assertEqual(retention.blanked(), {})

    def test_disposal_and_export_carry_no_sante_key_and_touch_no_sante_row(self):
        before = sante_rows()
        data = rules.export_account_data(self.account, actor=self.operator, request_ref="DPO-2")
        self.assertNotIn("sante_site_settings", data)
        counts = self.dispose()
        self.assertNotIn("sante_site_settings", counts)
        entry = OperatorAuditEntry.objects.get(action="account_disposed")
        self.assertNotIn("sante_site_settings", entry.detail["blanked"])
        self.assertEqual(sante_rows(), before)
