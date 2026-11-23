"""ORDERS: raw -> clean -> validate -> deduplicate (latest status wins) -> staging (orders + items)."""
from collections import Counter

from psycopg2.extras import execute_values

from . import raw
from .cleaning import (IST, UTC, clean_payment_mode, clean_phone, clean_status, has_timezone,
                       normalise_order_total, parse_money, parse_timestamp, to_int, to_ist_date)
from .context import ZERO, SourceStats
from .dedupe import latest_wins
from .validation import Issue, REJECTED, hard_failures, soft_issues, validate_order

ORDER_UPSERT_SQL = """
INSERT INTO staging.orders (order_id, phone, order_ts, order_date, status, payment_mode, total_amount,
                            items_total, was_paise, updated_at, run_id)
VALUES %s
ON CONFLICT (order_id) DO UPDATE
   SET phone = EXCLUDED.phone, order_ts = EXCLUDED.order_ts, order_date = EXCLUDED.order_date,
       status = EXCLUDED.status, payment_mode = EXCLUDED.payment_mode, total_amount = EXCLUDED.total_amount,
       items_total = EXCLUDED.items_total, was_paise = EXCLUDED.was_paise, updated_at = EXCLUDED.updated_at,
       run_id = EXCLUDED.run_id
 WHERE EXCLUDED.updated_at >= staging.orders.updated_at
   AND (EXCLUDED.phone, EXCLUDED.order_ts, EXCLUDED.status, EXCLUDED.payment_mode, EXCLUDED.total_amount,
        EXCLUDED.items_total, EXCLUDED.updated_at)
       IS DISTINCT FROM
       (staging.orders.phone, staging.orders.order_ts, staging.orders.status, staging.orders.payment_mode,
        staging.orders.total_amount, staging.orders.items_total, staging.orders.updated_at)
RETURNING (xmax = 0) AS inserted, order_id, total_amount
"""


def clean_order(rec):
    # the store renamed customer_phone -> phone_number in some records; map both to one name
    phone_key = "customer_phone" if "customer_phone" in rec else ("phone_number" if "phone_number" in rec else None)
    raw_phone = rec.get(phone_key) if phone_key else None
    raw_phone_txt = "" if raw_phone is None else str(raw_phone).strip()

    items = []
    raw_items = rec.get("items") if isinstance(rec.get("items"), list) else []
    for i, it in enumerate(raw_items, start=1):
        it = it if isinstance(it, dict) else {}
        sku = str(it.get("sku") or "").strip() or None
        items.append({"line_no": i, "sku": sku, "qty": to_int(it.get("qty")),
                      "unit_price": parse_money(it.get("unit_price"))})
    complete = bool(items) and all(x["qty"] is not None and x["unit_price"] is not None for x in items)
    items_total = sum((x["qty"] * x["unit_price"] for x in items), ZERO) if complete else None

    total_raw = parse_money(rec.get("total_amount"))
    total, was_paise = normalise_order_total(total_raw, items_total)
    order_ts = parse_timestamp(rec.get("order_ts"), IST)       # no zone written -> Indian time
    return {
        "order_id": str(rec.get("order_id") or "").strip() or None,
        "phone": clean_phone(raw_phone_txt) if raw_phone_txt else None,
        "phone_provided": bool(raw_phone_txt), "raw_phone": raw_phone, "phone_key": phone_key,
        "order_ts": order_ts, "raw_order_ts": rec.get("order_ts"), "order_date": to_ist_date(order_ts),
        "updated_at": parse_timestamp(rec.get("updated_at"), UTC), "raw_updated_at": rec.get("updated_at"),
        "status": clean_status(rec.get("status")), "raw_status": rec.get("status"),
        "payment_mode": clean_payment_mode(rec.get("payment_mode")), "raw_payment_mode": rec.get("payment_mode"),
        "total": total, "was_paise": was_paise, "items": items, "items_total": items_total,
    }


def process_orders(ctx, records):
    st = SourceStats(has_value=True, rows_read=len(records))
    count = Counter()

    received, _ = raw.load_raw(ctx.cur, "orders", ctx.run_id, records)
    for rec in records:
        st.naive_value_as_received += parse_money(rec.get("total_amount")) or ZERO

    valid = []
    for rec, _ in received:
        c = clean_order(rec)
        value = c["total"] or ZERO
        st.value_read += value
        issues = validate_order(c)
        hard = hard_failures(issues)
        if hard:
            st.rows_rejected += 1
            st.value_rejected += value
            ctx.log_issue("orders", Issue(hard[0].rule, "; ".join(i.reason for i in hard), REJECTED),
                          rec.get("order_id"), rec)
            continue
        for s in soft_issues(issues):
            st.warnings_logged += 1
            ctx.log_issue("orders", s, c["order_id"], rec)
        if c["phone_key"] == "phone_number":
            count["field_phone_number_mapped_to_customer_phone"] += 1
        if c["was_paise"]:
            count["total_paise_to_rupees"] += 1
        if any(isinstance(it.get("qty"), str) for it in rec.get("items", []) if isinstance(it, dict)):
            count["qty_text_to_integer"] += 1
        if str(rec.get("status")) != c["status"]:
            count["status_respelled_or_recased"] += 1
        count["order_ts_had_zone_(UTC_or_offset)" if has_timezone(rec.get("order_ts"))
              else "order_ts_assumed_IST_then_converted_to_UTC"] += 1
        valid.append(c)

    winners, losers = latest_wins(valid, key_fn=lambda c: c["order_id"], order_fn=lambda c: c["updated_at"])
    st.dup_superseded = len(losers)
    st.duplicates_removed += len(losers)
    st.value_duplicates += sum((c["total"] for c in losers), ZERO)

    values = [(c["order_id"], c["phone"], c["order_ts"], c["order_date"], c["status"], c["payment_mode"],
               c["total"], c["items_total"], c["was_paise"], c["updated_at"], ctx.run_id) for c in winners]
    returned = execute_values(ctx.cur, ORDER_UPSERT_SQL, values, page_size=1000, fetch=True) if values else []
    st.staging_inserted = sum(1 for r in returned if r[0])
    st.staging_updated = len(returned) - st.staging_inserted
    st.rows_loaded = len(returned)
    st.value_loaded = sum((r[2] for r in returned), ZERO)
    st.dup_unchanged_or_stale = len(winners) - len(returned)
    st.duplicates_removed += st.dup_unchanged_or_stale
    st.value_duplicates += sum((c["total"] for c in winners), ZERO) - st.value_loaded

    # items: for every order whose staging row changed, replace its items with the newest version's items
    changed = {r[1] for r in returned}
    if changed:
        ctx.cur.execute("DELETE FROM staging.order_items WHERE order_id = ANY(%s)", (list(changed),))
        item_rows = [(c["order_id"], it["line_no"], it["sku"], it["qty"], it["unit_price"])
                     for c in winners if c["order_id"] in changed for it in c["items"]]
        execute_values(ctx.cur, "INSERT INTO staging.order_items (order_id, line_no, sku, qty, unit_price) VALUES %s",
                       item_rows, page_size=1000)
        count["items_loaded"] = len(item_rows)
    st.cleaning = dict(count)

    stamps = [c["updated_at"] for c in (clean_order(r) for r in records) if c["updated_at"]]
    return st, (max(stamps) if stamps else None)
