"""manage.py site_hold: the setter for Site.operator_hold, and what a held site looks like to the readers."""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.billing.tests.base import make_subscription
from apps.operators.models import OperatorAuditEntry
from apps.sante.tests.agreement_helpers import make_operator
from apps.sites.models import Site
from apps.sites.tests.test_api_public import HOST, ByHostTestCase

OTHER = "bens-store.negosyotest.com"


class HoldCommandMixin:
    def hold(self, *args, **kw):
        out = StringIO()
        call_command("site_hold", *args, stdout=out, **kw)
        return out.getvalue().splitlines()

    def held(self, site):
        return Site.objects.get(pk=site.pk).operator_hold


class SiteHoldCommandTests(HoldCommandMixin, ByHostTestCase):
    def setUp(self):
        super().setUp()
        self.operator = make_operator("holder")
        self.a = make_subscription().site
        self.b = make_subscription(email="ben@example.test", name="bens-store").site

    def test_on_then_show_then_off(self):
        self.assertEqual(self.hold("--site", self.a.public_id, "--on", "--operator", "holder"),
                         [f"{self.a.public_id} on"])
        self.assertTrue(self.held(self.a))
        self.assertEqual(self.hold("--show", "--site", self.a.public_id), [f"{self.a.public_id} on"])
        self.assertEqual(self.hold("--site", self.a.public_id, "--off", "--operator", "holder"),
                         [f"{self.a.public_id} off"])
        self.assertFalse(self.held(self.a))
        self.assertEqual(self.hold("--show", "--site", self.a.public_id), [f"{self.a.public_id} off"])

    def test_one_audit_entry_per_change_and_none_for_a_no_op(self):
        args = ("--site", self.a.public_id, "--on", "--operator", "holder")
        self.hold(*args)
        entry = OperatorAuditEntry.objects.get(action="site_operator_hold_set")
        self.assertEqual((entry.target_type, entry.target_id), ("site", self.a.public_id))
        self.assertEqual(entry.detail, {"old": False, "new": True})
        self.assertEqual(entry.operator, self.operator)
        lines = self.hold(*args)
        self.assertEqual(lines, [f"{self.a.public_id} on (already, no change)"])
        self.assertEqual(OperatorAuditEntry.objects.filter(action="site_operator_hold_set").count(), 1)

    def test_output_holds_no_personal_data(self):
        text = "\n".join(self.hold("--site", self.a.public_id, "--on", "--operator", "holder"))
        for value in (self.a.account.email, self.a.display_name, "anas-store"):
            self.assertNotIn(value, text)

    def test_the_operator_must_be_an_active_staff_user(self):
        make_operator("gone", is_active=False)
        from django.contrib.auth import get_user_model
        get_user_model().objects.create_user("plain", "plain@example.test", "pw")
        for who in ("gone", "plain", "nobody", None):
            with self.subTest(who=who):
                extra = ("--operator", who) if who else ()
                with self.assertRaises(CommandError):
                    self.hold("--site", self.a.public_id, "--on", *extra)
        self.assertFalse(self.held(self.a))

    def test_an_unknown_or_deleted_site_is_refused(self):
        Site.objects.filter(pk=self.b.pk).update(deleted_at=timezone.now())
        for target in ("site_nothere", self.b.public_id):
            with self.subTest(target=target):
                with self.assertRaisesMessage(CommandError, "No such site."):
                    self.hold("--site", target, "--on", "--operator", "holder")
                with self.assertRaises(CommandError):
                    self.hold("--show", "--site", target)
        self.assertFalse(Site.objects.get(pk=self.b.pk).operator_hold)

    def test_all_except_holds_every_other_live_site(self):
        gone = make_subscription(email="cora@example.test", name="coras-store").site
        Site.objects.filter(pk=gone.pk).update(deleted_at=timezone.now())
        lines = self.hold("--all-except", self.a.public_id, "--on", "--operator", "holder")
        self.assertEqual(lines, [f"{self.b.public_id} on"])
        self.assertFalse(self.held(self.a))
        self.assertTrue(self.held(self.b))
        self.assertFalse(Site.objects.get(pk=gone.pk).operator_hold)
        self.assertEqual(OperatorAuditEntry.objects.filter(action="site_operator_hold_set").count(), 1)
        again = self.hold("--all-except", self.a.public_id, "--on", "--operator", "holder")
        self.assertEqual(again, [f"{self.b.public_id} on (already, no change)"])

    def test_all_except_refuses_off_an_unknown_site_and_show(self):
        for args in (("--all-except", self.a.public_id, "--off", "--operator", "holder"),
                     ("--all-except", "site_nothere", "--on", "--operator", "holder"),
                     ("--all-except", self.a.public_id, "--show")):
            with self.subTest(args=args):
                with self.assertRaises(CommandError):
                    self.hold(*args)
        self.assertFalse(Site.objects.filter(operator_hold=True).exists())


class HeldSiteIsRefusedTests(HoldCommandMixin, ByHostTestCase):
    def setUp(self):
        super().setUp()
        self.operator = make_operator("holder")
        self.a = make_subscription().site
        self.b = make_subscription(email="ben@example.test", name="bens-store").site

    def test_the_storefront_api_stops_and_resumes_and_the_other_site_is_unaffected(self):
        self.assert_served(self.lookup(HOST))
        self.assert_served(self.lookup(OTHER))
        self.hold("--site", self.a.public_id, "--on", "--operator", "holder")
        self.assert_not_found(self.lookup(HOST))
        self.assert_served(self.lookup(OTHER))
        self.assertFalse(self.held(self.b))
        self.hold("--site", self.a.public_id, "--off", "--operator", "holder")
        self.assert_served(self.lookup(HOST))

    def test_all_except_leaves_only_the_named_site_served(self):
        self.hold("--all-except", self.a.public_id, "--on", "--operator", "holder")
        self.assert_served(self.lookup(HOST))
        self.assert_not_found(self.lookup(OTHER))
