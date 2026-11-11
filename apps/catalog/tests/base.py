"""Helpers shared by the catalog tests. Fixture products use test values, not prices."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import override_settings

from apps.catalog.models import Catalog, CatalogProduct, CatalogProductRevision
from apps.sante import services as sante
from apps.sites.models import Site
from apps.sites.tests.api_base import SitesApiTestCase

switched_on = override_settings(SANTE_SETTINGS_ENABLED=True, SANTE_CATALOG_ENABLED=True)
MISSING_SITE = "site_00000000000000000000"
MISSING_PRODUCT = "prod_00000000000000000000"
VALID_CREATE = {
    "code": "FX-ALPHA", "name": "Fixture Alpha", "srp_centavos": 9999, "earnings_rate": "0.3500",
}


def products_url(site_id):
    return f"/v1/sites/{site_id}/catalog/products/"


def product_url(site_id, product_id):
    return f"/v1/sites/{site_id}/catalog/products/{product_id}/"


def revisions_url(site_id, product_id):
    return f"/v1/sites/{site_id}/catalog/products/{product_id}/revisions/"


def operator(username="catalog-op"):
    users = get_user_model().objects
    return users.filter(username=username).first() or users.create_superuser(
        username, f"{username}@example.test", "pw"
    )


def designate(site):
    """Name this site the catalog owner, as an operator does in the hq admin."""
    return sante.set_catalog_owner(site, operator=operator())


def make_product(code="FX-ALPHA", name="Fixture Alpha", srp_centavos=9999, rate="0.3500", *,
                 active=True, key="sante"):
    """A product with its first revision, written directly (no service rules applied)."""
    catalog, _ = Catalog.objects.get_or_create(template_key=key)
    product = CatalogProduct.objects.create(
        catalog=catalog, code=code, name=name, srp_centavos=srp_centavos,
        earnings_rate=Decimal(rate), is_active=active,
    )
    CatalogProductRevision.objects.create(
        product=product, revision=1, code=product.code, name=product.name,
        weight_kg=product.weight_kg, srp_centavos=product.srp_centavos,
        earnings_rate=product.earnings_rate, is_active=product.is_active,
        source="seed", changed_by_operator_username="fixture", seed_file_sha256="0" * 64,
    )
    return product


def catalog_call(case, method, path, client=None):
    """Call a catalog route for a test that walks every route under a site (Billing's
    inventory). It only prepares data: the site in the path is named the catalog owner, one
    product is made per test, and {product_id} is filled in. It never touches a subscription
    or a switch."""
    site = Site.objects.filter(public_id=path.split("/sites/")[1].split("/")[0]).first()
    if site is not None and sante.catalog_owner_site() != site:
        designate(site)
    if not hasattr(case, "catalog_product"):
        case.catalog_product, case.catalog_creates = make_product(), 0
    path = path.replace("{product_id}", case.catalog_product.public_id)
    if method == "GET":
        return case.get(path, client=client)
    if method == "PATCH":  # the values on file: it works and changes nothing
        body = {"revision": 1, "name": case.catalog_product.name}
    else:  # a new code each time, so a repeated create is never a 409
        case.catalog_creates += 1
        body = {**VALID_CREATE, "code": f"FX-NEW-{case.catalog_creates}"}
    return case.send(method.lower(), path, body, client=client)


def retemplate(site, template="other"):
    """Make this a site of another template, which the API cannot create in the MVP."""
    Site.objects.filter(pk=site.pk).update(template=template)
    site.refresh_from_db()
    return site


def catalog_rows():
    return (
        list(Catalog.objects.order_by("pk").values()),
        list(CatalogProduct.objects.order_by("pk").values()),
        list(CatalogProductRevision.objects.order_by("pk").values()),
    )


@switched_on
class CatalogApiTestCase(SitesApiTestCase):
    """Three Santé sites of three accounts. Ana is signed in on self.client and her site
    (self.site) is the one a test names the catalog owner; Ben and Cora have their own."""

    def setUp(self):
        super().setUp()
        self.site_id = self.create_site()["id"]
        self.site = Site.objects.get(public_id=self.site_id)
        self.ben, self.ben_site = self.subscriber("ben@example.test", "bens-store")
        self.cora, self.cora_site = self.subscriber("cora@example.test", "coras-store")
        self.products = products_url(self.site_id)

    def subscriber(self, email, subdomain):
        client = self.other_client(email)
        site_id = self.create_site(client=client, subdomain=subdomain)["id"]
        return client, Site.objects.get(public_id=site_id)

    def designate(self, site=None):
        return designate(site or self.site)

    def create(self, body=None, site=None, client=None, **changes):
        url = products_url((site or self.site).public_id)
        return self.post(url, {**(body or VALID_CREATE), **changes}, client=client)

    def created(self, **changes):
        response = self.create(**changes)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def update(self, product_id, body, site=None, client=None):
        url = product_url((site or self.site).public_id, product_id)
        return self.patch(url, body, client=client)

    def listed(self, site=None, client=None):
        response = self.get(products_url((site or self.site).public_id), client=client)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def revisions(self, product_id, site=None, client=None):
        return self.get(revisions_url((site or self.site).public_id, product_id), client=client)

    def fields(self, response):
        """The field messages of a refused write."""
        return self.error(response, 400, "invalid_request")["fields"]
