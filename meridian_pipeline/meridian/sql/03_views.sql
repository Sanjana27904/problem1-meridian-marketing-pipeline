-- =====================================================================
-- The "one trusted view" the marketing head reads every morning.
-- Views hold no data of their own, so they are always in step with the facts.
-- =====================================================================

-- One row per campaign per day.
--   spend / impressions / clicks : by ad date
--   leads                        : by lead date (India), only leads matched to a campaign
--   orders, net_revenue          : by order date (India), credited to the attributed campaign
--   new_customers                : customers whose FIRST net order (not cancelled/returned) falls on that day
CREATE OR REPLACE VIEW vw_campaign_daily AS
WITH spend AS (
    SELECT campaign_id, ad_date AS report_date, spend, impressions, clicks FROM fact_ad_spend_daily
), leads AS (
    SELECT campaign_id, created_date AS report_date, count(*) AS leads
    FROM fact_leads WHERE campaign_id IS NOT NULL GROUP BY 1, 2
), orders AS (
    SELECT attributed_campaign_id AS campaign_id, order_date AS report_date,
           count(*) AS orders,
           count(*) FILTER (WHERE is_net) AS net_orders,
           coalesce(sum(total_amount) FILTER (WHERE is_net), 0) AS net_revenue
    FROM fact_orders WHERE attributed_campaign_id IS NOT NULL GROUP BY 1, 2
), first_orders AS (
    SELECT attributed_campaign_id AS campaign_id, order_date AS report_date, count(*) AS new_customers
    FROM (SELECT o.*, row_number() OVER (PARTITION BY customer_id ORDER BY order_ts, order_id) AS rn
          FROM fact_orders o WHERE customer_id IS NOT NULL AND is_net) t
    WHERE rn = 1 AND attributed_campaign_id IS NOT NULL
    GROUP BY 1, 2
), keys AS (
    SELECT campaign_id, report_date FROM spend
    UNION SELECT campaign_id, report_date FROM leads
    UNION SELECT campaign_id, report_date FROM orders
    UNION SELECT campaign_id, report_date FROM first_orders
)
SELECT k.campaign_id, c.campaign_name, c.platform, k.report_date,
       coalesce(s.spend, 0)        AS spend,
       coalesce(s.impressions, 0)  AS impressions,
       coalesce(s.clicks, 0)       AS clicks,
       coalesce(l.leads, 0)        AS leads,
       coalesce(o.orders, 0)       AS orders,
       coalesce(o.net_orders, 0)   AS net_orders,
       coalesce(o.net_revenue, 0)  AS net_revenue,
       coalesce(f.new_customers, 0) AS new_customers,
       round(s.spend / nullif(f.new_customers, 0), 2)  AS cac,
       round(o.net_revenue / nullif(s.spend, 0), 2)    AS roas
FROM keys k
JOIN dim_campaign c ON c.campaign_id = k.campaign_id
LEFT JOIN spend s        ON (s.campaign_id, s.report_date) = (k.campaign_id, k.report_date)
LEFT JOIN leads l        ON (l.campaign_id, l.report_date) = (k.campaign_id, k.report_date)
LEFT JOIN orders o       ON (o.campaign_id, o.report_date) = (k.campaign_id, k.report_date)
LEFT JOIN first_orders f ON (f.campaign_id, f.report_date) = (k.campaign_id, k.report_date);

-- Whole-period totals per campaign. CAC and ROAS are RATIOS OF SUMS (never an average of daily ratios).
CREATE OR REPLACE VIEW vw_campaign_performance AS
SELECT campaign_id, campaign_name, platform,
       sum(spend) AS spend, sum(impressions) AS impressions, sum(clicks) AS clicks,
       sum(leads) AS leads, sum(orders) AS orders, sum(net_revenue) AS net_revenue,
       sum(new_customers) AS new_customers,
       round(sum(spend) / nullif(sum(new_customers), 0), 2) AS cac,
       round(sum(net_revenue) / nullif(sum(spend), 0), 2)   AS roas
FROM vw_campaign_daily
GROUP BY campaign_id, campaign_name, platform;

-- Control totals per run and source, flattened out of pipeline_runs.details
CREATE OR REPLACE VIEW vw_control_totals AS
SELECT r.run_id, r.started_at, s.key AS source,
       (s.value ->> 'rows_read')::int           AS rows_read,
       (s.value ->> 'rows_loaded')::int         AS rows_loaded,
       (s.value ->> 'rows_rejected')::int       AS rows_rejected,
       (s.value ->> 'duplicates_removed')::int  AS duplicates_removed,
       (s.value ->> 'identity_ok')::boolean     AS counts_balance
FROM pipeline_runs r, jsonb_each(r.details -> 'sources') s
WHERE r.details IS NOT NULL;
