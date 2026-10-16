"""Money helpers. All math uses Decimal; floats never touch an amount."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


def parse_money(text):
    """Parse '$1,234.5' -> Decimal('1234.50'). Raises ValueError on bad input."""
    cleaned = str(text).strip().replace("$", "").replace(",", "").replace(" ", "")
    if not cleaned:
        raise ValueError("Enter an amount, e.g. 250 or 1,250.00")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"'{text}' is not a valid amount")
    if not value.is_finite():
        raise ValueError(f"'{text}' is not a valid amount")
    if value.as_tuple().exponent < -2:
        raise ValueError("Use at most 2 decimal places")
    return value.quantize(CENT)


def parse_rate(text):
    """Parse a tax rate like '8.25' or '8.25%' -> Decimal('8.25')."""
    cleaned = str(text).strip().rstrip("%").strip()
    try:
        rate = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"'{text}' is not a valid tax rate")
    if not rate.is_finite() or rate < 0 or rate > 100:
        raise ValueError("Tax rate must be between 0 and 100")
    return rate


def compute_totals(prices, rate):
    """Return (subtotal, tax, total) with tax rounded to cents, half-up."""
    subtotal = sum((Decimal(p) for p in prices), Decimal("0.00")).quantize(CENT)
    tax = (subtotal * Decimal(rate) / Decimal(100)).quantize(CENT, rounding=ROUND_HALF_UP)
    return subtotal, tax, subtotal + tax


def fmt_money(value):
    """Decimal('1234.5') -> '$1,234.50'; negatives -> '-$1,234.50'."""
    value = Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def fmt_rate(rate):
    """Decimal('8.250') -> '8.25', Decimal('8.00') -> '8'."""
    return format(Decimal(rate).normalize(), "f")
