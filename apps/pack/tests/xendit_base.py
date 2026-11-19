"""Helpers for the Xendit tests of the Membership Pack: a Santé site with a working Xendit key (a
fake Xendit, no network), the storefront calls, and the webhook call."""
import json

from cryptography.fernet import Fernet
from django.test import override_settings

from apps.pack import constants as C
from apps.pack.models import PackOrder
from apps.pack.tests.base import BASE, HOST, PackTestCase, SECRET, VISITOR
from apps.payments import crypto, fake_xendit
from apps.payments.models import SitePaymentSettings, SiteXenditCredential

FERNET_KEY = Fernet.generate_key().decode()  # made for this test run; never a real value
API_KEY = "xnd_development_fakekeyfortests"
TOKEN = "fake-callback-token-1"
__all__ = ["XenditPackCase", "API_KEY", "TOKEN", "FERNET_KEY", "fake_xendit", "give_key", "post_webhook"]


def give_key(site, *, api_key=API_KEY, token=TOKEN, enabled=True):
    """Store a working Xendit key and webhook token for the site, switched on."""
    cred, _ = SiteXenditCredential.objects.update_or_create(
        site=site, defaults={
            "api_key_ciphertext": crypto.encrypt(api_key), "webhook_token_ciphertext": crypto.encrypt(token),
            "key_mode": "test",
        },
    )
    SitePaymentSettings.objects.update_or_create(site=site, defaults={"xendit_enabled": enabled})
    return cred


def post_webhook(client, cred, body, *, token=TOKEN, webhook_id=None, key=None, raw=None):
    headers = {}
    if token is not None:
        headers["HTTP_X_CALLBACK_TOKEN"] = token
    if webhook_id:
        headers["HTTP_WEBHOOK_ID"] = webhook_id
    data = raw if raw is not None else json.dumps(body)
    return client.post(
        f"/v1/webhooks/xendit/{key or cred.webhook_key}/", data, content_type="application/json",
        secure=True, **headers,
    )


@override_settings(XENDIT_ENABLED=True, XENDIT_FAKE=True, PAYMENT_CREDENTIALS_KEY=FERNET_KEY)
class XenditPackCase(PackTestCase):
    def setUp(self):
        super().setUp()
        fake_xendit.reset()
        self.cred = give_key(self.site)

    def place_xendit(self, token, host=HOST, submit_key="key-1", **fields):
        data = {"host": host, "visitor_ip": VISITOR, "token": token, "submit_key": submit_key,
                "pay_method": "xendit", **fields}
        return self.client.post(
            BASE + "place/", data, secure=True, HTTP_X_REAL_IP="198.51.100.200",
            HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )

    def placed_xendit(self, **kw):
        """A started checkout placed by Xendit: (raw token, answer body, order)."""
        token = self.start(**kw)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.place_xendit(token)
        self.assertEqual(response.status_code, 200, response.content)
        return token, response.json(), PackOrder.objects.get(order_number=response.json()["order_number"])

    def onboarding(self, path, token):
        return self.client.post(
            "/storefront/v1/" + path, {"host": HOST, "visitor_ip": VISITOR, "token": token},
            content_type="application/json", secure=True, HTTP_X_REAL_IP="198.51.100.200",
            HTTP_AUTHORIZATION=f"Bearer {SECRET}",
        )

    def give_legacy_member(self, order):
        """The shape an order placed before hotfix z8uq9mgpt5 has: a member made at placement. New Xendit
        orders have none until they are paid; the old ones in the database still do."""
        from apps.members import services as members
        order.refresh_from_db()
        if order.member_id is None:
            member = members.create_member(
                self.site, full_name=order.buyer_name, mobile=order.buyer_mobile, email=order.buyer_email,
            )
            PackOrder.objects.filter(pk=order.pk).update(member=member, buyer_mobile="", buyer_email="")
            order.refresh_from_db()
        return order.member

    def get_pack(self):
        return self.client.get(
            "/storefront/v1/pack/", {"host": HOST}, secure=True, HTTP_AUTHORIZATION=f"Bearer {SECRET}",
            HTTP_X_REAL_IP="198.51.100.200",
        ).json()

    def status(self, token, host=HOST, **fields):
        return self.post("payment-status/", host=host, token=token, **fields)

    def invoice_of(self, order):
        order.refresh_from_db()
        return fake_xendit.get(order.xendit_invoice_id)

    def webhook(self, order_or_doc, status="PAID", **kw):
        """Play Xendit's callback for the order's invoice: the invoice document as Xendit would send it."""
        if isinstance(order_or_doc, PackOrder):
            doc = dict(self.invoice_of(order_or_doc))
        else:
            doc = dict(order_or_doc)
        doc["status"] = status
        for name in ("paid_amount", "currency", "amount", "id"):
            if name in kw:
                doc[name] = kw.pop(name)
        if status in ("PAID", "SETTLED"):
            doc.setdefault("paid_at", "2026-11-18T10:00:00.000Z")
            if "amount" in doc:
                doc.setdefault("paid_amount", doc["amount"])
        return post_webhook(self.client, kw.pop("cred", self.cred), doc, **kw)

    def pay_in_fake(self, order, **kw):
        return fake_xendit.pay(order.xendit_invoice_id, **kw)
