#!/usr/bin/env bash
# One command that shows everything: fresh data -> run -> re-run -> new day arrives -> re-run -> evidence.
# Needs: PostgreSQL reachable at $MERIDIAN_DSN (default postgresql://postgres@localhost:5433/meridian)
set -euo pipefail
cd "$(dirname "$0")"

rm -rf data && mkdir -p data output
echo "### 1. generate messy source data";            python3 -m sources.generate_data --out data
echo "### 2. start the fake ads API (fails ~1 in 20)"; python3 -m sources.ads_api --data data/ads_source.json --port 5055 > output/api.log 2>&1 &
API_PID=$!; trap 'kill $API_PID 2>/dev/null || true' EXIT; sleep 2

echo "### 3. first run (full load, from an empty database)"; python3 -m meridian.pipeline --reset
echo "### 4. run again on the SAME input (must change nothing)"; python3 -m meridian.pipeline
echo "### 5. a new day of data arrives";               python3 -m sources.simulate_next_day --data data
echo "### 6. incremental run (reads only the new data)"; python3 -m meridian.pipeline
echo "### 7. run again (must change nothing)";          python3 -m meridian.pipeline
echo "### 8. evidence pack";                            python3 -m meridian.report --markdown output/evidence.md
echo "### 9. tests";                                    python3 -m pytest -q
