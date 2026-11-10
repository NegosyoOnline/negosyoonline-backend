"""Philippine mobile numbers, stored as +639XXXXXXXXX."""
import re

_STRIP = re.compile(r"[\s\-().]")
_FULL = re.compile(r"^639\d{9}$")


def normalise_ph_mobile(raw):
    digits = _STRIP.sub("", raw or "").removeprefix("+")
    if re.fullmatch(r"09\d{9}", digits):
        digits = "63" + digits[1:]
    elif re.fullmatch(r"9\d{9}", digits):
        digits = "63" + digits
    if not _FULL.match(digits):
        raise ValueError("Enter a valid Philippine mobile number.")
    return "+" + digits
