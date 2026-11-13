"""J&T zone and rate data for the shop's shipping fee, copied from TWCinc's
negosyoonline_storefront/shipping.py (the legacy J&T NCR tables), re-keyed in integer
centavos. Data only; the fee logic is in shipping.py. Origin is Metro Manila only."""
from decimal import Decimal

ZONES = ("MANILA", "LUZON", "VISAYAS", "MINDANAO", "ISLAND")
ORIGIN = "MANILA"


def _peso_row(*pesos):
    return tuple(p * 100 for p in pesos)


# (max chargeable kg, then one rate per zone in ZONES order), centavos.
BOX_RATES = [
    (Decimal(kg), *_peso_row(*rates)) for kg, rates in [
        ("0.50", (85, 95, 100, 105, 115)), ("1.00", (115, 165, 180, 195, 205)),
        ("3.00", (155, 190, 200, 220, 230)), ("4.00", (200, 280, 300, 330, 340)),
        ("5.00", (220, 320, 370, 370, 380)), ("6.00", (255, 375, 435, 435, 445)),
        ("7.00", (295, 435, 505, 505, 515)), ("8.00", (335, 495, 575, 575, 585)),
        ("9.00", (375, 555, 645, 645, 655)), ("10.00", (415, 615, 715, 715, 725)),
        ("11.00", (455, 675, 785, 785, 795)), ("12.00", (495, 735, 855, 855, 865)),
        ("13.00", (535, 795, 925, 925, 935)), ("14.00", (575, 855, 995, 995, 1005)),
        ("15.00", (615, 915, 1065, 1065, 1075)), ("16.00", (655, 975, 1135, 1135, 1145)),
        ("17.00", (695, 1035, 1205, 1205, 1215)), ("18.00", (735, 1095, 1275, 1275, 1285)),
        ("19.00", (775, 1155, 1345, 1345, 1355)), ("20.00", (815, 1215, 1415, 1415, 1425)),
        ("21.00", (855, 1275, 1485, 1485, 1495)),
    ]
]

# Slot capacity per packaging, smallest first.
PACKAGING_ORDER = ("PS", "PM", "PL", "BXS", "BS", "BM", "BL")
SLOT_CAPACITY = {
    "PS": Decimal("2.4"), "PM": Decimal("7"), "PL": Decimal("15"), "BXS": Decimal("24"),
    "BS": Decimal("43"), "BM": Decimal("100"), "BL": Decimal("250"),
}
POUCHES = ("PS", "PM", "PL")
BOX_FEE = {"BXS": 3000, "BS": 4000, "BM": 5000, "BL": 8000}  # centavos
BOX_MIN_KG = {"BXS": Decimal("3"), "BS": Decimal("4"), "BM": Decimal("6"), "BL": Decimal("10")}
# Pouch rates from the Metro Manila origin, per zone in ZONES order, centavos.
POUCH_RATES = {
    "PS": _peso_row(60, 80, 90, 95, 110),
    "PM": _peso_row(80, 120, 150, 160, 175),
    "PL": _peso_row(100, 160, 180, 190, 205),
}
VALUATION = 500  # centavos, added once per order

_VISAYAS = ("CEBU", "ILOILO", "BACOLOD", "DUMAGUETE", "BOHOL", "LEYTE", "SAMAR", "AKLAN", "CAPIZ",
            "ANTIQUE", "NEGROS", "BILIRAN", "EASTERN SAMAR", "NORTHERN SAMAR", "WESTERN SAMAR",
            "SIQUIJOR")
_MINDANAO = ("DAVAO", "CDO", "CAGAYAN DE ORO", "ZAMBOANGA", "GENERAL SANTOS", "GENSAN", "COTABATO",
             "BUTUAN", "SURIGAO", "ILIGAN", "DIPOLOG", "OZAMIZ", "PAGADIAN", "DAPITAN", "MARAWI",
             "MATI", "TAGUM", "KORONADAL", "KIDAPAWAN")
_ISLAND = ("PALAWAN", "BATANES", "CATANDUANES", "MASBATE", "ROMBLON", "MARINDUQUE", "CAMIGUIN",
           "SIARGAO", "DINAGAT")
_MANILA = ("MANILA", "QUEZON CITY", "MAKATI", "PASIG", "TAGUIG", "MARIKINA", "MANDALUYONG", "PASAY",
           "PARAÑAQUE", "PARANAQUE", "LAS PIÑAS", "LAS PINAS", "MUNTINLUPA", "CALOOCAN", "MALABON",
           "NAVOTAS", "VALENZUELA", "PATEROS", "SAN JUAN", "NCR", "METRO MANILA")


def dest_zone(address):
    """The destination zone from an address's city and province. No address, or neither field:
    the Metro Manila estimate. Visayas, Mindanao, island and Manila names are matched in that
    order; anything else is Luzon (TWCinc's order)."""
    if not address:
        return "MANILA"
    text = " ".join(filter(None, [address.get("city"), address.get("province")])).upper()
    if not text:
        return "MANILA"
    for words, zone in (
        (_VISAYAS, "VISAYAS"), (_MINDANAO, "MINDANAO"), (_ISLAND, "ISLAND"), (_MANILA, "MANILA")
    ):
        if any(word in text for word in words):
            return zone
    return "LUZON"
