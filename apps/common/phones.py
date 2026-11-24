"""Philippine mobile numbers, stored as +639XXXXXXXXX."""
import re

_STRIP = re.compile(r"[\s\-().]")
_FULL = re.compile(r"^639\d{9}$")
# A number a spreadsheet turned into scientific notation, such as 6.39563E+11. The digits are
# lost for good, so nothing here ever tries to rebuild them.
_SHORTENED = re.compile(r"\s*\d+(?:\.\d+)?[Ee]\+?\d+\s*")

SHORTENED_REASON = "Excel shortened this mobile number."


def is_shortened_by_excel(raw):
    return isinstance(raw, str) and _SHORTENED.fullmatch(raw) is not None


def normalise_ph_mobile(raw):
    digits = _STRIP.sub("", raw or "").removeprefix("+")
    if re.fullmatch(r"09\d{9}", digits):
        digits = "63" + digits[1:]
    elif re.fullmatch(r"9\d{9}", digits):
        digits = "63" + digits
    if not _FULL.match(digits):
        raise ValueError("Enter a valid Philippine mobile number.")
    return "+" + digits
