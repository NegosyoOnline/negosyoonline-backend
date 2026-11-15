"""Shared set-up for the tenant isolation suite over the owner API: Ana and Ben each own a Santé
site, every owner switch is on, and everything a route could write to is snapshotted so a test
can prove a refused request changed nothing."""
import itertools
from unittest import mock

from django.apps import apps as django_apps
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from apps.accounts.tests.base import ORIGIN
from apps.billing import plan
from apps.billing.models import BankTransferPayment, Subscription
from apps.billing.tests.base import JPEG, submit, transfers_on
from apps.catalog.models import CatalogProduct
from apps.catalog.tests.base import FakeMedia, designate, image_file, make_product
from apps.invoices import services as invoice_services
from apps.invoices.tests.test_scans import MemoryScanStore
from apps.members import recovery
from apps.members.models import Member, MemberNotice
from apps.inquiries.tests import base as inquiries_base
from apps.meta_ads.tests.base import CODE, KEY, PIXEL, TOKEN, FakeMeta, configure
from apps.pack import constants as PACK
from apps.pack.models import SanteRegistration
from apps.pack.tests.owner_base import make_order
from apps.payments.models import SitePaymentAccount
from apps.shop import constants as SHOP
from apps.shop.models import ProductReview
from apps.shop.tests.owner_base import make_order as make_shop_order
from apps.payments.tests.base import BANK, make_payment_account, qr_file, stored_files, use_local_private_store
from apps.sites.models import Site
from apps.training.models import Course, Lesson, LiveTraining, Module
from apps.media_library.models import MediaAsset
from apps.media_library.tests.base import FakeStore, HEAD, RW_SETTINGS, rw_blob_url
from apps.sites.tests.api_base import SitesApiTestCase
from apps.sites.tests.test_isolation import PROFILE_BODY, SITE_BODY
from apps.statements.models import BillingProfile
from apps.statements.tests.base import make_book, make_item, make_staff
from apps.statements.tests.test_owner_api import PDF_BYTES, PROFILE, issue_for
from apps.common import private_files

BEN_PAGE = "5000000000005"
BEN_REF = f"{BEN_PAGE}_7000000000001"
MISSING_SITE = "site_00000000000000000000"
MISSING_CHILD = {
    "order_id": "pko_00000000000000000000",
    "proof_id": "pps_00000000000000000000",
    "account_id": "pay_00000000000000000000",
    "payment_id": "btp_00000000000000000000",
    "product_id": "prod_00000000000000000000",
    "member_id": "mem_00000000000000000000",
    "notice_id": "ntc_00000000000000000000",
    "course_id": "crs_00000000000000000000",
    "module_id": "mdl_00000000000000000000",
    "lesson_id": "lsn_00000000000000000000",
    "live_id": "liv_00000000000000000000",
    "asset_id": "mda_00000000000000000000",
    "invoice_id": "mbi_00000000000000000000000000",
    "review_id": "review_00000000000000000000",
    "number": "NOBS-2026-000000",
    "ref": "999999_7000000000000",  # a conversation ref of no page of ours
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
    # members (the Subscriber's recovery of a member who lost both contacts)
    ("GET", "/sites/{site_id}/members/"): 200,
    ("GET", "/sites/{site_id}/members/by-order/"): 200,
    ("GET", "/sites/{site_id}/members/{member_id}/recovery/"): 200,
    ("POST", "/sites/{site_id}/members/{member_id}/recovery/check/"): 200,
    ("POST", "/sites/{site_id}/members/{member_id}/recovery/"): 200,
    # member notices (the Subscriber's announcements and promos for their members)
    ("GET", "/sites/{site_id}/member-notices/"): 200,
    ("POST", "/sites/{site_id}/member-notices/"): 201,
    ("PATCH", "/sites/{site_id}/member-notices/{notice_id}/"): 200,
    ("POST", "/sites/{site_id}/member-notices/{notice_id}/image/"): 200,
    ("DELETE", "/sites/{site_id}/member-notices/{notice_id}/image/"): 200,
    ("DELETE", "/sites/{site_id}/member-notices/{notice_id}/"): 200,
    # training (the Subscriber's courses, modules, lessons and live trainings for their members)
    ("GET", "/sites/{site_id}/training/courses/"): 200,
    ("POST", "/sites/{site_id}/training/courses/"): 201,
    ("POST", "/sites/{site_id}/training/courses/reorder/"): 200,
    ("GET", "/sites/{site_id}/training/courses/{course_id}/"): 200,
    ("PATCH", "/sites/{site_id}/training/courses/{course_id}/"): 200,
    ("POST", "/sites/{site_id}/training/courses/{course_id}/image/"): 200,
    ("DELETE", "/sites/{site_id}/training/courses/{course_id}/image/"): 200,
    ("POST", "/sites/{site_id}/training/courses/{course_id}/modules/"): 201,
    ("POST", "/sites/{site_id}/training/courses/{course_id}/modules/reorder/"): 200,
    ("PATCH", "/sites/{site_id}/training/modules/{module_id}/"): 200,
    ("POST", "/sites/{site_id}/training/modules/{module_id}/lessons/"): 201,
    ("POST", "/sites/{site_id}/training/modules/{module_id}/lessons/reorder/"): 200,
    ("PATCH", "/sites/{site_id}/training/lessons/{lesson_id}/"): 200,
    ("POST", "/sites/{site_id}/training/lessons/{lesson_id}/image/"): 200,
    ("DELETE", "/sites/{site_id}/training/lessons/{lesson_id}/image/"): 200,
    ("DELETE", "/sites/{site_id}/training/lessons/{lesson_id}/"): 200,
    ("DELETE", "/sites/{site_id}/training/modules/{module_id}/"): 200,
    ("DELETE", "/sites/{site_id}/training/courses/{course_id}/"): 200,
    ("GET", "/sites/{site_id}/training/live/"): 200,
    ("POST", "/sites/{site_id}/training/live/"): 201,
    ("PATCH", "/sites/{site_id}/training/live/{live_id}/"): 200,
    ("POST", "/sites/{site_id}/training/live/{live_id}/thumbnail/"): 200,
    ("DELETE", "/sites/{site_id}/training/live/{live_id}/thumbnail/"): 200,
    ("DELETE", "/sites/{site_id}/training/live/{live_id}/"): 200,
    # media library (the Subscriber's promotion images and videos for their members)
    ("GET", "/sites/{site_id}/media/"): 200,
    ("POST", "/sites/{site_id}/media/"): 201,
    ("POST", "/sites/{site_id}/media/reorder/"): 200,
    ("PATCH", "/sites/{site_id}/media/{asset_id}/"): 200,
    ("POST", "/sites/{site_id}/media/{asset_id}/publish/"): 200,
    ("POST", "/sites/{site_id}/media/{asset_id}/unpublish/"): 200,
    ("POST", "/sites/{site_id}/media/{asset_id}/image/"): 200,
    ("DELETE", "/sites/{site_id}/media/{asset_id}/image/"): 200,
    ("POST", "/sites/{site_id}/media/{asset_id}/video/token/"): 200,
    ("POST", "/sites/{site_id}/media/{asset_id}/video/attach/"): 200,
    ("DELETE", "/sites/{site_id}/media/{asset_id}/"): 200,
    # billing
    ("GET", "/sites/{site_id}/billing/"): 200,
    ("GET", "/sites/{site_id}/billing/payment/"): 200,
    ("GET", "/sites/{site_id}/billing/usage/"): 200,  # the usage-on world; UsageSwitchTests covers off
    ("GET", "/sites/{site_id}/billing/profile/"): 200,
    ("PUT", "/sites/{site_id}/billing/profile/"): 200,
    ("GET", "/sites/{site_id}/billing/statements/"): 200,
    ("GET", "/sites/{site_id}/billing/statements/{number}/"): 200,
    ("GET", "/sites/{site_id}/billing/statements/{number}/pdf/"): 200,
    ("GET", "/sites/{site_id}/billing/invoices/"): 200,
    ("GET", "/sites/{site_id}/billing/invoices/{invoice_id}/copy/"): 200,
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
    ("POST", "/sites/{site_id}/catalog/products/order/"): 200,
    ("PATCH", "/sites/{site_id}/catalog/products/{product_id}/"): 200,
    ("GET", "/sites/{site_id}/catalog/products/{product_id}/revisions/"): 200,
    ("POST", "/sites/{site_id}/catalog/products/{product_id}/image/"): 200,
    ("DELETE", "/sites/{site_id}/catalog/products/{product_id}/image/"): 200,
    ("GET", "/sites/{site_id}/catalog/categories/{category}/faq/"): 200,
    ("PUT", "/sites/{site_id}/catalog/categories/{category}/faq/"): 200,
    ("POST", "/sites/{site_id}/catalog/shipping-preview/"): 200,
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
    ("POST", "/sites/{site_id}/pack/orders/{order_id}/shipment/"): 200,
    ("POST", "/sites/{site_id}/pack/orders/{order_id}/shipment/correct/"): 200,
    ("POST", "/sites/{site_id}/pack/orders/{order_id}/delivery/"): 200,
    ("GET", "/sites/{site_id}/pack/registrations/"): 200,
    ("POST", "/sites/{site_id}/pack/registrations/{member_id}/start/"): 200,
    ("POST", "/sites/{site_id}/pack/registrations/{member_id}/registered/"): 200,
    # shop owner (the Subscriber's product orders; each call gets a fresh order in the state it needs)
    ("GET", "/sites/{site_id}/shop/orders/"): 200,
    ("GET", "/sites/{site_id}/shop/orders/{order_id}/"): 200,
    ("GET", "/sites/{site_id}/shop/orders/{order_id}/proofs/{proof_id}/"): 200,
    ("POST", "/sites/{site_id}/shop/orders/{order_id}/confirm-receipt/"): 200,
    ("POST", "/sites/{site_id}/shop/orders/{order_id}/cancel/"): 200,
    ("POST", "/sites/{site_id}/shop/orders/{order_id}/ship/"): 200,
    ("POST", "/sites/{site_id}/shop/orders/{order_id}/deliver/"): 200,
    ("GET", "/sites/{site_id}/shop/reviews/"): 200,
    ("POST", "/sites/{site_id}/shop/reviews/{review_id}/hide/"): 200,
    ("POST", "/sites/{site_id}/shop/reviews/{review_id}/show/"): 200,
    # meta_ads
    ("GET", "/sites/{site_id}/meta/"): 200,
    ("PUT", "/sites/{site_id}/meta/"): 200,
    ("POST", "/sites/{site_id}/meta/test/"): 200,
    ("DELETE", "/sites/{site_id}/meta/token/"): 200,
    # inquiries (the order matters: the connection is saved and tested, read, then disconnected)
    ("GET", "/sites/{site_id}/inquiries/connection/"): 200,
    ("PUT", "/sites/{site_id}/inquiries/connection/"): 200,
    ("POST", "/sites/{site_id}/inquiries/connection/test/"): 200,
    ("GET", "/sites/{site_id}/inquiries/conversations/"): 200,
    ("GET", "/sites/{site_id}/inquiries/conversations/{ref}/"): 200,
    ("POST", "/sites/{site_id}/inquiries/conversations/{ref}/reply/"): 200,
    ("DELETE", "/sites/{site_id}/inquiries/connection/"): 200,
    # last: a cancelled trial is Billing-only, so every route called after it would answer 403
    ("POST", "/sites/{site_id}/billing/cancel/"): 200,
}
APP_PREFIXES = [
    ("/sites/{site_id}/billing/", "billing"), ("/sites/{site_id}/sante/", "sante"),
    ("/sites/{site_id}/catalog/", "catalog"), ("/sites/{site_id}/payment-methods/", "payments"),
    ("/sites/{site_id}/pack/", "pack_owner"), ("/sites/{site_id}/meta/", "meta_ads"),
    ("/sites/{site_id}/inquiries/", "inquiries"),
    ("/sites/{site_id}/shop/", "shop_owner"),
    ("/sites/{site_id}/member-notices/", "member_notices"),
    ("/sites/{site_id}/training/", "training"),
    ("/sites/{site_id}/media/", "media"),
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
    BILLING_USAGE_ENABLED=True, SHOP_ENABLED=True, BILLING_INVOICE_REGISTER_ENABLED=True,
    MEMBERS_ENABLED=True, PACK_SHIPPING_ENABLED=True, BILLING_STATEMENTS_ENABLED=True,
    META_CREDENTIALS_KEY=KEY, INQUIRIES_ENABLED=True, INQUIRIES_CREDENTIALS_KEY=inquiries_base.KEY,
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
        self.media_store = FakeStore().__enter__()  # the media library's own store calls
        self.addCleanup(self.media_store.__exit__, None, None, None)
        self.meta = FakeMeta().__enter__()
        self.addCleanup(self.meta.__exit__, None, None, None)
        self.pancake = inquiries_base.FakePancake().__enter__()
        self.addCleanup(self.pancake.__exit__, None, None, None)
        inquiries_base.no_limiter(self)
        usage_on = mock.patch.object(plan, "STORAGE_METERED", True)  # usage is shown only when metered
        usage_on.start()
        self.addCleanup(usage_on.stop)
        self.scan_store = MemoryScanStore()
        for name in ("save", "read", "discard"):
            patcher = mock.patch(f"apps.invoices.scans.{name}", side_effect=getattr(self.scan_store, name))
            patcher.start()
            self.addCleanup(patcher.stop)
        real_read = private_files.read
        stored_pdf = mock.patch.object(
            private_files, "read",  # every statement here holds these bytes; any other key is read as before
            side_effect=lambda key: PDF_BYTES if key.startswith("statements/pdf/") else real_read(key),
        )
        stored_pdf.start()
        self.addCleanup(stored_pdf.stop)
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.ben = self.other_client()
        self.ben_site_id = self.create_site(
            client=self.ben, subdomain="bens-store", display_name="Ben's"
        )["id"]
        self.ben_site = Site.objects.get(public_id=self.ben_site_id)
        designate(self.site)  # Ana's site owns the one shared catalog
        self.invoice_ids = {
            site.pk: self.make_invoice_copy(site) for site in (self.site, self.ben_site)
        }
        # one issued statement per site (and Ben's profile, so a leaked profile would show)
        self.statement_numbers = {
            site.pk: issue_for(site, n).number for n, site in enumerate((self.site, self.ben_site), start=1)
        }
        BillingProfile.objects.create(site=self.ben_site, **PROFILE)
        self.product = make_product()
        self.creates = 0
        self.ben_ids = self.make_children(self.ben_site, payment=True, reg_status="in_registration")
        self.ben_shop_ids = self.make_children(self.ben_site, shop=True)
        configure(self.ben_site)
        inquiries_base.configure(self.site)
        inquiries_base.configure(self.ben_site, page=BEN_PAGE)
        for ids in (self.ben_ids, self.ben_shop_ids):
            ids["ref"] = BEN_REF
        mail.outbox.clear()
        self.meta.requests.clear()
        self.pancake.requests.clear()

    # children ---------------------------------------------------------------
    def make_invoice_copy(self, site):
        """A paid sale of this site with a verified invoice scan: the id of its register item. The item is
        written directly, so the subscription itself is not touched."""
        item = make_item(Subscription.objects.get(site=site), site_public_id=site.public_id)
        staff = make_staff("finance")
        book = make_book(series=f"S{site.pk}")
        invoice = invoice_services.record_invoice(staff, {
            "item": item.public_id, "series": book.series, "serial_from": book.serial_from,
            "serial_number": 1, "issued_on": item.sale_date.isoformat(), "sale_type": "cash",
            "buyer_name": "Fixture Buyer", "description": "Paper",
            "service_start": "2026-12-01", "service_end": "2026-12-31",
            "total_sales_centavos": item.total_centavos,
        })
        scan = invoice_services.upload_scan(staff, invoice.public_id, JPEG + b"scan" + bytes([site.pk % 250]))
        invoice_services.verify_scan(staff, scan.public_id)
        return item.public_id

    def make_training(self, site):
        """A course with a module and a lesson, and a live training, of this site."""
        self.creates += 1
        course = Course.objects.create(site=site, title="Fixture course", slug=f"fixture-{self.creates}")
        module = Module.objects.create(course=course, title="Fixture module")
        lesson = Lesson.objects.create(module=module, title="Fixture lesson")
        live = LiveTraining.objects.create(site=site, title="Fixture live", starts_at=timezone.now())
        return {
            "course_id": course.public_id, "module_id": module.public_id,
            "lesson_id": lesson.public_id, "live_id": live.public_id,
        }

    def make_media(self, site, video=False):
        """A media asset of this site: a published image with its file, or (video=True) a draft
        video with its poster and no video file yet."""
        self.creates += 1
        if video:
            asset = MediaAsset.objects.create(
                site=site, title="Fixture video", asset_type="video",
                poster_key=f"media-library/{site.public_id}/poster-{self.creates}.png",
            )
        else:
            asset = MediaAsset.objects.create(
                site=site, title="Fixture image", asset_type="image", published_at=timezone.now(),
                file_key=f"media-library/{site.public_id}/image-{self.creates}.png",
            )
        return {"asset_id": asset.public_id}

    def make_children(self, site, payment=False, shop=False, shop_status=SHOP.PENDING_REVIEW,
                      shop_shipped=False, pack_status=PACK.PENDING_REVIEW, pack_shipped=False, reg_status=None):
        """A shop order has its own ids: with shop=True order_id and proof_id name it, not the pack's."""
        review_id = MISSING_CHILD["review_id"]
        if shop:
            order, proof = make_shop_order(site, status=shop_status, shipped=shop_shipped)
            review_id = ProductReview.objects.create(
                site=site, order=order, order_line=order.lines.get(), product_id="prod_x", rating=5,
                body="Fixture review", display_name="Maria S.",
            ).public_id
        else:
            order, proof = make_order(site, status=pack_status, shipped=pack_shipped)
            if reg_status:  # the member's Santé registration, for the pack registration routes
                SanteRegistration.objects.create(
                    site=site, member=order.member, pack_order=order, status=reg_status,
                )
        ids = {
            **MISSING_CHILD, "category": "barley",
            "order_id": order.public_id,
            "proof_id": proof.public_id,
            "review_id": review_id,
            "account_id": SitePaymentAccount.objects.filter(site=site).order_by("-pk").first().public_id,
            "product_id": self.product.public_id,
            "member_id": order.member.public_id if not shop else MISSING_CHILD["member_id"],
            "notice_id": MemberNotice.objects.create(site=site, kind="promo", title="Fixture").public_id,
            **self.make_training(site),
            **self.make_media(site),
            "invoice_id": self.invoice_ids[site.pk],
            "number": self.statement_numbers[site.pk],
        }
        if payment:
            ids["payment_id"] = submit(
                Subscription.objects.get(site=site), timezone.now(), content=JPEG
            ).public_id
        return ids

    def own_ids(self, template):
        """Children of Ana's site for the placeholders this route has. An order is made anew each
        time because approve and reject use it up; a payment is whatever Ana last sent."""
        ids = {**MISSING_CHILD, "category": "barley", "product_id": self.product.public_id}
        if "/shop/orders/{order_id}" in template:
            ids.update(self.make_children(
                self.site, shop=True,
                shop_status=SHOP.VERIFIED if template.endswith(("/ship/", "/deliver/")) else SHOP.PENDING_REVIEW,
                shop_shipped=template.endswith("/deliver/"),
            ))
        elif "{review_id}" in template:
            ids["review_id"] = self.make_children(self.site, shop=True)["review_id"]
        elif "{order_id}" in template:
            ids.update(self.make_children(
                self.site,
                pack_status=PACK.VERIFIED if template.endswith(("/shipment/", "/shipment/correct/", "/delivery/")) else PACK.PENDING_REVIEW,
                pack_shipped=template.endswith(("/shipment/correct/", "/delivery/")),
            ))
        elif "/pack/registrations/{member_id}" in template:
            ids.update(self.make_children(
                self.site, pack_status=PACK.VERIFIED,
                reg_status="ready_to_register" if template.endswith("/start/") else "in_registration",
            ))
        elif "{member_id}" in template:
            ids.update(self.make_children(self.site))
        elif "{account_id}" in template:
            ids["account_id"] = make_payment_account(self.site).public_id
        if "{notice_id}" in template:  # made anew: the delete uses it up
            ids["notice_id"] = MemberNotice.objects.create(site=self.site, kind="promo", title="Fixture").public_id
        if "/training/" in template:  # made anew: the deletes use them up
            ids.update(self.make_training(self.site))
        if "/media/" in template:  # made anew: the delete uses it up, and a video route needs a video
            ids.update(self.make_media(self.site, video="/video/" in template))
        if "{ref}" in template:
            ids["ref"] = inquiries_base.REF
        if "{invoice_id}" in template:
            ids["invoice_id"] = self.invoice_ids[self.site.pk]
        if "{number}" in template:
            ids["number"] = self.statement_numbers[self.site.pk]
        if "{payment_id}" in template:
            payment = BankTransferPayment.objects.filter(
                subscription__site=self.site
            ).order_by("-pk").first()
            if payment:
                ids["payment_id"] = payment.public_id
        return ids

    def foreign_ids(self, template):
        """Ben's children for the placeholders this route has (a shop route gets his shop order)."""
        return self.ben_shop_ids if "/shop/" in template else self.ben_ids

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
        if "/training/" in path and path.endswith(("/image/", "/thumbnail/")) and method == "POST":
            return client.post(path, data={"file": image_file()}, secure=True, HTTP_ORIGIN=ORIGIN)
        if "/training/" in path and path.endswith("/reorder/"):
            return self.send("post", path, {"ids": self.reorder_ids(path)}, client=client)
        if "/media/" in path and path.endswith("/image/") and method == "POST":
            return client.post(path, data={"file": image_file()}, secure=True, HTTP_ORIGIN=ORIGIN)
        if "/media/" in path and path.endswith("/video/token/"):
            with override_settings(**RW_SETTINGS):
                return self.send("post", path, None, client=client)
        if "/media/" in path and path.endswith("/video/attach/"):
            site_pid = path.split("/sites/")[1].split("/")[0]
            asset_pid = path.split("/media/")[1].split("/")[0]
            with override_settings(**RW_SETTINGS), mock.patch(HEAD, return_value=("video/mp4", 5 * 1024 * 1024)):
                return self.send("post", path, {"url": rw_blob_url(site_pid, asset_pid)}, client=client)
        if "/media/" in path and path.endswith("/reorder/"):
            site_pid = path.split("/sites/")[1].split("/")[0]
            ids = list(MediaAsset.objects.filter(site__public_id=site_pid).order_by("order", "pk").values_list("public_id", flat=True))
            return self.send("post", path, {"ids": ids}, client=client)
        if "/member-notices/" in path and path.endswith("/image/") and method == "POST":
            return client.post(path, data={"file": image_file()}, secure=True, HTTP_ORIGIN=ORIGIN)
        if "/recovery/" in path and method == "POST":
            return self.call_recovery(path, client)
        if method == "GET":
            return self.get(path, client=client)
        return self.send(method.lower(), path, self.body_for(method, template), client=client)

    def reorder_ids(self, path):
        """Every sibling the route lists, as stored (the routes refuse a list that misses one)."""
        parts = path.split("/training/")[1].strip("/").split("/")
        if parts[0] == "courses" and len(parts) == 2:
            rows = Course.objects.filter(site__public_id=path.split("/sites/")[1].split("/")[0])
        elif parts[0] == "courses":
            rows = Module.objects.filter(course__public_id=parts[1])
        else:
            rows = Lesson.objects.filter(module__public_id=parts[1])
        return list(rows.order_by("order", "pk").values_list("public_id", flat=True))

    def call_recovery(self, path, client):
        """The checklist of a member, answered as the Subscriber would, then the contact change
        that the passed check allows. A member that is not the caller's has no checklist: the
        change is tried with a check that does not exist."""
        member = Member.objects.filter(public_id=path.split("/members/")[1].split("/")[0]).first()
        answers = {k: "matched" for k in recovery.applicable_for(member)} if member else {}
        if path.endswith("/recovery/check/"):
            return self.send("post", path, {"answers": answers}, client=client)
        check = self.send("post", path + "check/", {"answers": answers}, client=client)
        check_id = check.json().get("check_id", "0") if check.status_code == 200 else "0"
        return self.send("post", path, {"check_id": check_id, "new_email": "new-contact@buyer.test"}, client=client)

    def body_for(self, method, template):
        if template.endswith("/business-profile/"):
            return PROFILE_BODY
        if template == "/sites/{site_id}/":
            return SITE_BODY
        if "/sante/" in template:
            return {"sponsor_id": "TAKEN-1"}
        if "/catalog/" in template:
            self.product.refresh_from_db()
            if "/categories/" in template:
                return {"faq": [{"q": "Is it good?", "a": "Yes."}]}
            if template.endswith("/products/order/"):
                return {"product_ids": list(
                    CatalogProduct.objects.order_by("pk").values_list("public_id", flat=True)
                )}
            if template.endswith("/shipping-preview/"):
                return {"weight_kg": "1.00"}
            if method == "PATCH":
                return {"revision": self.product.revision, "name": self.product.name}
            self.creates += 1
            return {"code": f"FX-NEW-{self.creates}", "name": "Fixture New", "srp_centavos": 9999,
                    "earnings_rate": "0.3500"}
        if "/member-notices/" in template:
            if method == "POST":
                return {"kind": "announcement", "title": "Fixture notice"}
            return {"title": "Changed"} if method == "PATCH" else None
        if "/media/" in template:
            if method == "POST" and template.endswith("/media/"):
                return {"title": "Fixture media", "asset_type": "image"}
            if template.endswith("/publish/"):
                return {}
            return {"title": "Changed"} if method == "PATCH" else None
        if "/training/" in template:
            if template.endswith("/live/"):
                return {"title": "Fixture live", "starts_at": "2026-12-01T10:00:00+08:00"}
            if method == "POST":
                return {"title": "Fixture"}
            return {"title": "Changed"} if method == "PATCH" else None
        if template.endswith("/billing/profile/"):
            return {**PROFILE, "company_name": "Fixture Co"}
        if template.endswith("/payment-methods/"):
            return {"direct_enabled": True}
        if "/shop/" in template:
            body = {"state_version": 1}
            if template.endswith("/cancel/"):
                body["note"] = "Out of stock"
            if template.endswith("/ship/"):
                body["courier"] = "J&T"
            return body
        if "/pack/" in template:
            body = {"state_version": 1}
            if template.endswith(("/shipment/", "/shipment/correct/")):
                body.update(courier="lbc", tracking_number="LBC123456")
            if template.endswith("/reject/"):
                body["reason_code"] = "proof_unreadable"
            if template.endswith("/registered/"):
                body["sante_member_id"] = "SA-1234567"
            return body
        if "/meta/" in template and method == "PUT":
            return {"pixel_id": PIXEL, "access_token": TOKEN, "test_event_code": CODE}
        if "/inquiries/" in template and method == "PUT":
            return {"page_id": inquiries_base.PAGE, "access_token": inquiries_base.TOKEN}
        if template.endswith("/reply/"):
            self.creates += 1
            return {"text": "Fixture reply", "idempotency_key": f"fixture-key-{self.creates:06d}"}
        return None

    # what a refused request must not touch ---------------------------------
    def state(self):
        return {
            "rows": row_snapshot(),
            "private_files": stored_files(self.private_dir),
            "billing_proof_files": sorted(self.proof_store.files),
            "public_media": (sorted(self.media.files), list(self.media.deleted)),
            "media_library_store": (sorted(self.media_store.files), list(self.media_store.deleted)),
            "outbox": [(m.to, m.subject) for m in mail.outbox],
            "meta_requests": len(self.meta.requests),
            "inquiry_requests": len(self.pancake.requests),
        }

    def assertUnchanged(self, before, message):
        after = self.state()
        for part in before:
            self.assertEqual(after[part], before[part], f"{message}: {part} changed")
