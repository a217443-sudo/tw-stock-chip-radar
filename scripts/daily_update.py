"""
Daily refresh for index.html (台股籌碼雷達).

Reads index.html in place, carries forward every field already in the page's DATA
array (name, sector, group, capital, grossMargin, eps2026e, eps2025, eps2024,
thinBaseEPS, high52w) and refreshes only: price, pe, momentum, foreignChg20d,
trustChg20d, foreign5dSum, trust5dSum, dealer5dSum, foreign5dPosDays,
trust5dPosDays, smallBaseTrust, flow10.

Exchange (TWSE vs TPEx) is auto-detected per code from which day's price feed
contains it, so no per-stock hardcoding is needed here -- this script keeps working
even if the watchlist itself is edited later.

Data sources (public, no API key): openapi.twse.com.tw, www.twse.com.tw, www.tpex.org.tw
"""
import json, re, sys, os, time, datetime, subprocess

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_PATH = os.path.join(REPO_ROOT, "index.html")


def fetch_json(url, timeout=60, retries=3):
    """Fetch JSON via curl. Some public endpoints (esp. TPEx's ~4MB mainboard
    quotes dump) can be slow on shared CI runners; retry with backoff on both
    network failures and truncated/invalid JSON rather than crashing the whole run."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", str(timeout), "--retry", "2", "--retry-delay", "2",
                 "-A", "Mozilla/5.0 (compatible; stock-radar-bot/1.0)", url],
                capture_output=True, timeout=timeout + 15,
            )
            text = result.stdout.decode("utf-8", errors="replace")
            return json.loads(text)
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(3 * attempt)
    raise last_err


def roc_str(d):
    return f"{d.year-1911:03d}/{d.month:02d}/{d.day:02d}"


def ymd(d):
    return f"{d.year:04d}{d.month:02d}{d.day:02d}"  # TWSE T86 wants Gregorian YYYYMMDD


def clip(v, lo, hi):
    return max(lo, min(hi, v))


def get_twse_closes_for_date(date):
    """TWSE per-date closing-price report (每日收盤行情全部). Unlike the
    'opendata/STOCK_DAY_ALL' snapshot -- which was observed to lag by a full
    trading day or more (e.g. still showing 08/05 at 08/07 05:00 local time) --
    this endpoint reliably has same-day data once TWSE settles the day's trades."""
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={ymd(date)}&type=ALL&response=json"
    try:
        d = fetch_json(url)
    except Exception:
        return None
    if d.get("stat") != "OK" or d.get("date") != ymd(date):
        return None
    out = {}
    for t in d.get("tables", []):
        fields = t.get("fields") or []
        if "證券代號" not in fields or "收盤價" not in fields:
            continue
        ci, pi = fields.index("證券代號"), fields.index("收盤價")
        for row in t.get("data", []):
            try:
                out[row[ci].strip()] = float(row[pi].replace(",", ""))
            except Exception:
                continue
    return out or None


def get_tpex_closes_for_date(date):
    """TPEx per-date closing-price report (上櫃股票行情), explicit date query --
    same rationale as get_twse_closes_for_date: don't trust an unlabelled
    'latest' snapshot, ask for a specific date and verify the response echoes it back."""
    url = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&d={roc_str(date)}&se=EW&o=json"
    try:
        d = fetch_json(url)
    except Exception:
        return None
    if str(d.get("stat", "")).lower() != "ok" or d.get("date") != ymd(date):
        return None
    tables = d.get("tables", [])
    if not tables:
        return None
    fields = tables[0].get("fields") or []
    if "代號" not in fields or "收盤" not in fields:
        return None
    ci, pi = fields.index("代號"), fields.index("收盤")
    out = {}
    for row in tables[0].get("data", []):
        try:
            out[row[ci].strip()] = float(row[pi].replace(",", ""))
        except Exception:
            continue
    return out or None


def get_latest_prices(lookback_calendar_days=8):
    """Walk backward from today on BOTH exchanges independently, using
    explicit-date queries that we verify actually echo back the date we asked
    for, until each has data (handles weekends/holidays and same-day publish
    delay without ever silently serving an unknown-vintage 'latest' price)."""
    today = datetime.date.today()
    twse_prices, twse_date = {}, None
    d = today
    for _ in range(lookback_calendar_days):
        result = get_twse_closes_for_date(d)
        if result:
            twse_prices, twse_date = result, d
            break
        d -= datetime.timedelta(days=1)
    else:
        print("WARN: no TWSE closing-price data found in lookback window", file=sys.stderr)

    tpex_prices, tpex_date = {}, None
    d = today
    for _ in range(lookback_calendar_days):
        result = get_tpex_closes_for_date(d)
        if result:
            tpex_prices, tpex_date = result, d
            break
        d -= datetime.timedelta(days=1)
    else:
        print("WARN: no TPEx closing-price data found in lookback window", file=sys.stderr)

    print(f"Price dates used -- TWSE: {twse_date}, TPEx: {tpex_date}", file=sys.stderr)
    return twse_prices, tpex_prices


def get_twse_t86(date):
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={ymd(date)}&selectType=ALL&response=json"
    try:
        d = fetch_json(url)
    except Exception:
        return None
    if d.get("stat") != "OK":
        return None
    out = {}
    for row in d.get("data", []):
        code = row[0].strip()
        try:
            out[code] = {
                "f": float(row[4].replace(",", "")) / 1000.0,
                "t": float(row[10].replace(",", "")) / 1000.0,
                "de": float(row[11].replace(",", "")) / 1000.0,
            }
        except Exception:
            continue
    return out


def get_tpex_insti(date):
    url = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&d={roc_str(date)}&se=EW&t=D&o=json"
    try:
        d = fetch_json(url)
    except Exception:
        return None
    if str(d.get("stat", "")).lower() != "ok":
        return None
    tables = d.get("tables", [])
    if not tables:
        return None
    out = {}
    for row in tables[0].get("data", []):
        code = row[0].strip()
        try:
            out[code] = {
                "f": float(row[4].replace(",", "")) / 1000.0,
                "t": float(row[13].replace(",", "")) / 1000.0,
                "de": float(row[22].replace(",", "")) / 1000.0,
            }
        except Exception:
            continue
    return out


def collect_flow_history(codes, exchange_of, max_days=10, lookback_calendar_days=18):
    today = datetime.date.today()
    per_day = []
    d = today
    checked = 0
    while len(per_day) < max_days and checked < lookback_calendar_days:
        checked += 1
        twse_map = get_twse_t86(d)
        tpex_map = get_tpex_insti(d)
        if twse_map or tpex_map:
            per_day.append((d, twse_map or {}, tpex_map or {}))
        d = d - datetime.timedelta(days=1)
    per_day.reverse()
    history = {c: [] for c in codes}
    for (day, twse_map, tpex_map) in per_day:
        for c in codes:
            src = twse_map if exchange_of.get(c) == "TWSE" else tpex_map
            rec = src.get(str(c))
            if rec is None:
                continue
            history[c].append({"d": roc_str(day), "f": rec["f"], "t": rec["t"], "de": rec["de"]})
    return history


def main():
    with open(INDEX_PATH, encoding="utf-8") as f:
        html = f.read()

    m = re.search(r"/\*BEGIN_DATA\*/(\[.*?\])/\*END_DATA\*/", html, re.S)
    if not m:
        print("ERROR: could not find /*BEGIN_DATA*/ ... /*END_DATA*/ markers in index.html", file=sys.stderr)
        sys.exit(2)
    prev_data = json.loads(m.group(1))
    codes = [s["code"] for s in prev_data]

    twse_prices, tpex_prices = get_latest_prices()
    exchange_of = {}
    for c in codes:
        if str(c) in twse_prices:
            exchange_of[c] = "TWSE"
        elif str(c) in tpex_prices:
            exchange_of[c] = "TPEx"

    history = collect_flow_history(codes, exchange_of)

    new_data = []
    skipped = []
    for s in prev_data:
        c = s["code"]
        price = twse_prices.get(str(c)) if exchange_of.get(c) == "TWSE" else tpex_prices.get(str(c))
        flow = history.get(c, [])
        if price is None or len(flow) == 0:
            skipped.append((c, s.get("name")))
            new_data.append(s)
            continue

        capital = s["capital"]
        eps2026e = s["eps2026e"]
        shares_outstanding = capital * 1e7

        last5 = flow[-5:]
        foreign5dSum = int(round(sum(x["f"] for x in last5)))
        trust5dSum = int(round(sum(x["t"] for x in last5)))
        dealer5dSum = int(round(sum(x["de"] for x in last5)))
        foreign5dPosDays = sum(1 for x in last5 if x["f"] > 0)
        trust5dPosDays = sum(1 for x in last5 if x["t"] > 0)

        foreign_cum = sum(x["f"] for x in flow) * 1000
        trust_cum = sum(x["t"] for x in flow) * 1000
        foreignChg20d = foreign_cum / shares_outstanding if shares_outstanding else 0
        trustChg20d = trust_cum / shares_outstanding if shares_outstanding else 0
        momentum = (clip(foreignChg20d, -0.5, 0.5) + clip(trustChg20d, -0.5, 0.5)) / 2

        high52w = max(s.get("high52w", price), price)
        pe = round(price / eps2026e, 2) if eps2026e else None

        new_s = dict(s)
        new_s.update({
            "price": round(price, 2),
            "high52w": round(high52w, 2),
            "pe": pe,
            "momentum": round(momentum, 4),
            "foreignChg20d": round(foreignChg20d, 4),
            "trustChg20d": round(trustChg20d, 4),
            "foreign5dSum": foreign5dSum,
            "trust5dSum": trust5dSum,
            "dealer5dSum": dealer5dSum,
            "foreign5dPosDays": foreign5dPosDays,
            "trust5dPosDays": trust5dPosDays,
            "smallBaseTrust": max(abs(foreignChg20d), abs(trustChg20d)) > 0.08,
            "flow10": [{"d": x["d"], "f": round(x["f"], 1), "t": round(x["t"], 1)} for x in flow],
        })
        new_data.append(new_s)

    new_data.sort(key=lambda s: s["code"])
    new_json = json.dumps(new_data, ensure_ascii=False, separators=(",", ":"))
    new_html = html[:m.start()] + "/*BEGIN_DATA*/" + new_json + "/*END_DATA*/" + html[m.end():]

    if new_html == html:
        print("No changes (data identical to current index.html).", file=sys.stderr)
    else:
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            f.write(new_html)

    updated_count = len(new_data) - len(skipped)
    print(f"Updated {updated_count}/{len(new_data)} stocks; skipped (kept prior values): {skipped}", file=sys.stderr)
    # Exit non-zero only on a hard failure (handled above via sys.exit(2)); a low
    # update count is not fatal since every stock's prior values are preserved.


if __name__ == "__main__":
    main()
