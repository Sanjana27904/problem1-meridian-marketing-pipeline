"""LEADS: raw -> clean -> validate -> deduplicate (one row per lead_id) -> staging."""
import re
import unicodedata
from collections import Counter

from psycopg2.extras import execute_values

from . import raw
from .cleaning import IST, clean_campaign_name, clean_email, clean_phone, has_timezone, parse_timestamp, to_ist_date
from .context import SourceStats
from .dedupe import latest_wins
from .validation import Issue, REJECTED, hard_failures, soft_issues, validate_lead

UPSERT_SQL = """
INSERT INTO staging.leads (lead_id, name, phone, email, utm_raw, utm_clean, created_at, created_date,
                           source_file, source_file_date, run_id)
VALUES %s
ON CONFLICT (lead_id) DO UPDATE
   SET name = EXCLUDED.name, phone = EXCLUDED.phone, email = EXCLUDED.email, utm_raw = EXCLUDED.utm_raw,
       utm_clean = EXCLUDED.utm_clean, created_at = EXCLUDED.created_at, created_date = EXCLUDED.created_date,
       source_file = EXCLUDED.source_file, source_file_date = EXCLUDED.source_file_date, run_id = EXCLUDED.run_id
 WHERE EXCLUDED.source_file_date >= staging.leads.source_file_date
   AND (EXCLUDED.name, EXCLUDED.phone, EXCLUDED.email, EXCLUDED.utm_raw, EXCLUDED.utm_clean, EXCLUDED.created_at)
       IS DISTINCT FROM
       (staging.leads.name, staging.leads.phone, staging.leads.email, staging.leads.utm_raw, staging.leads.utm_clean, staging.leads.created_at)
RETURNING (xmax = 0) AS inserted
"""


def clean_lead(rec):
    raw_phone = (rec.get("phone") or "").strip()
    raw_email = (rec.get("email") or "").strip()
    name = unicodedata.normalize("NFC", re.sub(r"\s+", " ", rec.get("name") or "")).strip() or None
    created = parse_timestamp(rec.get("created_at"), IST)     # the CRM writes Indian time without a zone
    return {
        "lead_id": (rec.get("lead_id") or "").strip() or None,
        "name": name,
        "phone": clean_phone(raw_phone) if raw_phone else None,
        "phone_provided": bool(raw_phone), "raw_phone": raw_phone,
        "email": clean_email(raw_email), "email_provided": bool(raw_email), "raw_email": raw_email,
        "utm_raw": (rec.get("utm_campaign") or "") or None,
        "utm_clean": clean_campaign_name(rec.get("utm_campaign")),
        "created_at": created, "raw_created_at": rec.get("created_at"),
        "created_date": to_ist_date(created),
    }


def process_leads(ctx, files):
    st = SourceStats()
    records, extras, file_dates = [], [], {}
    for f in files:
        file_dates[f.name] = f.file_date
        for i, row in enumerate(f.rows, start=2):           # line 1 is the header
            records.append(row)
            extras.append((f.name, i))
    st.rows_read = len(records)
    count = Counter()

    received, _ = raw.load_raw(ctx.cur, "leads", ctx.run_id, records, extras)

    valid = []
    for rec, (fname, line) in received:
        c = clean_lead(rec)
        c.update(source_file=fname, file_date=file_dates[fname], line_no=line)
        original = {**rec, "_source_file": fname, "_line_no": line}
        key = c["lead_id"] or f"{fname}:{line}"
        issues = validate_lead(c)
        hard = hard_failures(issues)
        if hard:
            st.rows_rejected += 1
            ctx.log_issue("leads", Issue(hard[0].rule, "; ".join(i.reason for i in hard), REJECTED), key, original)
            continue
        for s in soft_issues(issues):                       # row is kept, one field blanked, problem still logged
            st.warnings_logged += 1
            ctx.log_issue("leads", s, key, original)
        if c["phone"] and c["phone"] != c["raw_phone"]:
            count["phone_reduced_to_10_digits"] += 1
        if c["email"] and c["email"] != c["raw_email"]:
            count["email_trimmed_or_lowercased"] += 1
        if c["name"] and not c["name"].isascii():
            count["non_latin_names_kept_intact"] += 1
        if not has_timezone(rec.get("created_at")):
            count["created_at_assumed_IST_then_converted_to_UTC"] += 1
        valid.append(c)

    winners, losers = latest_wins(valid, key_fn=lambda c: c["lead_id"],
                                  order_fn=lambda c: (c["file_date"], c["line_no"]))
    st.dup_superseded = len(losers)
    st.duplicates_removed += len(losers)

    values = [(c["lead_id"], c["name"], c["phone"], c["email"], c["utm_raw"], c["utm_clean"], c["created_at"],
               c["created_date"], c["source_file"], c["file_date"], ctx.run_id) for c in winners]
    returned = execute_values(ctx.cur, UPSERT_SQL, values, page_size=1000, fetch=True) if values else []
    st.staging_inserted = sum(1 for r in returned if r[0])
    st.staging_updated = len(returned) - st.staging_inserted
    st.rows_loaded = len(returned)
    st.dup_unchanged_or_stale = len(winners) - len(returned)   # e.g. the same lead repeated in a later file
    st.duplicates_removed += st.dup_unchanged_or_stale
    st.cleaning = dict(count)
    return st
