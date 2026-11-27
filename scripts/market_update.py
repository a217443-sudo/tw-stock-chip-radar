"""
Build market.json: every TWSE-listed company (上市), for the category ranking page.

All figures are official TWSE open data (no analyst estimates):
close/volume (MI_INDEX), PE/yield/PB (BWIBBU_d), 10-day 三大法人 net flow (T86),
company basics incl. industry code and shares (t187ap03_L), monthly revenue
growth (t187ap05_L), year-to-date EPS and gross margin (t187ap06_L_*).

Writes nothing unless the core data looks complete, so a bad download can never
replace a good market.json.
"""
import json, os, sys, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daily_update import fetch_json, ymd, get_twse_t86, SEASON_LABEL

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(REPO_ROOT, "market.json")
OPEN = "https://openapi.twse.com.tw/v1/opendata/"


def num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def rnd(v, n=2):
    return None if v is None else round(v, n)


def latest_quotes(today, lookback=10):
    d = today
    for _ in range(lookback):
        try:
            j = fetch_json(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={ymd(d)}&type=ALLBUT0999&response=json")
        except Exception:
            j = {}
        if j.get("stat") == "OK" and j.get("date") == ymd(d):
            for t in j.get("tables", []):
                f = t.get("fields") or []
                if "收盤價" not in f or "證券代號" not in f:
                    continue
                ix = {k: f.index(k) for k in ("證券代號", "收盤價", "成交股數", "漲跌(+/-)", "漲跌價差")}
                out = {}
                for row in t.get("data", []):
                    close = num(row[ix["收盤價"]])
                    if close is None:
                        continue
                    diff = num(row[ix["漲跌價差"]]) or 0.0
                    if "-" in row[ix["漲跌(+/-)"]]:
                        diff = -diff
                    prev = close - diff
                    out[row[ix["證券代號"]].strip()] = {
                        "close": close,
                        "chg": diff / prev * 100 if prev else None,
                        "vol": (num(row[ix["成交股數"]]) or 0) / 1000,
                    }
                if out:
                    return d, out
        d -= datetime.timedelta(days=1)
    return None, {}


def valuation(date):
    j = fetch_json(f"https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date={ymd(date)}&selectType=ALL&response=json")
    if j.get("stat") != "OK":
        return {}
    f = j["fields"]
    ci, yi, pi, bi = (f.index(k) for k in ("證券代號", "殖利率(%)", "本益比", "股價淨值比"))
    return {r[ci].strip(): {"pe": num(r[pi]), "yld": num(r[yi]), "pb": num(r[bi])} for r in j["data"]}


def flows(today, days=10, lookback=25):
    got = []
    d = today
    for _ in range(lookback):
        if len(got) >= days:
            break
        m = get_twse_t86(d)
        if m:
            got.append((d, m))
        d -= datetime.timedelta(days=1)
    got.reverse()
    return got


def main():
    today = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).date()  # Taipei
    price_date, quotes = latest_quotes(today)
    basic = fetch_json(OPEN + "t187ap03_L")
    history = flows(today)
    if not quotes or len(basic) < 800 or len(history) < 5:
        print(f"ERROR: core data incomplete (quotes={len(quotes)}, basic={len(basic)}, flow_days={len(history)}); keeping old market.json", file=sys.stderr)
        sys.exit(2)

    def optional(label, fn):
        try:
            return fn()
        except Exception as e:
            print(f"WARN: {label} failed: {e}", file=sys.stderr)
            return {}

    val = optional("valuation", lambda: valuation(price_date))
    rev_rows = optional("revenue", lambda: fetch_json(OPEN + "t187ap05_L"))
    rev, rev_month = {}, ""
    for r in rev_rows or []:
        cum = next((v for k, v in r.items() if k.startswith("累計") and "%" in k), None)
        yoy = next((v for k, v in r.items() if "去年同月增減" in k), None)
        rev[r["公司代號"].strip()] = (num(cum), num(yoy))
        rev_month = max(rev_month, str(r.get("資料年月", "")))

    fin, periods = {}, {}
    for kind in ("ci", "basi", "bd", "fh", "ins", "mim"):
        for r in optional("income " + kind, lambda k=kind: fetch_json(OPEN + "t187ap06_L_" + k)) or []:
            code = str(r.get("公司代號", "")).strip()
            eps = next((num(v) for k, v in r.items() if "每股盈餘" in k), None)
            sales, gross = num(r.get("營業收入")), num(r.get("營業毛利（毛損）"))
            fin[code] = (eps, gross / sales * 100 if sales and gross is not None else None)
            try:
                label = f"{(int(r['年度']) + 1911) % 100}年{SEASON_LABEL[int(r['季別'])]}"
                periods[label] = periods.get(label, 0) + 1
            except (KeyError, ValueError):
                pass

    if not val or not rev:
        print("ERROR: valuation or revenue data missing; keeping old market.json", file=sys.stderr)
        sys.exit(2)

    rows = []
    for b in basic:
        code = b["公司代號"].strip()
        q = quotes.get(code)
        if not q:
            continue
        shares = num(b.get("已發行普通股數或TDR原股發行股數"))
        recs = [m.get(code, {"f": 0.0, "t": 0.0}) for _, m in history]
        f10 = sum(x["f"] for x in recs)
        t10 = sum(x["t"] for x in recs)
        last5 = recs[-5:]
        v = val.get(code, {})
        cum, yoy = rev.get(code, (None, None))
        eps, gm = fin.get(code, (None, None))
        rows.append([
            code, b["公司簡稱"].strip(), b["產業別"].strip(),
            q["close"], rnd(q["chg"]), round(q["vol"]),
            v.get("pe"), v.get("yld"), v.get("pb"),
            rnd(cum, 1), rnd(yoy, 1), eps, rnd(gm, 1),
            rnd(f10 * 1000 / shares * 100, 3) if shares else None,
            rnd(t10 * 1000 / shares * 100, 3) if shares else None,
            round(f10), round(t10),
            round(sum(x["f"] for x in last5)), round(sum(x["t"] for x in last5)),
        ])

    if len(rows) < 800:
        print(f"ERROR: only {len(rows)} stocks matched; keeping old market.json", file=sys.stderr)
        sys.exit(2)

    out = {
        "priceDate": price_date.isoformat(),
        "flowFrom": history[0][0].isoformat(),
        "flowTo": history[-1][0].isoformat(),
        "revMonth": rev_month,
        "finPeriod": max(periods, key=periods.get) if periods else "",
        "fields": ["code", "name", "ind", "close", "chg", "vol", "pe", "yld", "pb", "revCum", "revYoY",
                   "eps", "gm", "f10Pct", "t10Pct", "f10", "t10", "f5", "t5"],
        "rows": sorted(rows),
    }
    with open(OUT_PATH, "w", encoding="utf-8") as fo:
        json.dump(out, fo, ensure_ascii=False, separators=(",", ":"))
    print(f"market.json: {len(rows)} stocks, price {out['priceDate']}, flow to {out['flowTo']}, revenue {rev_month}, fin {out['finPeriod']}", file=sys.stderr)


if __name__ == "__main__":
    main()
