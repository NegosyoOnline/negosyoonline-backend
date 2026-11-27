"""Learned ZIP codes (z8uq9mkwcj, ADDRESS-ZIP-POPUP-LEARN): lookup order, learning on the three Santé address
saves, first-wins with a conflict count, and a save that never fails because of the learning."""
from unittest import mock

from django.db import transaction

from apps.pack import address_zip
from apps.pack.models import LearnedZipCode
from apps.pack.tests.test_onboarding import ADDRESS, OnboardingCase
from apps.shop.tests.base import HOST as SHOP_HOST, ShopTestCase
from apps.shop.tests.test_address_edit import AddressEditTestCase

UNKNOWN = ("Camarines Sur", "Nowhere City", "Zzz")   # the dataset has nothing for it
APPROX = ("Metro Manila", "Caloocan", "Zzz")          # only the city's main ZIP, 1400
UNIQUE = ("Camarines Sur", "Naga City", "Triangulo")  # a city with one ZIP, 4400


def place(zip_code="4999", via=UNKNOWN, **extra):
    p, c, b = via
    return {"line": "1 Mabini St", "barangay": b, "city": c, "province": p, "zip": zip_code, "landmark": "", **extra}


class LookupTests(OnboardingCase):
    def test_lookup_order_barangay_learned_city_unique_city_approx_nothing(self):
        d = address_zip.derive_address_fields(*UNKNOWN)
        self.assertEqual((d.ok, d.error_field), (False, "postal_code"))
        d = address_zip.derive_address_fields(*APPROX)
        self.assertEqual((d.zip_code, d.zip_precision, d.should_learn), ("1400", "city_approx", False))
        d = address_zip.derive_address_fields(*UNIQUE)
        self.assertEqual((d.zip_code, d.zip_precision), ("4400", "city_unique"))
        # learned beats the city's ZIP; the exact barangay data beats learned
        address_zip.record_learned_zip(*UNIQUE, "4401", "member")
        d = address_zip.derive_address_fields(*UNIQUE)
        self.assertEqual((d.zip_code, d.zip_precision, d.zip_source), ("4401", "learned", "learned"))
        address_zip.record_learned_zip(*APPROX, "1401", "member")
        self.assertEqual(address_zip.derive_address_fields(*APPROX).zip_precision, "learned")
        barangay = next(iter(address_zip._dataset()["barangays"])).split("::")
        address_zip.record_learned_zip(*barangay, "9999", "member")
        d = address_zip.derive_address_fields(*barangay)
        self.assertEqual(d.zip_precision, "barangay")
        self.assertNotEqual(d.zip_code, "9999")

    def test_a_learned_hit_is_authoritative_and_the_endpoint_says_learned(self):
        address_zip.record_learned_zip(*UNKNOWN, "4999", "member")
        body = self.call("address/zip/", province=UNKNOWN[0], city=UNKNOWN[1], barangay=UNKNOWN[2],
                         postal_code="1111").json()
        self.assertEqual((body["ok"], body["zip_code"], body["zip_precision"]), (True, "4999", "learned"))
        self.assertIn("learned", address_zip.AUTHORITATIVE)

    def test_spelling_variants_hit_the_same_row(self):
        address_zip.record_learned_zip("Camarines Sur", "Nowhere City", "Zzz", "4999", "member")
        d = address_zip.derive_address_fields("CAMARINES  SUR", "nowhere-city", "zzz")
        self.assertEqual((d.zip_code, d.zip_precision), ("4999", "learned"))

    def test_a_database_failure_in_the_lookup_is_a_miss(self):
        with mock.patch("apps.pack.models.LearnedZipCode.objects") as objects:
            objects.filter.side_effect = RuntimeError("down")
            self.assertIsNone(address_zip.learned_zip(*UNKNOWN))


class RecordTests(OnboardingCase):
    def test_first_value_wins_and_a_conflict_is_only_counted(self):
        address_zip.record_learned_zip(*UNKNOWN, "4999", "onboarding")
        address_zip.record_learned_zip(*UNKNOWN, "4999", "member")
        row = LearnedZipCode.objects.get()
        self.assertEqual((row.zip_code, row.times_seen, row.conflict_count, row.conflict_zip), ("4999", 2, 0, ""))
        address_zip.record_learned_zip(*UNKNOWN, "4888", "checkout")
        address_zip.record_learned_zip(*UNKNOWN, "4777", "checkout")
        row = LearnedZipCode.objects.get()
        self.assertEqual((row.zip_code, row.times_seen, row.conflict_count, row.conflict_zip),
                         ("4999", 2, 2, "4777"))
        self.assertEqual(row.source, "onboarding")
        self.assertEqual(address_zip.learned_zip(*UNKNOWN), "4999")  # still served

    def test_an_invalid_zip_is_never_stored(self):
        for bad in ("", "123", "12345", "abcd", None):
            self.assertIsNone(address_zip.record_learned_zip(*UNKNOWN, bad, "member"))
        self.assertEqual(LearnedZipCode.objects.count(), 0)

    def test_the_row_holds_no_personal_data(self):
        names = {f.name for f in LearnedZipCode._meta.get_fields()}
        self.assertEqual(names, {"id", "lookup_key", "province", "city", "barangay", "zip_code", "times_seen",
                                 "conflict_zip", "conflict_count", "source", "created", "updated"})

    def test_a_failure_inside_learning_never_raises_nor_breaks_the_outer_transaction(self):
        with transaction.atomic():
            with mock.patch.object(address_zip, "record_learned_zip", side_effect=RuntimeError("boom")):
                address_zip.learn_saved_address(place(), "member")
            LearnedZipCode.objects.create(lookup_key="k", province="P", city="C", zip_code="1234", source="member")
        self.assertEqual(LearnedZipCode.objects.count(), 1)

    def test_what_is_not_worth_learning(self):
        for address in (
            place(via=UNIQUE, zip_code="4401"),   # authoritative city answer wins; typed is stale
            place(via=APPROX, zip_code="1400"),   # the accepted approximate prefill
            place(zip_code="12"),                 # invalid
            place(zip_code=""),                   # nothing typed
            place(barangay=""),                   # no barangay: no place key
            "not a dict",
        ):
            address_zip.learn_saved_address(address, "member")
        self.assertEqual(LearnedZipCode.objects.count(), 0)

    def test_a_typed_zip_that_differs_from_the_approx_suggestion_is_learned(self):
        address_zip.learn_saved_address(place(via=APPROX, zip_code="1401"), "checkout")
        row = LearnedZipCode.objects.get()
        self.assertEqual((row.zip_code, row.source, row.barangay), ("1401", "checkout", "ZZZ"))


class OnboardingLearnTests(OnboardingCase):
    def _address(self, token, **changes):
        return self.call("onboarding/address/", token=token,
                         **{**ADDRESS, "city": "Nowhere City", "barangay": "Zzz", **changes})

    def test_a_saved_delivery_address_teaches_the_zip_and_the_next_buyer_is_not_asked(self):
        token = self.placed_token()
        self.verify_both(token)
        self.assertEqual(self._address(token, postal_code="4999").status_code, 200)
        row = LearnedZipCode.objects.get()
        self.assertEqual((row.zip_code, row.source), ("4999", "onboarding"))
        body = self.call("address/zip/", province="Camarines Sur", city="Nowhere City", barangay="Zzz").json()
        self.assertEqual((body["zip_code"], body["zip_precision"]), ("4999", "learned"))

    def test_a_failed_save_learns_nothing(self):
        token = self.placed_token()
        self.verify_both(token)
        response = self._address(token, postal_code="4999", receiver_name="")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(LearnedZipCode.objects.count(), 0)

    def test_the_save_stands_when_learning_fails(self):
        token = self.placed_token()
        self.verify_both(token)
        with mock.patch.object(address_zip, "record_learned_zip", side_effect=RuntimeError("boom")):
            self.assertEqual(self._address(token, postal_code="4999").status_code, 200)
        self.assertEqual(LearnedZipCode.objects.count(), 0)

    def test_a_dataset_answer_learns_nothing(self):
        token = self.placed_token()
        self.verify_both(token)
        self.assertEqual(self.call("onboarding/address/", token=token, postal_code="4999", **ADDRESS).status_code, 200)
        self.assertEqual(LearnedZipCode.objects.count(), 0)


class CheckoutLearnTests(ShopTestCase):
    def test_a_placed_order_teaches_the_zip(self):
        self.placed(address=place())
        row = LearnedZipCode.objects.get()
        self.assertEqual((row.zip_code, row.source), ("4999", "checkout"))

    def test_a_refused_order_learns_nothing(self):
        response = self.place(self.cart(), address=place(), reference_number="")
        self.assertGreaterEqual(response.status_code, 400)
        self.assertEqual(LearnedZipCode.objects.count(), 0)

    def test_the_order_stands_when_learning_fails(self):
        with mock.patch.object(address_zip, "record_learned_zip", side_effect=RuntimeError("boom")):
            self.placed(address=place())
        self.assertEqual(LearnedZipCode.objects.count(), 0)


class MemberLearnTests(AddressEditTestCase):
    def test_a_saved_home_address_teaches_the_zip(self):
        response = self.save(**place())
        self.assertEqual(response.status_code, 200, response.content)
        row = LearnedZipCode.objects.get()
        self.assertEqual((row.zip_code, row.source), ("4999", "member"))

    def test_a_refused_save_learns_nothing(self):
        self.assertEqual(self.save(**place(line="")).status_code, 422)
        self.assertEqual(LearnedZipCode.objects.count(), 0)

    def test_the_save_stands_when_learning_fails(self):
        with mock.patch.object(address_zip, "record_learned_zip", side_effect=RuntimeError("boom")):
            self.assertEqual(self.save(**place()).status_code, 200)
        self.assertEqual(LearnedZipCode.objects.count(), 0)
