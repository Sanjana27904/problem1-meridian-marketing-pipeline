"""Append-only RAW layer: preserve every source occurrence exactly as received.

A raw row represents an ingestion occurrence, not a unique business record. Therefore
row_hash is deliberately non-unique: repeated API pages, overlapping watermarks and
identical source rows are all retained with their own run_id and loaded_at. Deduplication
happens downstream in staging/final, where business keys and source timestamps apply.
"""
import hashlib
import json

from psycopg2.extras import execute_values

from .context import to_json


def record_hash(rec, extra=""):
    """Stable fingerprint for lineage/diagnostics; never used to suppress raw inserts."""
    blob = f"{extra}|{json.dumps(rec, sort_keys=True, ensure_ascii=False, default=str)}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_raw(cur, table, run_id, records, extras=None):
    """Append every received record and return all records for downstream processing.

    ``extras`` is used for CRM source_file/line_no lineage. The second return value is
    retained as an empty compatibility value; duplicates are now classified by staging
    deduplication/upsert logic rather than by suppressing raw ingestion occurrences.
    """
    if table not in {"ads", "leads", "orders"}:
        raise ValueError(f"Unsupported raw table: {table}")
    extras = extras if extras is not None else [None] * len(records)
    if len(extras) != len(records):
        raise ValueError("extras must have the same number of entries as records")

    rows = []
    returned = []
    for rec, extra in zip(records, extras):
        fingerprint = record_hash(rec, extra[0] if extra else "")
        if table == "leads":
            if not extra or len(extra) != 2:
                raise ValueError("lead raw rows require (source_file, line_no) metadata")
            rows.append((run_id, fingerprint, extra[0], extra[1], to_json(rec)))
        else:
            rows.append((run_id, fingerprint, to_json(rec)))
        returned.append((rec, extra))

    if rows:
        if table == "leads":
            execute_values(cur,
                "INSERT INTO raw.leads (run_id, row_hash, source_file, line_no, payload) VALUES %s",
                rows, page_size=1000)
        else:
            execute_values(cur,
                f"INSERT INTO raw.{table} (run_id, row_hash, payload) VALUES %s",
                rows, page_size=1000)
    return returned, []
