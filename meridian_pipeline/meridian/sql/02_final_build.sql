-- =====================================================================
-- Build the FINAL layer from STAGING. Every statement is an UPSERT
-- (INSERT ... ON CONFLICT DO UPDATE), so running it again gives the same result.
-- Order matters: dimensions first, then facts that point at them.
-- =====================================================================

-- 1. campaigns: one row per campaign (latest platform/name wins)
INSERT INTO dim_campaign (campaign_id, platform, campaign_name, first_ad_date, last_ad_date)
SELECT campaign_id,
       (array_agg(platform      ORDER BY updated_at DESC))[1],
       (array_agg(campaign_name ORDER BY updated_at DESC))[1],
       min(ad_date), max(ad_date)
FROM staging.ads
GROUP BY campaign_id
ON CONFLICT (campaign_id) DO UPDATE
   SET platform = EXCLUDED.platform, campaign_name = EXCLUDED.campaign_name,
       first_ad_date = EXCLUDED.first_ad_date, last_ad_date = EXCLUDED.last_ad_date;

-- 2. daily ad spend
INSERT INTO fact_ad_spend_daily (campaign_id, ad_date, spend, impressions, clicks, updated_at)
SELECT campaign_id, ad_date, spend, impressions, clicks, updated_at FROM staging.ads
ON CONFLICT (campaign_id, ad_date) DO UPDATE
   SET spend = EXCLUDED.spend, impressions = EXCLUDED.impressions,
       clicks = EXCLUDED.clicks, updated_at = EXCLUDED.updated_at
 WHERE (fact_ad_spend_daily.spend, fact_ad_spend_daily.impressions, fact_ad_spend_daily.clicks, fact_ad_spend_daily.updated_at)
       IS DISTINCT FROM (EXCLUDED.spend, EXCLUDED.impressions, EXCLUDED.clicks, EXCLUDED.updated_at);

-- 3. customers: one row per cleaned phone number, seen in CRM and/or store
INSERT INTO dim_customer (phone, name, email, first_seen_at, source_systems)
SELECT phone,
       (array_agg(name  ORDER BY ts DESC) FILTER (WHERE name  IS NOT NULL))[1],
       (array_agg(email ORDER BY ts DESC) FILTER (WHERE email IS NOT NULL))[1],
       min(ts),
       string_agg(DISTINCT src, ',' ORDER BY src)
FROM (
    SELECT phone, name, email, created_at AS ts, 'crm'   AS src FROM staging.leads  WHERE phone IS NOT NULL
    UNION ALL
    SELECT phone, NULL,  NULL,  order_ts,        'store' AS src FROM staging.orders WHERE phone IS NOT NULL
) x
GROUP BY phone
ON CONFLICT (phone) DO UPDATE
   SET name = EXCLUDED.name, email = EXCLUDED.email,
       first_seen_at = EXCLUDED.first_seen_at, source_systems = EXCLUDED.source_systems;

-- 4. leads: link to customer (by phone) and campaign (by utm_campaign). No match -> kept, flagged 'unmatched'
INSERT INTO fact_leads (lead_id, customer_id, campaign_id, name, phone, email, utm_campaign_raw,
                        campaign_match_status, customer_match_status, created_at, created_date, source_file)
SELECT l.lead_id, c.customer_id, cp.campaign_id, l.name, l.phone, l.email, l.utm_raw,
       CASE WHEN cp.campaign_id IS NULL THEN 'unmatched' ELSE 'matched' END,
       CASE WHEN c.customer_id  IS NULL THEN 'unmatched' ELSE 'matched' END,
       l.created_at, l.created_date, l.source_file
FROM staging.leads l
LEFT JOIN dim_customer c ON c.phone = l.phone
LEFT JOIN LATERAL (
     SELECT d.campaign_id FROM dim_campaign d
     WHERE d.campaign_name = l.utm_clean ORDER BY d.campaign_id LIMIT 1) cp ON true
ON CONFLICT (lead_id) DO UPDATE
   SET customer_id = EXCLUDED.customer_id, campaign_id = EXCLUDED.campaign_id, name = EXCLUDED.name,
       phone = EXCLUDED.phone, email = EXCLUDED.email, utm_campaign_raw = EXCLUDED.utm_campaign_raw,
       campaign_match_status = EXCLUDED.campaign_match_status,
       customer_match_status = EXCLUDED.customer_match_status,
       created_at = EXCLUDED.created_at, created_date = EXCLUDED.created_date, source_file = EXCLUDED.source_file;

-- 5. orders: link to customer by phone; credit the campaign of the customer's most recent
--    lead in the 30 days before the order (last-touch attribution)
INSERT INTO fact_orders (order_id, customer_id, phone, order_ts, order_date, status, payment_mode,
                         total_amount, items_total, item_count, is_net, attributed_campaign_id,
                         attribution_status, customer_match_status, updated_at)
SELECT o.order_id, c.customer_id, o.phone, o.order_ts, o.order_date, o.status, o.payment_mode,
       o.total_amount, o.items_total,
       (SELECT count(*) FROM staging.order_items i WHERE i.order_id = o.order_id),
       o.status NOT IN ('cancelled', 'returned'),
       a.campaign_id,
       CASE WHEN a.campaign_id IS NULL THEN 'unattributed' ELSE 'last_lead_within_30d' END,
       CASE WHEN c.customer_id IS NULL THEN 'unmatched' ELSE 'matched' END,
       o.updated_at
FROM staging.orders o
LEFT JOIN dim_customer c ON c.phone = o.phone
LEFT JOIN LATERAL (
     SELECT fl.campaign_id FROM fact_leads fl
     WHERE fl.customer_id = c.customer_id AND fl.campaign_id IS NOT NULL
       AND fl.created_at <= o.order_ts AND fl.created_at >= o.order_ts - interval '30 days'
     ORDER BY fl.created_at DESC, fl.lead_id DESC LIMIT 1) a ON true
ON CONFLICT (order_id) DO UPDATE
   SET customer_id = EXCLUDED.customer_id, phone = EXCLUDED.phone, order_ts = EXCLUDED.order_ts,
       order_date = EXCLUDED.order_date, status = EXCLUDED.status, payment_mode = EXCLUDED.payment_mode,
       total_amount = EXCLUDED.total_amount, items_total = EXCLUDED.items_total,
       item_count = EXCLUDED.item_count, is_net = EXCLUDED.is_net,
       attributed_campaign_id = EXCLUDED.attributed_campaign_id,
       attribution_status = EXCLUDED.attribution_status,
       customer_match_status = EXCLUDED.customer_match_status, updated_at = EXCLUDED.updated_at;

-- 6. order items (+ remove lines that an order's newer version no longer has)
INSERT INTO fact_order_items (order_id, line_no, sku, qty, unit_price, line_total)
SELECT order_id, line_no, sku, qty, unit_price, qty * unit_price FROM staging.order_items
ON CONFLICT (order_id, line_no) DO UPDATE
   SET sku = EXCLUDED.sku, qty = EXCLUDED.qty, unit_price = EXCLUDED.unit_price, line_total = EXCLUDED.line_total;

DELETE FROM fact_order_items f
WHERE NOT EXISTS (SELECT 1 FROM staging.order_items s WHERE s.order_id = f.order_id AND s.line_no = f.line_no);
