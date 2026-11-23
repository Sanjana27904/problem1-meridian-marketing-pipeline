"""The pipeline. Run it with:   python -m meridian.pipeline [--reset]

One run =
  1. EXTRACT   fetch only NEW data from the 3 sources (no database transaction open yet)
  2. LOAD      raw -> staging (clean, validate, dedupe) -> final tables, all in ONE transaction
  3. CHECK     control totals must balance, staging must equal final; if not, roll everything back
  4. LOG       one row in pipeline_runs
If anything fails, the transaction is rolled back, so we never end up with half-loaded data.
"""
import argparse
import sys
import time
from datetime import datetime, timezone

from . import db, extract, stage_ads, stage_leads, stage_orders
from .config import Config
from .context import RunContext, to_json


class PipelineError(RuntimeError):
    pass


def _read_state(cur):
    cur.execute("SELECT source, watermark FROM etl_state")
    state = {s: w for s, w in cur.fetchall()}
    cur.execute("SELECT file_name FROM etl_loaded_files")
    return state, {r[0] for r in cur.fetchall()}


def _save_watermark(cur, source, new_value, old_value):
    value = max(v for v in (new_value, old_value) if v is not None) if (new_value or old_value) else None
    if value is not None:
        cur.execute("""INSERT INTO etl_state (source, watermark) VALUES (%s, %s)
                       ON CONFLICT (source) DO UPDATE SET watermark = EXCLUDED.watermark, updated_at = now()""",
                    (source, value))


def _table_totals(cur):
    q = {
        "dim_campaign": "SELECT count(*), NULL FROM dim_campaign",
        "fact_ad_spend_daily": "SELECT count(*), coalesce(sum(spend),0) FROM fact_ad_spend_daily",
        "dim_customer": "SELECT count(*), NULL FROM dim_customer",
        "fact_leads": "SELECT count(*), NULL FROM fact_leads",
        "fact_orders": "SELECT count(*), coalesce(sum(total_amount),0) FROM fact_orders",
        "fact_order_items": "SELECT count(*), coalesce(sum(line_total),0) FROM fact_order_items",
        "rejected_rows": "SELECT count(*), NULL FROM rejected_rows",
    }
    out = {}
    for name, sql in q.items():
        cur.execute(sql)
        n, total = cur.fetchone()
        out[name] = {"rows": n} if total is None else {"rows": n, "total": total}
    return out


def _unmatched(cur):
    cur.execute("""SELECT count(*), count(*) FILTER (WHERE campaign_id IS NULL),
                          count(*) FILTER (WHERE customer_id IS NULL) FROM fact_leads""")
    leads, l_camp, l_cust = cur.fetchone()
    cur.execute("""SELECT count(*), count(*) FILTER (WHERE customer_id IS NULL),
                          count(*) FILTER (WHERE attributed_campaign_id IS NULL) FROM fact_orders""")
    orders, o_cust, o_attr = cur.fetchone()
    return {"leads_total": leads, "leads_unmatched_to_campaign": l_camp, "leads_unmatched_to_customer": l_cust,
            "orders_total": orders, "orders_unmatched_to_customer": o_cust, "orders_unattributed": o_attr}


def _reconcile(cur):
    """Staging and final must agree. Returns a list of problems (empty = all good)."""
    checks = [
        ("ads rows", "SELECT count(*) FROM staging.ads", "SELECT count(*) FROM fact_ad_spend_daily"),
        ("ads spend", "SELECT coalesce(sum(spend),0) FROM staging.ads", "SELECT coalesce(sum(spend),0) FROM fact_ad_spend_daily"),
        ("lead rows", "SELECT count(*) FROM staging.leads", "SELECT count(*) FROM fact_leads"),
        ("order rows", "SELECT count(*) FROM staging.orders", "SELECT count(*) FROM fact_orders"),
        ("order value", "SELECT coalesce(sum(total_amount),0) FROM staging.orders", "SELECT coalesce(sum(total_amount),0) FROM fact_orders"),
        ("item rows", "SELECT count(*) FROM staging.order_items", "SELECT count(*) FROM fact_order_items"),
        ("customers", "SELECT count(DISTINCT phone) FROM (SELECT phone FROM staging.leads UNION ALL SELECT phone FROM staging.orders) x WHERE phone IS NOT NULL",
         "SELECT count(*) FROM dim_customer"),
        ("order totals vs item lines", "SELECT coalesce(sum(items_total),0) FROM staging.orders", "SELECT coalesce(sum(line_total),0) FROM fact_order_items"),
    ]
    problems = []
    for name, a, b in checks:
        cur.execute(a); x = cur.fetchone()[0]
        cur.execute(b); y = cur.fetchone()[0]
        if x != y:
            problems.append(f"{name}: staging={x} final={y}")
    return problems


def run_pipeline(cfg=None, client=None, verbose=True):
    cfg = cfg or Config()
    log = db.connect(cfg.dsn)
    log.autocommit = True                       # the run log is written outside the big transaction
    db.apply_schema(log)
    with log.cursor() as c:
        c.execute("INSERT INTO pipeline_runs DEFAULT VALUES RETURNING run_id, started_at")
        run_id, _ = c.fetchone()
    try:
        details = _execute(cfg, client, run_id)
    except Exception as exc:
        with log.cursor() as c:
            c.execute("UPDATE pipeline_runs SET status='failed', finished_at=now(), error_message=%s WHERE run_id=%s",
                      (f"{type(exc).__name__}: {exc}"[:2000], run_id))
        log.close()
        raise
    srcs = details["sources"].values()
    with log.cursor() as c:
        c.execute("""UPDATE pipeline_runs SET status=%s, finished_at=now(), rows_read=%s, rows_loaded=%s,
                     rows_rejected=%s, duplicates_removed=%s, details=%s WHERE run_id=%s""",
                  (details["status"], sum(s["rows_read"] for s in srcs), sum(s["rows_loaded"] for s in srcs),
                   sum(s["rows_rejected"] for s in srcs), sum(s["duplicates_removed"] for s in srcs),
                   to_json(details), run_id))
    log.close()
    if verbose:
        print_summary(run_id, details)
    return run_id


def _execute(cfg, client, run_id):
    conn = db.connect(cfg.dsn)
    try:
        # ---------------- 0. what did we load last time?
        with conn.cursor() as cur:
            state, loaded_files = _read_state(cur)
        conn.commit()

        # ---------------- 1. EXTRACT (network + files, no transaction open)
        client = client or extract.AdsApiClient(cfg.api_base_url, cfg.page_size, cfg.max_retries, cfg.retry_base_delay)
        ads_rows = list(client.fetch_all(updated_since=state.get("ads")))
        crm_files = extract.read_new_crm_files(cfg.crm_dir, loaded_files)
        order_records = extract.read_orders(cfg.orders_path, state.get("orders"))

        # ---------------- 2. LOAD (one transaction)
        with conn.cursor() as cur:
            ctx = RunContext(cur, run_id)
            ads_stats, ads_wm = stage_ads.process_ads(ctx, ads_rows)
            leads_stats = stage_leads.process_leads(ctx, crm_files)
            orders_stats, orders_wm = stage_orders.process_orders(ctx, order_records)

            _save_watermark(cur, "ads", ads_wm, state.get("ads"))
            _save_watermark(cur, "orders", orders_wm, state.get("orders"))
            for f in crm_files:
                cur.execute("INSERT INTO etl_loaded_files (file_name, file_hash, row_count, run_id) VALUES (%s,%s,%s,%s)",
                            (f.name, f.file_hash, len(f.rows), run_id))
            ctx.flush_issues()

            cur.execute(db.final_build_sql())

            # ---------------- 3. CHECK
            sources = {"ads": ads_stats, "leads": leads_stats, "orders": orders_stats}
            problems = [f"{name}: rows_read={s.rows_read} != loaded+rejected+duplicates "
                        f"({s.rows_loaded}+{s.rows_rejected}+{s.duplicates_removed}) or rupee totals do not balance"
                        for name, s in sources.items() if not s.identity_ok()]
            problems += _reconcile(cur)
            if problems:
                raise PipelineError("control totals failed: " + " | ".join(problems))

            warnings = []
            for name, s in sources.items():
                if s.rows_read and s.rows_rejected / s.rows_read > cfg.max_reject_rate:
                    warnings.append(f"{name}: {s.rows_rejected}/{s.rows_read} rows rejected (> {cfg.max_reject_rate:.0%})")
            details = {
                "status": "success_with_warnings" if warnings else "success",
                "warnings": warnings,
                "sources": {n: s.as_dict() for n, s in sources.items()},
                "api": getattr(client, "stats", {}),
                "crm_files_loaded": [f.name for f in crm_files],
                "watermarks": {"ads": (max(x for x in (ads_wm, state.get("ads")) if x) .isoformat() if (ads_wm or state.get("ads")) else None),
                               "orders": (max(x for x in (orders_wm, state.get("orders")) if x).isoformat() if (orders_wm or state.get("orders")) else None)},
                "final_tables": _table_totals(cur),
                "unmatched": _unmatched(cur),
            }
        conn.commit()
        return details
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def print_summary(run_id, d):
    print(f"\n=== Pipeline run {run_id}: {d['status'].upper()} ===")
    for w in d["warnings"]:
        print("  WARNING:", w)
    hdr = f"{'source':8} {'read':>7} {'loaded':>7} {'rejected':>8} {'dupes':>7}  balance"
    print(hdr + "\n" + "-" * len(hdr))
    for name, s in d["sources"].items():
        print(f"{name:8} {s['rows_read']:>7} {s['rows_loaded']:>7} {s['rows_rejected']:>8} {s['duplicates_removed']:>7}  "
              f"{'OK' if s['identity_ok'] else 'FAIL'}")
    for name in ("ads", "orders"):
        s = d["sources"][name]
        print(f"  {name} value (Rs): read={s['value_read']:,.2f} = loaded {s['value_loaded']:,.2f} "
              f"+ rejected {s['value_rejected']:,.2f} + duplicates {s['value_duplicates']:,.2f}   "
              f"(naive sum as received: {s['naive_value_as_received']:,.2f})")
    print("  API:", d["api"])
    print("  final tables:", {k: v["rows"] for k, v in d["final_tables"].items()})
    print("  unmatched:", d["unmatched"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="wipe the database first")
    a = ap.parse_args()
    cfg = Config()
    if a.reset:
        conn = db.connect(cfg.dsn); db.reset_all(conn); conn.close()
    t0 = time.time()
    try:
        run_pipeline(cfg)
    except Exception as e:
        print(f"PIPELINE FAILED: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"  done in {time.time() - t0:.1f}s")
