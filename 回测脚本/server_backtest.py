"""
Backtest using server's Binance API access.
Fetches 1m K-lines from server, resamples to 15m,
runs both OCC and Volty strategies.
"""
import os
import sys
import json
import urllib.request
import urllib.parse
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import pandas as pd
import numpy as np

SERVER = "http://47.76.187.153:8000"

def fetch_1m_from_server(symbol, limit=1000):
    """Fetch 1m K-lines from server API."""
    url = f"{SERVER}/api/klines/{symbol}?limit={limit}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            return data.get("klines", [])
    except Exception as e:
        print(f"  Error fetching from server: {e}")
        return []

def fetch_1m_paginated(symbol, total_needed):
    """Fetch 1m data in chunks (server API limit unknown, try 1000/chunk)."""
    all_klines = []
    # Fetch in batches - server may have its own limits
    for batch in range(40):  # max 40 batches = 40,000 bars
        chunk = fetch_1m_from_server(symbol, limit=1000)
        if not chunk:
            break
        all_klines.extend(chunk)
        print(f"  Batch {batch+1}: got {len(chunk)} bars, total={len(all_klines)}")
        if len(all_klines) >= total_needed:
            break
        time.sleep(0.3)
    return all_klines

def klines_to_df(klines):
    """Convert ccxt kline format to DataFrame."""
    if not klines:
        return None
    df = pd.DataFrame(klines, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)
    # Remove duplicates (from pagination overlap)
    df = df[~df.index.duplicated(keep='first')]
    df.sort_index(inplace=True)
    return df

def resample_to_15m(df_1m):
    """Resample 1m data to 15m OHLCV."""
    df_15m = df_1m.resample("15min").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })
    df_15m.dropna(inplace=True)
    return df_15m

def run_occ_backtest(df):
    """OCC v8.13 compounding backtest."""
    from strategy_bot import compute_signals

    sig = compute_signals(df, "TEMA", 8, 3, 15, 0, 5)

    equity = 10000.0
    position = None
    entry_price = 0.0
    trades = []
    peak = equity
    max_dd = 0.0

    start_i = max(8 * 3 + 10, 60)
    for i in range(start_i, len(sig)):
        long_trig = sig["long_cond"].iloc[i]
        short_trig = sig["short_cond"].iloc[i]
        close_px = df["close"].iloc[i]

        if position is None:
            if long_trig:
                position = "LONG"; entry_price = close_px
            elif short_trig:
                position = "SHORT"; entry_price = close_px
        elif position == "LONG":
            if short_trig:
                pnl = equity * (close_px - entry_price) / entry_price
                equity += pnl
                trades.append(pnl)
                if equity > peak: peak = equity
                dd = peak - equity
                if dd > max_dd: max_dd = dd
                position = "SHORT"; entry_price = close_px
        elif position == "SHORT":
            if long_trig:
                pnl = equity * (entry_price - close_px) / entry_price
                equity += pnl
                trades.append(pnl)
                if equity > peak: peak = equity
                dd = peak - equity
                if dd > max_dd: max_dd = dd
                position = "LONG"; entry_price = close_px

    wins = sum(1 for t in trades if t > 0)
    return {
        "equity": equity, "trades": len(trades), "wins": wins,
        "win_rate": wins/len(trades)*100 if trades else 0,
        "max_dd": max_dd, "pnl": equity - 10000,
        "ret_pct": (equity/10000 - 1) * 100
    }

def run_volty_backtest(df):
    """Volty Expan Close compounding backtest."""
    from strategy_bot import compute_signals_volty

    sig = compute_signals_volty(df, 5, 0.75)
    sma50 = df["close"].rolling(50).mean().shift(1)
    trend_up = df["close"] > sma50

    equity = 10000.0
    position = None
    entry_price = 0.0
    trades = []
    peak = equity
    max_dd = 0.0

    for i in range(5 + 50 + 2, len(df)):
        long_trig = sig["long_cond"].iloc[i] and trend_up.iloc[i]
        short_trig = sig["short_cond"].iloc[i] and (not trend_up.iloc[i])
        close_px = df["close"].iloc[i]

        if position is None:
            if long_trig:
                position = "LONG"; entry_price = close_px
            elif short_trig:
                position = "SHORT"; entry_price = close_px
        elif position == "LONG":
            if short_trig:
                pnl = equity * (close_px - entry_price) / entry_price
                equity += pnl
                trades.append(pnl)
                if equity > peak: peak = equity
                dd = peak - equity
                if dd > max_dd: max_dd = dd
                position = "SHORT"; entry_price = close_px
        elif position == "SHORT":
            if long_trig:
                pnl = equity * (entry_price - close_px) / entry_price
                equity += pnl
                trades.append(pnl)
                if equity > peak: peak = equity
                dd = peak - equity
                if dd > max_dd: max_dd = dd
                position = "LONG"; entry_price = close_px

    wins = sum(1 for t in trades if t > 0)
    return {
        "equity": equity, "trades": len(trades), "wins": wins,
        "win_rate": wins/len(trades)*100 if trades else 0,
        "max_dd": max_dd, "pnl": equity - 10000,
        "ret_pct": (equity/10000 - 1) * 100
    }


def main():
    # Periods to fetch
    # 1 year = 365 days * 24h * 4 bars/h = 35,040 15m bars
    # Each 15m bar needs 15 x 1m bars
    # Total 1m bars needed ~ 525,600... too many to fetch via API
    # Let's fetch what we can: try for ~5000 1m bars = ~3.5 days of 1m
    # Server limit might be 1000 bars per call

    print("=" * 70)
    print("  Server-based Backtest")
    print("  Fetching 1m data from server, resampling to 15m")
    print("=" * 70)

    # Try to fetch as much as possible
    print("\n[Fetching 1m K-lines from server...]")

    # The server klines endpoint only gives 1m data, limited bars
    # Let's try in chunks - first check max available
    klines = fetch_1m_paginated("ETHUSDT", total_needed=50000)

    if not klines:
        print("ERROR: Failed to fetch any data from server!")
        return

    df_1m = klines_to_df(klines)
    print(f"\nTotal 1m bars: {len(df_1m)}")
    print(f"Date range: {df_1m.index[0]} -> {df_1m.index[-1]}")

    # Resample to 15m
    df_15m = resample_to_15m(df_1m)
    print(f"15m bars after resample: {len(df_15m)}")
    print(f"15m range: {df_15m.index[0]} -> {df_15m.index[-1]}")

    df = df_15m

    # Split into periods
    now = df.index[-1]
    periods = {
        "Past Week": now - pd.Timedelta(days=7),
        "Past Month": now - pd.Timedelta(days=30),
        "Past 3 Months": now - pd.Timedelta(days=90),
        "All Available": df.index[0],
    }

    print(f"\n{'='*80}")
    print(f"  RESULTS (Compounding, starting $10,000)")
    print(f"{'='*80}")

    for period_name, cutoff in periods.items():
        df_p = df[df.index >= cutoff]
        if len(df_p) < 100:
            print(f"\n  [{period_name}]: Insufficient data ({len(df_p)} bars)")
            continue

        print(f"\n  [{period_name}] {len(df_p)} bars, {df_p.index[0]} -> {df_p.index[-1]}")

        try:
            occ = run_occ_backtest(df_p)
            print(f"    OCC v8.13:  ${occ['equity']:,.0f}  PnL=${occ['pnl']:+,.0f} ({occ['ret_pct']:+.2f}%)  "
                  f"{occ['trades']} trades  Win={occ['win_rate']:.1f}%  MaxDD=${occ['max_dd']:,.0f}")
        except Exception as e:
            print(f"    OCC ERROR: {e}")

        try:
            volty = run_volty_backtest(df_p)
            print(f"    Volty:      ${volty['equity']:,.0f}  PnL=${volty['pnl']:+,.0f} ({volty['ret_pct']:+.2f}%)  "
                  f"{volty['trades']} trades  Win={volty['win_rate']:.1f}%  MaxDD=${volty['max_dd']:,.0f}")
        except Exception as e:
            print(f"    Volty ERROR: {e}")


if __name__ == "__main__":
    main()
