"""A site put on hold with manage.py site_hold refuses a Pack order; another site does not."""
from io import StringIO

from django.core.management import call_command

from apps.pack.models import PackOrder
from apps.pack.tests.base import NOT_FOUND, PackTestCase
from apps.sante.tests.agreement_helpers import make_operator


class PlacementUnderHoldTests(PackTestCase):
    def hold(self, site, flag="--on"):
        call_command("site_hold", "--site", site.public_id, flag, "--operator", "holder", stdout=StringIO())

    def setUp(self):
        super().setUp()
        make_operator("holder")

    def test_a_held_site_refuses_to_place_and_creates_nothing(self):
        raw = self.start()
        self.hold(self.site)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.place(raw)
        self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND))
        self.assertEqual(PackOrder.objects.count(), 0)
        self.assertEqual(self.proof_files(), [])

    def test_the_same_order_is_placed_once_the_hold_is_off(self):
        raw = self.start()
        self.hold(self.site)
        self.hold(self.site, "--off")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.place(raw)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(PackOrder.objects.count(), 1)

    def test_holding_one_site_does_not_stop_the_other(self):
        self.hold(self.site)
        from apps.pack.tests.base import OTHER_HOST
        response = self.post("contact/", host=OTHER_HOST, **self.contact_fields())
        self.assertEqual(response.status_code, 200, response.content)
        refused = self.contact()
        self.assertEqual((refused.status_code, refused.json()), (404, NOT_FOUND))
