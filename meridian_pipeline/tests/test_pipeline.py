"""Integration tests: need PostgreSQL. Set MERIDIAN_TEST_ADMIN_DSN if yours is not the default.

    pytest tests/test_pipeline.py
"""
import os
import threading

import psycopg2
import pytest
from werkzeug.serving import make_server

from meridian import db
from meridian.config import Config
from meridian.pipeline import run_pipeline
from meridian.raw import load_raw, record_hash
from sources.ads_api import create_app
from sources.generate_data import generate_all
from sources.simulate_next_day import simulate

ADMIN_DSN = os.getenv("MERIDIAN_TEST_ADMIN_DSN", "postgresql://postgres@localhost:5433/postgres")
TEST_DSN = ADMIN_DSN.rsplit("/", 1)[0] + "/meridian_test"


@pytest.fixture(scope="session", autouse=True)
def test_database():
    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute("DROP DATABASE IF EXISTS meridian_test WITH (FORCE)")
        c.execute("CREATE DATABASE meridian_test")
    admin.close()


@pytest.fixture
def env(tmp_path):
    """Small messy dataset + fake API that fails 30% of the time (to exercise retries) + empty database."""
    generate_all(tmp_path, seed=7, campaigns=10, days=5, orders=250, leads_per_day=(30, 40), customers=300)
    server = make_server("127.0.0.1", 0, create_app(tmp_path / "ads_source.json", fail_rate=0.3, seed=1), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    conn = db.connect(TEST_DSN)
    db.reset_all(conn)
    conn.close()
    cfg = Config(dsn=TEST_DSN, api_base_url=f"http://127.0.0.1:{server.server_port}", crm_dir=tmp_path / "crm",
                 orders_path=tmp_path / "orders.json", retry_base_delay=0.001, max_retries=12)
    yield cfg, tmp_path
    server.shutdown()


# Raw is append-only ingestion history: it grows when a source occurrence is fetched again.
# Idempotency assertions therefore compare staging/final business tables, not raw audit history.
TABLES = {
    "staging.ads": "count(*), sum(spend)", "staging.leads": "count(*)", "staging.orders": "count(*), sum(total_amount)",
    "dim_campaign": "count(*)", "fact_ad_spend_daily": "count(*), sum(spend), sum(clicks)", "dim_customer": "count(*)",
    "fact_leads": "count(*)", "fact_orders": "count(*), sum(total_amount)", "fact_order_items": "count(*), sum(line_total)",
    "rejected_rows": "count(*)",
}


def snapshot(dsn):
    conn = psycopg2.connect(dsn)
    out = {}
    with conn.cursor() as c:
        for t, agg in TABLES.items():
            c.execute(f"SELECT {agg} FROM {t}")
            out[t] = c.fetchone()
    conn.close()
    return out


def raw_counts(dsn):
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as c:
            c.execute("SELECT (SELECT count(*) FROM raw.ads), (SELECT count(*) FROM raw.leads), (SELECT count(*) FROM raw.orders)")
            return c.fetchone()
    finally:
        conn.close()


def test_two_runs_on_same_input_give_identical_results(env):
    cfg, _ = env
    run_pipeline(cfg, verbose=False)
    first = snapshot(cfg.dsn)
    first_raw = raw_counts(cfg.dsn)
    run_pipeline(cfg, verbose=False)
    assert snapshot(cfg.dsn) == first                      # staging/final counts AND totals stay identical
    second_raw = raw_counts(cfg.dsn)
    assert all(after >= before for before, after in zip(first_raw, second_raw))
    assert second_raw != first_raw                        # repeated fetches are preserved in raw history


def test_raw_layer_appends_identical_occurrences_with_run_metadata(env):
    cfg, _ = env
    run_id = run_pipeline(cfg, verbose=False)
    conn = psycopg2.connect(cfg.dsn)
    try:
        with conn.cursor() as c:
            c.execute("SELECT payload FROM raw.ads ORDER BY raw_id LIMIT 1")
            payload = c.fetchone()[0]
            # Simulate the same source occurrence being received twice in one ingestion.
            load_raw(c, "ads", run_id, [payload, payload])
            fingerprint = record_hash(payload)
            c.execute("SELECT count(*), count(DISTINCT run_id) FROM raw.ads WHERE row_hash=%s", (fingerprint,))
            count, run_count = c.fetchone()
            assert count >= 2
            assert run_count == 1
        conn.commit()
    finally:
        conn.close()


def test_control_totals_balance_and_run_is_logged(env):
    cfg, _ = env
    run_id = run_pipeline(cfg, verbose=False)
    conn = psycopg2.connect(cfg.dsn)
    with conn.cursor() as c:
        c.execute("SELECT status, rows_read, rows_loaded, rows_rejected, duplicates_removed, finished_at FROM pipeline_runs WHERE run_id=%s", (run_id,))
        status, read, loaded, rejected, dups, finished = c.fetchone()
        assert status in ("success", "success_with_warnings") and finished is not None
        assert read == loaded + rejected + dups
        c.execute("SELECT count(*) FROM vw_control_totals WHERE run_id=%s AND NOT counts_balance", (run_id,))
        assert c.fetchone()[0] == 0
    conn.close()


def test_bad_rows_are_caught_with_rule_reason_and_original_data(env):
    cfg, _ = env
    run_pipeline(cfg, verbose=False)
    conn = psycopg2.connect(cfg.dsn)
    with conn.cursor() as c:
        c.execute("SELECT count(*) FROM rejected_rows WHERE rule_name IS NULL OR reason IS NULL OR original_data IS NULL")
        assert c.fetchone()[0] == 0
        c.execute("SELECT count(DISTINCT rule_name) FROM rejected_rows")
        assert c.fetchone()[0] >= 5
        c.execute("SELECT count(*) FROM fact_ad_spend_daily WHERE clicks > impressions OR spend < 0")
        assert c.fetchone()[0] == 0                        # nothing bad slipped into the final layer
    conn.close()


def test_unmatched_records_are_kept_not_dropped(env):
    cfg, _ = env
    run_pipeline(cfg, verbose=False)
    conn = psycopg2.connect(cfg.dsn)
    with conn.cursor() as c:
        c.execute("SELECT count(*) FROM fact_leads WHERE campaign_match_status='unmatched'")
        assert c.fetchone()[0] > 0
        c.execute("SELECT (SELECT count(*) FROM staging.leads), (SELECT count(*) FROM fact_leads)")
        a, b = c.fetchone()
        assert a == b                                      # unmatched leads are still in fact_leads
    conn.close()


def test_incremental_load_only_reads_new_data_and_latest_status_wins(env):
    cfg, tmp = env
    run_pipeline(cfg, verbose=False)
    before = snapshot(cfg.dsn)
    simulate(tmp)                                          # a new day of data arrives
    run_id = run_pipeline(cfg, verbose=False)
    after = snapshot(cfg.dsn)
    assert after["fact_ad_spend_daily"][0] > before["fact_ad_spend_daily"][0]
    assert after["fact_leads"][0] > before["fact_leads"][0]
    assert after["fact_orders"][0] > before["fact_orders"][0]
    conn = psycopg2.connect(cfg.dsn)
    with conn.cursor() as c:
        c.execute("SELECT details->'crm_files_loaded' FROM pipeline_runs WHERE run_id=%s", (run_id,))
        assert len(c.fetchone()[0]) == 1                   # only the ONE new CSV file was read
    conn.close()
    run_pipeline(cfg, verbose=False)                       # and re-running changes nothing again
    assert snapshot(cfg.dsn) == after
