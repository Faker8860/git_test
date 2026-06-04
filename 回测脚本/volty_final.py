"""Volty backtest - exact TV match: stop prices + compounding + monthly breakdown."""
import os, sys
sys.path.insert(0, '/opt/trading')
os.environ['TIMEFRAME_MINUTES'] = '15'

import pandas as pd
import numpy as np
from backtest_engine import fetch_binance_history

df = fetch_binance_history('ETHUSDT', 90, '15m')

# ATR
length, atr_mult = 5, 0.75
tr = pd.concat([
    df["high"] - df["low"],
    (df["high"] - df["close"].shift(1)).abs(),
    (df["low"] - df["close"].shift(1)).abs(),
], axis=1).max(axis=1)
atrs = tr.rolling(window=length).mean() * atr_mult

# Stop levels (bar N-1 sets level, checked during bar N)
long_stop = (df["close"] + atrs).shift(1)
short_stop = (df["close"] - atrs).shift(1)

# Trigger detection
long_trig = df["high"] >= long_stop
short_trig = df["low"] <= short_stop
both = long_trig & short_trig
dist_long = abs(df["open"] - long_stop)
dist_short = abs(df["open"] - short_stop)
long_trig[both & (dist_long > dist_short)] = False
short_trig[both & (dist_short >= dist_long)] = False

# Simulate with COMPOUNDING (like TV: each trade uses 100% equity, profits reinvest)
equity = 10000.0  # Start with $10k like TV default
position = None
entry_price = 0
entry_time = None
trades = []
equity_curve = [equity]
monthly_pnl = {}

for i in range(length + 2, len(df)):
    if position is None:
        if long_trig.iloc[i]:
            position = "LONG"
            entry_price = long_stop.iloc[i]
            entry_time = df.index[i]
        elif short_trig.iloc[i]:
            position = "SHORT"
            entry_price = short_stop.iloc[i]
            entry_time = df.index[i]
    elif position == "LONG":
        if short_trig.iloc[i]:
            exit_price = short_stop.iloc[i]
            pnl_pct = (exit_price - entry_price) / entry_price
            pnl_usdt = equity * pnl_pct
            equity += pnl_usdt  # compound!
            month = df.index[i].strftime('%Y-%m')
            monthly_pnl[month] = monthly_pnl.get(month, 0) + pnl_usdt
            trades.append({"entry": entry_time, "exit": df.index[i], "dir": "LONG",
                          "entry_px": entry_price, "exit_px": exit_price, "pnl_usdt": pnl_usdt, "pnl_pct": pnl_pct*100})
            position = "SHORT"
            entry_price = short_stop.iloc[i]
            entry_time = df.index[i]
            equity_curve.append(equity)
    elif position == "SHORT":
        if long_trig.iloc[i]:
            exit_price = long_stop.iloc[i]
            pnl_pct = (entry_price - exit_price) / entry_price
            pnl_usdt = equity * pnl_pct
            equity += pnl_usdt  # compound!
            month = df.index[i].strftime('%Y-%m')
            monthly_pnl[month] = monthly_pnl.get(month, 0) + pnl_usdt
            trades.append({"entry": entry_time, "exit": df.index[i], "dir": "SHORT",
                          "entry_px": entry_price, "exit_px": exit_price, "pnl_usdt": pnl_usdt, "pnl_pct": pnl_pct*100})
            position = "LONG"
            entry_price = long_stop.iloc[i]
            entry_time = df.index[i]
            equity_curve.append(equity)

# Results
print(f"Period: {df.index[0]} -> {df.index[-1]}")
print(f"Start equity: $10,000")
print(f"Final equity: ${equity:,.0f}")
print(f"Total return: {(equity/10000-1)*100:+.1f}%")
print(f"Trades: {len(trades)}")

wins = sum(1 for t in trades if t["pnl_usdt"] > 0)
print(f"Win rate: {wins}/{len(trades)} = {wins/len(trades)*100:.0f}%")

print(f"\nMonthly PnL (USDT, compounded):")
for m in sorted(monthly_pnl.keys()):
    print(f"  {m}: ${monthly_pnl[m]:+,.0f}")
print(f"  Total: ${sum(monthly_pnl.values()):+,.0f}")

# Compare with TV
print(f"\n{'='*50}")
print(f"Comparison (Mar-May 2026):")
print(f"  {'Month':<10} {'TV':>8} {'Python':>10}")
print(f"  {'-'*30}")
tv_monthly = {'2026-03': 35.8, '2026-04': 17.2, '2026-05': 18.7}
for m in ['2026-03', '2026-04', '2026-05']:
    py_pct = monthly_pnl.get(m, 0) / 10000 * 100
    tv_pct = tv_monthly.get(m, 0)
    print(f"  {m:<10} {tv_pct:>+7.1f}% {py_pct:>+9.1f}%")
