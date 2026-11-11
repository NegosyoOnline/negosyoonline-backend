"""How much of a prepaid term is earned at a date. A pure function: it writes nothing, is used
by nothing yet and is the figure the statements or the accounting build will read."""
from datetime import date


def earned_centavos(price_centavos, start_date, end_date, as_of_date):
    """round(price x days elapsed / days in the term), counted in Manila dates with the start
    date included, nothing before the start and exactly `price_centavos` at the end."""
    if not all(isinstance(d, date) for d in (start_date, end_date, as_of_date)):
        raise TypeError("Dates are needed.")
    term = (end_date - start_date).days
    if term <= 0:
        raise ValueError("A term has at least one day.")
    elapsed = min(max((as_of_date - start_date).days + 1, 0), term)
    # Integer rounding half up, so no float touches money.
    return (price_centavos * elapsed * 2 + term) // (term * 2)
