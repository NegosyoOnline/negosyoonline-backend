"""The delivery form's ZIP lookup (port of TWCinc's address_zip.py).

It reads the dataset in `data/` and the province list beside it (copies of TWCinc's reference
data, see `data/README.md`) and nothing else. Order: exact barangay, then a ZIP learned from an earlier buyer or member (`learned`), then a city with one ZIP
(`city_unique`), then a city's main ZIP (`city_approx`), then nothing. A ZIP typed for a place the lookup could not
answer is remembered after a successful address save (`learn_saved_address`; the first value wins and a later
different one is only counted), so the next person there is not asked. The normalizers and the result shape are
copies of the shipping lookup's, so the page's script and the registration read what they read before."""
import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.db import transaction

log = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_PATH = DATA_DIR / "zip_codes.json"
# The browser picker serves a copy of this same file, so the province spellings the page posts are the ones
# the region lookup knows.
PROVINCES_PATH = DATA_DIR / "provinces.json"
ZIP_RE = re.compile(r"^\d{4}$")
AUTHORITATIVE = ("barangay", "learned", "city_unique")  # the form hides its ZIP field for these

PROVINCE_ERROR = "Select a province from the list."
ZIP_ERROR = "Enter the 4-digit ZIP code for this address."

# Legacy PSGC province strings that mean METRO MANILA, matched on the normalized form.
_NCR_ALIASES = {"NCR", "METRO MANILA", "CITY OF MANILA", "NCR CITY OF MANILA FIRST DISTRICT", "NCR SECOND DISTRICT",
                "NCR THIRD DISTRICT", "NCR FOURTH DISTRICT"}
REGION_LABELS = {
    "01": "REGION I", "02": "REGION II", "03": "REGION III", "04": "REGION IV-A", "17": "REGION IV-B", "05": "REGION V",
    "06": "REGION VI", "07": "REGION VII", "08": "REGION VIII", "09": "REGION IX", "10": "REGION X", "11": "REGION XI",
    "12": "REGION XII", "13": "NCR", "14": "CAR", "15": "ARMM", "16": "CARAGA",
}


def norm(value):
    return " ".join(str(value or "").strip().upper().replace("-", " ").replace(",", " ").split())


def norm_province(value):
    p = norm(value)
    return "METRO MANILA" if p in _NCR_ALIASES else p


def zip_key(province, city, barangay=None):
    parts = [norm_province(province), norm(city)]
    if barangay is not None:
        parts.append(norm(barangay))
    return "::".join(parts)


@lru_cache(maxsize=1)
def _dataset():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return {"barangays": data.get("barangays", {}), "cities": data.get("cities", {}),
            "cities_approx": data.get("cities_approx", {})}


@lru_cache(maxsize=1)
def _province_regions():
    rows = json.loads(PROVINCES_PATH.read_text(encoding="utf-8"))
    return {norm_province(r["name"]): str(r["reg_code"]).strip() for r in rows if str(r.get("reg_code") or "").strip()}


def region_code_for_province(province):
    """The region code for a province as the picker spells it; None when unknown, never a guess."""
    key = norm_province(province)
    if not key:
        return None
    try:
        return _province_regions().get(key)
    except (OSError, ValueError, KeyError):
        log.exception("province file unreadable at %s", PROVINCES_PATH)
        return None


def region_label(code):
    code = str(code or "").strip()
    if len(code) == 1 and code.isdigit():
        code = "0" + code
    return REGION_LABELS.get(code, "")


def dataset_zip(province, city, barangay):
    """(zip, precision) from the copied file only; (None, None) on a miss or an unreadable file, so a data problem never
    fails a checkout."""
    try:
        d = _dataset()
    except (OSError, ValueError):
        log.exception("zip dataset unreadable at %s", DATA_PATH)
        return None, None
    bkey, ckey = zip_key(province, city, barangay), zip_key(province, city)
    z = d["barangays"].get(bkey)
    if z:
        return z, "barangay"
    z = d["cities"].get(ckey)
    if z:
        return z, "city_unique"
    z = d["cities_approx"].get(ckey)
    if z:
        return z, "city_approx"
    return None, None


def learned_zip(province, city, barangay):
    """The ZIP an earlier save taught us for this place, or None. A database problem is a miss, never a failure."""
    from apps.pack.models import LearnedZipCode

    try:
        with transaction.atomic():
            return (LearnedZipCode.objects.filter(lookup_key=zip_key(province, city, barangay))
                    .values_list("zip_code", flat=True).first())
    except Exception:
        log.exception("learned zip lookup failed; treating as a miss")
        return None


def resolve_zip(province, city, barangay):
    """(zip, source, precision); source is "dataset", "learned" or None. Order: exact barangay, learned, then the
    dataset's city_unique and city_approx. A learned hit is authoritative."""
    z, precision = dataset_zip(province, city, barangay)
    if precision == "barangay":
        return z, "dataset", precision
    learned = learned_zip(province, city, barangay)
    if learned:
        return learned, "learned", "learned"
    if precision:
        return z, "dataset", precision
    return None, None, None


def is_valid_zip(value):
    return bool(ZIP_RE.match(str(value or "").strip()))


@dataclass
class DerivedAddress:
    region_code: str = ""
    region_label: str = ""
    zip_code: str = ""
    zip_source: str | None = None      # "dataset" | "learned" | "typed" | None
    zip_precision: str | None = None   # "barangay" | "learned" | "city_unique" | "city_approx" | None
    should_learn: bool = False         # a typed ZIP the lookup could not give, worth remembering after the save
    error_field: str | None = None
    error_message: str = ""

    @property
    def ok(self):
        return self.error_field is None


def derive_address_fields(province, city, barangay, typed_zip=""):
    """What the server stores for a delivery address: the region, and the ZIP the dataset gives for the barangay. A ZIP
    the dataset can say always wins over a typed one (the field was hidden, so a typed value is stale or tampered). Only
    a ZIP that is truly unknown blocks the save; a city's main ZIP never does."""
    code = region_code_for_province(province) or ""
    if not code:
        return DerivedAddress(error_field="province", error_message=PROVINCE_ERROR)
    label = region_label(code)
    try:
        zip_code, source, precision = resolve_zip(province, city, barangay)
    except Exception:
        log.exception("resolve_zip failed; treating as unresolved")
        zip_code, source, precision = None, None, None

    if precision in AUTHORITATIVE:
        return DerivedAddress(region_code=code, region_label=label, zip_code=zip_code, zip_source=source,
                              zip_precision=precision)
    typed = str(typed_zip or "").strip()
    if is_valid_zip(typed):
        return DerivedAddress(region_code=code, region_label=label, zip_code=typed, zip_source="typed",
                              zip_precision=precision, should_learn=typed != (zip_code or ""))
    if precision == "city_approx":
        return DerivedAddress(region_code=code, region_label=label, zip_code=zip_code, zip_source=source,
                              zip_precision=precision)
    return DerivedAddress(region_code=code, region_label=label, error_field="postal_code", error_message=ZIP_ERROR)


def record_learned_zip(province, city, barangay, zip_code, source):
    """Remember a typed ZIP for the place (adapted from TWCinc shipping/services/zipcodes.py). The first value wins; the
    same value again counts a sighting; a different one never replaces it and is only counted. The nested atomic()
    is a savepoint, so a duplicate-key retry cannot poison a caller's transaction. Returns the row or None."""
    from apps.pack.models import LearnedZipCode

    zip_code = str(zip_code or "").strip()
    if not is_valid_zip(zip_code):
        return None
    with transaction.atomic():
        row, created = LearnedZipCode.objects.get_or_create(
            lookup_key=zip_key(province, city, barangay),
            defaults={"province": norm_province(province)[:120], "city": norm(city)[:120],
                      "barangay": norm(barangay)[:255], "zip_code": zip_code, "source": source})
        if created:
            return row
        if row.zip_code == zip_code:
            row.times_seen += 1
            row.save(update_fields=["times_seen", "updated"])
        else:
            row.conflict_zip, row.conflict_count = zip_code, row.conflict_count + 1
            row.save(update_fields=["conflict_zip", "conflict_count", "updated"])
    return row


def learn_saved_address(address, source):
    """Call AFTER an address was saved. Learns the saved ZIP when it is a valid typed one the lookup could not give
    (or that differs from a city's approximate suggestion). It never raises and never touches the save."""
    try:
        if not isinstance(address, dict):
            return
        p, c, b = address.get("province"), address.get("city"), address.get("barangay")
        if not (p and c and b):
            return
        derived = derive_address_fields(p, c, b, address.get("zip") or address.get("postal_code") or "")
        if derived.ok and derived.should_learn:
            record_learned_zip(p, c, b, derived.zip_code, source)
    except Exception:
        log.exception("learning a typed ZIP failed; the address save stands")
