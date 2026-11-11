"""The payment method rules below the API. Restates the service halves of TWCinc's
test_payment_settings and its test_account_type for public ids and this backend's errors.
TWCinc's Xendit key tests are not ported: Xendit is hidden by Coach Ed's ruling of
2026-11-11."""
import json

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import AccountAuditEvent
from apps.common.errors import ApiError
from apps.payments import services
from apps.payments.models import ImmutableRecordError, SitePaymentAccount, SitePaymentSettings
from apps.payments.tests.base import (
    BANK, EWALLET, make_payment_account, payment_rows, switched_on,
)
from apps.sites.tests.base import make_account, make_site


@switched_on
class PaymentsTestCase(TestCase):
    def setUp(self):
        self.account = make_account()
        self.site = make_site(self.account)

    def refused(self, call, *args, **kwargs):
        with self.assertRaises(ApiError) as refused:
            call(*args, **kwargs)
        return refused.exception


class AvailabilityTests(PaymentsTestCase):
    def test_no_method_until_one_is_configured(self):
        self.assertEqual(services.methods_available(self.site), {"direct": False, "xendit": False})
        self.assertFalse(services.any_method(self.site))
        make_payment_account(self.site)
        self.assertTrue(services.methods_available(self.site)["direct"])
        self.assertTrue(services.any_method(self.site))

    def test_no_row_means_direct_is_on_and_xendit_is_always_off(self):
        self.assertEqual(services.methods_enabled(self.site), {"direct": True, "xendit": False})
        self.assertFalse(SitePaymentSettings.objects.exists())  # reading writes nothing

    def test_removed_accounts_and_other_sites_accounts_never_show(self):
        other = make_site(make_account("ben@example.test"), "bens-store")
        mine = make_payment_account(self.site)
        make_payment_account(other, account_number="999999999999")
        services.remove_account(self.account, self.site, mine.public_id)
        self.assertEqual(list(services.active_accounts(self.site)), [])
        self.assertFalse(services.methods_available(self.site)["direct"])
        self.assertEqual(services.settings_out(self.site)["accounts"], [])
        mine.refresh_from_db()
        self.assertIsNotNone(mine.removed_at)  # kept, never deleted

    def test_switching_off_hides_the_method_and_keeps_its_accounts(self):
        make_payment_account(self.site)
        services.set_enabled(self.account, self.site, direct=False)
        self.assertEqual(services.methods_enabled(self.site), {"direct": False, "xendit": False})
        self.assertFalse(services.any_method(self.site))
        self.assertEqual(len(services.settings_out(self.site)["accounts"]), 1)
        services.set_enabled(self.account, self.site, direct=True)
        self.assertEqual(services.methods_available(self.site), {"direct": True, "xendit": False})
        self.assertEqual(SitePaymentSettings.objects.count(), 1)

    def test_on_without_an_account_stays_unavailable(self):
        services.set_enabled(self.account, self.site, direct=True)
        self.assertEqual(services.methods_available(self.site), {"direct": False, "xendit": False})

    def test_xendit_cannot_be_switched_on(self):
        make_payment_account(self.site)
        before = payment_rows()
        for direct in (None, False):
            error = self.refused(
                services.set_enabled, self.account, self.site, direct=direct, xendit=True
            )
            self.assertEqual((error.status, error.code), (400, "invalid_request"))
            self.assertEqual(error.fields, {"xendit_enabled": ["Xendit is not available yet."]})
        self.assertEqual(payment_rows(), before)
        self.assertFalse(AccountAuditEvent.objects.exists())
        self.assertIsNone(services.set_enabled(self.account, self.site, xendit=False))
        self.assertEqual(payment_rows(), before)

    def test_the_read_shows_xendit_as_not_set_up(self):
        make_payment_account(self.site)
        self.assertEqual(services.settings_out(self.site)["xendit"], {
            "configured": False, "key_mode": "", "webhook_url": "", "key_failed": False,
            "key_failed_at": None,
        })


class AccountTests(PaymentsTestCase):
    def add(self, **changes):
        return services.add_account(self.account, self.site, {**BANK, **changes})

    def test_account_fields_are_validated_and_the_number_goes_back_to_the_owner(self):
        error = self.refused(
            services.add_account, self.account, self.site,
            {"kind": "cod", "provider_name": "", "account_name": "", "account_number": ""},
        )
        self.assertEqual(
            set(error.fields), {"kind", "provider_name", "account_name", "account_number"}
        )
        self.assertFalse(SitePaymentAccount.objects.exists())
        row = services.add_account(self.account, self.site, EWALLET)
        out = services.account_out(row)
        self.assertEqual(out["account_number_last4"], "4567")
        self.assertEqual(out["account_number"], "09171234567")
        self.assertEqual(out["id"], row.public_id)
        self.assertTrue(row.public_id.startswith("pay_"))
        self.assertFalse(out["has_qr"])

    def test_the_number_and_the_lengths_are_checked(self):
        for field, bad in (
            ("account_number", "abc"), ("account_number", "- -"), ("account_number", "1" * 41),
            ("account_number", "0917+123"), ("provider_name", "x" * 61), ("account_name", "x" * 121),
            ("provider_name", "   "),
        ):
            with self.subTest(field=field, bad=bad[:12]):
                error = self.refused(self.add, **{field: bad})
                self.assertEqual(error.fields, {field: [services.FIELD_ERRORS[field]]})
        self.assertFalse(SitePaymentAccount.objects.exists())
        row = self.add(account_number=" 0012-3456 7890 ", provider_name="x" * 60)
        self.assertEqual(row.account_number, "0012-3456 7890")

    def test_a_bank_needs_savings_or_checking(self):
        for bad in ("", "ewallet", "current", None):
            with self.subTest(bad):
                error = self.refused(self.add, account_type=bad)
                self.assertEqual(error.fields, {"account_type": ["Choose Savings or Checking."]})
        self.assertFalse(SitePaymentAccount.objects.exists())

    def test_a_bank_keeps_the_type_it_was_given(self):
        for given in ("savings", "checking", " Checking "):
            row = self.add(account_type=given)
            self.assertEqual(row.account_type, given.strip().lower())
        self.assertEqual(services.account_out(row)["account_type"], "checking")

    def test_an_ewallet_gets_ewallet_automatically_whatever_was_sent(self):
        for sent in ("", None, "savings", "ewallet"):
            row = self.add(kind="ewallet", provider_name="GCash", account_type=sent)
            self.assertEqual(row.account_type, "ewallet", sent)
        self.assertEqual(services.account_out(row)["kind"], "ewallet")

    def test_changing_an_account_revalidates_the_type(self):
        row = self.add()
        change = lambda **kw: services.update_account(  # noqa: E731
            self.account, self.site, row.public_id, {**BANK, **kw}
        )
        self.refused(change, account_type="")
        self.assertEqual(change(account_type="checking").account_type, "checking")
        swapped = change(kind="ewallet", provider_name="Maya", account_type="checking")
        self.assertEqual(swapped.account_type, "ewallet")  # the type follows the kind
        self.assertEqual(SitePaymentAccount.objects.count(), 1)

    def test_a_removed_or_foreign_account_cannot_be_changed_or_removed(self):
        other = make_site(make_account("ben@example.test"), "bens-store")
        theirs = make_payment_account(other)
        gone = self.add()
        services.remove_account(self.account, self.site, gone.public_id)
        before = payment_rows()
        for account_id in (theirs.public_id, gone.public_id, str(gone.pk), "pay_nope"):
            with self.subTest(account_id):
                for call in (
                    lambda: services.update_account(self.account, self.site, account_id, BANK),
                    lambda: services.remove_account(self.account, self.site, account_id),
                ):
                    error = self.refused(call)
                    self.assertEqual((error.status, error.code), (404, "not_found"))
        self.assertEqual(payment_rows(), before)

    def test_a_qr_is_refused_as_a_field_and_nothing_is_saved(self):
        for data, files in (({**BANK, "qr": "x"}, None), (BANK, {"qr": object()})):
            error = self.refused(services.add_account, self.account, self.site, data, files)
            self.assertEqual(error.fields, {"qr": [services.QR_NOT_YET]})
        self.assertFalse(SitePaymentAccount.objects.exists())

    def test_the_model_clean_refuses_a_mismatch_and_allows_a_blank_bank_row(self):
        make = lambda kind, atype: SitePaymentAccount(  # noqa: E731
            site=self.site, kind=kind, account_type=atype, provider_name="X",
            account_name="Y", account_number="1",
        )
        for kind, atype in (("ewallet", "savings"), ("bank", "ewallet"), ("bank", "bogus")):
            with self.assertRaises(ValidationError, msg=(kind, atype)):
                make(kind, atype).clean()
        for kind, atype in (("bank", ""), ("bank", "checking"), ("ewallet", "ewallet")):
            make(kind, atype).clean()

    def test_a_bank_row_with_no_type_still_reads(self):
        out = services.account_out(make_payment_account(self.site, account_type=""))
        self.assertEqual(out["account_type"], "")
        self.assertEqual(set(out), {
            "id", "kind", "account_type", "provider_name", "account_name", "account_number",
            "account_number_last4", "has_qr",
        })

    def test_the_type_label(self):
        labels = {"savings": "Savings", "checking": "Checking", "ewallet": "E-wallet", "": ""}
        for atype, label in labels.items():
            self.assertEqual(SitePaymentAccount(account_type=atype).type_label, label)

    def test_an_account_row_is_never_deleted(self):
        row = self.add()
        with self.assertRaises(ImmutableRecordError):
            row.delete()
        self.assertTrue(SitePaymentAccount.objects.filter(pk=row.pk).exists())

    def test_the_account_events_name_the_account_and_never_its_number(self):
        row = self.add()
        services.update_account(self.account, self.site, row.public_id, BANK)
        services.remove_account(self.account, self.site, row.public_id)
        services.set_enabled(self.account, self.site, direct=False)
        events = list(AccountAuditEvent.objects.order_by("pk").values_list("event", "detail"))
        named = {"site": self.site.public_id, "payment_account": row.public_id, "kind": "bank"}
        self.assertEqual(events, [
            ("payment_account_added", named), ("payment_account_changed", named),
            ("payment_account_removed", named),
            ("payment_methods_switched", {"site": self.site.public_id, "direct_enabled": False}),
        ])
        text = json.dumps(events)
        self.assertNotIn(BANK["account_number"], text)
        self.assertNotIn(BANK["account_name"], text)
