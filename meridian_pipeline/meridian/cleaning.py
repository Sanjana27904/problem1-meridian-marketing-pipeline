"""Cleaning helpers: small pure functions, each does ONE job and is easy to unit-test.

Nothing in this file touches the database or the network.
"""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

ALLOWED_STATUSES = ("placed", "confirmed", "shipped", "delivered", "cancelled", "returned")
ALLOWED_PAYMENT_MODES = ("UPI", "CARD", "COD")
ALLOWED_PLATFORMS = ("Meta", "Google")

_PHONE_OK = re.compile(r"[6-9]\d{9}")  # Indian mobile numbers start with 6-9
_EMAIL_OK = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9\-]+(\.[a-z0-9\-]+)*\.[a-z]{2,}")
_MONEY_JUNK = re.compile(r"(₹|rs\.?|inr|,|\s)", re.IGNORECASE)
_MONEY_OK = re.compile(r"-?\d+(\.\d+)?")
_TZ_SUFFIX = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")

_TS_FORMATS = (
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d-%m-%Y %I:%M %p",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
)
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")

_STATUS_FIX = {"canceled": "cancelled"}
_PAYMENT_MAP = {
    "upi": "UPI",
    "card": "CARD", "credit card": "CARD", "debit card": "CARD",
    "cod": "COD", "cash on delivery": "COD",
}
_PLATFORM_MAP = {
    "meta": "Meta", "facebook": "Meta", "instagram": "Meta",
    "google": "Google", "google ads": "Google",
}


# ---------------------------------------------------------------- phone / email
def clean_phone(value):
    """Reduce any phone format to a 10-digit Indian mobile number, or None.

    '+91 98765 43210' -> '9876543210'   '098765 43210' -> '9876543210'
    '12345' -> None
    """
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    digits = re.sub(r"\D", "", str(value))
    if digits.startswith("00"):          # international prefix 00
        digits = digits[2:]
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 13 and digits.startswith("091"):
        digits = digits[3:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if _PHONE_OK.fullmatch(digits) else None


def clean_email(value):
    """Lower-case + trim; return None when the address is not valid."""
    if value is None:
        return None
    s = str(value).strip().lower()
    return s if s and _EMAIL_OK.fullmatch(s) else None


# ---------------------------------------------------------------- numbers
def parse_money(value):
    """Turn 1250.5, '1250.50' or '₹1,250.50' into Decimal('1250.50'). Bad input -> None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float, Decimal)):
            d = Decimal(str(value))
        else:
            s = _MONEY_JUNK.sub("", str(value))
            if not _MONEY_OK.fullmatch(s):
                return None
            d = Decimal(s)
        return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def to_int(value):
    """'2' -> 2, 2.0 -> 2, '2.5' -> None, None -> None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    s = str(value).strip().replace(",", "")
    if re.fullmatch(r"[+-]?\d+", s):
        return int(s)
    if re.fullmatch(r"[+-]?\d+\.0+", s):
        return int(float(s))
    return None


def normalise_order_total(total_raw, items_total):
    """Detect totals that were exported in paise and convert them to rupees.

    The export has no flag for paise, so we compare the total against the sum of
    its items: if total ~= items * 100 (within 1 rupee) it must be paise.
    Returns (total_in_rupees, was_paise).
    """
    if total_raw is None:
        return None, False
    if (
        items_total is not None
        and items_total > 0
        and abs(total_raw - items_total) > 1
        and abs(total_raw - items_total * 100) <= 100
    ):
        return (total_raw / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), True
    return total_raw, False


# ---------------------------------------------------------------- dates
def parse_date(value):
    """'2026-10-21' or '21/10/2026' -> date(2026, 10, 21). Invalid -> None.

    We read dd/mm/yyyy (Indian style), never mm/dd/yyyy.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def has_timezone(value):
    """True if a timestamp string carries its own zone ('Z' or '+05:30')."""
    return bool(value) and bool(_TZ_SUFFIX.search(str(value).strip()))


def parse_timestamp(value, assume_tz=IST):
    """Parse many timestamp formats and return a timezone-aware UTC datetime.

    * a string WITH a zone ('...Z', '...+05:30') keeps that zone
    * a string WITHOUT a zone is assumed to be `assume_tz` (IST for our CRM/store)
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        dt = None
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            for fmt in _TS_FORMATS:
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=assume_tz)
    dt = dt.astimezone(UTC)
    return dt if 2000 <= dt.year <= 2100 else None


def to_ist_date(dt):
    """Business day of a UTC timestamp, in Indian time."""
    return dt.astimezone(IST).date() if dt else None


# ---------------------------------------------------------------- text codes
def clean_campaign_name(value):
    """' Festive_Sale_META_01 ' -> 'festive_sale_meta_01' (lower, trimmed, one space max)."""
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip().lower()
    return s or None


def clean_platform(value):
    if value is None:
        return None
    return _PLATFORM_MAP.get(re.sub(r"\s+", " ", str(value)).strip().lower())


def clean_status(value):
    """'CANCELED', 'Cancelled ' -> 'cancelled'. Unknown words are kept (lower-case)
    so the validation rule can see them and reject the row."""
    if value is None:
        return None
    s = str(value).strip().lower()
    return _STATUS_FIX.get(s, s) or None


def clean_payment_mode(value):
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip().lower()
    return _PAYMENT_MAP.get(s)
