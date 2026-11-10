"""The derived "Santé Site Owner" label: worked out on each read, never stored, never sent out."""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import Account, AccountAuditEvent
from apps.sites import labels
from apps.sites.models import Onboarding, Site, SiteBusinessProfile, Subdomain
from apps.sites.tests.api_base import SitesApiTestCase
from apps.sites.tests.base import make_account, make_site

LABEL = "Santé Site Owner"


def flagged(account):
    """The annotated column for one account, read fresh."""
    return labels.with_sante_site_owner(Account.objects.filter(pk=account.pk)).get().sante_site_owner


class LabelRuleTests(TestCase):
    def setUp(self):
        self.ana = make_account()

    def assert_label(self, account, expected):
        self.assertEqual(labels.labels_for(account), [LABEL] if expected else [])
        self.assertIs(flagged(account), expected)

    def test_the_wording_is_one_constant(self):
        self.assertEqual(labels.SANTE_SITE_OWNER, LABEL)

    def test_one_eligible_sante_site_gives_the_label(self):
        make_site(self.ana)

        self.assert_label(self.ana, True)

    def test_no_site_gives_no_label(self):
        self.assert_label(self.ana, False)

    def test_a_removed_site_gives_no_label(self):
        make_site(self.ana, deleted_at=timezone.now())

        self.assert_label(self.ana, False)

    def test_a_held_site_gives_no_label(self):
        make_site(self.ana, operator_hold=True)

        self.assert_label(self.ana, False)

    def test_a_site_of_another_type_gives_no_label(self):
        site = make_site(self.ana)
        Site.objects.filter(pk=site.pk).update(template="basic")

        self.assert_label(self.ana, False)

    def test_a_closed_account_gives_no_label(self):
        make_site(self.ana)
        Account.objects.filter(pk=self.ana.pk).update(closed_at=timezone.now())

        self.assert_label(Account.objects.get(pk=self.ana.pk), False)

    def test_the_label_follows_the_sites_on_each_fresh_read(self):
        first = make_site(self.ana, deleted_at=timezone.now())
        second = make_site(self.ana, name="second-store", first=False)
        self.assert_label(self.ana, True)  # one removed, one eligible

        Site.objects.filter(pk=second.pk).update(deleted_at=timezone.now())
        self.assert_label(self.ana, False)  # the second is removed too

        Site.objects.filter(pk=first.pk).update(deleted_at=None, operator_hold=True)
        self.assert_label(self.ana, False)  # back, but held

        Site.objects.filter(pk=first.pk).update(operator_hold=False)
        self.assert_label(self.ana, True)  # the hold is cleared

    def test_eligible_sante_sites_holds_only_sites_meeting_all_three_conditions(self):
        eligible = make_site(self.ana)
        make_site(self.ana, name="removed-store", first=False, deleted_at=timezone.now())
        make_site(self.ana, name="held-store", first=False, operator_hold=True)
        other = make_site(self.ana, name="other-store", first=False)
        Site.objects.filter(pk=other.pk).update(template="basic")

        self.assertEqual(list(labels.eligible_sante_sites()), [eligible])

    def test_the_sites_list_column_is_no_for_removed_held_other_type_and_closed_account(self):
        eligible = make_site(self.ana)
        removed = make_site(self.ana, name="removed-store", first=False, deleted_at=timezone.now())
        held = make_site(self.ana, name="held-store", first=False, operator_hold=True)
        closed = make_site(make_account("cora@example.test"), name="coras-store")
        Account.objects.filter(pk=closed.account_id).update(closed_at=timezone.now())

        counts = {
            site.pk: site.counts_for_sante_site_owner
            for site in labels.with_counts_for_sante_site_owner(Site.objects.all())
        }

        self.assertEqual(
            counts, {eligible.pk: True, removed.pk: False, held.pk: False, closed.pk: False}
        )


class LabelTenantIsolationTests(TestCase):
    def setUp(self):
        self.ana = make_account()
        self.ben = make_account("ben@example.test")

    def test_anas_eligible_site_gives_ben_no_label(self):
        make_site(self.ana)

        self.assertEqual(labels.labels_for(self.ana), [LABEL])
        self.assertEqual(labels.labels_for(self.ben), [])
        self.assertIs(flagged(self.ben), False)

    def test_bens_eligible_site_gives_ana_no_label_after_hers_is_removed(self):
        anas = make_site(self.ana)
        make_site(self.ben, name="bens-store")
        Site.objects.filter(pk=anas.pk).update(deleted_at=timezone.now())

        self.assertEqual(labels.labels_for(self.ana), [])
        self.assertIs(flagged(self.ana), False)
        self.assertEqual(labels.labels_for(self.ben), [LABEL])

    def test_eligible_sites_filtered_to_one_account_never_hold_anothers_row(self):
        anas = make_site(self.ana)
        bens = make_site(self.ben, name="bens-store")

        self.assertEqual(list(labels.eligible_sante_sites().filter(account=self.ana)), [anas])
        self.assertEqual(list(labels.eligible_sante_sites().filter(account=self.ben)), [bens])

    def test_one_query_flags_each_account_by_its_own_sites(self):
        make_site(self.ana)
        make_site(self.ben, name="bens-store", operator_hold=True)

        with self.assertNumQueries(1):
            flags = dict(
                labels.with_sante_site_owner(Account.objects.all()).values_list(
                    "email", "sante_site_owner"
                )
            )

        self.assertEqual(flags, {"ana@example.test": True, "ben@example.test": False})


class LabelWritesNothingTests(TestCase):
    MODELS = (Account, AccountAuditEvent, Site, Subdomain, SiteBusinessProfile, Onboarding)

    def snapshot(self):
        return [list(model.objects.order_by("pk").values()) for model in self.MODELS]

    def test_reading_the_label_writes_nothing(self):
        ana = make_account()
        make_site(ana)
        ben = make_account("ben@example.test")
        before = self.snapshot()

        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(labels.labels_for(ana), [LABEL])
            self.assertEqual(labels.labels_for(ben), [])
            list(labels.with_sante_site_owner(Account.objects.all()))
            list(labels.with_counts_for_sante_site_owner(Site.objects.all()))
            list(labels.eligible_sante_sites())

        self.assertEqual(self.snapshot(), before)
        self.assertTrue(queries.captured_queries)
        for query in queries.captured_queries:
            self.assertTrue(query["sql"].lstrip().upper().startswith("SELECT"), query["sql"])

    def test_no_model_has_a_column_for_the_label(self):
        for model in (Account, Site):
            names = [field.name for field in model._meta.get_fields()]
            self.assertFalse([n for n in names if "owner" in n or "label" in n], names)


def walk(value):
    """Every key and every scalar value in a JSON body, as text."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield str(key)
            yield from walk(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from walk(inner)
    else:
        yield str(value)


class NoEndpointReturnsTheLabelTests(SitesApiTestCase):
    def test_account_setup_and_sites_hold_no_key_or_value_naming_the_label(self):
        self.create_site()
        self.assertEqual(labels.labels_for(self.account), [LABEL])  # Ana does have the label

        for path in ("/v1/account/", "/v1/account/setup/", "/v1/sites/"):
            with self.subTest(path=path):
                response = self.get(path)
                self.assertEqual(response.status_code, 200, response.content)
                for text in walk(response.json()):
                    lowered = text.lower()
                    self.assertNotIn("site owner", lowered)
                    self.assertNotIn("site_owner", lowered)
                    self.assertNotIn("siteowner", lowered)
                self.assertNotIn(LABEL, response.content.decode())
