"""EXTRACT: get data out of the three sources. No cleaning here, we only fetch.

* ads    -> REST API, paginated, retries with a growing wait
* leads  -> one CSV per day (only files we have not loaded yet)
* orders -> one JSON file (only records changed since the last run)
"""
import csv
import hashlib
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests

from .cleaning import UTC, parse_timestamp


class ApiError(RuntimeError):
    pass


class AdsApiClient:
    """Calls GET /ads page by page. Retries 429 / 5xx / network errors with exponential backoff:
    wait = base_delay * 2**attempt  (0.5s, 1s, 2s, 4s, 8s, 16s) plus a little random jitter."""

    def __init__(self, base_url, page_size=100, max_retries=6, base_delay=0.5,
                 session=None, sleep=time.sleep, timeout=15, jitter=0.1):
        self.base_url = base_url.rstrip("/")
        self.page_size, self.max_retries, self.base_delay = page_size, max_retries, base_delay
        self.session = session or requests.Session()
        self.sleep, self.timeout, self.jitter = sleep, timeout, jitter
        self.stats = {"requests": 0, "pages": 0, "retries": 0, "http_429": 0, "http_5xx": 0, "network_errors": 0}

    def _get_json(self, params):
        last_error = None
        for attempt in range(self.max_retries + 1):
            self.stats["requests"] += 1
            try:
                resp = self.session.get(f"{self.base_url}/ads", params=params, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                self.stats["network_errors"] += 1
                last_error = f"network error: {exc}"
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429:
                    self.stats["http_429"] += 1
                elif resp.status_code >= 500:
                    self.stats["http_5xx"] += 1
                else:                                   # 400, 401, 404 ... retrying will not help
                    raise ApiError(f"ads API returned {resp.status_code}: {resp.text[:200]}")
                last_error = f"HTTP {resp.status_code}"
            if attempt == self.max_retries:
                break
            wait = self.base_delay * (2 ** attempt)
            self.stats["retries"] += 1
            self.sleep(wait + random.uniform(0, wait * self.jitter))
        raise ApiError(f"ads API failed after {self.max_retries} retries ({last_error})")

    def fetch_all(self, updated_since=None):
        """Yield every row, page after page."""
        params = {"page": 1, "page_size": self.page_size}
        if updated_since is not None:
            params["updated_since"] = updated_since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        while True:
            body = self._get_json(params)
            self.stats["pages"] += 1
            yield from body["data"]
            if params["page"] >= body["total_pages"] or not body["data"]:
                return
            params["page"] += 1


# ------------------------------------------------------------------ CRM files
CRM_FILE_RE = re.compile(r"^leads_(\d{8})\.csv$")


@dataclass
class CrmFile:
    name: str
    file_date: object
    file_hash: str
    rows: list


def read_new_crm_files(crm_dir, already_loaded):
    """Read the CSV files whose names we have not loaded before, oldest first."""
    files = []
    for path in sorted(Path(crm_dir).glob("leads_*.csv")):
        m = CRM_FILE_RE.match(path.name)
        if not m or path.name in already_loaded:
            continue
        raw_bytes = path.read_bytes()
        # utf-8-sig silently removes the BOM (byte-order mark) that some exports put at the start
        text = raw_bytes.decode("utf-8-sig")
        reader = csv.DictReader(text.splitlines(), skipinitialspace=False)
        rows = []
        for row in reader:
            rows.append({(k or "").strip(): v for k, v in row.items() if k is not None})
        files.append(CrmFile(path.name, datetime.strptime(m.group(1), "%Y%m%d").date(),
                             hashlib.md5(raw_bytes).hexdigest(), rows))
    return files


# ---------------------------------------------------------------- orders JSON
def read_orders(path, since):
    """Load the orders export and keep only records with updated_at >= watermark.
    Records whose updated_at cannot be parsed are kept, so validation can reject and log them."""
    records = json.loads(Path(path).read_text(encoding="utf-8"))
    if since is None:
        return records
    keep = []
    for rec in records:
        ts = parse_timestamp(rec.get("updated_at"), UTC)
        if ts is None or ts >= since:
            keep.append(rec)
    return keep
