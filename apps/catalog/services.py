"""The shared catalog. Other apps call these functions, never the tables.

Everything here is behind SANTE_CATALOG_ENABLED, which counts as on only while
SANTE_SETTINGS_ENABLED is on too, because the catalog reads the catalog owner row. While it
is off the routes answer 404, the three tables are not read
or written: the code is released before its tables are created.

Only the catalog owner site writes. A saved change is the publication: every Santé site reads
the same rows at once, with no draft step. An owner write is not written to the operator
audit; the revision table is its record.

Lock order of a write: the catalog owner row, then the caller's Site row, then the Catalog
row (a create) or the product row (an update).
"""
import re
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.catalog import media, pricing
from apps.catalog.models import Catalog, CatalogProduct, CatalogProductRevision
from apps.common import private_files
from apps.common.errors import ApiError
from apps.common.throttle import hit
from apps.sante import services as sante
from apps.sites import templates
from apps.sites.models import Site

CATALOG_KEYS = {templates.SANTE: "sante"}  # which catalog a template's sites read

SRP_MAX_CENTAVOS = 999_999_999_999  # ten peso digits and two centavo digits
RATE_MIN = Decimal("0.1000")
RATE_MAX = Decimal("0.5000")
WRITE_LIMIT, WRITE_WINDOW = 120, 3600  # catalog writes per account per hour
DIM_FIELDS = ("length_cm", "width_cm", "height_cm")
SHIPPING_FIELDS = ("volume_slot", "shipping_qty")
OPTIONAL_FIELDS = (*SHIPPING_FIELDS, *DIM_FIELDS)  # may be left out of a create
CATEGORIES = ("barley", "drinks", "essentials", "beauty")  # blank is allowed too
CENTRAL_FIELDS = (
    "name", "short_description", "long_description", "category", "image_key", "weight_kg",
    "srp_centavos", "earnings_rate", "is_active", *OPTIONAL_FIELDS,
)
IMAGE_MESSAGES = {
    private_files.WRONG_TYPE: "Upload a JPEG, PNG or WebP image.",
    private_files.TOO_LARGE: "The image is too large. The limit is 2 MB.",
}
IMAGE_NOT_CONFIGURED_MSG = "Product images cannot be stored yet: the image store is not configured."
IMAGE_NOT_SAVED_MSG = "The image could not be stored. Try again."

CODE_RE = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,63}")
WEIGHT_RE = re.compile(r"[0-9]{1,8}(\.[0-9]{1,2})?")
RATE_RE = re.compile(r"[0-9](\.[0-9]{1,4})?")
SLOT_RE = re.compile(r"[0-9]{1,4}(\.[0-9]{1,2})?")
DIM_RE = re.compile(r"[0-9]{1,5}(\.[0-9])?")

READ_ONLY_MSG = "Only the catalog owner site can change the shared catalog."
STALE_MSG = "This product was changed since you loaded it. Reload it and try again."
CODE_TAKEN_MSG = "That product code is already in the catalog."
INVALID_MSG = "Some values are missing or invalid."
FIELD_MESSAGES = {
    "code": "Use 1 to 64 letters, digits, dots, hyphens or underscores, starting with a letter or digit.",
    "name": "Use 1 to 120 characters.",
    "short_description": "Use up to 500 characters.",
    "long_description": "Use up to 20000 characters.",
    "category": "Choose barley, drinks, essentials or beauty, or leave it blank.",
    "weight_kg": "Enter a weight in kg of 0 or more, with up to 2 decimal places.",
    "srp_centavos": "Enter an SRP above 0, in whole centavos.",
    "earnings_rate": "Enter an earnings rate from 0.1000 to 0.5000 (10% to 50%).",
    "volume_slot": "Enter a volume slot above 0, with up to 2 decimal places.",
    "shipping_qty": "Enter a shipping quantity of 1 or more.",
    **{name: "Enter a size in cm above 0, with up to 1 decimal place." for name in DIM_FIELDS},
}
DIMS_ALL_MSG = "Enter length, width and height together, each above 0."
# Shipping is never worked out from a missing weight: a write that gives a volume slot or a
# shipping quantity needs a measured weight above 0, and a weight given on update is above 0.
SHIPPING_WEIGHT_MSG = "Enter a measured weight in kg above 0; shipping inputs need a real weight."


def enabled():
    return settings.SANTE_CATALOG_ENABLED and sante.enabled()


def _not_found():
    return ApiError("not_found", "Not found.", status=404)


def _read_only():
    return ApiError("catalog_read_only", READ_ONLY_MSG, status=403)


def _invalid(fields):
    return ApiError(
        "invalid_request", INVALID_MSG,
        fields={name: [message] for name, message in fields.items()},
    )


# Reading

def catalog_key_for(site):
    """The key of the catalog this site reads, or "" when it reads none."""
    return CATALOG_KEYS.get(site.template, "") if enabled() else ""


def is_writer(site, *, lock=False):
    return bool(catalog_key_for(site)) and sante.is_catalog_writer(site, lock=lock)


def products_for_site(site, *, include_inactive=False):
    """The products of the catalog the site reads, oldest first. Empty when it reads none or
    nothing was written yet. Never creates the catalog row."""
    key = catalog_key_for(site)
    if not key:
        return CatalogProduct.objects.none()
    products = CatalogProduct.objects.filter(catalog__template_key=key).order_by("pk")
    return products if include_inactive else products.filter(is_active=True)


def _fixed(value, places):
    return None if value is None else f"{value:.{places}f}"


def _shared_out(row):
    """The values a product and its revisions both carry. Amounts are whole centavos; the
    other numbers are fixed-format text."""
    return {
        "code": row.code,
        "name": row.name,
        "short_description": row.short_description,
        "long_description": row.long_description,
        "category": row.category,
        "image_url": media.url_for(row.image_key),
        "weight_kg": _fixed(row.weight_kg, 2),
        "srp_centavos": row.srp_centavos,
        "earnings_rate": _fixed(row.earnings_rate, 4),
        "volume_slot": _fixed(row.volume_slot, 2),
        "shipping_qty": row.shipping_qty,
        **{name: _fixed(getattr(row, name), 1) for name in DIM_FIELDS},
        "is_active": row.is_active,
        "revision": row.revision,
    }


def product_out(product):
    return {
        "id": product.public_id,
        **_shared_out(product),
        "unit_members_price_centavos": pricing.unit_display(
            product.srp_centavos, product.earnings_rate
        ),
        "updated_at": product.updated_at,
    }


def revision_out(revision):
    """Who wrote it is never returned: only that the owner or a seed did."""
    return {**_shared_out(revision), "source": revision.source, "changed_at": revision.changed_at}


def public_product_out(product):
    """What a storefront shows. Never the earnings rate, the writer, `is_active`, weights,
    sizes or a file key."""
    return {
        "id": product.public_id,
        "code": product.code,
        "name": product.name,
        "category": product.category,
        "short_description": product.short_description,
        "long_description": product.long_description,
        "image_url": media.url_for(product.image_key),
        "srp_centavos": product.srp_centavos,
        "unit_members_price_centavos": pricing.unit_display(
            product.srp_centavos, product.earnings_rate
        ),
        "revision": product.revision,
    }


def storefront_products(site):
    """The active products of the site's catalog, in creation order."""
    return [public_product_out(product) for product in products_for_site(site)]


def storefront_product(site, public_id):
    """One active product, or a 404 for an unknown or inactive one."""
    product = products_for_site(site).filter(public_id=public_id).first()
    if product is None:
        raise _not_found()
    return public_product_out(product)


def list_for(site):
    """What a Santé site reads: active products, and for the owner the deactivated ones too."""
    can_edit = is_writer(site)
    products = products_for_site(site, include_inactive=can_edit)
    return {"can_edit": can_edit, "products": [product_out(product) for product in products]}


def revisions_for(site, product_id):
    """A product's history, newest first. The owner site only."""
    if not is_writer(site):
        raise _read_only()
    product = products_for_site(site, include_inactive=True).filter(public_id=product_id).first()
    if product is None:
        raise _not_found()
    return {"revisions": [revision_out(row) for row in product.revisions.order_by("-revision")]}


# The rules

def _code(value):
    value = value.strip().upper()
    return bool(CODE_RE.fullmatch(value)), value


def _text(shortest, longest):
    def check(value):
        value = value.strip()
        return shortest <= len(value) <= longest, value
    return check


def _category(value):
    value = value.strip()
    return value == "" or value in CATEGORIES, value


def _number(pattern, accept):
    def check(value):
        if not pattern.fullmatch(value):
            return False, None
        number = Decimal(value)
        return accept(number), number
    return check


FIELD_CHECKS = {
    "code": _code,
    "name": _text(1, 120),
    "short_description": _text(0, 500),
    "long_description": _text(0, 20000),
    "category": _category,
    "weight_kg": _number(WEIGHT_RE, lambda number: number >= 0),
    "earnings_rate": _number(RATE_RE, lambda number: RATE_MIN <= number <= RATE_MAX),
    "volume_slot": _number(SLOT_RE, lambda number: number > 0),
    **{name: _number(DIM_RE, lambda number: number > 0) for name in DIM_FIELDS},
}
WHOLE_NUMBER_MAX = {"srp_centavos": SRP_MAX_CENTAVOS, "shipping_qty": None}  # each from 1 up


def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool)


def field_errors(data):
    """(cleaned, errors) for the values given. An input is never rounded: a third weight
    decimal fails its pattern. Text arrives as text and whole numbers as JSON integers; a
    null, or a value of the wrong kind, gets that field's message. `is_active` is a strict
    boolean by the time it reaches here."""
    cleaned, errors = {}, {}
    for name, value in data.items():
        if name == "is_active":
            cleaned[name] = value
            continue
        if name in WHOLE_NUMBER_MAX:
            most = WHOLE_NUMBER_MAX[name]
            ok = _whole(value) and value >= 1 and (most is None or value <= most)
            result = value
        else:
            ok, result = FIELD_CHECKS[name](value) if isinstance(value, str) else (False, None)
        if ok:
            cleaned[name] = result
        else:
            errors[name] = FIELD_MESSAGES[name]
    return cleaned, errors


def dimension_errors(cleaned, existing=None):
    """{field: message} when this write would leave the size partly set. A write that gives
    any of length, width or height must leave all three set; `existing` is the product on an
    update."""
    if not any(name in cleaned for name in DIM_FIELDS):
        return {}
    return {
        name: DIMS_ALL_MSG for name in DIM_FIELDS
        if name not in cleaned and getattr(existing, name, None) is None
    }


def shipping_weight_error(cleaned, *, existing_weight=None, update=False):
    """The weight message when this write would leave shipping inputs beside a weight that
    is not above 0, else None. On a create the weight is the one given (0.00 means not
    measured). On an update a weight that is given must be above 0, and giving a slot or a
    quantity needs the resulting weight above 0."""
    weight = cleaned.get("weight_kg", existing_weight if update else Decimal("0"))
    if update and "weight_kg" in cleaned and cleaned["weight_kg"] <= 0:
        return SHIPPING_WEIGHT_MSG
    if any(name in cleaned for name in SHIPPING_FIELDS) and (weight is None or weight <= 0):
        return SHIPPING_WEIGHT_MSG
    return None


def _clean(data):
    cleaned, errors = field_errors(data)
    if errors:
        raise _invalid(errors)
    return cleaned


def _refuse_bad_measures(cleaned, existing=None, **weight):
    fields = dimension_errors(cleaned, existing)
    message = shipping_weight_error(cleaned, **weight)
    if message:
        fields["weight_kg"] = message
    if fields:
        raise _invalid(fields)


# Writing

def _writer_site(account, site):
    """The caller's site, locked, once it is known to be the catalog owner. Inside the
    write's transaction. `site` was already reached through site_for and checked against
    Billing by the API.

    The owner row is locked before the Site row, the order set_catalog_owner uses, so a write
    and a change of owner queue on the owner row and cannot deadlock.
    """
    if not is_writer(site, lock=True):
        raise _read_only()
    site = (
        Site.objects.select_for_update()
        .filter(pk=site.pk, account=account, template=templates.SANTE, deleted_at__isnull=True)
        .first()
    )
    if site is None:  # removed while this write waited
        raise _not_found()
    hit("catalog-write", account.pk, limit=WRITE_LIMIT, window=WRITE_WINDOW)
    return site


def _locked_catalog(key):
    """The Catalog row of a template, created on first use. Only inside a write."""
    Catalog.objects.get_or_create(template_key=key)
    return Catalog.objects.select_for_update().get(template_key=key)


def _append_revision(product, **writer):
    values = {name: getattr(product, name) for name in ("code", *CENTRAL_FIELDS)}
    return CatalogProductRevision.objects.create(
        product=product, revision=product.revision, **values, **writer
    )


def _owner_writer(account, site):
    return {
        "source": CatalogProductRevision.OWNER,
        "changed_by_account_id": account.pk,
        "changed_by_site_id": site.pk,
    }


def _code_taken():
    return ApiError("code_taken", CODE_TAKEN_MSG, status=409, fields={"code": [CODE_TAKEN_MSG]})


@transaction.atomic
def create_product(account, site, data):
    """Add a product, active, at revision 1."""
    data = {
        name: value for name, value in data.items()
        if not (name in OPTIONAL_FIELDS and value is None)
    }
    site = _writer_site(account, site)
    cleaned = _clean(data)
    _refuse_bad_measures(cleaned)
    catalog = _locked_catalog(catalog_key_for(site))
    if CatalogProduct.objects.filter(catalog=catalog, code=cleaned["code"]).exists():
        raise _code_taken()
    try:
        with transaction.atomic():
            product = CatalogProduct.objects.create(
                catalog=catalog, revision=1, is_active=True, **cleaned
            )
    except IntegrityError:
        raise _code_taken() from None
    _append_revision(product, **_owner_writer(account, site))
    return product


@transaction.atomic
def update_product(account, site, product_id, data):
    """Change the values present in `data`. `data["revision"]` is the revision the caller
    loaded: a product changed since then is refused. A write that changes nothing writes no
    revision."""
    site = _writer_site(account, site)
    cleaned = _clean({name: value for name, value in data.items() if name != "revision"})
    product = (
        CatalogProduct.objects.select_for_update(of=("self",))  # the product row only
        .filter(public_id=product_id, catalog__template_key=catalog_key_for(site))
        .first()
    )
    if product is None:
        raise _not_found()
    if data["revision"] != product.revision:
        raise ApiError("stale_revision", STALE_MSG, status=409)
    _refuse_bad_measures(
        cleaned, existing=product, existing_weight=product.weight_kg, update=True
    )
    changed = {name: value for name, value in cleaned.items() if getattr(product, name) != value}
    if not changed:
        return product
    for name, value in changed.items():
        setattr(product, name, value)
    product.revision += 1
    product.save(update_fields=[*changed, "revision", "updated_at"])
    _append_revision(product, **_owner_writer(account, site))
    return product


# The product image

def _locked_product(site, product_id, revision):
    """The product row, locked, once `revision` is the one the caller loaded."""
    product = (
        CatalogProduct.objects.select_for_update(of=("self",))
        .filter(public_id=product_id, catalog__template_key=catalog_key_for(site))
        .first()
    )
    if product is None:
        raise _not_found()
    if revision != product.revision:
        raise ApiError("stale_revision", STALE_MSG, status=409)
    return product


def set_image(account, site, product_id, upload, revision):
    """Store `upload` as the product's one image, replacing any. A write that is not allowed
    (not the owner site, no store, a bad file) stores nothing. The old file is removed once the
    change is committed; a new file whose change failed is removed at once."""
    if not is_writer(site):
        raise _read_only()
    if not media.configured():
        raise ApiError("image_store_not_configured", IMAGE_NOT_CONFIGURED_MSG, status=503)
    try:
        content, content_type = private_files.check(
            upload, allowed=private_files.IMAGE_TYPES, max_bytes=media.MAX_BYTES
        )
    except private_files.FileRefused as refused:
        raise _invalid({"file": IMAGE_MESSAGES[refused.reason]}) from None
    try:
        key = media.save(content, content_type)
    except media.StoreError:
        raise ApiError("image_not_saved", IMAGE_NOT_SAVED_MSG, status=502) from None
    try:
        with transaction.atomic():
            site = _writer_site(account, site)
            product = _locked_product(site, product_id, revision)
            old_key = product.image_key
            product.image_key = key
            product.revision += 1
            product.save(update_fields=["image_key", "revision", "updated_at"])
            _append_revision(product, **_owner_writer(account, site))
            transaction.on_commit(lambda: media.discard(old_key))
    except BaseException:
        media.discard(key)
        raise
    return product


@transaction.atomic
def clear_image(account, site, product_id, revision):
    """Remove the product's image. A product with none is returned as it is, writing no
    revision. The stored file is removed once the change is committed."""
    site = _writer_site(account, site)
    product = _locked_product(site, product_id, revision)
    if not product.image_key:
        return product
    old_key = product.image_key
    product.image_key = ""
    product.revision += 1
    product.save(update_fields=["image_key", "revision", "updated_at"])
    _append_revision(product, **_owner_writer(account, site))
    transaction.on_commit(lambda: media.discard(old_key))
    return product


# The seed (manage.py catalog_seed_sante)

SEED_FIELDS = (
    "name", "short_description", "long_description", "category", "weight_kg", "srp_centavos",
    "earnings_rate", *OPTIONAL_FIELDS,
)


def seed_clean(row):
    """(code, cleaned values) of a seed row, through the same rules as the API, or ApiError.
    A seed row never names a writer, a revision or an image."""
    unknown = set(row) - {"code", *SEED_FIELDS}
    if unknown:
        raise _invalid({name: "Not a seed field." for name in sorted(unknown)})
    values = {name: value for name, value in row.items() if value is not None}
    cleaned = _clean(values)
    _refuse_bad_measures(cleaned)
    code = cleaned.pop("code", None)
    if code is None:
        raise _invalid({"code": FIELD_MESSAGES["code"]})
    return code, cleaned


def _seed_outcome(product, cleaned):
    if product is None:
        return "create"
    return [name for name, value in cleaned.items() if getattr(product, name) != value] or "same"


def seed_state(rows):
    """What the seed would do with each row, writing nothing: a list of
    (code, "create" | "same" | [differing fields]). Reads the catalog only while it is
    switched on."""
    existing = {}
    if enabled():
        existing = {
            product.code: product
            for product in CatalogProduct.objects.filter(catalog__template_key="sante")
        }
    result = []
    for row in rows:
        code, cleaned = seed_clean(row)
        result.append((code, _seed_outcome(existing.get(code), cleaned)))
    return result


def seed_products(rows, *, operator, file_sha256, images=None):
    """Create the products of `rows` that are not in the catalog yet, each at revision 1 with
    source "seed", `operator` (an active staff user) and the seed file's hash. One transaction:
    all or none. An existing code is never changed. `images` maps a code to (bytes, content
    type), stored in the media store for the products created. Returns what seed_state gives,
    taken under the lock. Raises ApiError for a bad row."""
    images = images or {}
    stored = []
    try:
        with transaction.atomic():
            catalog = _locked_catalog("sante")
            existing = {product.code: product for product in catalog.products.all()}
            result = []
            for row in rows:
                code, cleaned = seed_clean(row)
                outcome = _seed_outcome(existing.get(code), cleaned)
                result.append((code, outcome))
                if outcome != "create":
                    continue
                key = ""
                if code in images:
                    key = media.save(*images[code])
                    stored.append(key)
                product = CatalogProduct.objects.create(
                    catalog=catalog, code=code, revision=1, is_active=True, image_key=key,
                    **cleaned,
                )
                existing[code] = product
                _append_revision(
                    product,
                    source=CatalogProductRevision.SEED,
                    changed_by_operator_user_id=operator.pk,
                    changed_by_operator_username=operator.get_username(),
                    seed_file_sha256=file_sha256,
                )
    except BaseException:
        for key in stored:
            media.discard(key)
        raise
    return result
