# Meridian Threads - pipeline evidence

## 1. Run log (pipeline_runs)
_One row per run. Re-runs may append fetched occurrences to raw, while staging/final business tables and monetary totals remain unchanged._

| run_id | status | started | finished | rows_read | rows_loaded | rows_rejected | duplicates_removed |
|---|---|---|---|---|---|---|---|
| 1 | success | 20:44:47 | 20:44:58 | 19739 | 15668 | 180 | 3891 |
| 2 | success | 20:44:59 | 20:45:00 | 2 | 0 | 0 | 2 |
| 3 | success | 20:45:02 | 20:45:05 | 809 | 679 | 16 | 114 |
| 4 | success | 20:45:06 | 20:45:07 | 2 | 0 | 0 | 2 |

## 2. Control totals per source (rows_read = loaded + rejected + duplicates)

| run_id | source | rows_read | rows_loaded | rows_rejected | duplicates_removed | counts_balance |
|---|---|---|---|---|---|---|
| 1 | ads | 2808 | 2691 | 10 | 107 | True |
| 1 | leads | 8229 | 8034 | 35 | 160 | True |
| 1 | orders | 8702 | 4943 | 135 | 3624 | True |
| 2 | ads | 1 | 0 | 0 | 1 | True |
| 2 | leads | 0 | 0 | 0 | 0 | True |
| 2 | orders | 1 | 0 | 0 | 1 | True |
| 3 | ads | 131 | 126 | 4 | 1 | True |
| 3 | leads | 271 | 266 | 0 | 5 | True |
| 3 | orders | 407 | 287 | 12 | 108 | True |
| 4 | ads | 1 | 0 | 0 | 1 | True |
| 4 | leads | 0 | 0 | 0 | 0 | True |
| 4 | orders | 1 | 0 | 0 | 1 | True |

## 3. Rupee control totals, latest run that loaded data
_read = loaded + rejected + duplicates. The naive sum shows what you would get without cleaning (text spend ignored, paise not converted, repeats counted)._

| run_id | source | value_read | loaded | rejected | duplicates | naive_sum_as_received |
|---|---|---|---|---|---|---|
| 3 | ads | 1,316,576.61 | 1,304,893.70 | -6,471.06 | 18,153.97 | 1,155,366.97 |
| 3 | orders | 1,846,391.00 | 1,070,604.00 | 409,187.00 | 366,600.00 | 14,186,642.00 |

## 4. Raw ingestion history (append-only)
_Every received source occurrence is retained. Repeated fingerprints are expected when an inclusive watermark re-fetches an already-seen record; staging/final remain idempotent._

| source | raw_occurrences | distinct_fingerprints | repeated_fingerprint_occurrences |
|---|---|---|---|
| ads | 2941 | 2938 | 3 |
| leads | 8500 | 8500 | 0 |
| orders | 9111 | 9108 | 3 |

## 5. Final table sizes

| tbl | rows | total |
|---|---|---|
| dim_campaign | 90 |  |
| fact_ad_spend_daily | 2777 | 29,463,223.31 |
| rejected_rows | 743 |  |
| dim_customer | 4779 |  |
| fact_orders | 5112 | 18,110,059.00 |
| fact_order_items | 12243 | 18,110,059.00 |
| fact_leads | 8300 |  |

## 6. Rejected / flagged rows by rule
_action=rejected: kept OUT of the final tables. action=field_nulled: the row is kept, only the bad phone/email is blanked._

| source | rule_name | action | rows |
|---|---|---|---|
| ads | ads_clicks_le_impressions | rejected | 4 |
| ads | ads_spend_non_negative | rejected | 4 |
| ads | ads_date_valid | rejected | 3 |
| ads | ads_spend_parsable | rejected | 3 |
| leads | lead_email_valid | field_nulled | 361 |
| leads | lead_phone_valid | field_nulled | 134 |
| leads | lead_has_contact | rejected | 35 |
| orders | order_total_matches_items | rejected | 131 |
| orders | order_phone_valid | field_nulled | 52 |
| orders | order_sku_present | rejected | 6 |
| orders | order_qty_positive | rejected | 5 |
| orders | order_status_allowed | rejected | 4 |
| orders | order_has_items | rejected | 1 |

## 7. Unmatched records (counted and kept)

| what | rows |
|---|---|
| leads with no matching campaign | 685 |
| leads with no customer (no valid phone) | 453 |
| orders with no customer (no valid phone) | 90 |
| orders not attributable to a campaign | 1200 |

## 8. Top utm_campaign values that matched no campaign

| utm_campaign | leads |
|---|---|
| (empty) | 255 |
| (not set) | 74 |
| diwali_blast | 67 |
| organic | 64 |
| fb_generic | 59 |
| kids_range_google84 | 3 |
| festive_sal_google_46 | 2 |
| retargeting_meta45 | 2 |

## 9. Best ROAS in the last 7 days of data (a reviewer-facing business question)
_ROAS = net revenue / ad spend. CAC = ad spend / new customers. Net revenue excludes cancelled and returned orders._

| campaign_name | platform | spend | leads | orders | net_revenue | new_customers | cac | roas |
|---|---|---|---|---|---|---|---|---|
| office_wear_google_68 | Google | 37,151.73 | 16.00 | 24.00 | 79,336.00 | 12.00 | 3,095.98 | 2.14 |
| winter_wear_meta_05 | Meta | 55,394.62 | 28.00 | 24.00 | 109,766.00 | 8.00 | 6,924.33 | 1.98 |
| sneaker_drop_google_70 | Google | 55,137.86 | 22.00 | 24.00 | 100,122.00 | 12.00 | 4,594.82 | 1.82 |
| retargeting_google_30 | Google | 49,032.84 | 21.00 | 26.00 | 86,181.00 | 9.00 | 5,448.09 | 1.76 |
| kids_range_google_24 | Google | 47,790.59 | 21.00 | 21.00 | 75,235.00 | 10.00 | 4,779.06 | 1.57 |
| monsoon_offer_google_42 | Google | 63,751.18 | 22.00 | 28.00 | 93,028.00 | 10.00 | 6,375.12 | 1.46 |
| kurta_collection_meta_03 | Meta | 52,347.35 | 28.00 | 25.00 | 76,685.00 | 7.00 | 7,478.19 | 1.46 |
| saree_story_google_26 | Google | 30,151.38 | 18.00 | 18.00 | 42,613.00 | 7.00 | 4,307.34 | 1.41 |
| new_arrivals_google_36 | Google | 50,712.33 | 22.00 | 22.00 | 71,098.00 | 7.00 | 7,244.62 | 1.40 |
| office_wear_google_08 | Google | 48,754.24 | 18.00 | 25.00 | 67,782.00 | 10.00 | 4,875.42 | 1.39 |
