"""Helpers for the Subscriber's order review tests: orders and proofs made directly, a signed-in
Subscriber (Ana) with a Santé site, and the walker the Billing-only inventory uses."""
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.common import private_files
from apps.members.models import Member
from apps.pack import constants as C
from apps.pack.models import PackOrder, PackProofSubmission
from apps.payments.tests.base import PNG, make_payment_account, stored_files, use_local_private_store
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase

__all__ = ["OwnerTestCase", "make_order", "pack_call", "PNG", "stored_files"]

_counter = [0]


def make_order(site, *, status=C.PENDING_REVIEW, content=PNG, content_type="image/png", lock=False):
    """A member, their order and its first proof submission (a real file in the private store)."""
    _counter[0] += 1
    n = _counter[0]
    member = Member.objects.create(
        site=site, full_name=f"Maria Santos {n}", mobile=f"+6391712{n:05d}"[:13], email=f"m{n}@buyer.test",
        mobile_state="verified", email_state="verified",
    )
    order = PackOrder.objects.create(
        site=site, member=member, order_number=f"SMP9{n:04d}", price_centavos=C.PRICE_CENTAVOS,
        buyer_name=member.full_name, payment_status=status, rejection_lock_active=lock,
        idempotency_key=f"key-{n}", benefits_active_at=timezone.now(),
    )
    account = make_payment_account(site)
    key = private_files.save(C.PROOF_FOLDER.format(site.pk), content, content_type)
    sub = PackProofSubmission.objects.create(
        site=site, order=order, seq=1, account_public_id=account.public_id, account_kind=account.kind,
        account_provider_name=account.provider_name, account_name=account.account_name,
        account_number_last4=account.account_number[-4:], reference_number="REF123",
        amount_sent_centavos=C.PRICE_CENTAVOS, sender_bank="BDO", sender_name="Maria Santos",
        sender_account_number="001122", transferred_at=timezone.now() - timedelta(hours=1),
        file_key=key, content_type=content_type, size=len(content),
    )
    return order, sub


def pack_call(case, method, path, client=None):
    """Call a pack order route for a test that walks every route under a site (Billing's
    inventory). The site in the path gets one order with one proof (made once per site), and
    {order_id} and {proof_id} are filled in. Each call starts with a new order for the writes, so a
    repeated approve is never a conflict."""
    cache.clear()
    site = Site.objects.filter(public_id=path.split("/sites/")[1].split("/")[0]).first()
    order = proof = None
    if site is not None:
        if "{order_id}" in path:
            order, proof = make_order(site)
        path = path.replace("{order_id}", order.public_id if order else "pko_missing")
        path = path.replace("{proof_id}", proof.public_id if proof else "pps_missing")
    if method == "GET":
        return case.get(path, client=client)
    body = {"state_version": order.state_version if order else 1}
    if path.endswith("/reject/"):
        body["reason_code"] = "proof_unreadable"
    return case.send("post", path, body, client=client)


@override_settings(PACK_ENABLED=True, PAYMENT_METHODS_ENABLED=True)
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
        return f"/v1/sites/{site_id or self.site_id}/pack/orders/{tail}"

    def other_site(self):
        """Ben's site, with an order."""
        ben = self.other_client()
        site_id = self.create_site(client=ben, subdomain="bens-store")["id"]
        site = Site.objects.get(public_id=site_id)
        return ben, site, *make_order(site)
