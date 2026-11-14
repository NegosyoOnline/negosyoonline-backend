"""The shared catalog. Other apps call these functions, never the tables.

Everything here is behind SANTE_CATALOG_ENABLED, which counts as on only while
SANTE_SETTINGS_ENABLED is on too, because the catalog reads the catalog owner row. While it
is off the routes answer 404, the three tables are not read
or written: the code is released before its tables are created.

Only the catalog owner site writes. A saved change is the publication: every Santé site reads
the same rows at once, with no draft step. An owner write is not written to the operator
audit; the revision table is its record.

Lock order of a write: the catalog owner row, then the caller's Site row, then the Catalog
row (a create or a reorder) or the product row (an update).
"""
import hashlib
import re
from decimal import ROUND_CEILING, Decimal
from math import ceil

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max

from apps.catalog import media, pricing
from apps.catalog.models import Catalog, CatalogProduct, CatalogProductRevision
from apps.common import private_files
from apps.common.errors import ApiError
from apps.common.throttle import hit
from apps.sante import services as sante
from apps.sites import templates
from apps.shop import constants as C, shipping, zones
from apps.sites.models import Site

CATALOG_KEYS = {templates.SANTE: "sante"}  # which catalog a template's sites read

SRP_MAX_CENTAVOS = 999_999_999_999  # ten peso digits and two centavo digits
RATE_MIN = Decimal("0.1000")
RATE_MAX = Decimal("0.5000")
WRITE_LIMIT, WRITE_WINDOW = 120, 3600  # catalog writes per account per hour
DIM_FIELDS = ("length_cm", "width_cm", "height_cm")
IMAGE_SLOTS = 5
IMAGE_FIELDS = ("image_key", "image_key_2", "image_key_3", "image_key_4", "image_key_5")
DETAIL_FIELDS = ("features", "specifications", "advantages", "benefits")  # at most 5000 each
SERVINGS_MAX = 999
FAQ_MAX_ITEMS, FAQ_Q_MAX, FAQ_A_MAX = 10, 150, 1000
PACK_FIELDS = ("servings_per_pack", "faq")
INGREDIENT_MAX_ITEMS, INGREDIENT_NAME_MAX, INGREDIENT_LINE_MAX = 8, 60, 200
SHIPPING_FIELDS = ("volume_slot", "shipping_qty")
OPTIONAL_FIELDS = (*SHIPPING_FIELDS, *DIM_FIELDS)  # may be left out of a create
CATEGORIES = ("barley", "drinks", "essentials", "beauty")  # blank is allowed too
CENTRAL_FIELDS = (
    "name", "short_description", "long_description", *DETAIL_FIELDS, *PACK_FIELDS, "ingredients", "category",
    *IMAGE_FIELDS,
    "weight_kg", "srp_centavos", "earnings_rate", "is_active", *OPTIONAL_FIELDS,
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

SLOT_MSG = "Choose a photo slot from 1 to 5."
READ_ONLY_MSG = "Only the catalog owner site can change the shared catalog."
STALE_MSG = "This product was changed since you loaded it. Reload it and try again."
CODE_TAKEN_MSG = "That product code is already in the catalog."
INVALID_MSG = "Some values are missing or invalid."
FIELD_MESSAGES = {
    "code": "Use 1 to 64 letters, digits, dots, hyphens or underscores, starting with a letter or digit.",
    "name": "Use 1 to 120 characters.",
    "short_description": "Use up to 500 characters.",
    "long_description": "Use up to 20000 characters.",
    **{name: "Use up to 5000 characters." for name in DETAIL_FIELDS},
    "servings_per_pack": "Enter the servings in one pack, a whole number from 1 to 999, or leave it blank.",
    "faq": "Send up to 10 questions, each with a question of 1 to 150 characters and an answer of 1 to 1000.",
    "ingredients": (
        "Send up to 8 ingredients, each with a name of 1 to 60 characters, a line of 0 to 200 "
        "and a photo slot from 1 to 5 or null."
    ),
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
    return ApiError("not_found", "Not found.", status=404, event="object_not_found", reason="missing")


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
    """The products of the catalog the site reads, in shop order: (display_order, pk), which is
    creation order until the owner first arranges them. Empty when it reads none or
    nothing was written yet. Never creates the catalog row."""
    key = catalog_key_for(site)
    if not key:
        return CatalogProduct.objects.none()
    products = CatalogProduct.objects.filter(catalog__template_key=key).order_by("display_order", "pk")
    return products if include_inactive else products.filter(is_active=True)


def _fixed(value, places):
    return None if value is None else f"{value:.{places}f}"


def _images_out(row):
    """`image_urls` is always five entries (slot 1 to 5), null where empty; `image_url` is
    slot 1, kept for readers that only know one photo."""
    urls = [media.url_for(getattr(row, name)) for name in IMAGE_FIELDS]
    return {"image_url": urls[0], "image_urls": urls}


def _ingredients_out(row):
    """Each ingredient with the URL of the photo it points at; null when that slot is empty."""
    urls = _images_out(row)["image_urls"]
    return [
        {**item, "photo_url": urls[item["photo_slot"] - 1] if item["photo_slot"] else None}
        for item in row.ingredients
    ]


def _shared_out(row):
    """The values a product and its revisions both carry. Amounts are whole centavos; the
    other numbers are fixed-format text."""
    return {
        "code": row.code,
        "name": row.name,
        "short_description": row.short_description,
        "long_description": row.long_description,
        "category": row.category,
        **_images_out(row),
        **{name: getattr(row, name) for name in DETAIL_FIELDS},
        **{name: getattr(row, name) for name in PACK_FIELDS},
        "ingredients": _ingredients_out(row),
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
    members_price = pricing.unit_display(product.srp_centavos, product.earnings_rate)
    return {
        "id": product.public_id,
        "code": product.code,
        "name": product.name,
        "category": product.category,
        "short_description": product.short_description,
        "long_description": product.long_description,
        **_images_out(product),
        **{name: getattr(product, name) for name in DETAIL_FIELDS},
        **{name: getattr(product, name) for name in PACK_FIELDS},
        "ingredients": _ingredients_out(product),
        "srp_centavos": product.srp_centavos,
        "unit_members_price_centavos": members_price,
        "price_per_serving_centavos": pricing.per_serving(
            product.srp_centavos, product.servings_per_pack
        ),
        "members_price_per_serving_centavos": pricing.per_serving(
            members_price, product.servings_per_pack
        ),
        "revision": product.revision,
    }


def storefront_products(site):
    """The active products of the site's catalog, in shop order."""
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


def _faq(value):
    """A list of {"q", "a"} pairs, stripped; the cleaned list is what is stored."""
    if not isinstance(value, list) or len(value) > FAQ_MAX_ITEMS:
        return False, None
    cleaned = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"q", "a"}:
            return False, None
        if not isinstance(item["q"], str) or not isinstance(item["a"], str):
            return False, None
        q, a = item["q"].strip(), item["a"].strip()
        if not 1 <= len(q) <= FAQ_Q_MAX or not 1 <= len(a) <= FAQ_A_MAX:
            return False, None
        cleaned.append({"q": q, "a": a})
    return True, cleaned


def _ingredients(value):
    """A list of {"name", "line", "photo_slot"}, stripped; the cleaned list is what is stored.
    A photo slot that is empty today is accepted: the storefront shows no photo for it."""
    if not isinstance(value, list) or len(value) > INGREDIENT_MAX_ITEMS:
        return False, None
    cleaned = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"name", "line", "photo_slot"}:
            return False, None
        name, line, slot = item["name"], item["line"], item["photo_slot"]
        if not isinstance(name, str) or not isinstance(line, str):
            return False, None
        if slot is not None and not (_whole(slot) and 1 <= slot <= IMAGE_SLOTS):
            return False, None
        name, line = name.strip(), line.strip()
        if not 1 <= len(name) <= INGREDIENT_NAME_MAX or len(line) > INGREDIENT_LINE_MAX:
            return False, None
        cleaned.append({"name": name, "line": line, "photo_slot": slot})
    return True, cleaned


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
    **{name: _text(0, 5000) for name in DETAIL_FIELDS},
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
        if name == "servings_per_pack":  # null clears it
            if value is None or (_whole(value) and 1 <= value <= SERVINGS_MAX):
                cleaned[name] = value
            else:
                errors[name] = FIELD_MESSAGES[name]
            continue
        if name == "faq":
            ok, result = _faq(value)
        elif name == "ingredients":
            ok, result = _ingredients(value)
        elif name in WHOLE_NUMBER_MAX:
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


# One slot is the Medium box volume / 100 (TWCinc app/shops/services/dimensions.py SLOT_CM3,
# an inferred figure there too). Only the volume part of TWCinc's derivation is ported: the
# smallest-box fit floor needs the package sizes, which apps/shop/zones.py does not carry.
SLOT_CM3 = Decimal("317.4")
SLOT_STEP = Decimal("0.1")
SLOT_MAX = Decimal("9999.99")  # what the volume_slot column holds
SIZES_TOO_BIG_MSG = "These sizes are too large to ship. Check the length, width and height."


def slot_from_sizes(length, width, height):
    """The volume slot of a product of these sizes in cm: length x width x height over
    SLOT_CM3, rounded up to SLOT_STEP. The one formula, used by the shipping preview and by
    the product writes, so a preview and a save always agree."""
    return (length * width * height / SLOT_CM3).quantize(SLOT_STEP, rounding=ROUND_CEILING)


def _derive_slot(cleaned, existing=None):
    """Make cleaned["volume_slot"] the slot of the sizes the write leaves. A slot sent by the
    client is never trusted (the key is still accepted so older clients do not error).
    Create (existing None): all three sizes give the derived slot; none leave it null.
    Update: sizes given give the derived slot of the resulting three; sizes not given leave the
    stored slot alone (a product measured before sizes existed keeps its slot). Updates cannot
    clear sizes (a null is refused)."""
    cleaned.pop("volume_slot", None)
    if existing is not None and not any(name in cleaned for name in DIM_FIELDS):
        return
    sizes = [cleaned.get(name, getattr(existing, name, None)) for name in DIM_FIELDS]
    if any(size is None for size in sizes):
        if existing is None:
            cleaned["volume_slot"] = None
        return
    slot = slot_from_sizes(*sizes)
    if slot > SLOT_MAX:
        raise _invalid({name: SIZES_TOO_BIG_MSG for name in DIM_FIELDS})
    cleaned["volume_slot"] = slot


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


def _next_display_order(catalog):
    """The position after the last product of the catalog. Only inside a write that holds the
    Catalog row lock."""
    highest = catalog.products.aggregate(highest=Max("display_order"))["highest"]
    return 0 if highest is None else highest + 1


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
    _derive_slot(cleaned)
    catalog = _locked_catalog(catalog_key_for(site))
    if CatalogProduct.objects.filter(catalog=catalog, code=cleaned["code"]).exists():
        raise _code_taken()
    try:
        with transaction.atomic():
            product = CatalogProduct.objects.create(
                catalog=catalog, revision=1, is_active=True,
                display_order=_next_display_order(catalog), **cleaned,
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
    _derive_slot(cleaned, product)
    changed = {name: value for name, value in cleaned.items() if getattr(product, name) != value}
    if not changed:
        return product
    for name, value in changed.items():
        setattr(product, name, value)
    product.revision += 1
    product.save(update_fields=[*changed, "revision", "updated_at"])
    _append_revision(product, **_owner_writer(account, site))
    return product


ORDER_MSG = (
    "Send every product of the catalog exactly once. The catalog may have changed, so reload "
    "the list and try again."
)


@transaction.atomic
def reorder_products(account, site, product_ids):
    """Arrange: set the shop position of every product (active and inactive) to its place in
    `product_ids`, which must name each product of the catalog exactly once. The Catalog row
    and every product row are locked, so two reorders and a create queue. Writes no revision:
    the order is not a product value, and a revision bump would make a form open in another tab
    stale. A product left in its place is not written."""
    site = _writer_site(account, site)
    catalog = _locked_catalog(catalog_key_for(site))
    products = list(
        CatalogProduct.objects.select_for_update(of=("self",)).filter(catalog=catalog).order_by("pk")
    )
    if len(set(product_ids)) != len(product_ids) or set(product_ids) != {
        product.public_id for product in products
    }:
        raise _invalid({"product_ids": ORDER_MSG})
    place = {public_id: number for number, public_id in enumerate(product_ids, start=1)}
    moved = []
    for product in products:
        if product.display_order != place[product.public_id]:
            product.display_order = place[product.public_id]
            moved.append(product)
    CatalogProduct.objects.bulk_update(moved, ["display_order"])
    return list_for(site)


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


def _slot_field(slot):
    """The model field of photo slot 1 to 5."""
    if not _whole(slot) or not 1 <= slot <= IMAGE_SLOTS:
        raise _invalid({"slot": SLOT_MSG})
    return IMAGE_FIELDS[slot - 1]


def set_image(account, site, product_id, upload, revision, slot=1):
    """Store `upload` as the product's photo in `slot` (1 to 5), replacing any there. A write
    that is not allowed (not the owner site, no store, a bad file) stores nothing. The old file
    is removed once the change is committed; a new file whose change failed is removed at once.
    Only that slot changes."""
    field = _slot_field(slot)
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
            old_key = getattr(product, field)
            setattr(product, field, key)
            product.revision += 1
            product.save(update_fields=[field, "revision", "updated_at"])
            _append_revision(product, **_owner_writer(account, site))
            transaction.on_commit(lambda: media.discard(old_key))
    except BaseException:
        media.discard(key)
        raise
    return product


@transaction.atomic
def clear_image(account, site, product_id, revision, slot=1):
    """Remove the product's photo in `slot`, and only that slot. A slot with none is returned
    as it is, writing no revision. The stored file is removed once the change is committed."""
    field = _slot_field(slot)
    site = _writer_site(account, site)
    product = _locked_product(site, product_id, revision)
    old_key = getattr(product, field)
    if not old_key:
        return product
    setattr(product, field, "")
    product.revision += 1
    product.save(update_fields=[field, "revision", "updated_at"])
    _append_revision(product, **_owner_writer(account, site))
    transaction.on_commit(lambda: media.discard(old_key))
    return product


# The shipping preview (read-only; nothing is stored)

PREVIEW_NCR = {"city": "Metro Manila"}
PREVIEW_MINDANAO = {"province": "Davao del Sur"}
PREVIEW_REASONS = {
    "weight": "Enter the weight in kg (above 0) to see the shipping fee.",
    "dims": "Enter length, width and height together (each above 0) to see the shipping fee.",
    "qty": "Enter the units per box (1 or more) to see the shipping fee.",
    "table": "This load is above the shipping rate table, so no fee can be shown.",
    "zero": "Customers are not charged shipping yet; this is the fee that would apply.",
}


def _billable_kg(total_weight, total_slots):
    """The chargeable kg of the packages fee_centavos picks for this load: a pouch bills its
    weight, a box at least its minimum. Same package choice as apps/shop/shipping.fee_centavos."""
    for pkg in zones.PACKAGING_ORDER:
        if total_slots <= zones.SLOT_CAPACITY[pkg]:
            packages = [(pkg, total_weight)]
            break
    else:
        boxes = int(ceil(total_slots / zones.SLOT_CAPACITY["BL"])) or 1
        packages = [("BL", total_weight / Decimal(boxes))] * boxes
    total = Decimal(0)
    for pkg, weight in packages:
        total += weight if pkg in zones.POUCHES else max(weight, zones.BOX_MIN_KG[pkg])
    return total


def shipping_preview(site, data):
    """The J&T fee for ONE unit to an NCR address and to a Mindanao address, from
    apps/shop/shipping.fee_centavos, with the billed weight. Writes nothing. Numbers are
    checked with the product's own rules (a bad one is a field error); a value that is well
    formed but cannot price the product (no weight, partly entered sizes, no units per box)
    gives null amounts and a plain reason."""
    if not is_writer(site):
        raise _read_only()
    data = {name: value for name, value in data.items() if value is not None}
    cleaned, errors = field_errors(data)
    if errors:
        raise _invalid(errors)
    empty = {"ncr_centavos": None, "mindanao_centavos": None, "billable_kg": None}
    weight = cleaned.get("weight_kg", Decimal(0))
    if weight <= 0:
        return {**empty, "reason": PREVIEW_REASONS["weight"]}
    # Checkout prices on the stored volume slot, which a save derives from the sizes; so with
    # all three sizes the preview derives it the same way. A slot alone (no sizes) is priced
    # as given.
    if all(name in cleaned for name in DIM_FIELDS):
        slot = slot_from_sizes(*(cleaned[name] for name in DIM_FIELDS))
    elif "volume_slot" in cleaned:
        slot = cleaned["volume_slot"]
    else:
        return {**empty, "reason": PREVIEW_REASONS["dims"]}
    if "shipping_qty" not in cleaned:
        return {**empty, "reason": PREVIEW_REASONS["qty"]}
    items = [shipping.ShippingItem(
        weight_kg=weight, volume_slot=slot, shipping_qty=cleaned["shipping_qty"], quantity=1
    )]
    try:
        ncr = shipping.fee_centavos(items, zones.dest_zone(PREVIEW_NCR))
        mindanao = shipping.fee_centavos(items, zones.dest_zone(PREVIEW_MINDANAO))
    except shipping.ShippingUnavailable:
        return {**empty, "reason": PREVIEW_REASONS["table"]}
    units = Decimal(cleaned["shipping_qty"])
    billable = _billable_kg(weight * units, slot * units)
    return {
        "ncr_centavos": ncr, "mindanao_centavos": mindanao,
        "billable_kg": _fixed(billable, 2),
        "reason": PREVIEW_REASONS["zero"] if shipping.policy() == C.ZERO else "",
    }


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
    if all(name in cleaned for name in DIM_FIELDS):  # sizes given: same formula as the API
        _derive_slot(cleaned)
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
                    display_order=_next_display_order(catalog), **cleaned,
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


# Product images set by an operator (manage.py catalog_set_images)

def image_outcome(product, replace):
    """What catalog_set_images does with one image file for `product` (None: no such product)."""
    if product is None:
        return "skip (no such product)"
    if product.image_key and not replace:
        return "skip (has image)"
    return "set"


def image_state(images, *, replace=False):
    """[(code, outcome)] for the image files in `images` ({code: (bytes, type)}), writing
    nothing. None while the catalog is switched off: the products are not read."""
    if not enabled():
        return None
    products = {
        product.code: product
        for product in CatalogProduct.objects.filter(
            catalog__template_key="sante", code__in=list(images)
        )
    }
    return [(code, image_outcome(products.get(code), replace)) for code in sorted(images)]


def set_images_as_operator(images, *, operator, replace=False):
    """Store `images` ({code: (bytes, content type)}) as the images of the Santé products of
    those codes. One transaction: all or none. A product that has an image is left alone unless
    `replace`. Each product set gets a revision with source "seed", `operator` (an active staff
    user) and, as the file hash, the SHA-256 of the image bytes. A replaced file is removed once
    committed; a new file whose change failed is removed at once. Returns what image_state gives,
    taken under the lock."""
    stored = []
    try:
        with transaction.atomic():
            catalog = Catalog.objects.select_for_update().filter(template_key="sante").first()
            products = {}
            if catalog is not None:
                products = {
                    product.code: product
                    for product in CatalogProduct.objects.select_for_update(of=("self",)).filter(
                        catalog=catalog, code__in=list(images)
                    )
                }
            result = []
            for code in sorted(images):
                product = products.get(code)
                outcome = image_outcome(product, replace)
                result.append((code, outcome))
                if outcome != "set":
                    continue
                content, content_type = images[code]
                key = media.save(content, content_type)
                stored.append(key)
                old_key = product.image_key
                product.image_key = key
                product.revision += 1
                product.save(update_fields=["image_key", "revision", "updated_at"])
                _append_revision(
                    product,
                    source=CatalogProductRevision.SEED,
                    changed_by_operator_user_id=operator.pk,
                    changed_by_operator_username=operator.get_username(),
                    seed_file_sha256=hashlib.sha256(content).hexdigest(),
                )
                transaction.on_commit(lambda old_key=old_key: media.discard(old_key))
    except BaseException:
        for key in stored:
            media.discard(key)
        raise
    return result
