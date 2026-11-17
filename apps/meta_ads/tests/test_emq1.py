"""EMQ-1 (ClickUp z8uq9mg7gx): the richer Conversions API user_data. Meta is never called."""
from django.utils import timezone

from apps.meta_ads import events, services
from apps.meta_ads.models import MetaDelivery
from apps.meta_ads.tests.base import FakeMeta, configure, switched_on
from apps.meta_ads.tests.base import setUpModule, tearDownModule  # noqa: F401
from apps.meta_ads.tests.test_storefront import IC, PV, VC, MetaStorefrontTestCase
from apps.pack.models import PackOrder, PackVisit
from apps.pack.tests.base import PackTestCase

REF = "v" * 20
OTHER_REF = "w" * 20
CONSENT = {
    "meta_consent": "1", "fbp": "fb.1.1700000000000.123456789", "user_agent": "Mozilla/5.0 Buyer",
    "source_url": "https://example.test/funnel/membership-pack/checkout",
}


class ContactOnPageEventsTests(MetaStorefrontTestCase):
    def visit(self, site=None, ref=REF, **fields):
        return PackVisit.objects.create(site=site or self.site, visitor_ref=ref, **fields)

    def sent(self, *items, **fields):
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
            self.events(*items, **fields)
        return meta.events()

    def test_the_saved_checkout_contact_is_hashed_into_em_and_ph(self):
        self.visit(contact_email="Maria@Buyer.test", contact_mobile="+63 917 123 4567")
        for event in self.sent(("InitiateCheckout", IC), ("ViewContent", VC), visitor_ref=REF):
            user = event["user_data"]
            self.assertEqual(user["em"], [events.sha("maria@buyer.test")])
            self.assertEqual(user["ph"], [events.sha("639171234567")])
            self.assertEqual(user["country"], [events.sha("ph")])
            self.assertNotIn("fn", user)

    def test_a_visit_without_a_contact_or_no_ref_adds_nothing(self):
        self.visit()
        for n, fields in enumerate(({"visitor_ref": REF}, {})):
            user = self.sent(("InitiateCheckout", f"ic_{n:016x}"), **fields)[0]["user_data"]
            self.assertEqual({"em", "ph", "country"} & set(user), set())

    def test_only_one_contact_field_is_hashed_when_the_other_is_blank(self):
        self.visit(contact_email="a@b.co")
        user = self.sent(("InitiateCheckout", IC), visitor_ref=REF)[0]["user_data"]
        self.assertEqual((user["em"], "ph" in user), ([events.sha("a@b.co")], False))

    def test_a_ref_of_another_site_a_bad_ref_and_an_unknown_ref_are_ignored(self):
        self.visit(site=self.other_site, ref=OTHER_REF, contact_email="other@buyer.test")
        for n, ref in enumerate((OTHER_REF, "short", "x" * 33, "bad ref!" * 3, REF, 123, None)):
            with self.subTest(ref=ref):
                with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
                    response = self.events(("ViewContent", f"vc_{n:016x}"), visitor_ref=ref)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual({"em", "ph", "country"} & set(meta.events()[0]["user_data"]), set())

    def test_without_consent_nothing_is_queued_even_with_a_known_visit(self):
        self.visit(contact_email="a@b.co")
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
            self.events(("ViewContent", VC), visitor_ref=REF, consent=False)
        self.assertEqual((meta.requests, MetaDelivery.objects.count()), ([], 0))

    def test_with_the_switch_off_the_route_is_unchanged(self):
        self.visit(contact_email="a@b.co")
        with self.settings(META_EVENTS_ENABLED=False):
            self.assertEqual(self.events(("ViewContent", VC), visitor_ref=REF).status_code, 404)


class ExternalIdTests(MetaStorefrontTestCase):
    def sent(self, *items, **fields):
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
            self.events(*items, **fields)
        return meta.events()

    def test_vectors(self):
        self.assertEqual(events.hashed_external_id("v" * 20), events.sha("v" * 20))
        self.assertEqual(events.hashed_external_id(""), "")

    def test_every_page_event_carries_the_hashed_ref_of_an_existing_visit(self):
        PackVisit.objects.create(site=self.site, visitor_ref=REF)
        for event in self.sent(("PageView", PV), ("ViewContent", VC), ("InitiateCheckout", IC), visitor_ref=REF):
            self.assertEqual(event["user_data"]["external_id"], [events.sha(REF)])
            self.assertNotIn(REF, str(event))

    def test_a_foreign_bad_unknown_or_missing_ref_gives_no_external_id(self):
        PackVisit.objects.create(site=self.other_site, visitor_ref=OTHER_REF)
        for n, ref in enumerate((OTHER_REF, "short", "x" * 33, REF, 5, None)):
            with self.subTest(ref=ref):
                event = self.sent(("ViewContent", f"vc_{n:016x}"), visitor_ref=ref)[0]
                self.assertNotIn("external_id", event["user_data"])
        self.assertNotIn("external_id", self.sent(("ViewContent", VC))[0]["user_data"])

    def test_without_consent_nothing_is_queued(self):
        PackVisit.objects.create(site=self.site, visitor_ref=REF)
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
            self.events(("ViewContent", VC), visitor_ref=REF, consent=False)
        self.assertEqual((meta.requests, MetaDelivery.objects.count()), ([], 0))


@switched_on
class OrderExternalIdTests(PackTestCase):
    def setUp(self):
        super().setUp()
        configure(self.site)

    def place_with_consent(self, **fields):
        with FakeMeta(200) as meta:
            self.placed(**CONSENT, **fields)
        return meta

    def test_add_payment_info_and_purchase_carry_the_ref_of_the_visit_linked_to_the_order(self):
        PackVisit.objects.create(site=self.site, visitor_ref=REF)
        meta = self.place_with_consent(visitor_ref=REF)
        order = PackOrder.objects.get()
        self.assertEqual(PackVisit.objects.get().order_id, order.pk)
        self.assertEqual(meta.events()[0]["user_data"]["external_id"], [events.sha(REF)])
        # Purchase is rebuilt from rows when it is sent
        delivery = MetaDelivery.objects.create(site=self.site, event_name="Purchase", event_id=f"pur_{order.public_id}")
        with FakeMeta(200) as later, self.captureOnCommitCallbacks(execute=True):
            services.deliver(delivery.pk)
        self.assertEqual(later.events()[0]["user_data"]["external_id"], [events.sha(REF)])

    def test_no_visit_no_external_id_and_none_is_invented(self):
        meta = self.place_with_consent()
        self.assertNotIn("external_id", meta.events()[0]["user_data"])

    def test_a_ref_of_another_site_links_nothing_and_sends_nothing(self):
        PackVisit.objects.create(site=self.other_site, visitor_ref=OTHER_REF)
        meta = self.place_with_consent(visitor_ref=OTHER_REF)
        self.assertNotIn("external_id", meta.events()[0]["user_data"])
        self.assertIsNone(PackVisit.objects.get().order_id)

    def test_without_consent_nothing_is_sent(self):
        PackVisit.objects.create(site=self.site, visitor_ref=REF)
        with FakeMeta(200) as meta:
            self.placed(visitor_ref=REF)
        self.assertEqual((meta.requests, MetaDelivery.objects.count()), ([], 0))


class NormalisationTests(MetaStorefrontTestCase):
    def test_names_places_and_zip_vectors(self):
        sha = events.sha
        self.assertEqual(events.split_name("Ma. Niña dela Cruz"), ("Ma.", "Niña dela Cruz"))
        self.assertEqual(events.hashed_letters("Ma."), sha("ma"))
        self.assertEqual(events.hashed_letters(" Niña  dela-Cruz "), sha("niñadelacruz"))
        self.assertEqual(events.hashed_letters("NIÑA"), sha("niña"))
        self.assertEqual(events.hashed_letters("Niña"), sha("niña"))  # decomposed ñ
        self.assertEqual(events.hashed_letters("Metro Manila"), sha("metromanila"))
        self.assertEqual(events.hashed_letters("Davao del Sur"), sha("davaodelsur"))
        self.assertEqual(events.hashed_letters("Quezon City 1"), sha("quezoncity"))
        self.assertEqual(events.hashed_zip(" 1100 "), sha("1100"))
        self.assertEqual(events.hashed_zip("ZIP 1-100"), sha("1100"))
        for blank in ("", "   ", "123", "-.", None):
            self.assertEqual(events.hashed_letters(blank), "")
        self.assertEqual(events.hashed_zip("abc"), "")
        self.assertEqual(events.split_name("Maria"), ("Maria", ""))
        self.assertEqual(events.split_name("  "), ("", ""))

    def test_blank_fields_are_left_out_and_one_word_names_have_no_ln(self):
        user = events.user_data(ip="1.2.3.4", full_name="Maria", city="", province=" ", zip_code="")
        self.assertEqual(set(user) & {"fn", "ln", "ct", "st", "zp"}, {"fn"})
        self.assertEqual(user["fn"], [events.sha("maria")])

    def test_page_events_never_carry_name_or_address(self):
        user = events.page_event(
            "InitiateCheckout", "ic_" + "a" * 16, now=timezone.now(), url="", ip="1.2.3.4", user_agent="UA", fbp="", fbc="",
        )["user_data"]
        self.assertEqual(set(user) & {"fn", "ln", "ct", "st", "zp"}, set())


@switched_on
class OrderAddressTests(PackTestCase):
    ADDRESS = {"line": "1 Rizal St", "barangay": "Poblacion", "city": "Quezon City", "province": "Metro Manila", "zip": "1100"}
    KEYS = {"fn", "ln", "ct", "st", "zp"}

    def setUp(self):
        super().setUp()
        configure(self.site)
        with FakeMeta(200):
            self.placed(**CONSENT)
        self.order = PackOrder.objects.get()

    def user_data(self, event_name, *, address, name="Ma. Niña dela Cruz"):
        """The user_data of the event as it is sent: rebuilt from the order row."""
        PackOrder.objects.filter(pk=self.order.pk).update(buyer_name=name, delivery_address=address)
        MetaDelivery.objects.filter(event_name=event_name).delete()
        delivery = MetaDelivery.objects.create(
            site=self.site, event_name=event_name, event_id=f"{events.ORDER_EVENTS[event_name]}_{self.order.public_id}",
        )
        with FakeMeta(200) as meta, self.captureOnCommitCallbacks(execute=True):
            services.deliver(delivery.pk)
        return meta.events()[0]["user_data"]

    def test_add_payment_info_and_purchase_carry_the_hashed_name_and_address(self):
        sha = events.sha
        for name in ("AddPaymentInfo", "Purchase"):
            with self.subTest(event=name):
                user = self.user_data(name, address=self.ADDRESS)
                self.assertEqual(user["fn"], [sha("ma")])
                self.assertEqual(user["ln"], [sha("niñadelacruz")])
                self.assertEqual(user["ct"], [sha("quezoncity")])
                self.assertEqual(user["st"], [sha("metromanila")])
                self.assertEqual(user["zp"], [sha("1100")])
                self.assertNotIn("Rizal", str(user))

    def test_a_blank_or_missing_address_part_is_skipped(self):
        user = self.user_data("AddPaymentInfo", address={"city": "Cebu City", "province": "", "zip": "  "})
        self.assertEqual(set(user) & self.KEYS, {"fn", "ln", "ct"})
        user = self.user_data("AddPaymentInfo", address={}, name="Maria")
        self.assertEqual(set(user) & self.KEYS, {"fn"})
        user = self.user_data("AddPaymentInfo", address={}, name="")
        self.assertEqual(set(user) & self.KEYS, set())
