"""Helpers shared by the shop tests: a Santé site with a payment account and two products, buyers
(guest, member, member with benefits), and calls as the storefronts server."""
import json
from decimal import Decimal

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from apps.billing.tests.base import make_subscription
from apps.catalog.models import CatalogProduct
from apps.catalog.tests.base import make_product
from apps.members import services as members, sessions
from apps.pack.models import PackOrder
from apps.payments.tests.base import PNG, make_payment_account, stored_files, use_local_private_store
from apps.shop import constants as C
from apps.storefront.tests.base import HOST, SECRET, SurfaceTestCase

BASE = "/storefront/v1/"
VISITOR = "203.0.113.50"
NOT_FOUND = {"error": {"code": "not_found", "message": "Not found."}}
PDF = b"%PDF-1.4\n" + b"proof\n" * 10 + b"%%EOF"
MANILA_ADDRESS = {
    "line": "1 Mabini St", "barangay": "Bagumbayan", "city": "Quezon City", "province": "Metro Manila",
    "zip": "", "landmark": "",
}
CEBU_ADDRESS = {
    "line": "5 Osmena Blvd", "barangay": "Lahug", "city": "Cebu City", "province": "Cebu",
    "zip": "", "landmark": "",
}
__all__ = [
    "ShopTestCase", "PNG", "PDF", "HOST", "SECRET", "NOT_FOUND", "MANILA_ADDRESS", "CEBU_ADDRESS",
    "mail", "stored_files",
]


@override_settings(
    SHOP_ENABLED=True, SANTE_SETTINGS_ENABLED=True, SANTE_CATALOG_ENABLED=True,
    PAYMENT_METHODS_ENABLED=True, MEMBERS_ENABLED=True, PACK_ENABLED=True,
)
class ShopTestCase(SurfaceTestCase):
    def setUp(self):
        super().setUp()
        self.directory = use_local_private_store(self)
        self.site = make_subscription().site
        self.other_site = make_subscription(email="ben@example.test", name="bens-store").site
        self.account = make_payment_account(self.site)
        # A: 100.00, earns 35% (members total 65.00). B: 250.50, earns 10% (members 225.45).
        self.a = self.product("FX-A", 10000, "0.3500", weight="0.25", slot="1", sqty=1)
        self.b = self.product("FX-B", 25050, "0.1000", weight="0.50", slot="2", sqty=1)
        self.key = 0

    # -- fixtures
    def product(self, code, srp, rate, *, weight="0.25", slot="1", sqty=1, active=True):
        product = make_product(code, code, srp, rate, active=active)
        CatalogProduct.objects.filter(pk=product.pk).update(
            weight_kg=Decimal(weight), volume_slot=None if slot is None else Decimal(slot),
            shipping_qty=sqty,
        )
        product.refresh_from_db()
        return product

    def member(self, email="maria@buyer.test", mobile="09171234567", name="Maria Santos", *,
               benefits=False, site=None, address=None):
        site = site or self.site
        member = members.create_member(
            site, full_name=name, mobile=mobile, email=email, email_state="pending",
            delivery_address=address,
        )
        if benefits:
            self.give_benefits(member)
        return member

    def give_benefits(self, member, status="pending_review"):
        count = PackOrder.objects.count() + 1
        return PackOrder.objects.create(
            site=member.site, member=member, order_number=f"SMP{count:05d}", price_centavos=148800,
            buyer_name=member.full_name, payment_status=status, idempotency_key=f"pk-{count}",
            benefits_active_at=timezone.now(),
        )

    def token(self, member):
        return sessions.create_session(member.site, member)[1]

    # -- calls as the storefronts server
    def headers(self, secret=SECRET):
        headers = {"HTTP_X_REAL_IP": "198.51.100.200"}
        if secret is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {secret}"
        return headers

    def post(self, path, host=HOST, secret=SECRET, **fields):
        body = {"host": host, "visitor_ip": VISITOR, **fields}
        return self.client.post(
            BASE + path, body, content_type="application/json", secure=True, **self.headers(secret)
        )

    def quote(self, lines, **fields):
        return self.post("shop/quote/", lines=lines, **fields)

    def cart(self, a=1, b=0):
        lines = []
        if a:
            lines.append({"id": self.a.public_id, "qty": a})
        if b:
            lines.append({"id": self.b.public_id, "qty": b})
        return lines

    def order_fields(self, lines, *, member_token=None, ship_mode=None, address=MANILA_ADDRESS, **fields):
        """The multipart fields of an order, with the fingerprint of its own quote. `fields`
        override any of them (a value of None drops the field)."""
        asked = {"member_token": member_token, "ship_mode": ship_mode, "address": address}
        body = self.quote(lines, **{k: v for k, v in asked.items() if v})
        fingerprint = body.json().get("fingerprint", "") if body.status_code == 200 else ""
        self.key += 1
        data = {
            "lines": json.dumps(lines), "fingerprint": fingerprint, "submit_key": f"key-{self.key}",
            "account_id": self.account.public_id, "reference_number": "REF123", "amount_sent": "100.00",
            "receiver_name": "Maria Santos", "receiver_mobile": "09171234567",
            "contact_email": "buyer@example.test", "member_token": member_token, "ship_mode": ship_mode,
            "address": json.dumps(address) if address else None,
        }
        data.update(fields)
        return {k: v for k, v in data.items() if v is not None}

    def place(self, lines, *, host=HOST, secret=SECRET, content=PNG, name="proof.png",
              declared="image/png", **fields):
        data = {"host": host, "visitor_ip": VISITOR, **self.order_fields(lines, **fields)}
        if content is not None:
            data["proof"] = SimpleUploadedFile(name, content, content_type=declared)
        return self.client.post(BASE + "shop/orders/", data, secure=True, **self.headers(secret))

    def placed(self, lines=None, **fields):
        """Place an order and run its after-commit emails. Returns the response body."""
        with self.captureOnCommitCallbacks(execute=True):
            response = self.place(lines or self.cart(), **fields)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def proof_files(self):
        return [k for k in stored_files(self.directory) if k.startswith("sante/shop-proofs/")]
