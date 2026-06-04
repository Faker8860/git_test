"""
Dual Strategy Backtest - OCC v8.13 (TEMA) + Volty Expan Close
=============================================================
Fetch real Binance K-line data, run both strategies.
Compounding mode, always-in-market.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import pandas as pd
import numpy as np
import ccxt
from datetime import datetime, timezone, timedelta


def fetch_binance_history(symbol: str, since_days: int, timeframe: str = "15m"):
    exchange = ccxt.binance({"enableRateLimit": True})

    if symbol.endswith("USDT"):
        sym = f"{symbol[:-4]}/USDT:USDT"
    else:
        sym = symbol

    since_ms = int((datetime.now(timezone.utc) - timedelta(days=since_days + 1)).timestamp() * 1000)
    all_candles = []
    limit = 1000

    print(f"  Fetching {sym} {timeframe} last {since_days} days...")

    while True:
        try:
            candles = exchange.fetch_ohlcv(sym, timeframe, since=since_ms, limit=limit)
        except Exception as e:
            print(f"  Fetch error: {e}")
            break
        if not candles:
            break
        all_candles.extend(candles)
        if len(candles) < limit:
            break
        since_ms = candles[-1][0] + 1

    if not all_candles:
        return None

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)
    print(f"  Got {len(df)} bars, {df.index[0]} -> {df.index[-1]}")
    return df


def run_occ_strategy(df_15m, initial_capital=10000):
    """
    OCC v8.13 (TEMA) Strategy
    Params: TEMA(8), CROSS_MULT=3, 15min, DELAY=5
    Compounding, always-in-market
    """
    from strategy_bot import compute_signals

    ma_type = "TEMA"
    ma_len = 8
    cross_mult = 3
    tf_minutes = 15
    delay_offset = 0
    delay_minutes = 5

    sig = compute_signals(df_15m, ma_type, ma_len, cross_mult, tf_minutes, delay_offset, delay_minutes)

    equity = float(initial_capital)
    position = None
    entry_price = 0.0
    trades = []
    peak = equity
    max_dd = 0.0

    start_i = max(ma_len * cross_mult + 10, 60)
    for i in range(start_i, len(sig)):
        long_trig = sig["long_cond"].iloc[i]
        short_trig = sig["short_cond"].iloc[i]
        close_px = df_15m["close"].iloc[i]

        if position is None:
            if long_trig:
                position = "LONG"
                entry_price = close_px
            elif short_trig:
                position = "SHORT"
                entry_price = close_px
        elif position == "LONG":
            if short_trig:
                pnl_pct = (close_px - entry_price) / entry_price
                pnl_usdt = equity * pnl_pct
                equity += pnl_usdt
                trades.append({
                    "entry_time": df_15m.index[i],
                    "exit_time": df_15m.index[i],
                    "direction": "LONG",
                    "entry_px": entry_price,
                    "exit_px": close_px,
                    "pnl_usdt": pnl_usdt,
                    "pnl_pct": pnl_pct * 100
                })
                if equity > peak:
                    peak = equity
                dd = peak - equity
                if dd > max_dd:
                    max_dd = dd
                position = "SHORT"
                entry_price = close_px
        elif position == "SHORT":
            if long_trig:
                pnl_pct = (entry_price - close_px) / entry_price
                pnl_usdt = equity * pnl_pct
                equity += pnl_usdt
                trades.append({
                    "entry_time": df_15m.index[i],
                    "exit_time": df_15m.index[i],
                    "direction": "SHORT",
                    "entry_px": entry_price,
                    "exit_px": close_px,
                    "pnl_usdt": pnl_usdt,
                    "pnl_pct": pnl_pct * 100
                })
                if equity > peak:
                    peak = equity
                dd = peak - equity
                if dd > max_dd:
                    max_dd = dd
                position = "LONG"
                entry_price = close_px

    wins = sum(1 for t in trades if t["pnl_usdt"] > 0)
    total_pnl = equity - initial_capital

    return {
        "strategy": "OCC v8.13 (TEMA)",
        "initial_capital": initial_capital,
        "final_equity": equity,
        "total_pnl": total_pnl,
        "total_return_pct": (equity / initial_capital - 1) * 100,
        "num_trades": len(trades),
        "wins": wins,
        "win_rate": wins / len(trades) * 100 if trades else 0,
        "max_dd": max_dd,
        "max_dd_pct": max_dd / peak * 100 if peak > 0 else 0,
        "trades": trades,
        "period_start": df_15m.index[0],
        "period_end": df_15m.index[-1],
    }


def run_volty_strategy(df_15m, initial_capital=10000):
    """
    Volty Expan Close Strategy
    Params: length=5, ATR multiplier=0.75, 15min, SMA50 trend filter
    Compounding, always-in-market
    """
    from strategy_bot import compute_signals_volty

    length = 5
    atr_mult = 0.75
    volty_trend_filter = True

    sig = compute_signals_volty(df_15m, length, atr_mult)

    sma50 = df_15m["close"].rolling(50).mean().shift(1)
    trend_up = df_15m["close"] > sma50

    equity = float(initial_capital)
    position = None
    entry_price = 0.0
    trades = []
    peak = equity
    max_dd = 0.0

    for i in range(length + 50 + 2, len(df_15m)):
        long_trig = sig["long_cond"].iloc[i]
        short_trig = sig["short_cond"].iloc[i]
        close_px = df_15m["close"].iloc[i]

        if volty_trend_filter:
            long_trig = long_trig and trend_up.iloc[i]
            short_trig = short_trig and (not trend_up.iloc[i])

        if position is None:
            if long_trig:
                position = "LONG"
                entry_price = close_px
            elif short_trig:
                position = "SHORT"
                entry_price = close_px
        elif position == "LONG":
            if short_trig:
                pnl_pct = (close_px - entry_price) / entry_price
                pnl_usdt = equity * pnl_pct
                equity += pnl_usdt
                trades.append({
                    "entry_time": df_15m.index[i],
                    "exit_time": df_15m.index[i],
                    "direction": "LONG",
                    "entry_px": entry_price,
                    "exit_px": close_px,
                    "pnl_usdt": pnl_usdt,
                    "pnl_pct": pnl_pct * 100
                })
                if equity > peak:
                    peak = equity
                dd = peak - equity
                if dd > max_dd:
                    max_dd = dd
                position = "SHORT"
                entry_price = close_px
        elif position == "SHORT":
            if long_trig:
                pnl_pct = (entry_price - close_px) / entry_price
                pnl_usdt = equity * pnl_pct
                equity += pnl_usdt
                trades.append({
                    "entry_time": df_15m.index[i],
                    "exit_time": df_15m.index[i],
                    "direction": "SHORT",
                    "entry_px": entry_price,
                    "exit_px": close_px,
                    "pnl_usdt": pnl_usdt,
                    "pnl_pct": pnl_pct * 100
                })
                if equity > peak:
                    peak = equity
                dd = peak - equity
                if dd > max_dd:
                    max_dd = dd
                position = "LONG"
                entry_price = close_px

    wins = sum(1 for t in trades if t["pnl_usdt"] > 0)
    total_pnl = equity - initial_capital

    return {
        "strategy": "Volty Expan Close",
        "initial_capital": initial_capital,
        "final_equity": equity,
        "total_pnl": total_pnl,
        "total_return_pct": (equity / initial_capital - 1) * 100,
        "num_trades": len(trades),
        "wins": wins,
        "win_rate": wins / len(trades) * 100 if trades else 0,
        "max_dd": max_dd,
        "max_dd_pct": max_dd / peak * 100 if peak > 0 else 0,
        "trades": trades,
        "period_start": df_15m.index[0],
        "period_end": df_15m.index[-1],
    }


def print_results(result, period_label):
    r = result
    print(f"\n{'='*70}")
    print(f"  {r['strategy']} -- {period_label}")
    print(f"{'='*70}")
    print(f"  Period: {r['period_start']} -> {r['period_end']}")
    print(f"  Start:      ${r['initial_capital']:,.0f}")
    print(f"  Final:      ${r['final_equity']:,.0f}")
    print(f"  Total PnL:  ${r['total_pnl']:+,.0f}  ({r['total_return_pct']:+.2f}%)")
    print(f"  Trades:     {r['num_trades']}")
    print(f"  Win Rate:   {r['win_rate']:.1f}%  ({r['wins']}/{r['num_trades']})")
    print(f"  Max DD:     ${r['max_dd']:,.0f}  ({r['max_dd_pct']:.2f}%)")

    if r['num_trades'] > 0:
        win_trades = [t['pnl_usdt'] for t in r['trades'] if t['pnl_usdt'] > 0]
        loss_trades = [t['pnl_usdt'] for t in r['trades'] if t['pnl_usdt'] < 0]
        avg_win = np.mean(win_trades) if win_trades else 0
        avg_loss = np.mean(loss_trades) if loss_trades else 0
        total_wins = sum(win_trades) if win_trades else 0
        total_losses = abs(sum(loss_trades)) if loss_trades else 0
        profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
        print(f"  Avg Win:    ${avg_win:+,.0f}  Avg Loss: ${avg_loss:+,.0f}")
        print(f"  Profit Factor: {profit_factor:.2f}")

    if r['trades']:
        print(f"\n  Last 5 trades:")
        for t in r['trades'][-5:]:
            mark = "WIN" if t['pnl_usdt'] > 0 else "LOSS"
            print(f"    [{mark}] {t['direction']:>5}  entry={t['entry_px']:.2f}  exit={t['exit_px']:.2f}  "
                  f"PnL=${t['pnl_usdt']:+,.2f} ({t['pnl_pct']:+.2f}%)")


def main():
    print("=" * 70)
    print("  Dual Strategy Backtest - Binance ETHUSDT 15m")
    print("  Strategy 1: OCC v8.13 (TEMA Trend Following)")
    print("  Strategy 2: Volty Expan Close (ATR Breakout)")
    print("  Compounding, initial capital $10,000")
    print("=" * 70)

    max_days = 365
    print(f"\n[1] Fetching Binance ETHUSDT 15m data (last {max_days} days)...")
    df_full = fetch_binance_history("ETHUSDT", max_days, "15m")

    if df_full is None or df_full.empty:
        print("ERROR: Cannot fetch data. Check network.")
        return

    now = datetime.now(timezone.utc)
    periods = {
        "Past Week":       7,
        "Past Month":      30,
        "Past 3 Months":   90,
        "Past Year":       365,
    }

    all_results = {}

    for period_name, days in periods.items():
        cutoff = now - timedelta(days=days)
        df_period = df_full[df_full.index >= cutoff]

        if len(df_period) < 100:
            print(f"\n[!] {period_name}: insufficient data ({len(df_period)} bars), skipping")
            continue

        print(f"\n{'#'*70}")
        print(f"# {period_name} ({len(df_period)} bars, {df_period.index[0]} -> {df_period.index[-1]})")
        print(f"{'#'*70}")

        # OCC strategy
        try:
            occ_result = run_occ_strategy(df_period, initial_capital=10000)
            print_results(occ_result, period_name)
            all_results[f"OCC_{period_name}"] = occ_result
        except Exception as e:
            print(f"ERROR: OCC {period_name} backtest failed: {e}")
            import traceback
            traceback.print_exc()

        # Volty strategy
        try:
            volty_result = run_volty_strategy(df_period, initial_capital=10000)
            print_results(volty_result, period_name)
            all_results[f"VOLTY_{period_name}"] = volty_result
        except Exception as e:
            print(f"ERROR: Volty {period_name} backtest failed: {e}")
            import traceback
            traceback.print_exc()

    # Summary table
    print(f"\n\n{'='*90}")
    print(f"  SUMMARY COMPARISON TABLE")
    print(f"{'='*90}")
    print(f"  {'Period':<18} {'Strategy':<24} {'Final $':>10} {'Return':>9} {'Trades':>7} {'Win%':>8} {'Max DD':>10}")
    print(f"  {'-'*90}")

    for period_name in ["Past Week", "Past Month", "Past 3 Months", "Past Year"]:
        for strat_key in [f"OCC_{period_name}", f"VOLTY_{period_name}"]:
            if strat_key in all_results:
                r = all_results[strat_key]
                strat_short = "OCC v8.13" if "OCC" in strat_key else "Volty"
                print(f"  {period_name:<18} {strat_short:<24} ${r['final_equity']:>9,.0f} "
                      f"{r['total_return_pct']:>8.2f}% {r['num_trades']:>6}  "
                      f"{r['win_rate']:>7.1f}% ${r['max_dd']:>9,.0f}")
        print(f"  {'-'*90}")

    print(f"\nBacktest complete!")


if __name__ == "__main__":
    main()
