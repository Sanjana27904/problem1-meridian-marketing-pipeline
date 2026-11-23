"""A tiny fake 'Meta + Google Ads' REST API.

    python -m sources.ads_api --data data/ads_source.json --port 5055

GET /ads?page=1&page_size=100&updated_since=2026-10-22T02:00:00Z
  -> {"page", "page_size", "total_rows", "total_pages", "data": [...]}

* pages of at most 100 rows
* updated_since is INCLUSIVE (>=) so a row that shares the exact watermark is never missed
* about 1 request in 20 fails with 429 (too many requests) or 500 (server error), like a real API
"""
import argparse
import json
import math
import random
from datetime import datetime, timezone

from flask import Flask, jsonify, request


def _parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def create_app(data_path, fail_rate=0.05, seed=None):
    app = Flask(__name__)
    rng = random.Random(seed)
    app.config["failures"] = 0

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/ads")
    def ads():
        if rng.random() < fail_rate:
            app.config["failures"] += 1
            code = rng.choice([429, 500])
            return jsonify(error="too many requests" if code == 429 else "internal error"), code
        with open(data_path, encoding="utf-8") as f:
            rows = json.load(f)
        since = request.args.get("updated_since")
        if since:
            cutoff = _parse(since)
            rows = [r for r in rows if _parse(r["updated_at"]) >= cutoff]
        rows.sort(key=lambda r: (_parse(r["updated_at"]), r["campaign_id"], str(r["date"])))
        page = max(1, request.args.get("page", 1, type=int))
        size = min(100, max(1, request.args.get("page_size", 100, type=int)))
        total = len(rows)
        return jsonify(page=page, page_size=size, total_rows=total,
                       total_pages=math.ceil(total / size),
                       data=rows[(page - 1) * size: page * size])

    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/ads_source.json")
    ap.add_argument("--port", type=int, default=5055)
    ap.add_argument("--fail-rate", type=float, default=0.05)
    a = ap.parse_args()
    create_app(a.data, a.fail_rate).run(host="127.0.0.1", port=a.port, threaded=True)
