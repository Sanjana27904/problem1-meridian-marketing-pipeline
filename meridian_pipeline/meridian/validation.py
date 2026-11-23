"""Validation rules (23 of them). Each rule has a name that ends up in rejected_rows.

Two kinds of outcome:
  rejected      -> the row is NOT trusted; it goes to rejected_rows and is kept out of staging/final
  field_nulled  -> the row is fine, one bad field (phone/email) is blanked; the problem is still logged
"""
from dataclasses import dataclass

from .cleaning import ALLOWED_PAYMENT_MODES, ALLOWED_PLATFORMS, ALLOWED_STATUSES

REJECTED = "rejected"
FIELD_NULLED = "field_nulled"


@dataclass
class Issue:
    rule: str
    reason: str
    action: str = REJECTED


# (rule name, source, action, plain-English description) -- used by README and tests
RULES = [
    ("ads_required_fields", "ads", REJECTED, "campaign_id and campaign_name must be present"),
    ("ads_date_valid", "ads", REJECTED, "date must parse (yyyy-mm-dd or dd/mm/yyyy) and be a real calendar date"),
    ("ads_updated_at_valid", "ads", REJECTED, "updated_at must parse - we need it to pick the latest version"),
    ("ads_platform_allowed", "ads", REJECTED, "platform must be Meta or Google"),
    ("ads_spend_parsable", "ads", REJECTED, "spend must be a number or text like '₹1,250.50'"),
    ("ads_spend_non_negative", "ads", REJECTED, "spend must not be negative"),
    ("ads_counts_valid", "ads", REJECTED, "impressions and clicks must be whole numbers >= 0"),
    ("ads_clicks_le_impressions", "ads", REJECTED, "clicks cannot be higher than impressions"),
    ("lead_id_present", "leads", REJECTED, "lead_id must be present"),
    ("lead_created_at_valid", "leads", REJECTED, "created_at must parse"),
    ("lead_has_contact", "leads", REJECTED, "lead needs at least one valid phone or email"),
    ("lead_phone_valid", "leads", FIELD_NULLED, "a phone that is given must be a valid 10-digit Indian mobile"),
    ("lead_email_valid", "leads", FIELD_NULLED, "an email that is given must look like an email"),
    ("order_id_present", "orders", REJECTED, "order_id must be present"),
    ("order_ts_valid", "orders", REJECTED, "order_ts must parse"),
    ("order_updated_at_valid", "orders", REJECTED, "updated_at must parse"),
    ("order_status_allowed", "orders", REJECTED, "status must be placed/confirmed/shipped/delivered/cancelled/returned"),
    ("order_payment_mode_allowed", "orders", REJECTED, "payment_mode must be UPI, card or COD"),
    ("order_has_items", "orders", REJECTED, "an order needs at least one item"),
    ("order_sku_present", "orders", REJECTED, "every item needs a sku"),
    ("order_qty_positive", "orders", REJECTED, "every item quantity must be a whole number above zero"),
    ("order_unit_price_valid", "orders", REJECTED, "every unit_price must be a number >= 0"),
    ("order_total_matches_items", "orders", REJECTED, "order total must equal sum of items within Rs 1"),
    ("order_phone_valid", "orders", FIELD_NULLED, "a customer phone that is given must be valid (else order stays, unmatched)"),
]


def validate_ad(r):
    issues = []
    if not r["campaign_id"] or not r["campaign_name"]:
        issues.append(Issue("ads_required_fields", "campaign_id or campaign_name is missing"))
    if r["ad_date"] is None:
        issues.append(Issue("ads_date_valid", f"date {r['raw_date']!r} is not a valid date"))
    if r["updated_at"] is None:
        issues.append(Issue("ads_updated_at_valid", f"updated_at {r['raw_updated_at']!r} could not be parsed"))
    if r["platform"] not in ALLOWED_PLATFORMS:
        issues.append(Issue("ads_platform_allowed", f"platform {r['raw_platform']!r} is not Meta/Google"))
    if r["spend"] is None:
        issues.append(Issue("ads_spend_parsable", f"spend {r['raw_spend']!r} is not a number"))
    elif r["spend"] < 0:
        issues.append(Issue("ads_spend_non_negative", f"spend is negative ({r['spend']})"))
    imp, clk = r["impressions"], r["clicks"]
    if imp is None or clk is None or imp < 0 or clk < 0:
        issues.append(Issue("ads_counts_valid", f"impressions={r['raw_impressions']!r} clicks={r['raw_clicks']!r} invalid"))
    elif clk > imp:
        issues.append(Issue("ads_clicks_le_impressions", f"clicks ({clk}) are higher than impressions ({imp})"))
    return issues


def validate_lead(r):
    issues = []
    if not r["lead_id"]:
        issues.append(Issue("lead_id_present", "lead_id is missing"))
    if r["created_at"] is None:
        issues.append(Issue("lead_created_at_valid", f"created_at {r['raw_created_at']!r} could not be parsed"))
    if r["phone_provided"] and r["phone"] is None:
        issues.append(Issue("lead_phone_valid", f"phone {r['raw_phone']!r} is not a valid 10-digit number; blanked", FIELD_NULLED))
    if r["email_provided"] and r["email"] is None:
        issues.append(Issue("lead_email_valid", f"email {r['raw_email']!r} is not valid; blanked", FIELD_NULLED))
    if r["phone"] is None and r["email"] is None:
        issues.append(Issue("lead_has_contact", "no valid phone and no valid email"))
    return issues


def validate_order(r):
    issues = []
    if not r["order_id"]:
        issues.append(Issue("order_id_present", "order_id is missing"))
    if r["order_ts"] is None:
        issues.append(Issue("order_ts_valid", f"order_ts {r['raw_order_ts']!r} could not be parsed"))
    if r["updated_at"] is None:
        issues.append(Issue("order_updated_at_valid", f"updated_at {r['raw_updated_at']!r} could not be parsed"))
    if r["status"] not in ALLOWED_STATUSES:
        issues.append(Issue("order_status_allowed", f"status {r['raw_status']!r} is not allowed"))
    if r["payment_mode"] not in ALLOWED_PAYMENT_MODES:
        issues.append(Issue("order_payment_mode_allowed", f"payment_mode {r['raw_payment_mode']!r} is not allowed"))
    items = r["items"]
    if not items:
        issues.append(Issue("order_has_items", "order has no items"))
    if any(not it["sku"] for it in items):
        issues.append(Issue("order_sku_present", "an item has no sku"))
    if any(it["qty"] is None or it["qty"] <= 0 for it in items):
        issues.append(Issue("order_qty_positive", "an item quantity is missing, zero or negative"))
    if any(it["unit_price"] is None or it["unit_price"] < 0 for it in items):
        issues.append(Issue("order_unit_price_valid", "an item unit_price is missing or negative"))
    if items and r["items_total"] is not None:
        if r["total"] is None or abs(r["total"] - r["items_total"]) > 1:
            issues.append(Issue(
                "order_total_matches_items",
                f"total {r['total']} does not match items sum {r['items_total']}",
            ))
    if r["phone_provided"] and r["phone"] is None:
        issues.append(Issue("order_phone_valid", f"phone {r['raw_phone']!r} is not valid; order kept as unmatched customer", FIELD_NULLED))
    return issues


def hard_failures(issues):
    return [i for i in issues if i.action == REJECTED]


def soft_issues(issues):
    return [i for i in issues if i.action != REJECTED]
