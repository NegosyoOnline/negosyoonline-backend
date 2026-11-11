"""The Subscriber's payment methods over HTTP, in the shapes the Payments page reads and
sends: the on/off switch, bank and e-wallet accounts, Xendit shown as not set up and off,
an account's QR code image, tenant isolation and the settings switch. Restates the API halves of TWCinc's
test_payment_settings for public ids, this backend's error shape and the 404 rule. TWCinc's
Xendit key tests are not ported: Xendit is hidden by Coach Ed's ruling of 2026-11-11."""
import re
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from apps.common import private_files
from apps.payments import services
from apps.payments.api import router
from apps.payments.models import SitePaymentAccount, SitePaymentSettings
from apps.payments.tests.base import (
    BANK, EWALLET, MISSING_ACCOUNT, MISSING_SITE, PaymentsApiTestCase, account_url,
    accounts_url, make_payment_account, methods_url, payment_rows, payments_call, qr_file,
    stored_files,
)
from apps.sante.tests.base import retemplate
from apps.sites.models import Site
from apps.sites.tests.test_isolation import routes_under_a_site

XENDIT_NOT_SET_UP = {
    "configured": False, "key_mode": "", "webhook_url": "", "key_failed": False,
    "key_failed_at": None,
}
EMPTY = {
    "enabled": {"direct": True, "xendit": False},
    "available": {"direct": False, "xendit": False},
    "accounts": [],
    "xendit": XENDIT_NOT_SET_UP,
}
ROUTES = [
    ("DELETE", "/sites/{site_id}/payment-methods/accounts/{account_id}/"),
    ("GET", "/sites/{site_id}/payment-methods/"),
    ("PATCH", "/sites/{site_id}/payment-methods/"),
    ("POST", "/sites/{site_id}/payment-methods/accounts/"),
    ("POST", "/sites/{site_id}/payment-methods/accounts/{account_id}/"),
]


class ReadTests(PaymentsApiTestCase):
    def test_a_new_site_reads_direct_on_xendit_off_none_available_and_writes_nothing(self):
        self.assertEqual(self.read(), EMPTY)
        self.assertEqual(payment_rows(), ([], []))

    def test_a_site_of_another_template_is_served_too(self):
        retemplate(self.site)
        self.assertEqual(self.read(), EMPTY)
        self.assertEqual(self.added()["kind"], "bank")
        self.assertTrue(self.read()["available"]["direct"])

    def test_the_answer_carries_the_owners_full_numbers_in_the_order_added(self):
        first = self.added()
        second = self.added(EWALLET)
        self.assertEqual(first, {
            "id": first["id"], "kind": "bank", "account_type": "savings", "provider_name": "BDO",
            "account_name": "Ana's Store", "account_number": "001234567890",
            "account_number_last4": "7890", "has_qr": False,
        })
        self.assertEqual(second["account_type"], "ewallet")
        body = self.read()
        self.assertEqual(body["accounts"], [first, second])
        self.assertEqual(body["available"], {"direct": True, "xendit": False})

    def test_a_signed_out_request_is_refused(self):
        self.client.cookies.clear()
        for method, path in ROUTES:
            with self.subTest(method=method, path=path):
                response = payments_call(self, method, "/v1" + path.replace("{site_id}", self.site_id))
                self.error(response, 401, "unauthenticated")
        self.assertEqual(len(SitePaymentAccount.objects.all()), 1)  # the helper's own row


class SwitchTests(PaymentsApiTestCase):
    def setUp(self):
        super().setUp()
        self.added()

    def test_switching_off_hides_the_method_and_keeps_its_accounts(self):
        response = self.patch(self.url, {"direct_enabled": False})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["enabled"], {"direct": False, "xendit": False})
        self.assertEqual(body["available"], {"direct": False, "xendit": False})
        self.assertEqual(len(body["accounts"]), 1)  # the account stays saved
        self.assertFalse(services.any_method(self.site))
        body = self.patch(self.url, {"direct_enabled": True}).json()
        self.assertEqual(body["available"], {"direct": True, "xendit": False})
        self.assertEqual(self.read(), body)
        self.assertEqual(SitePaymentSettings.objects.count(), 1)

    def test_on_without_an_account_stays_unavailable(self):
        self.delete(account_url(self.site_id, self.read()["accounts"][0]["id"]))
        body = self.patch(self.url, {"direct_enabled": True}).json()
        self.assertEqual((body["enabled"]["direct"], body["available"]["direct"]), (True, False))

    def test_xendit_cannot_be_switched_on_and_nothing_is_written(self):
        for body in ({"xendit_enabled": True}, {"xendit_enabled": True, "direct_enabled": False}):
            with self.subTest(body):
                response = self.patch(self.url, body)
                self.assertEqual(
                    self.fields(response), {"xendit_enabled": ["Xendit is not available yet."]}
                )
        self.assertFalse(SitePaymentSettings.objects.exists())
        body = self.read()
        self.assertEqual(body["enabled"], {"direct": True, "xendit": False})
        self.assertEqual(body["available"], {"direct": True, "xendit": False})
        self.assertEqual(body["xendit"], XENDIT_NOT_SET_UP)

    def test_switching_xendit_off_changes_nothing(self):
        before = payment_rows()
        response = self.patch(self.url, {"xendit_enabled": False})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["enabled"], {"direct": True, "xendit": False})
        self.assertEqual(payment_rows(), before)

    def test_xendit_reads_not_set_up_whatever_is_saved(self):
        self.patch(self.url, {"direct_enabled": False})
        self.added(EWALLET)
        for body in (self.read(), self.patch(self.url, {"direct_enabled": True}).json()):
            self.assertEqual(body["xendit"], XENDIT_NOT_SET_UP)
            self.assertEqual((body["enabled"]["xendit"], body["available"]["xendit"]), (False, False))

    def test_there_is_no_route_for_a_xendit_key(self):
        url = f"{self.url}xendit/"
        body = {"api_key": "xnd_development_x", "webhook_token": "t"}
        self.error(self.send("put", url, body), 404, "not_found")
        self.error(self.delete(url), 404, "not_found")
        self.error(self.get(url), 404, "not_found")

    def test_bad_body_is_refused_and_changes_nothing(self):
        for body in ({"xendit_enabled": "maybe"}, {"cod_enabled": True}, {"direct_enabled": 1},
                     {"direct_enabled": "true"}, {"enabled": {"direct": False}},
                     {"direct_enabled": False, "site": self.site_id}):
            with self.subTest(body):
                self.error(self.patch(self.url, body), 400, "invalid_request")
        self.assertEqual(services.methods_enabled(self.site), {"direct": True, "xendit": False})
        self.assertFalse(SitePaymentSettings.objects.exists())

    def test_an_empty_body_or_null_changes_nothing_and_answers_the_settings(self):
        before = payment_rows()
        for body in ({}, {"direct_enabled": None}, {"xendit_enabled": None}):
            self.assertEqual(self.patch(self.url, body).json(), self.read())
        self.assertEqual(payment_rows(), before)


class AccountTests(PaymentsApiTestCase):
    def test_add_a_bank_and_an_ewallet_account_then_remove_one(self):
        bank = self.added()
        wallet = self.added(EWALLET)
        response = self.delete(account_url(self.site_id, bank["id"]))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"removed": True})
        self.assertEqual(self.read()["accounts"], [wallet])
        self.error(self.delete(account_url(self.site_id, bank["id"])), 404, "not_found")
        self.assertEqual(SitePaymentAccount.objects.count(), 2)  # removed, never deleted

    def test_bad_fields_come_back_by_name_and_nothing_is_saved(self):
        fields = self.fields(self.form({"kind": "cod"}))
        self.assertEqual(fields, {
            "kind": ["Choose bank or e-wallet."],
            "provider_name": ["Enter the bank or e-wallet name."],
            "account_name": ["Enter the account name."],
            "account_number": ["Enter the account number."],
        })
        self.assertEqual(
            self.fields(self.form({**BANK, "account_type": ""})),
            {"account_type": ["Choose Savings or Checking."]},
        )
        self.assertFalse(SitePaymentAccount.objects.exists())

    def test_a_server_owned_field_in_the_form_is_ignored(self):
        other = self.ben_with_site()[1]
        row = self.added(id="pay_mine", site=other.public_id, removed_at="2020-01-01", has_qr="1")
        saved = SitePaymentAccount.objects.get()
        self.assertEqual((saved.site, saved.removed_at), (self.site, None))
        self.assertEqual(row["id"], saved.public_id)
        self.assertNotEqual(row["id"], "pay_mine")

    def test_change_opens_with_the_saved_number_and_saves_the_form(self):
        row = self.added(EWALLET)
        self.assertEqual(self.read()["accounts"][0]["account_number"], "09171234567")
        url = account_url(self.site_id, row["id"])
        response = self.form({**EWALLET, "account_name": "New Name"}, url=url)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {**row, "account_name": "New Name"})
        self.assertEqual(self.read()["accounts"], [response.json()])

    def test_a_change_with_a_bad_number_is_refused_and_changes_nothing(self):
        row = self.added(EWALLET)
        url = account_url(self.site_id, row["id"])
        fields = self.fields(self.form({**EWALLET, "account_number": ""}, url=url))
        self.assertEqual(fields, {"account_number": ["Enter the account number."]})
        self.assertEqual(self.read()["accounts"], [row])

    def test_an_unknown_removed_or_foreign_account_is_not_found_before_its_fields(self):
        ben, theirs = self.ben_with_site()
        foreign = make_payment_account(theirs)
        gone = self.added()
        self.delete(account_url(self.site_id, gone["id"]))
        before = payment_rows()
        for account_id in (MISSING_ACCOUNT, gone["id"], foreign.public_id, str(foreign.pk)):
            with self.subTest(account_id):
                url = account_url(self.site_id, account_id)
                self.error(self.form({"kind": "cod"}, url=url), 404, "not_found")
                self.error(self.delete(url), 404, "not_found")
        self.assertEqual(payment_rows(), before)

    def test_writes_are_limited_per_account(self):
        from apps.payments import api
        for _ in range(api.WRITE_LIMIT):
            self.assertEqual(self.patch(self.url, {}).status_code, 200)
        self.error(self.patch(self.url, {}), 429, "rate_limited")
        self.error(self.form(BANK), 429, "rate_limited")
        self.assertEqual(self.get(self.url).status_code, 200)  # reading is not counted
        self.assertFalse(SitePaymentAccount.objects.exists())


class QrTests(PaymentsApiTestCase):
    """An account's QR code image (ClickUp z8uq9me7aq). Ports TWCinc's QR assertions in
    test_account_fields_are_validated... and its ChangeAccountTests, for this backend's key
    layout, error shape and private store."""

    KEY = r"sante/qr/site-%d/[0-9a-f]{32}\.png"
    TYPE = {"qr": ["Upload a JPEG, PNG or WebP image."]}
    SIZE = {"qr": ["The file is too large. The limit is 2 MB."]}

    def key_of(self, row):
        return SitePaymentAccount.objects.get(public_id=row["id"]).qr_file_key

    def assert_nothing_stored(self):
        self.assertEqual(stored_files(self.private_dir), [])

    def with_qr(self):
        """An e-wallet account added with a QR, and its key."""
        row = self.added({**EWALLET, "qr": qr_file()})
        return row, self.key_of(row)

    def test_an_account_added_with_a_qr_stores_it_under_the_sites_folder_and_reads_has_qr(self):
        response = self.form({**BANK, "qr": qr_file()})
        self.assertEqual(response.status_code, 201, response.content)
        row = response.json()
        self.assertTrue(row["has_qr"])
        key = self.key_of(row)
        self.assertTrue(re.fullmatch(self.KEY % self.site.pk, key), key)
        self.assertEqual(stored_files(self.private_dir), [key])
        self.assertEqual((self.private_dir / key).read_bytes(), qr_file().read())
        self.assertEqual(self.read()["accounts"], [row])

    def test_no_answer_carries_the_key_or_a_link_to_the_file(self):
        row, key = self.with_qr()
        url = account_url(self.site_id, row["id"])
        answers = [
            self.form({**BANK, "qr": qr_file()}), self.form({**EWALLET, "qr": qr_file()}, url=url),
            self.get(self.url),
        ]
        for response in answers:
            text = response.content.decode()
            for private in ("qr_file_key", "sante/qr", key.rsplit("/", 1)[1], "blob", "http"):
                self.assertNotIn(private, text)

    def test_a_file_that_is_not_an_image_by_its_type_or_its_bytes_is_refused(self):
        bad = (
            qr_file(b"not an image at all", "image/png"),        # the bytes are not an image
            qr_file(content_type="text/plain", name="qr.txt"),   # the declared type is not
            qr_file(b"GIF89a" + b"0" * 32, "image/gif", "qr.gif"),
            qr_file(b"%PDF-1.7 " + b"0" * 32, "application/pdf", "qr.pdf"),
            qr_file(b"", "image/png"),
            "a word, not a file",
        )
        for qr in bad:
            with self.subTest(qr=str(qr)):
                self.assertEqual(self.fields(self.form({**BANK, "qr": qr})), self.TYPE)
        self.assertFalse(SitePaymentAccount.objects.exists())
        self.assert_nothing_stored()

    def test_a_file_over_2_mb_is_refused_and_one_of_exactly_2_mb_is_taken(self):
        head = qr_file().read()
        over = qr_file(head + b"0" * (services.QR_MAX - len(head) + 1))
        self.assertEqual(self.fields(self.form({**BANK, "qr": over})), self.SIZE)
        self.assertFalse(SitePaymentAccount.objects.exists())
        self.assert_nothing_stored()
        self.added({**BANK, "qr": qr_file(head + b"0" * (services.QR_MAX - len(head)))})

    def test_a_bad_qr_and_a_bad_field_are_both_named_and_nothing_is_stored(self):
        fields = self.fields(self.form({**BANK, "account_number": "", "qr": qr_file(b"x")}))
        self.assertEqual(set(fields), {"account_number", "qr"})
        fields = self.fields(self.form({**BANK, "account_number": "", "qr": qr_file()}))
        self.assertEqual(set(fields), {"account_number"})
        self.assertFalse(SitePaymentAccount.objects.exists())
        self.assert_nothing_stored()

    def test_a_multipart_change_without_a_qr_keeps_the_current_one(self):
        row, key = self.with_qr()
        response = self.form(
            {**EWALLET, "account_name": "New Name"}, url=account_url(self.site_id, row["id"])
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {**row, "account_name": "New Name"})
        self.assertEqual(self.key_of(row), key)
        self.assertEqual(stored_files(self.private_dir), [key])

    def test_a_multipart_change_with_a_qr_replaces_it_and_removes_the_old_file(self):
        row, old_key = self.with_qr()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.form(
                {**EWALLET, "qr": qr_file()}, url=account_url(self.site_id, row["id"])
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["has_qr"])
        new_key = self.key_of(row)
        self.assertNotEqual(new_key, old_key)
        self.assertTrue(re.fullmatch(self.KEY % self.site.pk, new_key), new_key)
        self.assertEqual(stored_files(self.private_dir), [new_key])

    def test_a_qr_can_be_added_to_an_account_that_had_none(self):
        row = self.added()
        self.assertFalse(row["has_qr"])
        response = self.form({**BANK, "qr": qr_file()}, url=account_url(self.site_id, row["id"]))
        self.assertTrue(response.json()["has_qr"])
        self.assertEqual(stored_files(self.private_dir), [self.key_of(row)])

    def test_a_change_with_a_bad_number_or_a_bad_qr_changes_nothing_and_stores_nothing(self):
        row, key = self.with_qr()
        url = account_url(self.site_id, row["id"])
        fields = self.fields(self.form({**EWALLET, "account_number": "", "qr": qr_file()}, url=url))
        self.assertEqual(set(fields), {"account_number"})
        self.assertEqual(self.fields(self.form({**EWALLET, "qr": qr_file(b"x")}, url=url)), self.TYPE)
        self.assertEqual(self.read()["accounts"], [row])
        self.assertEqual(self.key_of(row), key)
        self.assertEqual(stored_files(self.private_dir), [key])

    def test_an_account_that_is_not_found_answers_404_before_its_file_is_looked_at(self):
        url = account_url(self.site_id, MISSING_ACCOUNT)
        self.error(self.form({**BANK, "qr": qr_file(b"x")}, url=url), 404, "not_found")
        self.error(self.form({**BANK, "qr": qr_file()}, url=url), 404, "not_found")
        self.assert_nothing_stored()

    def test_a_store_that_fails_is_a_field_error_and_nothing_is_saved(self):
        failing = mock.Mock()
        failing.put.side_effect = private_files.StoreError("down")
        for store in (
            mock.patch.object(private_files, "store", return_value=failing),
            mock.patch.object(private_files, "store", side_effect=private_files.StoreUnavailable()),
        ):
            with store:
                fields = self.fields(self.form({**BANK, "qr": qr_file()}))
            self.assertEqual(
                fields, {"qr": ["The QR code could not be saved. Try again in a moment."]}
            )
        self.assertFalse(SitePaymentAccount.objects.exists())
        self.added()  # without a QR the account still saves

    def test_a_removed_account_keeps_its_qr_file(self):
        row, key = self.with_qr()
        self.delete(account_url(self.site_id, row["id"]))
        self.assertEqual(self.key_of(row), key)
        self.assertEqual(stored_files(self.private_dir), [key])
        self.assertEqual(self.read()["accounts"], [])

    def test_each_sites_qr_is_in_its_own_folder_and_only_its_owner_sees_has_qr(self):
        ben, theirs = self.ben_with_site()
        row, key = self.with_qr()
        self.assertIn(f"/site-{self.site.pk}/", key)
        self.assertEqual(self.read(methods_url(theirs.public_id), client=ben)["accounts"], [])
        url = account_url(theirs.public_id, row["id"])
        self.error(self.form({**EWALLET, "qr": qr_file()}, url=url, client=ben), 404, "not_found")
        self.assertEqual(stored_files(self.private_dir), [key])


class IsolationTests(PaymentsApiTestCase):
    """A foreign, missing or removed site answers the same 404 on every route."""

    def setUp(self):
        super().setUp()
        self.ben, self.theirs = self.ben_with_site()
        self.their_id = self.theirs.public_id
        self.form(
            {**BANK, "account_number": "777700001111"}, url=accounts_url(self.their_id),
            client=self.ben,
        )
        self.their_account = SitePaymentAccount.objects.get(site=self.theirs)

    def call(self, method, template, site_id, client=None):
        path = "/v1" + template.replace("{site_id}", site_id).replace(
            "{account_id}", self.their_account.public_id
        )
        return payments_call(self, method, path, client=client)

    def assert_same_404(self, site_id, client=None):
        reference = self.get(methods_url(MISSING_SITE)).json()
        before = payment_rows()
        for method, template in ROUTES:
            with self.subTest(method=method, template=template):
                response = self.call(method, template, site_id, client=client)
                self.assertEqual(response.status_code, 404, response.content)
                self.assertEqual(response.json(), reference)
                self.assertNotIn("777700001111", response.content.decode())
        self.assertEqual(payment_rows(), before)

    def test_every_route_under_a_site_is_one_this_test_knows(self):
        self.assertEqual(sorted(routes_under_a_site(router)), ROUTES)
        self.assertEqual(len(routes_under_a_site(router)), sum(
            len(operation.methods) for view in router.path_operations.values()
            for operation in view.operations
        ))  # the router has no route outside a site

    def test_another_accounts_site(self):
        self.assert_same_404(self.their_id)

    def test_a_missing_site(self):
        self.assert_same_404(MISSING_SITE)

    def test_a_removed_site(self):
        Site.objects.filter(pk=self.theirs.pk).update(deleted_at=timezone.now())
        self.assert_same_404(self.their_id, client=self.ben)

    def test_a_row_number_is_not_an_id(self):
        self.assert_same_404(str(self.theirs.pk))

    def test_the_404_is_the_unknown_endpoint_code(self):
        unknown = self.get("/v1/no-such-endpoint/").json()["error"]["code"]
        self.assertEqual(self.get(methods_url(self.their_id)).json()["error"]["code"], unknown)

    def test_another_sites_account_id_under_my_site_is_not_found(self):
        url = account_url(self.site_id, self.their_account.public_id)
        self.error(self.delete(url), 404, "not_found")
        self.their_account.refresh_from_db()
        self.assertIsNone(self.their_account.removed_at)

    def test_each_site_reads_only_its_own(self):
        self.assertEqual(self.read(), EMPTY)
        theirs = self.read(methods_url(self.their_id), client=self.ben)
        self.assertEqual([a["account_number"] for a in theirs["accounts"]], ["777700001111"])
        self.assertEqual(theirs["xendit"], XENDIT_NOT_SET_UP)


@override_settings(PAYMENT_METHODS_ENABLED=False)
class SwitchedOffTests(PaymentsApiTestCase):
    def test_every_route_answers_404_signed_in_or_not_and_reads_no_payment_table(self):
        reference = self.get("/v1/no-such-endpoint/").json()["error"]["code"]
        self.assertEqual(reference, "not_found")
        for signed_in in (True, False):
            if not signed_in:
                self.client.cookies.clear()
            for method, template in ROUTES:
                path = "/v1" + template.replace("{site_id}", self.site_id).replace(
                    "{account_id}", MISSING_ACCOUNT
                )
                with self.subTest(method=method, template=template, signed_in=signed_in):
                    with self.assertNumQueries(0):
                        response = payments_call(self, method, path)
                    self.error(response, 404, "not_found")
        self.assertEqual(payment_rows(), ([], []))
