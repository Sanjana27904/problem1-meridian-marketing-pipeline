# Problem 1 - Unified Marketing Pipeline: a beginner's guide

## 0. The story in one minute

Meridian Threads has three systems that never talk to each other:

| System | What it holds | How it arrives | Why it is messy |
|---|---|---|---|
| Ads platform | money spent per campaign per day | API, pages of 100 | text money, 2 date formats, corrected rows, random 429/500 errors |
| CRM | people who left their details (leads) | one CSV per day | many phone formats, bad emails, repeated lead_id, BOM, Hindi/Malayalam names |
| Store | orders and items | one JSON file | repeated orders, paise vs rupees, CANCELED vs CANCELLED, IST vs UTC |

The marketing head wants ONE trusted table: spend, leads, orders, revenue, CAC, ROAS per campaign.
Our job is the plumbing that turns the three messy sources into that table, **safely and provably**.

```
 SOURCES            RAW (as received)      STAGING (clean+checked)        FINAL (what people read)
 ads API   ──────►  raw.ads        ─────►  staging.ads          ─────►   dim_campaign, fact_ad_spend_daily
 CRM CSVs  ──────►  raw.leads      ─────►  staging.leads        ─────►   dim_customer, fact_leads
 orders    ──────►  raw.orders     ─────►  staging.orders/items ─────►   fact_orders, fact_order_items
                                                │
                                                └──► rejected_rows (bad data, with reason)
                                     pipeline_runs (one row per run)  +  vw_campaign_daily (the "one view")
```

## 1. Words you must know (with examples)

- **ETL** = Extract (fetch), Transform (clean), Load (store). Ours: `extract.py` -> `stage_*.py` -> `02_final_build.sql`.
- **Raw / staging / final layers** = three "rooms". Raw is a photocopy of what arrived (so we can always prove what we received). Staging is cleaned and checked. Final is what dashboards read. *Why three?* If a rule was wrong, you can rebuild staging/final from raw without asking the source again.
- **Pagination** = an API sends 100 rows at a time: page 1, page 2 ... `fetch_all()` keeps asking until the last page.
- **Retry with growing wait (exponential backoff)** = if the API says 429 ("slow down") or 500 ("my fault"), wait 0.5s, then 1s, then 2s, then 4s... *Why growing?* Hammering a struggling server makes it worse. Test: `test_api_retries_with_growing_wait`.
- **Incremental load** = each run fetches only what is new. We remember a **watermark** (the newest `updated_at` seen) in `etl_state`. Example: watermark = 22 Oct 02:00 -> next run asks "rows updated since 22 Oct 02:00". CRM files are tracked by file name in `etl_loaded_files`.
- **Idempotent / safe to re-run** = running twice gives the same staging/final business results as running once. Like pressing a lift button twice. We get it from (1) **primary keys**, (2) **upserts** (INSERT, and if the key exists UPDATE instead), and (3) business-key deduplication in staging. The append-only raw audit history intentionally grows when a source occurrence is fetched again.
- **Upsert** example: `INSERT ... ON CONFLICT (order_id) DO UPDATE ...`. New order -> inserted. Known order with new status -> updated. Never two rows for one order.
- **Deduplicate, latest wins** = same order twice, "placed" at 10:00 and "shipped" at 15:00 -> keep "shipped" (bigger `updated_at`).
- **Standardise** = one format for everything: `₹1,250.50` -> `1250.50`; `+91 98765 43210` and `098765 43210` -> `9876543210`; `21/10/2026` -> `2026-10-21`; IST and UTC -> UTC.
- **Paise** = 1/100 of a rupee. `129900` paise = `₹1299.00`. The export has no flag, so we compare total with the sum of its items: if total is about 100x the items, it is paise.
- **UTM** = a tag in the ad link (`utm_campaign=festive_sale_meta_01`) that the CRM stores. We match it to the campaign name after cleaning case and spaces. `festive_sal_meta_01` (typo) cannot match -> **unmatched**.
- **Unmatched** = cannot be linked. We never drop it. It stays with `campaign_match_status = 'unmatched'` and is counted.
- **Attribution** = who gets credit for an order. Orders carry no UTM, so: order -> customer (by phone) -> that customer's most recent lead in the previous 30 days -> that lead's campaign (*last-touch*).
- **ROAS** = net revenue / ad spend. **CAC** = ad spend / new customers. **Net revenue** = orders not cancelled and not returned. We compute them as ratio of sums, never an average of daily ratios.
- **Validation rule** = a yes/no check, e.g. "clicks <= impressions". Fail -> `rejected_rows` with rule name, reason and the original record.
- **Control total** = proof nothing vanished: `rows_read = rows_loaded + rows_rejected + duplicates_removed`, and the same for rupees. If it does not balance the run is rolled back.
- **Transaction / rollback** = all-or-nothing. A crash halfway leaves the database exactly as before.

## 2. Step by step: what we did and why

| # | Step | File | Why |
|---|---|---|---|
| 1 | Make messy data with every required problem | `sources/generate_data.py` | The brief says create the sources yourself. Seeded, so repeatable. |
| 2 | Fake ads API: pages of 100, ~1 in 20 requests fail | `sources/ads_api.py` | Needed to prove pagination + retries. |
| 3 | Design the tables | `meridian/sql/01_schema.sql` | Layers, primary keys, CHECK constraints as a last line of defence. |
| 4 | Small cleaning functions | `meridian/cleaning.py` | One job each, so easy to unit-test. |
| 5 | 23 validation rules | `meridian/validation.py` | Catch bad data, never hide it. |
| 6 | Fetch with retries; read only new files/records | `meridian/extract.py` | Incremental load. |
| 7 | Raw load with fingerprints | `meridian/raw.py` | Append every received occurrence with run_id/loaded_at; deduplicate downstream in staging. |
| 8 | Clean -> validate -> dedupe -> upsert into staging | `stage_ads.py`, `stage_leads.py`, `stage_orders.py` | The core transform. |
| 9 | Link and build final tables | `meridian/sql/02_final_build.sql` | Customers by phone, campaigns by UTM, orders to campaigns. |
| 10 | The "one trusted view" | `meridian/sql/03_views.sql` | Spend, leads, orders, revenue, CAC, ROAS per campaign per day. |
| 11 | Orchestrate, check totals, log the run | `meridian/pipeline.py` | One transaction, balance check, `pipeline_runs`. |
| 12 | Tests | `tests/` | 25 unit tests + 6 PostgreSQL integration tests (including identical raw-occurrence preservation and two-run idempotency). |
| 13 | Evidence pack | `meridian/report.py` -> `output/evidence.md` | This is what a reviewer wants to see. |

## 3. Meridian requirements -> where we meet each one

| Requirement from the brief | Where / proof |
|---|---|
| Data size ~90 campaigns, 30 days, 8,000 leads, 5,000 orders, ~12,000 items | 90 campaigns, 2,808 ad rows, 8,229 lead rows, 5,000 distinct orders, 11,853 items loaded |
| Ads API with Flask, pages of 100, 429/500 on ~1 in 20 | `sources/ads_api.py` |
| All listed data problems per source | `generate_data.py` (text money, 2 date formats, casing/spaces, clicks>impressions, repeated rows, phone formats, bad emails, UTM mismatches, BOM, Hindi/Malayalam, paise, string qty, CANCELED, total mismatch, `phone_number` rename, IST/UTC) |
| Target tables incl. `rejected_rows`, `pipeline_runs` | `01_schema.sql` (+ `etl_state`, `etl_loaded_files` as helpers) |
| 1. Three layers, raw has run_id + loaded_at | schemas `raw`, `staging`, `public`; raw is append-only and row_hash is non-unique |
| 2. Pagination, retries, growing wait | `AdsApiClient` + `test_api_retries_with_growing_wait` |
| 3. Incremental load (ads + orders by updated_at, CRM by unseen files) | `etl_state`, `etl_loaded_files`; `test_incremental_load_...` |
| 4. Safe to re-run, PKs + upserts | `test_two_runs_on_same_input_give_identical_results` (staging/final business tables, counts AND sums; raw audit history is expected to grow) |
| 5. Dedupe, latest wins; repeated lead kept once | `dedupe.latest_wins`; `stage_*` |
| 6. Standardise (date, time zone, paise, spend text, phone, names, status, renamed fields) | `cleaning.py`; counts of each fix are stored in `pipeline_runs.details` |
| 7. Link; unmatched counted and kept | `02_final_build.sql`; evidence section 6 |
| 8. 10+ validation rules -> `rejected_rows` | 23 rules in `validation.py` |
| 9. Control totals + run log | `vw_control_totals`, rupee totals in evidence section 3, `pipeline_runs` |
| 10. 5+ unit tests + 1 two-run test | 25 unit tests pass in the current verification; 6 PostgreSQL integration tests are included and must be run against PostgreSQL |

## 4. How to run it on your laptop

1. Install PostgreSQL and Python 3.10+. Create a database: `createdb meridian`.
2. `pip install -r requirements.txt`
3. Tell the code where the database is (change user/password/port to yours):
   `export MERIDIAN_DSN=postgresql://postgres:password@localhost:5432/meridian`
   (tests also read `MERIDIAN_TEST_ADMIN_DSN`, e.g. `postgresql://postgres:password@localhost:5432/postgres`)
4. `./run_demo.sh` (Windows: use Git Bash or WSL). It does the whole story and writes `output/evidence.md`.
   Or by hand, in two terminals:
   - T1: `python -m sources.generate_data --out data` then `python -m sources.ads_api`
   - T2: `python -m meridian.pipeline --reset`, again `python -m meridian.pipeline`, then `python -m sources.simulate_next_day` and run the pipeline twice more, then `python -m meridian.report`.

## 5. Decisions you should be able to explain (assumptions)

- **Naive timestamps are IST** (CRM and store are Indian systems); anything with `Z` or `+00:00` keeps its zone. Everything is stored as UTC, plus an India calendar-day column for reporting.
- **Dates are dd/mm/yyyy**, never mm/dd.
- **Paise detection** uses the item sum, because the export has no flag.
- **Reject vs keep.** Hard problems (negative spend, bad status, total not matching items) -> row rejected. A bad *phone or email only* -> row kept, field blanked, still logged (`action = field_nulled`), because the lead is still valuable. A lead with neither valid phone nor email is rejected.
- **Validate first, then dedupe.** If a newer version is bad but an older version was valid, the newer one is rejected (logged) and the older valid one stays. Alternative designs exist; say why you chose this.
- **New customer** = customer's first net order. **Attribution window** = 30 days, last lead before the order. Both are business choices, easy to change in `02_final_build.sql` / `03_views.sql`.
- **A run is all-or-nothing.** If the API is down for a long time the whole run fails and is retried later.
- **Boundary overlap on purpose.** The watermark is inclusive (`>=`), so records at the watermark can be fetched again. Each fetch is retained as a new raw ingestion occurrence; staging/final upserts classify it as unchanged/duplicate and do not change business counts or totals.

## 6. Raw history versus idempotent business tables

Raw is append-only: every source record occurrence read by a run is stored with its own `raw_id`, `run_id`, and `loaded_at`, even if its payload was seen before. `row_hash` is a non-unique fingerprint for traceability, not a uniqueness constraint. Repeated source occurrences are deduplicated in staging using business keys and source timestamps. Therefore raw row counts can increase on a repeated fetch, while staging/final row counts and monetary totals remain unchanged. This is intentional and preserves both auditability and idempotency.

## 7. Honest limits (say these if asked)

- Data is synthetic, generated by us. Numbers like ROAS are illustrative.
- The orders "incremental" filter reads the file then filters by `updated_at` (a file has no server to ask); the ads API filter is truly server-side.
- Rejected ad rows with a negative spend show a negative "rejected" rupee total, by design, so the totals still add up exactly.
- Built for this data size; for millions of rows you would batch the final build and use a scheduler (Airflow/cron).

## 8. Likely viva questions

1. Why three layers? 2. How exactly is a re-run safe? 3. What happens if the API dies mid-run? 4. Why validate before dedupe? 5. How do you know no row was lost? (control totals) 6. How is ROAS calculated and why a ratio of sums? 7. What happens to a lead whose UTM matches no campaign? 8. How did you detect paise? 9. What would you change for 100x data?

### Windows PowerShell (recommended on Windows)

From the project root, open PowerShell and run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
.\run_demo.ps1
```

If PowerShell blocks local scripts, run this once in the current terminal, then retry:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

The script starts and stops the mock Ads API automatically. PostgreSQL must already be running and the database/credentials must match `MERIDIAN_DSN` (default: `postgresql://postgres@localhost:5433/meridian`). For integration tests, the test admin connection defaults to `postgresql://postgres@localhost:5433/postgres`; override it with `MERIDIAN_TEST_ADMIN_DSN` if needed. Docker is not required.
