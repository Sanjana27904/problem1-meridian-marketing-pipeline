"""Create deliberately MESSY source data for Meridian Threads.

    python -m sources.generate_data --out data

Writes:
    data/ads_source.json     rows the ads API will serve (the API reads this file)
    data/crm/leads_YYYYMMDD.csv   one CSV per day
    data/orders.json         store orders export
    data/.gen_meta.json      small helper file used by simulate_next_day.py

Everything is seeded, so the same seed gives the same data.
"""
import argparse
import csv
import json
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
START_DATE = date(2026, 10, 20)

THEMES = ["festive_sale", "summer_edit", "kurta_collection", "denim_days", "winter_wear",
          "new_arrivals", "ethnic_week", "office_wear", "kids_range", "sneaker_drop",
          "saree_story", "monsoon_offer", "wedding_season", "brand_awareness", "retargeting"]
FIRST = ["Aarav", "Priya", "Rohan", "Sneha", "Vikram", "Ananya", "Karthik", "Divya", "Arjun",
         "Meera", "Rahul", "Pooja", "Suresh", "Lakshmi", "Imran", "Fatima", "Nikhil", "Kavya"]
LAST = ["Sharma", "Reddy", "Nair", "Iyer", "Khan", "Patel", "Gupta", "Menon", "Singh", "Das", "Rao"]
HINDI = ["प्रिया शर्मा", "अमित कुमार", "राहुल वर्मा", "सुनीता देवी", "अनिल यादव", "पूजा गुप्ता"]
MALAYALAM = ["അനു മേനോൻ", "ജോസ് തോമസ്", "ലക്ഷ്മി നായർ", "രാജീവ് കുമാർ", "ശ്രീജ പിള്ള"]
DOMAINS = ["gmail.com", "yahoo.in", "outlook.com", "rediffmail.com"]
STATUS_CHAIN = {
    "placed": ["placed"],
    "confirmed": ["placed", "confirmed"],
    "shipped": ["placed", "confirmed", "shipped"],
    "delivered": ["placed", "confirmed", "shipped", "delivered"],
    "cancelled": ["placed", "cancelled"],
    "returned": ["placed", "confirmed", "shipped", "delivered", "returned"],
}
FINAL_STATUS_WEIGHTS = [("placed", 8), ("confirmed", 5), ("shipped", 10),
                        ("delivered", 55), ("cancelled", 12), ("returned", 10)]


def iso_z(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Generator:
    def __init__(self, seed=42, n_campaigns=90, n_days=30, n_customers=6000):
        self.seed, self.n_days = seed, n_days
        r = random.Random(f"{seed}-setup")
        self.campaigns = []
        for i in range(n_campaigns):
            platform = "Meta" if i % 2 == 0 else "Google"
            self.campaigns.append({
                "id": f"CMP{i + 1:03d}",
                "name": f"{THEMES[i % len(THEMES)]}_{platform.lower()}_{i + 1:02d}",
                "platform": platform,
            })
        pool = set()
        while len(pool) < n_customers:
            pool.add(r.choice("6789") + "".join(r.choice("0123456789") for _ in range(9)))
        self.pool = sorted(pool)
        cats = ["TSH", "JNS", "KRT", "SRE", "DRS", "JKT", "SNK", "SHT"]
        prices = [349, 399, 499, 599, 699, 799, 999, 1199, 1299, 1499, 1799, 1999, 2499, 2999]
        self.catalog = {f"SKU-{cats[i % len(cats)]}-{i + 1:03d}": r.choice(prices) for i in range(60)}

    # ------------------------------------------------------------------ ads
    def _ad_base(self, c, d, rng):
        impressions = rng.randint(2000, 40000)
        clicks = int(impressions * rng.uniform(0.01, 0.05))
        spend = round(clicks * rng.uniform(4, 30), 2)
        upd = datetime.combine(d + timedelta(days=1), time(1, rng.randint(0, 59), rng.randint(0, 59)), tzinfo=UTC)
        return {"campaign_id": c["id"], "campaign_name": c["name"], "platform": c["platform"], "date": d,
                "spend": spend, "impressions": impressions, "clicks": clicks, "updated_at": upd}

    @staticmethod
    def _messify_ad(row, rng):
        out = dict(row)
        out["date"] = row["date"].isoformat() if rng.random() < 0.7 else row["date"].strftime("%d/%m/%Y")
        if rng.random() < 0.2:                       # money as text
            out["spend"] = f"₹{row['spend']:,.2f}"
        r = rng.random()                              # campaign name: casing + trailing space
        name = row["campaign_name"]
        out["campaign_name"] = (name.upper() if r < 0.10 else name.title() if r < 0.15
                                else name + " " if r < 0.30 else name)
        out["platform"] = row["platform"] if rng.random() < 0.9 else row["platform"].upper()
        out["updated_at"] = iso_z(row["updated_at"])
        return out

    def ads(self, first_day=0, last_day=None, correction_rate=0.04):
        last_day = self.n_days if last_day is None else last_day
        rng = random.Random(f"{self.seed}-ads-{first_day}-{last_day}")
        base = [self._ad_base(c, START_DATE + timedelta(days=d), rng)
                for d in range(first_day, last_day) for c in self.campaigns]
        corrections = []
        for b in rng.sample(base, int(len(base) * correction_rate)):
            c = dict(b)                              # same campaign+date, corrected spend, NEWER updated_at
            c["spend"] = round(b["spend"] * rng.uniform(0.9, 1.1), 2)
            c["updated_at"] = b["updated_at"] + timedelta(days=2, hours=rng.randint(0, 5))
            corrections.append(c)
        rows = [self._messify_ad(b, rng) for b in base]
        n = len(rows)
        picks = rng.sample(range(n), max(1, n // 700) + max(1, n // 900) + 2 * max(1, n // 1300))
        k = max(1, n // 700)
        for i in picks[:k]:                          # clicks higher than impressions
            rows[i]["clicks"] = rows[i]["impressions"] + rng.randint(1, 500)
        j = k + max(1, n // 900)
        for i in picks[k:j]:                         # negative spend
            rows[i]["spend"] = -abs(float(str(rows[i]["spend"]).replace("₹", "").replace(",", "")))
        m = max(1, n // 1300)
        for i in picks[j:j + m]:                     # spend that is not a number
            rows[i]["spend"] = "N/A"
        for i in picks[j + m:]:                      # a date that does not exist
            rows[i]["date"] = "31/02/2026"
        rows += [self._messify_ad(c, rng) for c in corrections]
        return rows

    # ----------------------------------------------------------------- leads
    @staticmethod
    def fmt_phone(p, rng):
        forms = [p, f"+91 {p[:5]} {p[5:]}", f"+91{p}", f"0{p[:5]} {p[5:]}", f"0{p}",
                 f"{p[:5]}-{p[5:]}", f"91 {p}", f"+91-{p[:5]}-{p[5:]}", f"00 91 {p}"]
        return rng.choice(forms)

    def _lead(self, rng, no, day):
        phone = rng.choice(self.pool)
        camp = rng.choice(self.campaigns)
        created = datetime.combine(START_DATE + timedelta(days=day), time(0), IST) + timedelta(seconds=rng.randint(0, 86399))
        r = rng.random()
        if r < 0.08:
            name, handle = rng.choice(HINDI), f"user{rng.randint(1000, 99999)}"
        elif r < 0.15:
            name, handle = rng.choice(MALAYALAM), f"user{rng.randint(1000, 99999)}"
        else:
            f, l = rng.choice(FIRST), rng.choice(LAST)
            name, handle = f"{f} {l}", f"{f.lower()}{rng.randint(1, 999)}"
        if rng.random() < 0.05:
            name = f"  {name} "
        email = f"{handle}@{rng.choice(DOMAINS)}"
        r = rng.random()
        if r < 0.10:
            email = email.upper()
        elif r < 0.15:
            email = rng.choice([handle + "@", handle + ".gmail.com", f"{handle}@@gmail.com", f"{handle} @gmail.com", handle + "@gmail"])
        elif r < 0.18:
            email = ""
        r = rng.random()
        if r < 0.04:
            phone_txt = ""
        elif r < 0.055:
            phone_txt = rng.choice(["12345", "98765", "00000 00000", "9876-54"])
        else:
            phone_txt = self.fmt_phone(phone, rng)
        name_c = camp["name"]
        r = rng.random()
        if r < 0.03:
            utm = rng.choice(["diwali_blast", "(not set)", "organic", "fb_generic"])
        elif r < 0.05:
            k = rng.randrange(len(name_c))
            utm = name_c[:k] + name_c[k + 1:]        # typo -> will not match
        elif r < 0.08:
            utm = ""
        else:
            utm = (name_c if r < 0.55 else name_c.upper() if r < 0.70 else name_c.title() if r < 0.80
                   else name_c + " " if r < 0.90 else " " + name_c)
        r = rng.random()
        created_txt = (created.strftime("%Y-%m-%d %H:%M:%S") if r < 0.55
                       else created.strftime("%d/%m/%Y %H:%M") if r < 0.85
                       else created.strftime("%d-%m-%Y %I:%M %p"))
        row = {"lead_id": f"L{no:06d}", "name": name, "phone": phone_txt, "email": email,
               "utm_campaign": utm, "created_at": created_txt}
        meta = {"phone": phone if phone_txt not in ("", "12345", "98765", "00000 00000", "9876-54") else None,
                "created": iso_z(created)}
        return row, meta

    def leads(self, first_day, last_day, start_no, per_day=(240, 293)):
        """Returns ({day_index: [rows]}, [meta], next_lead_no)."""
        rng = random.Random(f"{self.seed}-leads-{first_day}-{last_day}")
        by_day, metas, no = {}, [], start_no
        for d in range(first_day, last_day):
            by_day[d] = []
            for _ in range(rng.randint(*per_day)):
                row, meta = self._lead(rng, no, d)
                by_day[d].append(row)
                metas.append(meta)
                no += 1
        # the same lead_id also shows up in a neighbouring day's file
        all_rows = [(d, r) for d, rows in by_day.items() for r in rows]
        days = sorted(by_day)
        for d, row in rng.sample(all_rows, max(1, int(len(all_rows) * 0.02))):
            other = d + 1 if d + 1 in by_day else d - 1
            if other not in by_day:
                continue
            copy = dict(row)
            if rng.random() < 0.3 and copy["phone"][-10:].isdigit():
                copy["phone"] = self.fmt_phone(copy["phone"][-10:], rng)
            by_day[other].append(copy)
        return by_day, metas, no

    # ---------------------------------------------------------------- orders
    def orders(self, n, start_no, lead_metas, day_range, tag="orders"):
        rng = random.Random(f"{self.seed}-{tag}-{start_no}")
        lo = datetime.combine(START_DATE + timedelta(days=day_range[0]), time(0), IST)
        hi = datetime.combine(START_DATE + timedelta(days=day_range[1]), time(0), IST) - timedelta(seconds=1)
        skus = list(self.catalog)
        records = []
        statuses, weights = zip(*FINAL_STATUS_WEIGHTS)
        for k in range(n):
            order_id = f"ORD{start_no + k}"
            if lead_metas and rng.random() < 0.7:
                lead = rng.choice(lead_metas)
                phone = lead["phone"] or rng.choice(self.pool)
                t = datetime.fromisoformat(lead["created"].replace("Z", "+00:00")) + timedelta(hours=rng.uniform(1, 240))
                t = min(max(t, lo), hi)
            else:
                phone = rng.choice(self.pool)
                t = lo + timedelta(seconds=rng.randint(0, int((hi - lo).total_seconds())))
            phone_field = "customer_phone" if rng.random() < 0.85 else "phone_number"
            items = []
            for _ in range(rng.choices([1, 2, 3, 4, 5], [30, 30, 20, 12, 8])[0]):
                sku = rng.choice(skus)
                items.append((sku, rng.choices([1, 2, 3], [75, 20, 5])[0], self.catalog[sku]))
            rupees = sum(q * p for _, q, p in items)
            final = rng.choices(statuses, weights)[0]
            chain = STATUS_CHAIN[final]
            emit = chain if rng.random() < 0.30 else [final]
            upd = t + timedelta(minutes=rng.randint(0, 5))
            for st in chain:
                if st in emit:
                    records.append(self._order_record(rng, order_id, phone, phone_field, t, upd, st, items, rupees))
                upd = upd + timedelta(hours=rng.uniform(2, 36))
        # a few rows that must be rejected
        n_rec = len(records)
        for i in rng.sample(range(n_rec), min(n_rec, 8)):
            r = records[i]
            kind = i % 4
            if kind == 0:
                r["items"][0]["qty"] = rng.choice([0, -1])
            elif kind == 1:
                r["status"] = "UNKNOWN"
            elif kind == 2:
                r["items"] = []
            else:
                r["items"][0]["sku"] = ""
        return records

    def _order_record(self, rng, order_id, phone, phone_field, t, upd, status, items, rupees):
        # status spelling
        if status == "cancelled":
            st = rng.choice(["CANCELED", "CANCELLED", "cancelled", "Cancelled", "canceled"])
        else:
            st = rng.choice([status, status.upper(), status.capitalize() + " "])
        # timestamp: some IST, some UTC
        r = rng.random()
        if r < 0.35:
            ts = t.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")             # IST, no zone written
        elif r < 0.50:
            ts = t.astimezone(IST).isoformat(timespec="seconds")             # IST with +05:30
        elif r < 0.90:
            ts = t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")            # UTC with Z
        else:
            ts = t.astimezone(UTC).isoformat(timespec="seconds")             # UTC with +00:00
        # total: normal / paise / wrong
        r = rng.random()
        total = float(rupees)
        if r < 0.08:
            total = int(rupees * 100)                                        # paise
        elif r < 0.095:
            total = float(rupees + rng.choice([-1, 1]) * rng.randint(50, 500))  # does not match
        # phone
        r = rng.random()
        phone_txt = (rng.choice(["", "12345", None]) if r < 0.02 else self.fmt_phone(phone, rng))
        rec = {"order_id": order_id, phone_field: phone_txt, "order_ts": ts, "updated_at": iso_z(upd),
               "status": st, "payment_mode": rng.choices(["UPI", "Card", "COD", "upi", "cod"], [45, 20, 20, 8, 7])[0],
               "total_amount": total,
               "items": [{"sku": s, "qty": (str(q) if rng.random() < 0.10 else q), "unit_price": float(p)} for s, q, p in items]}
        return rec


def write_leads(out_dir, by_day, bom_day=None):
    crm = Path(out_dir) / "crm"
    crm.mkdir(parents=True, exist_ok=True)
    for d, rows in by_day.items():
        fname = crm / f"leads_{(START_DATE + timedelta(days=d)):%Y%m%d}.csv"
        enc = "utf-8-sig" if d == bom_day else "utf-8"
        with open(fname, "w", newline="", encoding=enc) as f:
            w = csv.DictWriter(f, fieldnames=["lead_id", "name", "phone", "email", "utm_campaign", "created_at"])
            w.writeheader()
            w.writerows(rows)


def generate_all(out_dir, seed=42, campaigns=90, days=30, orders=5000, leads_per_day=(240, 293), customers=6000):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    g = Generator(seed, campaigns, days, customers)
    ads = g.ads(0, days)
    (out / "ads_source.json").write_text(json.dumps(ads, ensure_ascii=False), encoding="utf-8")
    by_day, metas, next_lead = g.leads(0, days, 1, leads_per_day)
    write_leads(out, by_day, bom_day=min(4, days - 1))
    recs = g.orders(orders, 100001, metas, (0, days))
    (out / "orders.json").write_text(json.dumps(recs, ensure_ascii=False), encoding="utf-8")
    (out / ".gen_meta.json").write_text(json.dumps({
        "seed": seed, "campaigns": campaigns, "days": days, "customers": customers,
        "next_lead_no": next_lead, "next_order_no": 100001 + orders, "lead_metas": metas[-3000:]}), encoding="utf-8")
    return {"ads_rows": len(ads), "lead_rows": sum(len(v) for v in by_day.values()),
            "order_records": len(recs), "crm_files": len(by_day)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--campaigns", type=int, default=90)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--orders", type=int, default=5000)
    a = ap.parse_args()
    print(generate_all(a.out, a.seed, a.campaigns, a.days, a.orders))
