"""EMQ-1 (ClickUp z8uq9mg7gx): the richer Conversions API user_data. Meta is never called."""
from apps.meta_ads import events
from apps.meta_ads.models import MetaDelivery
from apps.meta_ads.tests.base import FakeMeta
from apps.meta_ads.tests.base import setUpModule, tearDownModule  # noqa: F401
from apps.meta_ads.tests.test_storefront import IC, VC, MetaStorefrontTestCase
from apps.pack.models import PackVisit

REF = "v" * 20
OTHER_REF = "w" * 20


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
