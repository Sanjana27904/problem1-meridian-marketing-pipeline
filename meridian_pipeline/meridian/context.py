"""Shared bookkeeping for one pipeline run: counters, rejected-row log, JSON helper."""
import json
from dataclasses import dataclass, field
from decimal import Decimal

from psycopg2.extras import Json, execute_values

ZERO = Decimal("0")


def json_default(o):
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def to_json(obj):
    return Json(obj, dumps=lambda x: json.dumps(x, default=json_default, ensure_ascii=False))


@dataclass
class SourceStats:
    """The control totals for one source in one run.

    The golden rule:  rows_read = rows_loaded + rows_rejected + duplicates_removed
    For sources that carry money the same rule must hold for the rupee totals.
    """
    has_value: bool = False
    rows_read: int = 0
    rows_loaded: int = 0
    rows_rejected: int = 0
    duplicates_removed: int = 0
    dup_superseded: int = 0            # an older version of the same key inside this batch
    dup_unchanged_or_stale: int = 0    # already in staging with same content, or older than staging
    staging_inserted: int = 0
    staging_updated: int = 0
    warnings_logged: int = 0           # field_nulled issues (row kept, one field blanked)
    value_read: Decimal = ZERO
    value_loaded: Decimal = ZERO
    value_rejected: Decimal = ZERO
    value_duplicates: Decimal = ZERO
    naive_value_as_received: Decimal = ZERO
    cleaning: dict = field(default_factory=dict)

    def counts_balance(self):
        return self.rows_read == self.rows_loaded + self.rows_rejected + self.duplicates_removed

    def values_balance(self):
        if not self.has_value:
            return True
        return self.value_read == self.value_loaded + self.value_rejected + self.value_duplicates

    def identity_ok(self):
        return self.counts_balance() and self.values_balance()

    def as_dict(self):
        d = dict(self.__dict__)
        d["identity_ok"] = self.identity_ok()
        return d


class RunContext:
    def __init__(self, cur, run_id):
        self.cur, self.run_id = cur, run_id
        self._issues = []

    def log_issue(self, source, issue, key, original):
        self._issues.append((self.run_id, source, issue.rule, issue.reason, issue.action,
                             None if key is None else str(key), to_json(original)))

    def flush_issues(self):
        if self._issues:
            execute_values(
                self.cur,
                "INSERT INTO rejected_rows (run_id, source, rule_name, reason, action, record_key, original_data) VALUES %s",
                self._issues, page_size=1000)
        n = len(self._issues)
        self._issues = []
        return n
