"""
Live Binance K-line Backtest - OCC v8.13 + Volty Expan Close
============================================================
Fetches REAL 15m K-line data from Binance public API (data-api.binance.vision).
Runs both strategies using the ACTUAL strategy_bot.py signal logic.
Compounding mode, always-in-market.
"""
import os
import sys
import json
import urllib.request
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import pandas as pd
import numpy as np

# Binance public data API (works in mainland China)
BINANCE_API = "https://data-api.binance.vision/api/v3/klines"

def fetch_klines(symbol, interval, limit=1000, end_time=None):
    """Fetch K-lines from Binance public API with pagination support."""
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }
    if end_time:
        params["endTime"] = end_time

    query = urllib.parse.urlencode(params)
    url = f"{BINANCE_API}?{query}"

    for attempt in range(3):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                return data
        except Exception as e:
            if attempt < 2:
                time.sleep(1)
            else:
                print(f"  HTTP error: {e}")
                return []

def fetch_klines_paginated(symbol, interval, total_days):
    """Fetch historical K-lines with pagination to cover total_days."""
    all_klines = []
    end_time = None

    # Each call returns up to 1000 bars
    # 15m: 1000 bars = ~10.4 days. For 365 days need ~35 calls.
    max_calls = 50 if total_days > 180 else 20 if total_days > 90 else 10

    for i in range(max_calls):
        batch = fetch_klines(symbol, interval, limit=1000, end_time=end_time)
        if not batch:
            break

        all_klines = batch + all_klines  # prepend since we're going backwards

        first_ts = batch[0][0]
        if end_time is None:
            print(f"  Batch {i+1}: {len(batch)} bars (latest)", end="")
        else:
            print(f"  Batch {i+1}: {len(batch)} bars", end="")

        # Check if we have enough data
        if len(all_klines) > 0:
            earliest = pd.to_datetime(all_klines[0][0], unit="ms")
            latest = pd.to_datetime(all_klines[-1][0], unit="ms")
            days_covered = (latest - earliest).total_seconds() / 86400
            print(f", total={len(all_klines)} bars, {earliest.date()} -> {latest.date()} ({days_covered:.0f}d)")
            if days_covered >= total_days:
                break

        if len(batch) < 1000:
            break  # no more data

        end_time = first_ts - 1  # fetch earlier bars
        time.sleep(0.1)  # rate limit

    return all_klines

def klines_to_df(klines):
    """Convert Binance kline format to DataFrame."""
    if not klines:
        return None
    df = pd.DataFrame(klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "taker_buy_vol",
        "taker_buy_quote_vol", "ignore"
    ])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[~df.index.duplicated(keep='first')]
    df.sort_index(inplace=True)
    return df[["open", "high", "low", "close", "volume"]]


def run_occ_backtest(df, initial=10000):
    """OCC v8.13 (TEMA) strategy backtest - compounding, always-in-market."""
    from strategy_bot import compute_signals

    sig = compute_signals(df, "TEMA", 8, 3, 15, 0, 5)

    equity = float(initial)
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
                equity += pnl; trades.append(pnl)
                peak = max(peak, equity)
                max_dd = max(max_dd, peak - equity)
                position = "SHORT"; entry_price = close_px
        elif position == "SHORT":
            if long_trig:
                pnl = equity * (entry_price - close_px) / entry_price
                equity += pnl; trades.append(pnl)
                peak = max(peak, equity)
                max_dd = max(max_dd, peak - equity)
                position = "LONG"; entry_price = close_px

    wins = sum(1 for t in trades if t > 0)
    n = len(trades)
    return {
        "final": equity, "trades": n, "wins": wins,
        "wr": wins/n*100 if n else 0,
        "max_dd": max_dd, "pnl": equity - initial,
        "ret": (equity/initial - 1) * 100,
        "avg_win": np.mean([t for t in trades if t > 0]) if wins else 0,
        "avg_loss": np.mean([t for t in trades if t < 0]) if (n - wins) else 0,
    }


def run_volty_backtest(df, initial=10000):
    """Volty Expan Close strategy backtest - STOP ORDER execution, compounding, always-in-market.

    TV Pine Script logic:
      atrs = ta.sma(ta.tr, length) * numATRs
      strategy.entry("VltClsLE", strategy.long,  stop=close+atrs)
      strategy.entry("VltClsSE", strategy.short, stop=close-atrs)

    Each bar, stop orders are placed at close +/- ATR from the PREVIOUS bar.
    If high >= long_stop -> long entry fills at long_stop price.
    If low <= short_stop -> short entry fills at short_stop price.
    Exit is also at the opposite stop level (always-in-market flip).
    """
    from strategy_bot import compute_signals_volty

    sig = compute_signals_volty(df, 5, 0.75)
    sma50 = df["close"].rolling(50).mean().shift(1)
    trend_up = df["close"] > sma50

    # Compute stop levels (same as in compute_signals_volty)
    length, atr_mult = 5, 0.75
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    atrs = tr.rolling(window=length).mean() * atr_mult
    long_stop_level = (df["close"] + atrs).shift(1)
    short_stop_level = (df["close"] - atrs).shift(1)

    equity = float(initial)
    position = None
    entry_price = 0.0
    trades = []
    peak = equity
    max_dd = 0.0

    for i in range(length + 50 + 2, len(df)):
        long_trig = sig["long_cond"].iloc[i] and trend_up.iloc[i]
        short_trig = sig["short_cond"].iloc[i] and (not trend_up.iloc[i])
        long_stop = long_stop_level.iloc[i]
        short_stop = short_stop_level.iloc[i]

        if position is None:
            if long_trig:
                position = "LONG"; entry_price = long_stop
            elif short_trig:
                position = "SHORT"; entry_price = short_stop
        elif position == "LONG":
            if short_trig:
                exit_px = short_stop  # exit at the short stop level
                pnl = equity * (exit_px - entry_price) / entry_price
                equity += pnl; trades.append(pnl)
                peak = max(peak, equity)
                max_dd = max(max_dd, peak - equity)
                position = "SHORT"; entry_price = short_stop
        elif position == "SHORT":
            if long_trig:
                exit_px = long_stop  # exit at the long stop level
                pnl = equity * (entry_price - exit_px) / entry_price
                equity += pnl; trades.append(pnl)
                peak = max(peak, equity)
                max_dd = max(max_dd, peak - equity)
                position = "LONG"; entry_price = long_stop

    wins = sum(1 for t in trades if t > 0)
    n = len(trades)
    return {
        "final": equity, "trades": n, "wins": wins,
        "wr": wins/n*100 if n else 0,
        "max_dd": max_dd, "pnl": equity - initial,
        "ret": (equity/initial - 1) * 100,
        "avg_win": np.mean([t for t in trades if t > 0]) if wins else 0,
        "avg_loss": np.mean([t for t in trades if t < 0]) if (n - wins) else 0,
    }


def print_result(name, r):
    print(f"  Final equity:    ${r['final']:,.0f}")
    print(f"  Total PnL:       ${r['pnl']:+,.0f}  ({r['ret']:+.2f}%)")
    print(f"  Trades:          {r['trades']}")
    print(f"  Win Rate:        {r['wr']:.1f}%  ({r['wins']}/{r['trades']})")
    print(f"  Avg Win/Loss:    ${r['avg_win']:+,.0f} / ${r['avg_loss']:+,.0f}")
    print(f"  Max Drawdown:    ${r['max_dd']:,.0f}")


def main():
    print("=" * 72)
    print("  LIVE BINANCE K-LINE BACKTEST")
    print("  Data: Binance ETHUSDT 15m (data-api.binance.vision)")
    print("  Logic: strategy_bot.py compute_signals / compute_signals_volty")
    print("  Mode: Compounding, always-in-market, $10,000 start")
    print("=" * 72)

    # Fetch 1 year of 15m data
    print(f"\n[1] Fetching 15m K-lines from Binance (365 days)...")
    klines = fetch_klines_paginated("ETHUSDT", "15m", 365)

    if not klines:
        print("ERROR: Failed to fetch data!")
        return

    df = klines_to_df(klines)
    print(f"\n  Total: {len(df)} 15m bars")
    print(f"  Range: {df.index[0]} -> {df.index[-1]}")

    now = df.index[-1]

    periods = [
        ("Past Week",      now - pd.Timedelta(days=7)),
        ("Past Month",     now - pd.Timedelta(days=30)),
        ("Past 3 Months",  now - pd.Timedelta(days=90)),
        ("Past Year",      now - pd.Timedelta(days=365)),
    ]

    occ_results = []
    volty_results = []

    for period_name, cutoff in periods:
        df_p = df[df.index >= cutoff]
        if len(df_p) < 100:
            continue

        print(f"\n{'#'*72}")
        print(f"# {period_name} - {len(df_p)} bars ({df_p.index[0]} -> {df_p.index[-1]})")
        print(f"{'#'*72}")

        # OCC
        print(f"\n  [OCC v8.13 - TEMA Trend Following]")
        try:
            occ_r = run_occ_backtest(df_p)
            print_result("OCC", occ_r)
            occ_r["period"] = period_name
            occ_results.append(occ_r)
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback; traceback.print_exc()

        # Volty
        print(f"\n  [Volty Expan Close - ATR Breakout]")
        try:
            volty_r = run_volty_backtest(df_p)
            print_result("Volty", volty_r)
            volty_r["period"] = period_name
            volty_results.append(volty_r)
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback; traceback.print_exc()

    # ── Summary ──
    print(f"\n\n{'='*90}")
    print(f"  FINAL SUMMARY - Real Binance 15m K-line Backtest")
    print(f"  strategy_bot.py signal logic, Compounding, $10,000 start")
    print(f"{'='*90}")
    print(f"  {'Period':<18} {'Strategy':<24} {'Final $':>10} {'Return':>9} {'Trades':>7} {'Win%':>8} {'MaxDD':>11} {'AvgW':>8} {'AvgL':>8}")
    print(f"  {'-'*90}")

    for i, period_name in enumerate([p[0] for p in periods]):
        for results, strat_name in [(occ_results, "OCC v8.13 (TEMA)"), (volty_results, "Volty Expan Close")]:
            for r in results:
                if r["period"] == period_name:
                    print(f"  {period_name:<18} {strat_name:<24} ${r['final']:>9,.0f} "
                          f"{r['ret']:>8.2f}% {r['trades']:>6}  "
                          f"{r['wr']:>7.1f}% ${r['max_dd']:>10,.0f} "
                          f"${r['avg_win']:>7,.0f} ${r['avg_loss']:>7,.0f}")
        print(f"  {'-'*90}")

    print(f"\n  [INFO] This is based on Binance SPOT ETHUSDT prices (data-api.binance.vision).")
    print(f"  Futures prices may differ slightly but strategy PnL% should be nearly identical.")


if __name__ == "__main__":
    main()
