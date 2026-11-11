"""Helpers shared by the payment method tests."""
from django.core.cache import cache
from django.test import override_settings

from apps.accounts.tests.base import ORIGIN
from apps.payments.models import SitePaymentAccount, SitePaymentSettings
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase

switched_on = override_settings(PAYMENT_METHODS_ENABLED=True)
MISSING_SITE = "site_00000000000000000000"
MISSING_ACCOUNT = "pay_00000000000000000000"
BANK = {
    "kind": "bank", "account_type": "savings", "provider_name": "BDO",
    "account_name": "Ana's Store", "account_number": "001234567890",
}
EWALLET = {
    "kind": "ewallet", "account_type": "", "provider_name": "GCash",
    "account_name": "Ana Santos", "account_number": "09171234567",
}


def methods_url(site_id):
    return f"/v1/sites/{site_id}/payment-methods/"


def accounts_url(site_id):
    return f"/v1/sites/{site_id}/payment-methods/accounts/"


def account_url(site_id, account_id):
    return f"/v1/sites/{site_id}/payment-methods/accounts/{account_id}/"


def make_payment_account(site, **fields):
    """A payment account written directly (no service rules applied)."""
    return SitePaymentAccount.objects.create(site=site, **{**BANK, **fields})


def payment_rows():
    return (
        list(SitePaymentAccount.objects.order_by("pk").values()),
        list(SitePaymentSettings.objects.order_by("pk").values()),
    )


def form_post(case, path, data, client=None):
    """A multipart form post, as the Payments page sends an account."""
    return (client or case.client).post(path, data=data, secure=True, HTTP_ORIGIN=ORIGIN)


def payments_call(case, method, path, client=None):
    """Call a payment route for a test that walks every route under a site (Billing's
    inventory). It only prepares data: {account_id} is filled in with an account of the site
    in the path, made when the site has none, and the signed-in account's hourly write count
    starts again, so a walk of many routes is never rate limited. It never touches a
    subscription or a switch."""
    cache.delete(f"throttle:payment-methods-write:{case.account.pk}")
    if "{account_id}" in path:
        site = Site.objects.filter(public_id=path.split("/sites/")[1].split("/")[0]).first()
        row = site and (
            SitePaymentAccount.objects.filter(site=site, removed_at__isnull=True).first()
            or make_payment_account(site)
        )
        path = path.replace("{account_id}", row.public_id if row else MISSING_ACCOUNT)
    if method == "GET":
        return case.get(path, client=client)
    if method == "POST":
        return form_post(case, path, BANK, client=client)
    if method == "PATCH":
        return case.send("patch", path, {"direct_enabled": True}, client=client)
    return case.send("delete", path, None, client=client)


@switched_on
class PaymentsApiTestCase(SitesApiTestCase):
    """Signed in as Ana, who has one site: self.site and its public id self.site_id."""

    def setUp(self):
        super().setUp()
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.url = methods_url(self.site_id)
        self.accounts = accounts_url(self.site_id)

    def ben_with_site(self):
        """Ben signed in on his own client, with his own site."""
        client = self.other_client()
        site_id = self.create_site(client=client, subdomain="bens-store")["id"]
        return client, Site.objects.get(public_id=site_id)

    def read(self, url=None, client=None):
        response = self.get(url or self.url, client=client)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def form(self, data, url=None, client=None):
        return form_post(self, url or self.accounts, data, client=client)

    def added(self, data=None, **changes):
        response = self.form({**(data or BANK), **changes})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def delete(self, path, **extra):
        return self.send("delete", path, None, **extra)

    def fields(self, response):
        """The field messages of a refused write."""
        return self.error(response, 400, "invalid_request")["fields"]
