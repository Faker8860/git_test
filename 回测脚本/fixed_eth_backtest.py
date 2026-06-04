"""
Fixed 1 ETH Backtest - OCC v8.13 + Volty Expan Close
=====================================================
Uses Binance real 15m K-line data.
Fixed position: 1 ETH per trade (no compounding).
OCC: close-price execution (matching TV process_orders_on_close=true)
Volty: stop-price execution (matching TV stop order logic)
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

BINANCE_API = "https://data-api.binance.vision/api/v3/klines"

def fetch_klines(symbol, interval, limit=1000, end_time=None):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if end_time:
        params["endTime"] = end_time
    query = urllib.parse.urlencode(params)
    url = f"{BINANCE_API}?{query}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            if attempt < 2:
                time.sleep(1)
            else:
                print(f"  HTTP error: {e}")
                return []

def fetch_klines_paginated(symbol, interval, total_days):
    all_klines = []
    end_time = None
    max_calls = 50 if total_days > 180 else 20 if total_days > 90 else 10
    for i in range(max_calls):
        batch = fetch_klines(symbol, interval, limit=1000, end_time=end_time)
        if not batch:
            break
        all_klines = batch + all_klines
        first_ts = batch[0][0]
        if len(all_klines) > 0:
            earliest = pd.to_datetime(all_klines[0][0], unit="ms")
            latest = pd.to_datetime(all_klines[-1][0], unit="ms")
            days_covered = (latest - earliest).total_seconds() / 86400
            print(f"  Batch {i+1}: {len(batch)} bars, total={len(all_klines)}, "
                  f"{earliest.date()} -> {latest.date()} ({days_covered:.0f}d)")
            if days_covered >= total_days:
                break
        if len(batch) < 1000:
            break
        end_time = first_ts - 1
        time.sleep(0.1)
    return all_klines

def klines_to_df(klines):
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

def run_occ_fixed(df):
    """OCC v8.13 - fixed 1 ETH per trade, close-price execution."""
    from strategy_bot import compute_signals

    sig = compute_signals(df, "TEMA", 8, 3, 15, 0, 5)

    account = 10000.0
    eth_per_trade = 1.0
    position = None
    entry_price = 0.0
    trades = []
    peak = account
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
                pnl = (close_px - entry_price) * eth_per_trade
                account += pnl; trades.append(pnl)
                peak = max(peak, account)
                max_dd = max(max_dd, peak - account)
                position = "SHORT"; entry_price = close_px
        elif position == "SHORT":
            if long_trig:
                pnl = (entry_price - close_px) * eth_per_trade
                account += pnl; trades.append(pnl)
                peak = max(peak, account)
                max_dd = max(max_dd, peak - account)
                position = "LONG"; entry_price = close_px

    wins = sum(1 for t in trades if t > 0)
    n = len(trades)
    total_pnl = account - 10000
    return {
        "final": account, "trades": n, "wins": wins,
        "wr": wins/n*100 if n else 0,
        "max_dd": max_dd, "pnl": total_pnl,
        "ret": total_pnl / 10000 * 100,
        "avg_win": np.mean([t for t in trades if t > 0]) if wins else 0,
        "avg_loss": np.mean([t for t in trades if t < 0]) if (n - wins) else 0,
        "best": max(trades) if trades else 0,
        "worst": min(trades) if trades else 0,
    }


def run_volty_fixed(df):
    """Volty Expan Close - fixed 1 ETH per trade, stop-price execution."""
    from strategy_bot import compute_signals_volty

    sig = compute_signals_volty(df, 5, 0.75)
    sma50 = df["close"].rolling(50).mean().shift(1)
    trend_up = df["close"] > sma50

    # ATR stop levels
    length, atr_mult = 5, 0.75
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    atrs = tr.rolling(window=length).mean() * atr_mult
    long_stop_level = (df["close"] + atrs).shift(1)
    short_stop_level = (df["close"] - atrs).shift(1)

    account = 10000.0
    eth_per_trade = 1.0
    position = None
    entry_price = 0.0
    trades = []
    peak = account
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
                pnl = (short_stop - entry_price) * eth_per_trade
                account += pnl; trades.append(pnl)
                peak = max(peak, account)
                max_dd = max(max_dd, peak - account)
                position = "SHORT"; entry_price = short_stop
        elif position == "SHORT":
            if long_trig:
                pnl = (entry_price - long_stop) * eth_per_trade
                account += pnl; trades.append(pnl)
                peak = max(peak, account)
                max_dd = max(max_dd, peak - account)
                position = "LONG"; entry_price = long_stop

    wins = sum(1 for t in trades if t > 0)
    n = len(trades)
    total_pnl = account - 10000
    return {
        "final": account, "trades": n, "wins": wins,
        "wr": wins/n*100 if n else 0,
        "max_dd": max_dd, "pnl": total_pnl,
        "ret": total_pnl / 10000 * 100,
        "avg_win": np.mean([t for t in trades if t > 0]) if wins else 0,
        "avg_loss": np.mean([t for t in trades if t < 0]) if (n - wins) else 0,
        "best": max(trades) if trades else 0,
        "worst": min(trades) if trades else 0,
    }


def print_detail(name, r):
    print(f"  Start:     $10,000")
    print(f"  Final:     ${r['final']:,.0f}")
    print(f"  PnL:       ${r['pnl']:+,.0f}  ({r['ret']:+.2f}%)")
    print(f"  Trades:    {r['trades']}  |  Wins: {r['wins']}  |  WinRate: {r['wr']:.1f}%")
    print(f"  Avg Win:   ${r['avg_win']:+,.2f}  |  Avg Loss: ${r['avg_loss']:+,.2f}")
    print(f"  Best:      ${r['best']:+,.2f}  |  Worst: ${r['worst']:+,.2f}")
    print(f"  Max DD:    ${r['max_dd']:,.0f}")


def main():
    print("=" * 72)
    print("  FIXED 1 ETH BACKTEST - Binance ETHUSDT 15m Real K-lines")
    print("  Mode: Fixed 1 ETH per trade, non-compounding")
    print("=" * 72)

    print(f"\n[1] Fetching 15m K-lines from Binance (365 days)...")
    klines = fetch_klines_paginated("ETHUSDT", "15m", 365)

    if not klines:
        print("ERROR: Failed to fetch data!")
        return

    df = klines_to_df(klines)
    print(f"\n  Total: {len(df)} bars, {df.index[0]} -> {df.index[-1]}")

    now = df.index[-1]
    periods = [
        ("Past Week",      now - pd.Timedelta(days=7)),
        ("Past Month",     now - pd.Timedelta(days=30)),
        ("Past 3 Months",  now - pd.Timedelta(days=90)),
        ("Past Year",      now - pd.Timedelta(days=365)),
    ]

    occ_all = []
    volty_all = []

    for period_name, cutoff in periods:
        df_p = df[df.index >= cutoff]
        if len(df_p) < 100:
            continue

        print(f"\n{'#'*72}")
        print(f"# {period_name} - {len(df_p)} bars ({df_p.index[0]} -> {df_p.index[-1]})")
        print(f"{'#'*72}")

        # OCC
        print(f"\n  [OCC v8.13 - TEMA, 1 ETH/trade, close-price]")
        occ_r = run_occ_fixed(df_p)
        occ_r["period"] = period_name
        occ_all.append(occ_r)
        print_detail("OCC", occ_r)

        # Volty
        print(f"\n  [Volty Expan Close - ATR, 1 ETH/trade, stop-price]")
        volty_r = run_volty_fixed(df_p)
        volty_r["period"] = period_name
        volty_all.append(volty_r)
        print_detail("Volty", volty_r)

    # ── Summary ──
    print(f"\n\n{'='*95}")
    print(f"  FINAL SUMMARY - Fixed 1 ETH per Trade (Non-Compounding)")
    print(f"  Binance real 15m K-lines, strategy_bot.py signal logic")
    print(f"{'='*95}")
    print(f"  {'Period':<18} {'Strategy':<24} {'PnL($)':>10} {'Ret%':>7} {'Trades':>6} {'Win%':>7} {'MaxDD':>10} {'AvgW':>8} {'AvgL':>8} {'Best/Worst':>14}")
    print(f"  {'-'*95}")

    for i, (period_name, _) in enumerate(periods):
        for results, strat_name in [(occ_all, "OCC v8.13 (TEMA)"), (volty_all, "Volty Expan Close")]:
            for r in results:
                if r["period"] == period_name:
                    print(f"  {period_name:<18} {strat_name:<24} ${r['pnl']:>9,.0f} "
                          f"{r['ret']:>6.1f}% {r['trades']:>5}  "
                          f"{r['wr']:>6.1f}% ${r['max_dd']:>9,.0f} "
                          f"${r['avg_win']:>7,.0f} ${r['avg_loss']:>7,.0f} "
                          f"${r['best']:+.0f}/${r['worst']:+.0f}")
        print(f"  {'-'*95}")

    # ── Monthly breakdown for past year ──
    print(f"\n\n{'='*70}")
    print(f"  MONTHLY PnL BREAKDOWN (Past Year, 1 ETH fixed)")
    print(f"{'='*70}")
    print(f"  {'Month':<12} {'OCC v8.13':>12} {'Volty':>12} {'ETH Price':>12}")
    print(f"  {'-'*50}")

    df_year = df[df.index >= now - pd.Timedelta(days=365)]
    monthly_occ = {}
    monthly_volty = {}
    monthly_price = {}

    # Run through the year data once for each strategy to get monthly
    from strategy_bot import compute_signals, compute_signals_volty

    # OCC monthly
    sig_occ = compute_signals(df_year, "TEMA", 8, 3, 15, 0, 5)
    pos = None; ep = 0.0
    for i in range(80, len(sig_occ)):
        lt = sig_occ["long_cond"].iloc[i]; st = sig_occ["short_cond"].iloc[i]
        cp = df_year["close"].iloc[i]; m = df_year.index[i].strftime("%Y-%m")
        monthly_price[m] = cp
        if pos is None:
            if lt: pos = "L"; ep = cp
            elif st: pos = "S"; ep = cp
        elif pos == "L":
            if st:
                pnl = (cp - ep) * 1.0
                monthly_occ[m] = monthly_occ.get(m, 0) + pnl
                pos = "S"; ep = cp
        elif pos == "S":
            if lt:
                pnl = (ep - cp) * 1.0
                monthly_occ[m] = monthly_occ.get(m, 0) + pnl
                pos = "L"; ep = cp

    # Volty monthly
    sig_volty = compute_signals_volty(df_year, 5, 0.75)
    sma50 = df_year["close"].rolling(50).mean().shift(1)
    trend_up = df_year["close"] > sma50
    length, atr_mult = 5, 0.75
    tr = pd.concat([
        df_year["high"] - df_year["low"],
        (df_year["high"] - df_year["close"].shift(1)).abs(),
        (df_year["low"] - df_year["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    atrs = tr.rolling(window=length).mean() * atr_mult
    lsl = (df_year["close"] + atrs).shift(1)
    ssl = (df_year["close"] - atrs).shift(1)

    pos = None; ep = 0.0
    for i in range(length + 50 + 2, len(df_year)):
        lt = sig_volty["long_cond"].iloc[i] and trend_up.iloc[i]
        st = sig_volty["short_cond"].iloc[i] and (not trend_up.iloc[i])
        m = df_year.index[i].strftime("%Y-%m")
        if pos is None:
            if lt: pos = "L"; ep = lsl.iloc[i]
            elif st: pos = "S"; ep = ssl.iloc[i]
        elif pos == "L":
            if st:
                pnl = (ssl.iloc[i] - ep) * 1.0
                monthly_volty[m] = monthly_volty.get(m, 0) + pnl
                pos = "S"; ep = ssl.iloc[i]
        elif pos == "S":
            if lt:
                pnl = (ep - lsl.iloc[i]) * 1.0
                monthly_volty[m] = monthly_volty.get(m, 0) + pnl
                pos = "L"; ep = lsl.iloc[i]

    all_months = sorted(set(list(monthly_occ.keys()) + list(monthly_volty.keys())))
    occ_total = 0; volty_total = 0
    for m in all_months:
        o = monthly_occ.get(m, 0)
        v = monthly_volty.get(m, 0)
        p = monthly_price.get(m, 0)
        occ_total += o; volty_total += v
        print(f"  {m:<12} ${o:>+10,.0f} ${v:>+10,.0f} ${p:>10,.0f}")
    print(f"  {'-'*50}")
    print(f"  {'TOTAL':<12} ${occ_total:>+10,.0f} ${volty_total:>+10,.0f}")


if __name__ == "__main__":
    main()
