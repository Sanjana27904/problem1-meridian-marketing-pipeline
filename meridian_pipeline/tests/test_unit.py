"""Unit tests: no database needed. Run:  pytest tests/test_unit.py"""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from meridian.cleaning import (clean_campaign_name, clean_email, clean_phone, clean_status,
                               normalise_order_total, parse_date, parse_money, parse_timestamp, to_int)
from meridian.dedupe import latest_wins
from meridian.extract import AdsApiClient, ApiError
from meridian.validation import validate_ad, validate_order


# 1. phone cleaning ---------------------------------------------------------
@pytest.mark.parametrize("raw", ["9876543210", "+91 98765 43210", "+919876543210", "098765 43210",
                                 "09876543210", "98765-43210", "91 9876543210", "00 91 9876543210"])
def test_phone_many_formats_become_one_10_digit_form(raw):
    assert clean_phone(raw) == "9876543210"


@pytest.mark.parametrize("raw", ["", None, "12345", "00000 00000", "1234567890"])
def test_bad_phone_becomes_none(raw):
    assert clean_phone(raw) is None


# 2. paise -> rupees --------------------------------------------------------
def test_paise_total_is_converted_to_rupees():
    total, was_paise = normalise_order_total(Decimal("129900.00"), Decimal("1299.00"))
    assert (total, was_paise) == (Decimal("1299.00"), True)


def test_normal_total_is_left_alone():
    assert normalise_order_total(Decimal("1299.00"), Decimal("1299.00")) == (Decimal("1299.00"), False)


# 3. spend text -> number ---------------------------------------------------
def test_spend_text_becomes_number():
    assert parse_money("₹1,250.50") == Decimal("1250.50")
    assert parse_money(1250.5) == Decimal("1250.50")
    assert parse_money("N/A") is None


# 4. date + timezone parsing --------------------------------------------------
def test_two_date_formats_give_same_date():
    assert parse_date("2026-10-21") == parse_date("21/10/2026") == date(2026, 10, 21)
    assert parse_date("31/02/2026") is None            # not a real day


def test_ist_and_utc_timestamps_meet_at_the_same_instant():
    ist = parse_timestamp("2026-10-25 14:30:00")           # no zone -> assumed IST
    utc = parse_timestamp("2026-10-25T09:00:00Z")
    assert ist == utc == datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)


# 5. dedupe: latest version wins -----------------------------------------------
def test_latest_version_wins():
    rows = [{"id": "A", "v": 1, "ts": 1}, {"id": "A", "v": 3, "ts": 3}, {"id": "A", "v": 2, "ts": 2}, {"id": "B", "v": 9, "ts": 1}]
    winners, losers = latest_wins(rows, lambda r: r["id"], lambda r: r["ts"])
    assert {w["id"]: w["v"] for w in winners} == {"A": 3, "B": 9}
    assert len(losers) == 2                              # nothing vanishes silently


# 6. names / status / email -----------------------------------------------------
def test_campaign_name_and_status_spelling_are_unified():
    assert clean_campaign_name("  Festive_SALE_meta_01 ") == "festive_sale_meta_01"
    assert clean_status("CANCELED") == clean_status("Cancelled ") == "cancelled"
    assert clean_email(" JOHN@Gmail.COM ") == "john@gmail.com"
    assert clean_email("john@@gmail.com") is None
    assert to_int("2") == 2


# 7. validation rules ------------------------------------------------------------
def _ad(**kw):
    base = dict(campaign_id="C1", campaign_name="x", platform="Meta", ad_date=date(2026, 10, 1), spend=Decimal("10"),
                impressions=100, clicks=5, updated_at=datetime(2026, 10, 2, tzinfo=timezone.utc), raw_date="", raw_updated_at="",
                raw_platform="", raw_spend="", raw_impressions="", raw_clicks="")
    base.update(kw)
    return base


def test_rule_clicks_cannot_exceed_impressions():
    assert [i.rule for i in validate_ad(_ad(clicks=500))] == ["ads_clicks_le_impressions"]
    assert validate_ad(_ad()) == []


def test_rule_negative_spend_rejected():
    assert [i.rule for i in validate_ad(_ad(spend=Decimal("-1")))] == ["ads_spend_non_negative"]


def test_rule_order_total_must_match_items_within_one_rupee():
    base = dict(order_id="O1", order_ts=datetime(2026, 10, 1, tzinfo=timezone.utc), updated_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                status="placed", payment_mode="UPI", phone_provided=False, phone=None, raw_phone=None,
                raw_order_ts="", raw_updated_at="", raw_status="", raw_payment_mode="",
                items=[{"line_no": 1, "sku": "S", "qty": 2, "unit_price": Decimal("100")}], items_total=Decimal("200"))
    assert validate_order({**base, "total": Decimal("200.50")}) == []
    assert [i.rule for i in validate_order({**base, "total": Decimal("260")})] == ["order_total_matches_items"]


# 8. API retries with a GROWING wait ---------------------------------------------
class _Resp:
    def __init__(self, code, body=None):
        self.status_code, self._b, self.text = code, body, ""

    def json(self):
        return self._b


class _Session:
    def __init__(self, codes):
        self.codes = list(codes)

    def get(self, url, params=None, timeout=None):
        code = self.codes.pop(0)
        return _Resp(code, {"data": [{"a": 1}], "total_pages": 1} if code == 200 else None)


def test_api_retries_with_growing_wait():
    waits = []
    c = AdsApiClient("http://x", base_delay=0.5, jitter=0, session=_Session([429, 500, 200]), sleep=waits.append)
    assert list(c.fetch_all()) == [{"a": 1}]
    assert waits == [0.5, 1.0]                           # doubles each time
    assert c.stats["http_429"] == 1 and c.stats["http_5xx"] == 1


def test_api_gives_up_after_max_retries():
    c = AdsApiClient("http://x", max_retries=2, jitter=0, session=_Session([500, 500, 500]), sleep=lambda s: None)
    with pytest.raises(ApiError):
        list(c.fetch_all())
