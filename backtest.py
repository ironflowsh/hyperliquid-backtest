"""Backtest a Hyperliquid strategy on Ironflow candles and liquidations.

    python backtest.py                                # BTC, 5m bars, last 24 hours, no key
    IRONFLOW_KEY=if_... python backtest.py --days 14  # 14 days with a free key
    python backtest.py --months 2026-08,2026-10       # 1h bars from the free monthly files

Each bar has open, high, low, close, volume and the USD of longs and shorts
liquidated in it. The default strategy fades liquidation flushes; replace
signal() with your own. Fees and slippage are charged on every fill.

Python 3.9+. The API path needs only the standard library; --months needs
pandas and pyarrow.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.ironflow.sh"
HF = "https://huggingface.co/datasets/ironflow/hyperliquid-{}/resolve/main/data/{}.parquet"
KEY = os.environ.get("IRONFLOW_KEY", "")  # optional, from https://ironflow.sh/key
HEADERS = {"User-Agent": os.environ.get("IRONFLOW_USER_AGENT", "hyperliquid-backtest/0.1")}
if KEY:
    HEADERS["Authorization"] = f"Bearer {KEY}"

FEE = 0.00045       # Hyperliquid's base perp taker fee, per side
SLIPPAGE = 0.0001   # 1 bp per side
HOLD = 6            # bars to hold a position
LOOKBACK = 96       # bars that set what a large liquidation bar is
START_USD = 10_000
MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}


# ─── Data ────────────────────────────────────────────────────────────────────

def get(path: str, params: dict) -> dict:
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    for _ in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            wait = int(e.headers.get("Retry-After") or 10)
            if e.code != 429 or wait > 120:
                sys.exit(f"Ironflow API error {e.code}: {body}")
            time.sleep(wait)
    sys.exit("still rate limited after 5 tries")


def load_api(coin: str, interval: str, days: int) -> list[dict]:
    """Candles from /v1/candles and liquidations per bar from one SQL query."""
    bars, params = {}, {"market": f"{coin}-PERP", "interval": interval, "from": f"-{days}d", "limit": 1000}
    while True:
        page = get("/v1/candles", params)
        for c in page["data"]:
            bars[c["timestamp"]] = {"t": c["timestamp"], "open": float(c["open"]), "high": float(c["high"]),
                                    "low": float(c["low"]), "close": float(c["close"]),
                                    "volume": float(c["volume"]), "long_liq": 0.0, "short_liq": 0.0}
        if not page["pagination"].get("has_more"):
            break
        params["cursor"] = page["pagination"]["next_cursor"]
        time.sleep(2.1 if not KEY else 1.1)
    # side is the liquidated wallet's closing fill: sell closes a long.
    sql = f"""
        SELECT toUnixTimestamp(toStartOfInterval(ts, INTERVAL {MINUTES[interval]} MINUTE)) * 1000 AS t,
               sumIf(notional, side = 'sell') AS long_liq, sumIf(notional, side = 'buy') AS short_liq
        FROM hl.liquidations
        WHERE market = '{coin}-PERP' AND ts >= now() - INTERVAL {days} DAY
        GROUP BY t ORDER BY t"""
    for t, long_liq, short_liq in get("/v1/query", {"sql": sql})["rows"]:
        if t in bars:
            bars[t].update(long_liq=long_liq, short_liq=short_liq)
    return [bars[t] for t in sorted(bars)]


def load_hf(coin: str, months: list[str]) -> list[dict]:
    """Hourly candles and liquidations from the free monthly Parquet files."""
    import pandas as pd

    market = f"{coin}-PERP"
    candles = pd.concat(pd.read_parquet(HF.format("candles-1h", m), filters=[("market", "==", market)]) for m in months)
    liqs = pd.concat(pd.read_parquet(HF.format("liquidations", f"events/{m}"), filters=[("market", "==", market)])
                     for m in months)
    liqs["t"] = liqs["timestamp"].dt.floor("h")
    by_hour = liqs.pivot_table(index="t", columns="position_side", values="notional_usd", aggfunc="sum").fillna(0)
    bars = []
    for row in candles.sort_values("timestamp").itertuples():
        hour = row.timestamp.floor("h")
        bars.append({"t": int(hour.timestamp() * 1000), "open": row.open, "high": row.high, "low": row.low,
                     "close": row.close, "volume": row.volume,
                     "long_liq": float(by_hour["long"].get(hour, 0)) if "long" in by_hour else 0.0,
                     "short_liq": float(by_hour["short"].get(hour, 0)) if "short" in by_hour else 0.0})
    return bars


# ─── Strategy ────────────────────────────────────────────────────────────────

def signal(bars: list[dict], i: int) -> int:
    """Position to take after bar i closes: +1 long, -1 short, 0 none.

    Fade liquidation flushes: buy after a down bar whose long liquidations are
    the largest of the last LOOKBACK bars, sell after the same with shorts.
    Only bars up to i are read, so there is no look-ahead.
    """
    if i < LOOKBACK:
        return 0
    bar = bars[i]
    past = sorted(b["long_liq"] + b["short_liq"] for b in bars[i - LOOKBACK:i])
    big = max(past[int(len(past) * 0.95)], 100_000)  # top 5% of recent bars, at least $100k
    if bar["long_liq"] > big and bar["close"] < bar["open"]:
        return 1
    if bar["short_liq"] > big and bar["close"] > bar["open"]:
        return -1
    return 0


# ─── Backtest ────────────────────────────────────────────────────────────────

def backtest(bars: list[dict]) -> dict:
    """Enter at the next bar's open, exit at the open HOLD bars later."""
    equity, peak, max_dd, trades, i = START_USD, START_USD, 0.0, [], 0
    while i < len(bars) - 1:
        side = signal(bars, i)
        if not side:
            i += 1
            continue
        entry, exit_at = bars[i + 1], min(i + 1 + HOLD, len(bars) - 1)
        ret = side * (bars[exit_at]["open"] / entry["open"] - 1) - 2 * (FEE + SLIPPAGE)
        equity *= 1 + ret
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
        trades.append({"t": entry["t"], "side": side, "entry": entry["open"], "exit": bars[exit_at]["open"], "ret": ret})
        i = exit_at
    hold = bars[-1]["close"] / bars[0]["open"] - 1
    return {"equity": equity, "max_dd": max_dd, "trades": trades, "buy_and_hold": hold}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--coin", default="BTC", help="Hyperliquid perp: BTC, ETH, SOL, HYPE, ...")
    parser.add_argument("--interval", default="5m", choices=list(MINUTES), help="bar size for the API path")
    parser.add_argument("--days", type=int, default=1, help="days back: 1 without a key, 14 with a free key")
    parser.add_argument("--months", help="YYYY-MM months from the free files, e.g. 2026-08,2026-10 (1h bars)")
    args = parser.parse_args()
    if not args.coin.replace(":", "").isalnum():
        sys.exit("--coin is a Hyperliquid coin name such as BTC or xyz:NVDA")

    if args.months:
        first, _, last = args.months.partition(",")
        import pandas as pd
        months = [str(p) for p in pd.period_range(first, last or first, freq="M")]
        bars, label = load_hf(args.coin, months), f"{args.coin} 1h, {months[0]} to {months[-1]}"
    else:
        bars, label = load_api(args.coin, args.interval, args.days), f"{args.coin} {args.interval}, last {args.days} d"
    if len(bars) <= LOOKBACK + HOLD:
        sys.exit(f"only {len(bars)} bars: use a longer window or a shorter --interval")

    r = backtest(bars)
    wins = sum(t["ret"] > 0 for t in r["trades"])
    print(f"{label}: {len(bars):,} bars, ${sum(b['long_liq'] + b['short_liq'] for b in bars):,.0f} liquidated")
    for t in r["trades"][-5:]:
        print(f"  {time.strftime('%m-%d %H:%M', time.gmtime(t['t'] / 1000))}  {'long ' if t['side'] > 0 else 'short'}"
              f"  {t['entry']:>10,.2f} -> {t['exit']:>10,.2f}  {t['ret']:+.2%}")
    print(f"trades {len(r['trades'])}, won {wins}, return {r['equity'] / START_USD - 1:+.2%}, "
          f"max drawdown {r['max_dd']:.2%}, buy and hold {r['buy_and_hold']:+.2%}")


if __name__ == "__main__":
    main()
