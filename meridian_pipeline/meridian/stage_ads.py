"""ADS: raw -> clean -> validate -> deduplicate (latest wins) -> staging."""
from collections import Counter

from psycopg2.extras import execute_values

from . import raw
from .cleaning import (UTC, clean_campaign_name, clean_platform, parse_date, parse_money,
                       parse_timestamp, to_int)
from .context import ZERO, SourceStats
from .dedupe import latest_wins
from .validation import Issue, REJECTED, hard_failures, validate_ad

UPSERT_SQL = """
INSERT INTO staging.ads (campaign_id, ad_date, campaign_name, platform, spend, impressions, clicks, updated_at, run_id)
VALUES %s
ON CONFLICT (campaign_id, ad_date) DO UPDATE
   SET campaign_name = EXCLUDED.campaign_name, platform = EXCLUDED.platform, spend = EXCLUDED.spend,
       impressions = EXCLUDED.impressions, clicks = EXCLUDED.clicks, updated_at = EXCLUDED.updated_at,
       run_id = EXCLUDED.run_id
 WHERE EXCLUDED.updated_at >= staging.ads.updated_at                   -- never let an OLDER version overwrite a newer one
   AND (EXCLUDED.campaign_name, EXCLUDED.platform, EXCLUDED.spend, EXCLUDED.impressions, EXCLUDED.clicks, EXCLUDED.updated_at)
       IS DISTINCT FROM
       (staging.ads.campaign_name, staging.ads.platform, staging.ads.spend, staging.ads.impressions, staging.ads.clicks, staging.ads.updated_at)
RETURNING (xmax = 0) AS inserted, spend
"""


def clean_ad(rec):
    cid = rec.get("campaign_id")
    return {
        "campaign_id": str(cid).strip() or None if cid is not None else None,
        "campaign_name": clean_campaign_name(rec.get("campaign_name")),
        "platform": clean_platform(rec.get("platform")),
        "ad_date": parse_date(rec.get("date")),
        "spend": parse_money(rec.get("spend")),
        "impressions": to_int(rec.get("impressions")),
        "clicks": to_int(rec.get("clicks")),
        "updated_at": parse_timestamp(rec.get("updated_at"), UTC),
        "raw_date": rec.get("date"), "raw_updated_at": rec.get("updated_at"),
        "raw_platform": rec.get("platform"), "raw_spend": rec.get("spend"),
        "raw_impressions": rec.get("impressions"), "raw_clicks": rec.get("clicks"),
    }


def process_ads(ctx, rows):
    st = SourceStats(has_value=True, rows_read=len(rows))
    count = Counter()

    received, _ = raw.load_raw(ctx.cur, "ads", ctx.run_id, rows)
    for rec in rows:                                        # what a naive pipeline would add up
        if isinstance(rec.get("spend"), (int, float)):
            st.naive_value_as_received += parse_money(rec["spend"]) or ZERO

    valid = []
    for rec, _ in received:
        c = clean_ad(rec)
        value = c["spend"] or ZERO
        st.value_read += value
        if isinstance(rec.get("spend"), str) and c["spend"] is not None:
            count["spend_text_to_number"] += 1
        if "/" in str(rec.get("date", "")):
            count["date_dd/mm/yyyy_to_iso"] += 1
        if c["campaign_name"] != rec.get("campaign_name"):
            count["campaign_name_recased_or_trimmed"] += 1
        if c["platform"] != rec.get("platform"):
            count["platform_recased"] += 1
        hard = hard_failures(validate_ad(c))
        if hard:
            st.rows_rejected += 1
            st.value_rejected += value
            ctx.log_issue("ads", Issue(hard[0].rule, "; ".join(i.reason for i in hard), REJECTED),
                          f"{rec.get('campaign_id')}|{rec.get('date')}", rec)
            continue
        valid.append(c)

    winners, losers = latest_wins(valid, key_fn=lambda c: (c["campaign_id"], c["ad_date"]),
                                  order_fn=lambda c: c["updated_at"])
    st.dup_superseded = len(losers)
    st.duplicates_removed += len(losers)
    st.value_duplicates += sum((c["spend"] for c in losers), ZERO)

    values = [(c["campaign_id"], c["ad_date"], c["campaign_name"], c["platform"], c["spend"],
               c["impressions"], c["clicks"], c["updated_at"], ctx.run_id) for c in winners]
    returned = execute_values(ctx.cur, UPSERT_SQL, values, page_size=1000, fetch=True) if values else []
    st.staging_inserted = sum(1 for r in returned if r[0])
    st.staging_updated = len(returned) - st.staging_inserted
    st.rows_loaded = len(returned)
    st.value_loaded = sum((r[1] for r in returned), ZERO)
    st.dup_unchanged_or_stale = len(winners) - len(returned)
    st.duplicates_removed += st.dup_unchanged_or_stale
    st.value_duplicates += sum((c["spend"] for c in winners), ZERO) - st.value_loaded
    st.cleaning = dict(count)

    # newest updated_at we have seen -> next run only asks for rows at/after it
    stamps = [c["updated_at"] for c in (clean_ad(r) for r in rows) if c["updated_at"]]
    return st, (max(stamps) if stamps else None)
