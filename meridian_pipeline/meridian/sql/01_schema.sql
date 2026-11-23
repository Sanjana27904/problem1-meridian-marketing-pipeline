-- =====================================================================
-- Meridian Threads pipeline: database layout
--   schema raw      = data exactly as received (+ run_id, loaded_at)
--   schema staging  = typed, cleaned, validated rows
--   schema public   = FINAL layer (dim_/fact_ tables) + control tables
-- Everything is CREATE ... IF NOT EXISTS so running it twice changes nothing.
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;

-- ---------------------------------------------------------- control tables
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id             serial PRIMARY KEY,
    started_at         timestamptz NOT NULL DEFAULT now(),
    finished_at        timestamptz,
    status             text NOT NULL DEFAULT 'running',   -- running | success | success_with_warnings | failed
    rows_read          int,
    rows_loaded        int,
    rows_rejected      int,
    duplicates_removed int,
    details            jsonb,                             -- per-source control totals, API stats, unmatched counts
    error_message      text
);

CREATE TABLE IF NOT EXISTS rejected_rows (
    reject_id     bigserial PRIMARY KEY,
    run_id        int NOT NULL REFERENCES pipeline_runs(run_id),
    source        text NOT NULL,          -- ads | leads | orders
    rule_name     text NOT NULL,
    reason        text NOT NULL,
    action        text NOT NULL,          -- rejected | field_nulled
    record_key    text,
    original_data jsonb NOT NULL,         -- the record exactly as it arrived
    rejected_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_rejected_rule ON rejected_rows (source, rule_name);

-- remembers "how far did we get" for incremental loads
CREATE TABLE IF NOT EXISTS etl_state (
    source     text PRIMARY KEY,
    watermark  timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- remembers which CRM files were already loaded
CREATE TABLE IF NOT EXISTS etl_loaded_files (
    file_name text PRIMARY KEY,
    file_hash text NOT NULL,
    row_count int  NOT NULL,
    run_id    int  NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------- RAW
-- Every ingestion occurrence is retained. row_hash is a non-unique fingerprint for lineage.
-- Repeated source rows are deduplicated downstream, never discarded from raw.
CREATE TABLE IF NOT EXISTS raw.ads (
    raw_id bigserial PRIMARY KEY, run_id int NOT NULL, loaded_at timestamptz NOT NULL DEFAULT now(),
    row_hash text NOT NULL, payload jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS raw.leads (
    raw_id bigserial PRIMARY KEY, run_id int NOT NULL, loaded_at timestamptz NOT NULL DEFAULT now(),
    source_file text NOT NULL, line_no int NOT NULL,
    row_hash text NOT NULL, payload jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS raw.orders (
    raw_id bigserial PRIMARY KEY, run_id int NOT NULL, loaded_at timestamptz NOT NULL DEFAULT now(),
    row_hash text NOT NULL, payload jsonb NOT NULL);

-- Upgrade databases created by earlier versions where row_hash was UNIQUE.
ALTER TABLE raw.ads DROP CONSTRAINT IF EXISTS raw_ads_row_hash_key;
ALTER TABLE raw.leads DROP CONSTRAINT IF EXISTS raw_leads_row_hash_key;
ALTER TABLE raw.orders DROP CONSTRAINT IF EXISTS raw_orders_row_hash_key;
CREATE INDEX IF NOT EXISTS ix_raw_ads_hash ON raw.ads (row_hash);
CREATE INDEX IF NOT EXISTS ix_raw_leads_hash ON raw.leads (row_hash);
CREATE INDEX IF NOT EXISTS ix_raw_orders_hash ON raw.orders (row_hash);
CREATE INDEX IF NOT EXISTS ix_raw_ads_run ON raw.ads (run_id);
CREATE INDEX IF NOT EXISTS ix_raw_leads_run ON raw.leads (run_id);
CREATE INDEX IF NOT EXISTS ix_raw_orders_run ON raw.orders (run_id);

-- --------------------------------------------------------------- STAGING
CREATE TABLE IF NOT EXISTS staging.ads (
    campaign_id   text NOT NULL,
    ad_date       date NOT NULL,
    campaign_name text NOT NULL,
    platform      text NOT NULL,
    spend         numeric(14,2) NOT NULL,
    impressions   bigint NOT NULL,
    clicks        bigint NOT NULL,
    updated_at    timestamptz NOT NULL,
    run_id        int NOT NULL,
    PRIMARY KEY (campaign_id, ad_date));

CREATE TABLE IF NOT EXISTS staging.leads (
    lead_id          text PRIMARY KEY,
    name             text,
    phone            text,
    email            text,
    utm_raw          text,
    utm_clean        text,
    created_at       timestamptz NOT NULL,
    created_date     date NOT NULL,          -- Indian calendar day
    source_file      text NOT NULL,
    source_file_date date NOT NULL,
    run_id           int NOT NULL);

CREATE TABLE IF NOT EXISTS staging.orders (
    order_id     text PRIMARY KEY,
    phone        text,
    order_ts     timestamptz NOT NULL,
    order_date   date NOT NULL,              -- Indian calendar day
    status       text NOT NULL,
    payment_mode text NOT NULL,
    total_amount numeric(14,2) NOT NULL,     -- in RUPEES
    items_total  numeric(14,2) NOT NULL,
    was_paise    boolean NOT NULL,
    updated_at   timestamptz NOT NULL,
    run_id       int NOT NULL);

CREATE TABLE IF NOT EXISTS staging.order_items (
    order_id   text NOT NULL,
    line_no    int  NOT NULL,
    sku        text NOT NULL,
    qty        int  NOT NULL,
    unit_price numeric(12,2) NOT NULL,
    PRIMARY KEY (order_id, line_no));

-- ----------------------------------------------------------------- FINAL
CREATE TABLE IF NOT EXISTS dim_campaign (
    campaign_id   text PRIMARY KEY,
    platform      text NOT NULL,
    campaign_name text NOT NULL,             -- clean: lower-case, trimmed
    first_ad_date date,
    last_ad_date  date);

CREATE TABLE IF NOT EXISTS fact_ad_spend_daily (
    campaign_id text NOT NULL REFERENCES dim_campaign(campaign_id),
    ad_date     date NOT NULL,
    spend       numeric(14,2) NOT NULL CHECK (spend >= 0),
    impressions bigint NOT NULL CHECK (impressions >= 0),
    clicks      bigint NOT NULL CHECK (clicks >= 0),
    updated_at  timestamptz NOT NULL,
    PRIMARY KEY (campaign_id, ad_date),
    CHECK (clicks <= impressions));

CREATE TABLE IF NOT EXISTS dim_customer (
    customer_id    bigserial PRIMARY KEY,
    phone          text NOT NULL UNIQUE CHECK (phone ~ '^[6-9][0-9]{9}$'),
    name           text,
    email          text,
    first_seen_at  timestamptz,
    source_systems text);                    -- crm | store | crm,store

CREATE TABLE IF NOT EXISTS fact_leads (
    lead_id               text PRIMARY KEY,
    customer_id           bigint REFERENCES dim_customer(customer_id),
    campaign_id           text REFERENCES dim_campaign(campaign_id),
    name                  text,
    phone                 text,
    email                 text,
    utm_campaign_raw      text,
    campaign_match_status text NOT NULL,     -- matched | unmatched
    customer_match_status text NOT NULL,     -- matched | unmatched
    created_at            timestamptz NOT NULL,
    created_date          date NOT NULL,
    source_file           text NOT NULL);
CREATE INDEX IF NOT EXISTS ix_fact_leads_customer ON fact_leads (customer_id, created_at);

CREATE TABLE IF NOT EXISTS fact_orders (
    order_id               text PRIMARY KEY,
    customer_id            bigint REFERENCES dim_customer(customer_id),
    phone                  text,
    order_ts               timestamptz NOT NULL,
    order_date             date NOT NULL,
    status                 text NOT NULL,
    payment_mode           text NOT NULL,
    total_amount           numeric(14,2) NOT NULL,   -- rupees
    items_total            numeric(14,2) NOT NULL,
    item_count             int NOT NULL,
    is_net                 boolean NOT NULL,         -- not cancelled and not returned
    attributed_campaign_id text REFERENCES dim_campaign(campaign_id),
    attribution_status     text NOT NULL,            -- last_lead_within_30d | unattributed
    customer_match_status  text NOT NULL,            -- matched | unmatched
    updated_at             timestamptz NOT NULL);

CREATE TABLE IF NOT EXISTS fact_order_items (
    order_id   text NOT NULL REFERENCES fact_orders(order_id),
    line_no    int  NOT NULL,
    sku        text NOT NULL,
    qty        int  NOT NULL CHECK (qty > 0),
    unit_price numeric(12,2) NOT NULL CHECK (unit_price >= 0),
    line_total numeric(14,2) NOT NULL,
    PRIMARY KEY (order_id, line_no));
