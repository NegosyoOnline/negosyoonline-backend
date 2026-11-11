"""Publication across sites: a saved owner change is what every Santé site reads, nobody else
can override it, and a central write reaches no site's own records. Restates TWCinc's
test_publication_isolation. Its seed half is not here: no seed is built in this slice."""
import re

from django.apps import apps
from django.core import mail
from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import AccountAuditEvent
from apps.catalog.models import Catalog, CatalogProduct, CatalogProductRevision
from apps.catalog.tests.base import CatalogApiTestCase, catalog_rows, make_product
from apps.notifications.models import EmailMessage, SmsMessage
from apps.operators.models import OperatorAuditEntry
from apps.sante.models import SanteCatalogOwner, SanteSiteSettings
from apps.sites.models import Onboarding, Site, SiteBusinessProfile, Subdomain

CATALOG_TABLES = {
    model._meta.db_table for model in (Catalog, CatalogProduct, CatalogProductRevision)
}
WRITE_RE = re.compile(r'^\s*(?:INSERT INTO|UPDATE|DELETE FROM)\s+"(?P<table>[^"]+)"', re.IGNORECASE)
SITE_OWNED = (
    (Site, "pk"), (SiteBusinessProfile, "site"), (Subdomain, "site"),
    (Onboarding, "first_site"), (SanteSiteSettings, "site"),
)


def site_records(site):
    """Every row a site owns, on every table that holds one."""
    return {
        model._meta.label: list(
            model.objects.filter(**{field: site.pk if field == "pk" else site})
            .order_by("pk").values()
        )
        for model, field in SITE_OWNED
    }


class SharedModelShapeTests(SimpleTestCase):
    def test_the_catalog_tables_point_at_no_site_account_or_other_app(self):
        models = list(apps.get_app_config("catalog").get_models())
        self.assertEqual(
            {model.__name__ for model in models},
            {"Catalog", "CatalogProduct", "CatalogProductRevision"},
        )
        for model in models:
            for field in model._meta.get_fields():
                if field.is_relation and not field.auto_created:
                    self.assertEqual(
                        field.related_model._meta.app_label, "catalog", (model, field.name)
                    )


class PublicationTests(CatalogApiTestCase):
    def setUp(self):
        super().setUp()
        self.designate()

    def views(self):
        return [
            self.listed(site, client)["products"]
            for site, client in ((self.ben_site, self.ben), (self.cora_site, self.cora))
        ]

    def owner_writes(self):
        made = self.created(code="FX-OWNER")
        response = self.update(made["id"], {"revision": 1, "srp_centavos": 12000})
        self.assertEqual(response.status_code, 200, response.content)
        return made["id"]

    def test_an_owner_change_reaches_every_sante_site_at_once(self):
        made = self.created()
        ben, cora = self.views()
        self.assertEqual(ben, cora)
        self.assertEqual(ben, [made])

        self.update(made["id"], {"revision": 1, "srp_centavos": 12000})
        ben, cora = self.views()
        self.assertEqual(ben, cora)
        self.assertEqual(
            [(row["srp_centavos"], row["unit_members_price_centavos"], row["revision"])
             for row in ben],
            [(12000, 7800, 2)],
        )

    def test_another_subscribers_override_fails(self):
        product = make_product()
        before = catalog_rows()
        for response in (
            self.create(site=self.ben_site, client=self.ben, code="FX-B"),
            self.update(product.public_id, {"revision": 1, "srp_centavos": 100},
                        site=self.ben_site, client=self.ben),
        ):
            self.error(response, 403, "catalog_read_only")
        self.assertEqual(catalog_rows(), before)
        self.assertEqual(self.views()[1][0]["srp_centavos"], 9999)

    def test_a_central_write_touches_no_sites_records(self):
        others = (self.ben_site, self.cora_site)
        before = (
            [site_records(site) for site in (self.site, *others)],
            list(SanteCatalogOwner.objects.values()),
        )
        self.listed()  # the first request of a session may record that it was used
        with CaptureQueriesContext(connection) as queries:
            self.owner_writes()
        site_table = Site._meta.db_table
        for query in queries.captured_queries:
            sql = query["sql"]
            written = WRITE_RE.match(sql)
            if written:
                self.assertIn(written.group("table"), CATALOG_TABLES, sql)
            if f'"{site_table}"' in sql:  # only the writer's own site is ever read
                for site in others:
                    self.assertNotIn(site.public_id, sql)
                    self.assertNotIn(f'"{site_table}"."id" = {site.pk}', sql)
        after = (
            [site_records(site) for site in (self.site, *others)],
            list(SanteCatalogOwner.objects.values()),
        )
        self.assertEqual(after, before)

    def test_it_writes_no_billing_row_and_no_audit_entry(self):
        watched = [*apps.get_app_config("billing").get_models(), OperatorAuditEntry, AccountAuditEvent]
        before = {model._meta.label: model.objects.count() for model in watched}
        product_id = self.owner_writes()
        self.views()
        self.revisions(product_id)
        self.assertEqual({model._meta.label: model.objects.count() for model in watched}, before)

    def test_it_sends_no_email_and_no_text(self):
        before = (EmailMessage.objects.count(), SmsMessage.objects.count())
        mail.outbox = []
        product_id = self.owner_writes()
        self.update(product_id, {"revision": 2, "is_active": False})
        self.assertEqual((EmailMessage.objects.count(), SmsMessage.objects.count()), before)
        self.assertEqual(mail.outbox, [])
