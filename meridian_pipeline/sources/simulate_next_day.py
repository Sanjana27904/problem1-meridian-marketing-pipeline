"""Simulate ONE more day of data arriving, to prove the incremental load works.

    python -m sources.simulate_next_day --data data

Adds to the existing source files:
  * ads    : a new day of rows for every campaign + corrections for ~40 older rows
  * leads  : a NEW csv file for the next day (with a few repeated lead_ids from the previous file)
  * orders : ~170 new orders + new status versions (e.g. shipped -> delivered) for ~120 old orders
New rows get an updated_at AFTER the current newest one, like a real system stamping the time of change.
"""
import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .generate_data import START_DATE, UTC, Generator, iso_z, write_leads


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def simulate(data_dir):
    out = Path(data_dir)
    meta = json.loads((out / ".gen_meta.json").read_text(encoding="utf-8"))
    g = Generator(meta["seed"], meta["campaigns"], meta["days"], meta["customers"])
    new_day = meta["days"]
    rng = random.Random(f"{meta['seed']}-sim-{new_day}")

    # ---------------- ads
    ads = json.loads((out / "ads_source.json").read_text(encoding="utf-8"))
    base = max(_t(r["updated_at"]) for r in ads) + timedelta(hours=1)
    new_rows = g.ads(new_day, new_day + 1, correction_rate=0.0)
    for r in new_rows:
        r["updated_at"] = iso_z(base + timedelta(seconds=rng.randint(0, 3000)))
    numeric = [r for r in ads if isinstance(r["spend"], (int, float)) and r["spend"] > 0 and r["clicks"] <= r["impressions"]]
    corrections = []
    for r in rng.sample(numeric, min(40, len(numeric))):
        c = dict(r)
        c["spend"] = round(r["spend"] * rng.uniform(0.9, 1.1), 2)
        c["updated_at"] = iso_z(base + timedelta(seconds=rng.randint(0, 3000)))
        corrections.append(c)
    (out / "ads_source.json").write_text(json.dumps(ads + new_rows + corrections, ensure_ascii=False), encoding="utf-8")

    # ---------------- leads (a brand-new file)
    by_day, metas, next_no = g.leads(new_day, new_day + 1, meta["next_lead_no"], per_day=(250, 270))
    prev_file = sorted((out / "crm").glob("leads_*.csv"))[-1]
    import csv
    with open(prev_file, newline="", encoding="utf-8-sig") as f:
        prev_rows = list(csv.DictReader(f))
    by_day[new_day] += rng.sample(prev_rows, min(5, len(prev_rows)))              # same lead_id arrives again in the next file
    write_leads(out, by_day)

    # ---------------- orders
    orders = json.loads((out / "orders.json").read_text(encoding="utf-8"))
    base_o = max(_t(r["updated_at"]) for r in orders) + timedelta(hours=1)
    latest = {}
    for r in orders:
        if r["order_id"] not in latest or _t(r["updated_at"]) >= _t(latest[r["order_id"]]["updated_at"]):
            latest[r["order_id"]] = r
    progress = {"placed": "confirmed", "confirmed": "shipped", "shipped": "delivered"}
    movers = [r for r in latest.values() if str(r["status"]).strip().lower() in progress and r["items"]
              and all(i.get("sku") and str(i.get("qty")) not in ("0", "-1") for i in r["items"])]
    versions = []
    for r in rng.sample(movers, min(120, len(movers))):
        v = json.loads(json.dumps(r))
        v["status"] = progress[str(r["status"]).strip().lower()]
        v["total_amount"] = float(sum(int(i["qty"]) * i["unit_price"] for i in v["items"]))   # a clean, correct total
        v["updated_at"] = iso_z(base_o + timedelta(seconds=rng.randint(0, 3000)))
        versions.append(v)
    new_orders = g.orders(170, meta["next_order_no"], meta["lead_metas"] + metas, (new_day, new_day + 1), tag="sim")
    for r in new_orders:
        r["updated_at"] = iso_z(base_o + timedelta(seconds=rng.randint(0, 3000)))
    (out / "orders.json").write_text(json.dumps(orders + versions + new_orders, ensure_ascii=False), encoding="utf-8")

    meta.update(days=new_day + 1, next_lead_no=next_no, next_order_no=meta["next_order_no"] + 170,
                lead_metas=(meta["lead_metas"] + metas)[-3000:])
    (out / ".gen_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return {"new_ad_rows": len(new_rows), "ad_corrections": len(corrections),
            "new_leads_file": f"leads_{(START_DATE + timedelta(days=new_day)):%Y%m%d}.csv",
            "new_lead_rows": len(by_day[new_day]), "new_orders": 170, "order_status_updates": len(versions)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    print(simulate(ap.parse_args().data))
