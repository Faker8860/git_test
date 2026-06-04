"""Volty backtest with STOP PRICE entries (matching TV execution)."""
import os, sys
sys.path.insert(0, '/opt/trading')
os.environ['TIMEFRAME_MINUTES'] = '15'

import pandas as pd
import numpy as np
from backtest_engine import fetch_binance_history

df = fetch_binance_history('ETHUSDT', 90, '15m')

# Calculate ATR and stop levels
length, atr_mult = 5, 0.75
tr = pd.concat([
    df["high"] - df["low"],
    (df["high"] - df["close"].shift(1)).abs(),
    (df["low"] - df["close"].shift(1)).abs(),
], axis=1).max(axis=1)
atrs = tr.rolling(window=length).mean() * atr_mult

# Stop levels placed at bar N-1, checked during bar N
long_stop = (df["close"] + atrs).shift(1)   # stop buy level
short_stop = (df["close"] - atrs).shift(1)  # stop sell level

# Entry: triggered if bar's high >= long_stop or low <= short_stop
long_trig = df["high"] >= long_stop
short_trig = df["low"] <= short_stop

# Both triggered? Pick the one closer to open
both = long_trig & short_trig
dist_long = abs(df["open"] - long_stop)
dist_short = abs(df["open"] - short_stop)
long_trig[both & (dist_long > dist_short)] = False
short_trig[both & (dist_short >= dist_long)] = False

# Simulate trades with STOP PRICE entries (matching TV exactly)
position = None
entry_price = 0
entry_time = None
trades = []

for i in range(length + 2, len(df)):
    if position is None:
        if long_trig.iloc[i]:
            position = "LONG"
            entry_price = long_stop.iloc[i]  # <-- STOP PRICE, not close!
            entry_time = df.index[i]
        elif short_trig.iloc[i]:
            position = "SHORT"
            entry_price = short_stop.iloc[i]  # <-- STOP PRICE, not close!
            entry_time = df.index[i]
    elif position == "LONG":
        if short_trig.iloc[i]:
            exit_price = short_stop.iloc[i]  # flip: exit at short stop
            pnl = (exit_price - entry_price) / entry_price * 100
            trades.append({"entry": entry_time, "exit": df.index[i], "dir": "LONG",
                          "entry_px": entry_price, "exit_px": exit_price, "pnl": pnl})
            position = "SHORT"
            entry_price = short_stop.iloc[i]
            entry_time = df.index[i]
    elif position == "SHORT":
        if long_trig.iloc[i]:
            exit_price = long_stop.iloc[i]  # flip: exit at long stop
            pnl = (entry_price - exit_price) / entry_price * 100
            trades.append({"entry": entry_time, "exit": df.index[i], "dir": "SHORT",
                          "entry_px": entry_price, "exit_px": exit_price, "pnl": pnl})
            position = "LONG"
            entry_price = long_stop.iloc[i]
            entry_time = df.index[i]

# Stats
closed = [t for t in trades if t["pnl"] != 0]
wins = sum(1 for t in closed if t["pnl"] > 0)
total = sum(t["pnl"] for t in closed)

print(f"Period: {df.index[0]} -> {df.index[-1]}")
print(f"Trades: {len(closed)}")
print(f"Win: {wins}/{len(closed)} = {wins/len(closed)*100:.0f}%")
print(f"Total PnL: {total:+.2f}%")
print(f"Avg/trade: {total/len(closed):+.4f}%")
print(f"Max win: {max(t['pnl'] for t in closed):+.2f}%")
print(f"Max loss: {min(t['pnl'] for t in closed):+.2f}%")

# Monthly breakdown
t_df = pd.DataFrame(closed)
t_df['month'] = pd.to_datetime(t_df['entry']).dt.to_period('M')
monthly = t_df.groupby('month')['pnl'].sum()
print(f"\nMonthly:")
for m, v in monthly.items():
    print(f"  {m}: {v:+.1f}%")
print(f"  Total: {monthly.sum():+.1f}%")
