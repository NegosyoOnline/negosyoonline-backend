"""Helpers for the Subscriber's product order tests: orders made directly (one line, one proof
file), a signed-in Subscriber (Ana) with a Santé site, and the walker the Billing-only inventory
uses."""
from datetime import date, timedelta

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.common import private_files
from apps.payments.tests.base import PNG, make_payment_account, stored_files, use_local_private_store
from apps.shop import constants as C
from apps.shop.models import ShopOrder, ShopOrderEvent, ShopOrderLine, ShopProofSubmission
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase

__all__ = ["OwnerTestCase", "make_order", "shop_call", "PNG", "stored_files"]

_counter = [0]


def make_order(site, *, status=C.PENDING_REVIEW, shipped=False, delivered=False, content=PNG,
               content_type="image/png", total=10000, member=None):
    """A guest order with one line and one proof (a real file in the private store)."""
    _counter[0] += 1
    n = _counter[0]
    verified = status == C.VERIFIED
    today = timezone.localdate()
    order = ShopOrder.objects.create(
        site=site, order_number=f"SO9{n:04d}", buyer_kind="member" if member else "guest", member=member,
        ship_mode="guest", receiver_name=f"Maria Santos {n}", receiver_mobile=f"+6391712{n:05d}"[:13],
        contact_email=f"m{n}@buyer.test", delivery_address={"line": "1 St", "city": "Makati", "province": "Metro Manila"},
        payment_status=status, subtotal_centavos=total, shipping_charged_centavos=0, shipping_policy="zero",
        total_centavos=total, quote_fingerprint="f" * 64, idempotency_key=f"key-{n}", view_token_hash="t" * 64,
        verified_at=timezone.now() if verified else None,
        cancelled_at=timezone.now() if status == C.CANCELLED else None,
        cancel_note="Out of stock" if status == C.CANCELLED else "",
        courier="J&T" if shipped else "", tracking_no="T123" if shipped else "",
        shipped_on=today - timedelta(days=1) if shipped else None,
        delivered_on=today if delivered else None,
    )
    ShopOrderLine.objects.create(
        order=order, position=1, product_id="prod_x", code="FX-A", name="Fixture A", revision=1,
        srp_centavos=total, earnings_rate="0.3500", quantity=1, line_srp_total_centavos=total,
        line_earnings_centavos=total * 35 // 100, line_members_total_centavos=total - total * 35 // 100,
        line_charged_centavos=total,
    )
    account = make_payment_account(site)
    key = private_files.save(C.PROOF_FOLDER.format(site.pk), content, content_type)
    proof = ShopProofSubmission.objects.create(
        order=order, account_public_id=account.public_id, account_kind=account.kind,
        account_provider_name=account.provider_name, account_name=account.account_name,
        account_number_last4=account.account_number[-4:], reference_number="REF123",
        amount_sent_centavos=total, file_key=key, content_type=content_type, size=len(content),
    )
    ShopOrderEvent.objects.create(order=order, kind="placed", actor_type="buyer")
    return order, proof


def shop_call(case, method, path, client=None):
    """Call a shop order route for a test that walks every route under a site (Billing's
    inventory). The site in the path gets orders made on the spot, and {order_id} and {proof_id}
    are filled in. Each call starts with a new order for the writes, so a repeated write is never
    a conflict."""
    cache.clear()
    site = Site.objects.filter(public_id=path.split("/sites/")[1].split("/")[0]).first()
    order = proof = None
    if site is not None and "{order_id}" in path:
        needs_verified = path.endswith(("/ship/", "/deliver/"))
        order, proof = make_order(site, status=C.VERIFIED if needs_verified else C.PENDING_REVIEW,
                                  shipped=path.endswith("/deliver/"))
    path = path.replace("{order_id}", order.public_id if order else "sho_missing")
    path = path.replace("{proof_id}", proof.public_id if proof else "shp_missing")
    if method == "GET":
        return case.get(path, client=client)
    body = {"state_version": order.state_version if order else 1}
    if path.endswith("/cancel/"):
        body["note"] = "Out of stock"
    if path.endswith("/ship/"):
        body["courier"] = "J&T"
    return case.send("post", path, body, client=client)


@override_settings(SHOP_ENABLED=True, PAYMENT_METHODS_ENABLED=True)
class OwnerTestCase(SitesApiTestCase):
    """Signed in as Ana, who has one Santé site: self.site and its public id self.site_id."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.directory = use_local_private_store(self)
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        mail.outbox.clear()  # Ana's sign-in mail

    def url(self, tail="", site_id=None):
        return f"/v1/sites/{site_id or self.site_id}/shop/orders/{tail}"

    def other_site(self):
        """Ben's site, with an order."""
        ben = self.other_client()
        site_id = self.create_site(client=ben, subdomain="bens-store")["id"]
        site = Site.objects.get(public_id=site_id)
        return ben, site, *make_order(site)

    def act(self, order, action, **body):
        return self.post(self.url(f"{order.public_id}/{action}/"), {"state_version": order.state_version, **body})
