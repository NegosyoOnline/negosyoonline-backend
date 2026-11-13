"""Helpers for the tests of the proposal, approval and run of an erasure (plan z8uq9mb22v).

`fill` plants a personal value in every table an erasure blanks, and a private file for every file
it deletes or keeps. Time is always passed in; no test reads the clock."""
from datetime import date

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import override_settings

from apps.billing import services as billing
from apps.billing.models import Subscription
from apps.common import private_files
from apps.inbox.models import Contact, ContactGroup, Conversation, ConversationMessage, DoNotText
from apps.lifecycle import workflow
from apps.lifecycle.models import ErasureBatch, ErasureItem
from apps.lifecycle.tests.base import (
    DUE, ENDED, NOW, LifecycleTestCase, State, days, ended_site, second_site,
)
from apps.members.models import Member, MemberAddressEvent, MemberOtpChallenge, MemberSession
from apps.meta_ads.models import MetaOrderContext, MetaSettings
from apps.notifications.models import SmsDevice
from apps.pack.models import CheckoutToken, PackOrder, SanteRegistration
from apps.pack.tests.owner_base import make_order as make_pack_order
from apps.payments.models import SitePaymentAccount
from apps.payments.tests.base import PNG, stored_files, use_local_private_store
from apps.sante.models import SanteSiteSettings
from apps.shop.tests.owner_base import make_order as make_shop_order
from apps.sites.models import Site, SiteBusinessProfile

NAME, EMAIL, MOBILE = "Zuleika Planted-Name", "planted.person@example.test", "+639998887776"
NUMBER = "5550001234567"

# Every feature switch an erasure must not depend on (plan D13) is left at its default (off) here
# unless a test turns it on. BILLING_ENABLED comes from LifecycleTestCase.
ON = dict(SITE_ERASURE_ENABLED=True, RETENTION_ENABLED=True)


def staff_user(name="approver"):
    user = get_user_model().objects.filter(username=name).first()
    return user or get_user_model().objects.create_user(name, password="pw", is_staff=True)


def add_due_site(subscription, name="second-store", due=DUE):
    """A second site of the same account with a subscription of its own, ended and due like
    `ended_site`'s."""
    other = second_site(subscription, name=name)
    row = billing.start_subscription(other, other.account, now=ENDED - days(30))
    Subscription.objects.filter(pk=row.pk).update(
        state=State.CANCELLED_NO_PAYMENT_METHOD, service_ended_at=ENDED,
        download_until=ENDED + days(30), deletion_due_at=due,
    )
    return Subscription.objects.get(pk=row.pk)


def fill(subscription, tag=""):
    """Plant a personal value and a file in every table and folder an erasure touches. Returns the
    keys of the files that an erasure deletes and of the pack proof files that it keeps."""
    site = subscription.site
    seal = private_files.save("site-seals", PNG, "image/png")
    qr = private_files.save("payment-qr", PNG, "image/png")
    Site.objects.filter(pk=site.pk).update(
        contact_email=EMAIL, contact_mobile=MOBILE, display_name=NAME,
        facebook_url="https://facebook.test/planted",
    )
    SiteBusinessProfile.objects.filter(site=site).update(
        business_name="Planted Trading", seal_key=seal, tin=NUMBER, business_address="1 Planted St",
    )
    SitePaymentAccount.objects.create(
        site=site, kind="bank", account_type="savings", provider_name="BDO", account_name=NAME,
        account_number=NUMBER, qr_file_key=qr,
    )
    SanteSiteSettings.objects.create(site=site, sponsor_id="PLANTED-SPONSOR")
    member = Member.objects.create(
        site=site, full_name=NAME, mobile=MOBILE, email=EMAIL, delivery_address={"line": NAME},
    )
    MemberAddressEvent.objects.create(
        site=site, member=member, old={"line": f"Old {NAME}"}, new={"line": f"New {NAME}"},
        ip_advisory="203.0.113.7",
    )
    MemberSession.objects.create(
        member=member, site=site, token_hash=f"s{site.pk}{tag}".ljust(64, "a"),
        expires_at=NOW + days(1), ip_advisory="203.0.113.9",
    )
    MemberOtpChallenge.objects.create(
        site=site, member=member, purpose="signin", channel="email", target=EMAIL,
        requested_contact=EMAIL, code_hash="c" * 64, binding_hash="b" * 64, expires_at=NOW + days(1),
    )
    pack_order, pack_proof = make_pack_order(site)
    shop_order, shop_proof = make_shop_order(site)
    PackOrder.objects.filter(pk=pack_order.pk).update(buyer_name=NAME, delivery_address={"line": NAME})
    SanteRegistration.objects.create(
        site=site, member=pack_order.member, pack_order=pack_order, status="awaiting_buyer_details",
        date_of_birth=date(1990, 1, 2), desired_username="planted90", alternate_username="planted91",
        status_entered_at=NOW,
    )
    MetaSettings.objects.create(site=site, token_encrypted="CIPHER", test_event_code="TEST123")
    MetaOrderContext.objects.create(order=pack_order, site=site, fbp="fb.1.planted")
    device = SmsDevice.objects.get_or_create(name="phone", defaults={"token_hash": "d" * 64})[0]
    conversation = Conversation.objects.create(
        platform="sms", line=device, contact=MOBILE, last_message_at=NOW, owner_kind="site", site=site,
    )
    message = ConversationMessage.objects.create(
        conversation=conversation, platform="sms", direction="in", body=f"Hello {NAME}",
    )
    DoNotText.objects.get_or_create(platform="sms", mobile=MOBILE, defaults={"message": message})
    group = ContactGroup.objects.create(owner_kind="site", site=site, name="Regulars")
    contact = Contact.objects.create(
        owner_kind="site", site=site, first_name="Zuleika", mobile=MOBILE,
        created_by=staff_user("filler"),
    )
    contact.groups.add(group)
    return {
        "deleted": [seal, qr, shop_proof.file_key],
        "kept": [pack_proof.file_key],
        "member": member, "pack_order": pack_order, "shop_order": shop_order,
        "shop_proof": shop_proof, "pack_proof": pack_proof,
    }


def every_row():
    """Every row of every table, to show what a call changed."""
    return {
        model._meta.label: list(model._base_manager.order_by("pk").values())
        for model in apps.get_models()
    }


# Tables an erasure must never write (plan section 3, "Kept whole"), by app label.
KEPT_APPS = ("billing", "statements", "invoices", "ledger")


def kept_rows():
    rows = every_row()
    kept = {label: value for label, value in rows.items() if label.split(".")[0] in KEPT_APPS}
    kept["accounts.AccountAuditEvent"] = rows["accounts.AccountAuditEvent"]
    kept["operators.OperatorAuditEntry"] = rows["operators.OperatorAuditEntry"]
    return kept


@override_settings(**ON)
class RunTestCase(LifecycleTestCase):
    """A due site with data everywhere, an approver, and a private store of its own."""

    def setUp(self):
        self.store = use_local_private_store(self)
        self.staff = staff_user()
        self.sub = ended_site()
        self.planted = fill(self.sub)
        self.site = self.sub.site

    def propose(self, *subs, now=NOW):
        sites = [s.site.public_id for s in (subs or (self.sub,))]
        return workflow.propose(self.staff, sites, now)

    def approve(self, batch, exclude=(), now=NOW, reason="weekly review"):
        count = batch.items.exclude(site__public_id__in=exclude).count()
        return workflow.approve(
            self.staff, batch.public_id, confirmation=f"erase batch {batch.public_id} {count} sites",
            exclude=list(exclude), reason=reason, now=now,
        )

    def approved(self, *subs, now=NOW):
        return self.approve(self.propose(*subs, now=now), now=now)

    def run_batch(self, batch, now=NOW):
        return workflow.run_batch(batch.public_id, now=now)

    def files(self):
        return stored_files(self.store)

    def item(self, batch, sub=None):
        return ErasureItem.objects.get(batch=batch, site=(sub or self.sub).site)

    def fresh_batch(self, batch):
        return ErasureBatch.objects.get(pk=batch.pk)
