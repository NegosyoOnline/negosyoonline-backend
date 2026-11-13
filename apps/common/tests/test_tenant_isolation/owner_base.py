"""Shared set-up for the tenant isolation suite over the owner API: Ana and Ben each own a Santé
site, every owner switch is on, and everything a route could write to is snapshotted so a test
can prove a refused request changed nothing."""
import itertools

from django.apps import apps as django_apps
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from apps.accounts.tests.base import ORIGIN
from apps.billing.models import BankTransferPayment, Subscription
from apps.billing.tests.base import JPEG, submit, transfers_on
from apps.catalog.models import CatalogProduct
from apps.catalog.tests.base import FakeMedia, designate, image_file, make_product
from apps.meta_ads.tests.base import CODE, KEY, PIXEL, TOKEN, FakeMeta, configure
from apps.pack.tests.owner_base import make_order
from apps.payments.models import SitePaymentAccount
from apps.payments.tests.base import BANK, make_payment_account, qr_file, stored_files, use_local_private_store
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase
from apps.sites.tests.test_isolation import PROFILE_BODY, SITE_BODY

MISSING_SITE = "site_00000000000000000000"
MISSING_CHILD = {
    "order_id": "pko_00000000000000000000",
    "proof_id": "pps_00000000000000000000",
    "account_id": "pay_00000000000000000000",
    "payment_id": "btp_00000000000000000000",
    "product_id": "prod_00000000000000000000",
}
PLACEHOLDERS = tuple("{%s}" % name for name in MISSING_CHILD)

# Every route under /v1/sites/{site_id}/, in the order the control test calls them (a payment is
# sent before it is read, a cancellation before it is withdrawn). Key: (method, path template).
# Value: the status the signed-in owner gets. The guard test fails when this list and the
# URLconf differ.
OWNER_ROUTES = {
    # sites
    ("GET", "/sites/{site_id}/"): 200,
    ("PATCH", "/sites/{site_id}/"): 200,
    ("GET", "/sites/{site_id}/business-profile/"): 200,
    ("PATCH", "/sites/{site_id}/business-profile/"): 200,
    ("POST", "/sites/{site_id}/business-profile/seal/"): 200,
    ("GET", "/sites/{site_id}/business-profile/seal/"): 200,
    ("DELETE", "/sites/{site_id}/business-profile/seal/"): 200,
    # billing
    ("GET", "/sites/{site_id}/billing/"): 200,
    ("GET", "/sites/{site_id}/billing/payment/"): 200,
    ("GET", "/sites/{site_id}/billing/usage/"): 200,
    ("POST", "/sites/{site_id}/billing/payments/"): 201,
    ("GET", "/sites/{site_id}/billing/payments/"): 200,
    ("GET", "/sites/{site_id}/billing/payments/{payment_id}/"): 200,
    ("GET", "/sites/{site_id}/billing/payments/{payment_id}/proof/"): 200,
    ("POST", "/sites/{site_id}/billing/reactivate/"): 409,  # nothing is cancelled yet: payment_required
    # sante
    ("GET", "/sites/{site_id}/sante/settings/"): 200,
    ("PATCH", "/sites/{site_id}/sante/settings/"): 200,
    # catalog (one shared catalog: these routes get the foreign-site check only)
    ("GET", "/sites/{site_id}/catalog/products/"): 200,
    ("POST", "/sites/{site_id}/catalog/products/"): 201,
    ("PATCH", "/sites/{site_id}/catalog/products/{product_id}/"): 200,
    ("GET", "/sites/{site_id}/catalog/products/{product_id}/revisions/"): 200,
    ("POST", "/sites/{site_id}/catalog/products/{product_id}/image/"): 200,
    ("DELETE", "/sites/{site_id}/catalog/products/{product_id}/image/"): 200,
    # payments
    ("GET", "/sites/{site_id}/payment-methods/"): 200,
    ("PATCH", "/sites/{site_id}/payment-methods/"): 200,
    ("POST", "/sites/{site_id}/payment-methods/accounts/"): 201,
    ("POST", "/sites/{site_id}/payment-methods/accounts/{account_id}/"): 200,
    ("DELETE", "/sites/{site_id}/payment-methods/accounts/{account_id}/"): 200,
    # pack owner
    ("GET", "/sites/{site_id}/pack/orders/"): 200,
    ("GET", "/sites/{site_id}/pack/orders/{order_id}/"): 200,
    ("GET", "/sites/{site_id}/pack/orders/{order_id}/proofs/{proof_id}/"): 200,
    ("POST", "/sites/{site_id}/pack/orders/{order_id}/approve/"): 200,
    ("POST", "/sites/{site_id}/pack/orders/{order_id}/reject/"): 200,
    # meta_ads
    ("GET", "/sites/{site_id}/meta/"): 200,
    ("PUT", "/sites/{site_id}/meta/"): 200,
    ("POST", "/sites/{site_id}/meta/test/"): 200,
    ("DELETE", "/sites/{site_id}/meta/token/"): 200,
    # last: a cancelled trial is Billing-only, so every route called after it would answer 403
    ("POST", "/sites/{site_id}/billing/cancel/"): 200,
}
APP_PREFIXES = [
    ("/sites/{site_id}/billing/", "billing"), ("/sites/{site_id}/sante/", "sante"),
    ("/sites/{site_id}/catalog/", "catalog"), ("/sites/{site_id}/payment-methods/", "payments"),
    ("/sites/{site_id}/pack/", "pack_owner"), ("/sites/{site_id}/meta/", "meta_ads"),
]


def app_of(template):
    for prefix, app in APP_PREFIXES:
        if template.startswith(prefix):
            return app
    return "sites"


def urlconf_routes():
    """(method, path template) for every route mounted on the API under a {site_id}."""
    from config.api import api

    routes = []
    for prefix, router in api._routers:
        for path, view in router.path_operations.items():
            if "{site_id}" in prefix + path:
                for operation in view.operations:
                    routes.extend((method, prefix + path) for method in operation.methods)
    return routes


def row_snapshot():
    """Every row of every model of this project, in primary key order."""
    snap = {}
    for model in django_apps.get_models():
        if model.__module__.startswith("apps."):
            snap[model._meta.label] = list(model.objects.order_by("pk").values())
    return snap


def child_variants(template, own, foreign):
    """Every way to fill a route's child placeholders with at least one id of the other site."""
    holes = [name for name in MISSING_CHILD if "{%s}" % name in template and name != "product_id"]
    for picks in itertools.product((False, True), repeat=len(holes)):
        if any(picks):
            yield {**own, **{name: foreign[name] for name, use in zip(holes, picks) if use}}


@override_settings(
    BILLING_ENABLED=True, SANTE_SETTINGS_ENABLED=True, SANTE_CATALOG_ENABLED=True,
    PAYMENT_METHODS_ENABLED=True, PACK_ENABLED=True, META_EVENTS_ENABLED=True,
    BILLING_USAGE_ENABLED=True,
    META_CREDENTIALS_KEY=KEY,
)
class TwoOwnersTestCase(SitesApiTestCase):
    """Ana (self.client, self.site) and Ben (self.ben, self.ben_site), each with a Santé site and
    a subscription. Ben also has a child of every kind: an order with its proof, a payment
    account, a billing payment with its proof and Meta settings."""

    def setUp(self):
        super().setUp()
        on = transfers_on()
        self.proof_store = on.__enter__()
        self.addCleanup(on.__exit__, None, None, None)
        self.private_dir = use_local_private_store(self)
        self.media = FakeMedia().__enter__()
        self.addCleanup(self.media.__exit__, None, None, None)
        self.meta = FakeMeta().__enter__()
        self.addCleanup(self.meta.__exit__, None, None, None)
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.ben = self.other_client()
        self.ben_site_id = self.create_site(
            client=self.ben, subdomain="bens-store", display_name="Ben's"
        )["id"]
        self.ben_site = Site.objects.get(public_id=self.ben_site_id)
        designate(self.site)  # Ana's site owns the one shared catalog
        self.product = make_product()
        self.creates = 0
        self.ben_ids = self.make_children(self.ben_site, payment=True)
        configure(self.ben_site)
        mail.outbox.clear()
        self.meta.requests.clear()

    # children ---------------------------------------------------------------
    def make_children(self, site, payment=False):
        order, proof = make_order(site)
        ids = {
            **MISSING_CHILD,
            "order_id": order.public_id,
            "proof_id": proof.public_id,
            "account_id": SitePaymentAccount.objects.filter(site=site).order_by("-pk").first().public_id,
            "product_id": self.product.public_id,
        }
        if payment:
            ids["payment_id"] = submit(
                Subscription.objects.get(site=site), timezone.now(), content=JPEG
            ).public_id
        return ids

    def own_ids(self, template):
        """Children of Ana's site for the placeholders this route has. An order is made anew each
        time because approve and reject use it up; a payment is whatever Ana last sent."""
        ids = {**MISSING_CHILD, "product_id": self.product.public_id}
        if "{order_id}" in template:
            ids.update(self.make_children(self.site))
        elif "{account_id}" in template:
            ids["account_id"] = make_payment_account(self.site).public_id
        if "{payment_id}" in template:
            payment = BankTransferPayment.objects.filter(
                subscription__site=self.site
            ).order_by("-pk").first()
            if payment:
                ids["payment_id"] = payment.public_id
        return ids

    # calling a route --------------------------------------------------------
    def call(self, method, template, site_id, ids, client=None):
        """One route, as this client, with these ids filled in. Rate limits start afresh."""
        cache.clear()
        path = "/v1" + template.replace("{site_id}", site_id)
        for name, value in ids.items():
            path = path.replace("{%s}" % name, value)
        client = client or self.client
        if path.endswith("/business-profile/seal/") and method == "POST":
            return client.post(
                path, data={"seal": qr_file(name="seal.png")}, secure=True, HTTP_ORIGIN=ORIGIN
            )
        if path.endswith("/billing/payments/") and method == "POST":
            return client.post(
                path, secure=True, HTTP_ORIGIN=ORIGIN,
                data={"plan": "monthly", "transferred_on": timezone.localdate().isoformat(),
                      "transferred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                      "transfer_reference": "REF-0001", "sender_bank": "BDO",
                      "sender_first_name": "Ana", "sender_last_name": "Reyes",
                      "sender_account_number": "001234567890", "amount_sent": "499.00",
                      "proof": SimpleUploadedFile("slip.jpg", JPEG, content_type="image/jpeg")},
            )
        if path.endswith("/payment-methods/accounts/") and method == "POST":
            return client.post(path, data=BANK, secure=True, HTTP_ORIGIN=ORIGIN)
        if "/payment-methods/accounts/" in path and method == "POST":
            return client.post(path, data=BANK, secure=True, HTTP_ORIGIN=ORIGIN)
        if "/catalog/" in path and path.endswith("/image/"):
            revision = CatalogProduct.objects.get(pk=self.product.pk).revision
            if method == "POST":
                return client.post(
                    path, data={"file": image_file(), "revision": revision}, secure=True,
                    HTTP_ORIGIN=ORIGIN,
                )
            return client.delete(f"{path}?revision={revision}", secure=True, HTTP_ORIGIN=ORIGIN)
        if method == "GET":
            return self.get(path, client=client)
        return self.send(method.lower(), path, self.body_for(method, template), client=client)

    def body_for(self, method, template):
        if template.endswith("/business-profile/"):
            return PROFILE_BODY
        if template == "/sites/{site_id}/":
            return SITE_BODY
        if "/sante/" in template:
            return {"sponsor_id": "TAKEN-1"}
        if "/catalog/" in template:
            self.product.refresh_from_db()
            if method == "PATCH":
                return {"revision": self.product.revision, "name": self.product.name}
            self.creates += 1
            return {"code": f"FX-NEW-{self.creates}", "name": "Fixture New", "srp_centavos": 9999,
                    "earnings_rate": "0.3500"}
        if template.endswith("/payment-methods/"):
            return {"direct_enabled": True}
        if "/pack/" in template:
            body = {"state_version": 1}
            if template.endswith("/reject/"):
                body["reason_code"] = "proof_unreadable"
            return body
        if "/meta/" in template and method == "PUT":
            return {"pixel_id": PIXEL, "access_token": TOKEN, "test_event_code": CODE}
        return None

    # what a refused request must not touch ---------------------------------
    def state(self):
        return {
            "rows": row_snapshot(),
            "private_files": stored_files(self.private_dir),
            "billing_proof_files": sorted(self.proof_store.files),
            "public_media": (sorted(self.media.files), list(self.media.deleted)),
            "outbox": [(m.to, m.subject) for m in mail.outbox],
            "meta_requests": len(self.meta.requests),
        }

    def assertUnchanged(self, before, message):
        after = self.state()
        for part in before:
            self.assertEqual(after[part], before[part], f"{message}: {part} changed")
