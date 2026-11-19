"""Helpers shared by the Membership Pack checkout tests."""
from datetime import timedelta

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from apps.billing.tests.base import make_subscription
from apps.pack.models import CheckoutToken
from apps.payments.tests.base import (
    PNG, make_payment_account, stored_files, use_local_private_store,
)
from apps.sante.tests.agreement_helpers import make_master, make_operator, published
from apps.storefront.tests.base import HOST, SECRET, SurfaceTestCase

BASE = "/storefront/v1/checkout/"
OTHER_HOST = "bens-store.negosyotest.com"
VISITOR = "203.0.113.50"
NAME, EMAIL, MOBILE = "Maria Santos", "maria@buyer.test", "09171234567"
NOT_FOUND = {"error": {"code": "not_found", "message": "Not found."}}
PDF = b"%PDF-1.4\n" + b"proof\n" * 10 + b"%%EOF"

__all__ = ["PackTestCase", "stored_files", "PNG", "PDF", "HOST", "OTHER_HOST", "NOT_FOUND", "mail"]


@override_settings(
    PACK_ENABLED=True, MEMBERS_ENABLED=True, SANTE_AGREEMENT_ENABLED=True,
    PAYMENT_METHODS_ENABLED=True,
)
class PackTestCase(SurfaceTestCase):
    def setUp(self):
        super().setUp()
        self.directory = use_local_private_store(self)
        self.site = make_subscription().site
        self.other_site = make_subscription(email="ben@example.test", name="bens-store").site
        operator = make_operator()
        make_master(operator)
        self.version = published(operator)
        self.account = make_payment_account(self.site)

    # contact step
    def contact_fields(self, **changes):
        return {
            "full_name": NAME, "mobile": MOBILE, "email": EMAIL,
            "agreement_version_id": str(self.version.pk), "agreement_accept": "1",
            "enrollment_authorized": "1", **changes,
        }

    def contact_fields_without_agreement(self, **changes):
        """What the storefront posts now: the Membership Terms are accepted at the details step."""
        fields = self.contact_fields(**changes)
        del fields["agreement_version_id"], fields["agreement_accept"]
        return fields

    def post(self, path, host=HOST, secret=SECRET, ip=VISITOR, **fields):
        body = {"host": host, **fields}
        if ip is not None:
            body["visitor_ip"] = ip
        headers = {"HTTP_X_REAL_IP": "198.51.100.200"}
        if secret is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {secret}"
        return self.client.post(BASE + path, body, content_type="application/json", secure=True, **headers)

    def contact(self, **fields):
        return self.post("contact/", **self.contact_fields(**fields))

    def start(self, **fields):
        """A started checkout: returns the raw token."""
        response = self.contact(**fields)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["token"]

    # place step
    def proof_fields(self, token, **changes):
        when = (timezone.localtime() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        return {
            "token": token, "submit_key": "key-1", "account_id": self.account.public_id,
            "reference_number": "REF123", "amount_sent": "1488.00", "sender_bank": "BDO",
            "sender_first_name": "Maria", "sender_last_name": "Santos",
            "sender_account_number": "001122", "transferred_at": when, **changes,
        }

    def place(self, token, host=HOST, secret=SECRET, content=PNG, name="proof.png", declared="image/png", **fields):
        data = {"host": host, "visitor_ip": VISITOR, **self.proof_fields(token, **fields)}
        data = {k: v for k, v in data.items() if v is not None}
        if content is not None:
            data["proof"] = SimpleUploadedFile(name, content, content_type=declared)
        headers = {"HTTP_X_REAL_IP": "198.51.100.200"}
        if secret is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {secret}"
        return self.client.post(BASE + "place/", data, secure=True, **headers)

    def proof_files(self):
        return [key for key in stored_files(self.directory) if key.startswith("sante/proofs/")]

    def placed(self, **kw):
        """A bank order placed, then given its member as a bank order of before the change had it (the
        fixture of the member-side tests). A new bank order has none: use `placed_memberless`."""
        token, body = self.placed_memberless(**kw)
        self.give_member(body["order_number"])
        return token, body

    def give_member(self, order_number, site=None):
        """What the old placement did at once: the member, benefits on and the registration (T1)."""
        from django.db import transaction
        from apps.pack import buyer, registration
        from apps.pack.models import PackOrder
        with transaction.atomic():
            order = PackOrder.objects.select_for_update().get(site=site or self.site, order_number=order_number)
            member, refusal = buyer.attach_member(order)
            assert member is not None, refusal
            order.benefits_active_at = timezone.now()
            order.save(update_fields=["benefits_active_at"])
            registration.activate(order, order.benefits_active_at)
        return PackOrder.objects.get(pk=order.pk)

    def placed_memberless(self, **kw):
        token = self.start()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.place(token, **kw)
        self.assertEqual(response.status_code, 200, response.content)
        return token, response.json()

    def token_row(self, raw):
        from apps.pack.checkout import token_hash
        return CheckoutToken.objects.get(token_hash=token_hash(raw))
