"""Print / save the evidence a reviewer wants to see.   python -m meridian.report [--markdown output/evidence.md]"""
import argparse
from pathlib import Path

import psycopg2

from .config import Config


def q(cur, sql, params=None):
    cur.execute(sql, params)
    return [d.name for d in cur.description], cur.fetchall()


def md_table(cols, rows):
    f = lambda v: "" if v is None else (f"{v:,.2f}" if hasattr(v, "quantize") else str(v))
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(f(v) for v in r) + " |" for r in rows]
    return "\n".join(out)


def build(cfg):
    conn = psycopg2.connect(cfg.dsn)
    cur = conn.cursor()
    parts = ["# Meridian Threads - pipeline evidence\n"]

    def section(title, sql, note=None):
        cols, rows = q(cur, sql)
        parts.append(f"## {title}\n" + (f"_{note}_\n\n" if note else "\n") + md_table(cols, rows) + "\n")

    section("1. Run log (pipeline_runs)",
            "SELECT run_id, status, to_char(started_at,'HH24:MI:SS') AS started, to_char(finished_at,'HH24:MI:SS') AS finished, "
            "rows_read, rows_loaded, rows_rejected, duplicates_removed FROM pipeline_runs ORDER BY run_id",
            "One row per run. Re-runs may append fetched occurrences to raw, while staging/final business tables and monetary totals remain unchanged.")
    section("2. Control totals per source (rows_read = loaded + rejected + duplicates)",
            "SELECT run_id, source, rows_read, rows_loaded, rows_rejected, duplicates_removed, counts_balance "
            "FROM vw_control_totals ORDER BY run_id, source")
    section("3. Rupee control totals, latest run that loaded data",
            """WITH r AS (SELECT run_id, details FROM pipeline_runs WHERE rows_loaded > 0 ORDER BY run_id DESC LIMIT 1)
               SELECT r.run_id, s.key AS source, (s.value->>'value_read')::numeric AS value_read,
                      (s.value->>'value_loaded')::numeric AS loaded, (s.value->>'value_rejected')::numeric AS rejected,
                      (s.value->>'value_duplicates')::numeric AS duplicates,
                      (s.value->>'naive_value_as_received')::numeric AS naive_sum_as_received
               FROM r, jsonb_each(r.details->'sources') s WHERE s.key IN ('ads','orders')""",
            "read = loaded + rejected + duplicates. The naive sum shows what you would get without cleaning (text spend ignored, paise not converted, repeats counted).")
    section("4. Raw ingestion history (append-only)",
            """SELECT source, raw_occurrences, distinct_fingerprints,
                      raw_occurrences - distinct_fingerprints AS repeated_fingerprint_occurrences
               FROM (
                 SELECT 'ads' AS source, count(*) AS raw_occurrences, count(DISTINCT row_hash) AS distinct_fingerprints FROM raw.ads
                 UNION ALL SELECT 'leads', count(*), count(DISTINCT row_hash) FROM raw.leads
                 UNION ALL SELECT 'orders', count(*), count(DISTINCT row_hash) FROM raw.orders
               ) raw_counts ORDER BY source""",
            "Every received source occurrence is retained. Repeated fingerprints are expected when an inclusive watermark re-fetches an already-seen record; staging/final remain idempotent.")
    section("5. Final table sizes",
            """SELECT 'dim_campaign' AS tbl, count(*) AS rows, NULL::numeric AS total FROM dim_campaign
               UNION ALL SELECT 'fact_ad_spend_daily', count(*), sum(spend) FROM fact_ad_spend_daily
               UNION ALL SELECT 'dim_customer', count(*), NULL FROM dim_customer
               UNION ALL SELECT 'fact_leads', count(*), NULL FROM fact_leads
               UNION ALL SELECT 'fact_orders', count(*), sum(total_amount) FROM fact_orders
               UNION ALL SELECT 'fact_order_items', count(*), sum(line_total) FROM fact_order_items
               UNION ALL SELECT 'rejected_rows', count(*), NULL FROM rejected_rows""")
    section("6. Rejected / flagged rows by rule",
            "SELECT source, rule_name, action, count(*) AS rows FROM rejected_rows GROUP BY 1,2,3 ORDER BY 1, 4 DESC",
            "action=rejected: kept OUT of the final tables. action=field_nulled: the row is kept, only the bad phone/email is blanked.")
    section("7. Unmatched records (counted and kept)",
            """SELECT 'leads with no matching campaign' AS what, count(*) AS rows FROM fact_leads WHERE campaign_id IS NULL
               UNION ALL SELECT 'leads with no customer (no valid phone)', count(*) FROM fact_leads WHERE customer_id IS NULL
               UNION ALL SELECT 'orders with no customer (no valid phone)', count(*) FROM fact_orders WHERE customer_id IS NULL
               UNION ALL SELECT 'orders not attributable to a campaign', count(*) FROM fact_orders WHERE attributed_campaign_id IS NULL""")
    section("8. Top utm_campaign values that matched no campaign",
            "SELECT coalesce(utm_campaign_raw,'(empty)') AS utm_campaign, count(*) AS leads FROM fact_leads "
            "WHERE campaign_id IS NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 8")
    section("9. Best ROAS in the last 7 days of data (a reviewer-facing business question)",
            """WITH mx AS (SELECT max(report_date) d FROM vw_campaign_daily)
               SELECT campaign_name, platform, round(sum(spend),2) AS spend, sum(leads) AS leads, sum(orders) AS orders,
                      round(sum(net_revenue),2) AS net_revenue, sum(new_customers) AS new_customers,
                      round(sum(spend)/nullif(sum(new_customers),0),2) AS cac,
                      round(sum(net_revenue)/nullif(sum(spend),0),2) AS roas
               FROM vw_campaign_daily, mx WHERE report_date > mx.d - 7
               GROUP BY 1,2 HAVING sum(spend) > 0 ORDER BY roas DESC NULLS LAST LIMIT 10""",
            "ROAS = net revenue / ad spend. CAC = ad spend / new customers. Net revenue excludes cancelled and returned orders.")
    conn.close()
    return "\n".join(parts)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown")
    a = ap.parse_args()
    text = build(Config())
    if a.markdown:
        Path(a.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(a.markdown).write_text(text, encoding="utf-8")
        print("wrote", a.markdown)
    else:
        print(text)
