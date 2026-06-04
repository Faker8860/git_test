"""OCC v8.13 - compounding backtest (process_orders_on_close=true)."""
import os, sys
sys.path.insert(0, '/opt/trading')
os.environ['TIMEFRAME_MINUTES'] = '15'

import pandas as pd
import numpy as np
from strategy_bot import compute_signals
from backtest_engine import fetch_binance_history

df = fetch_binance_history('ETHUSDT', 90, '15m')
sig = compute_signals(df, 'TEMA', 8, 3, 15, 0, 5)

equity = 10000.0
position = None
entry_price = 0
trades = []
monthly_pnl = {}

for i in range(1, len(sig)):
    long_trig = sig["long_cond"].iloc[i]
    short_trig = sig["short_cond"].iloc[i]
    close_px = df["close"].iloc[i]

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
            month = df.index[i].strftime('%Y-%m')
            monthly_pnl[month] = monthly_pnl.get(month, 0) + pnl_usdt
            trades.append({'pnl': pnl_usdt})
            position = "SHORT"
            entry_price = close_px
    elif position == "SHORT":
        if long_trig:
            pnl_pct = (entry_price - close_px) / entry_price
            pnl_usdt = equity * pnl_pct
            equity += pnl_usdt
            month = df.index[i].strftime('%Y-%m')
            monthly_pnl[month] = monthly_pnl.get(month, 0) + pnl_usdt
            trades.append({'pnl': pnl_usdt})
            position = "LONG"
            entry_price = close_px

wins = sum(1 for t in trades if t['pnl'] > 0)
total_pnl = sum(t['pnl'] for t in trades)

print(f"OCC v8.13 (TEMA 15-min, compounding)")
print(f"Period: {df.index[0]} -> {df.index[-1]}")
print(f"Trades: {len(trades)}")
print(f"Win: {wins}/{len(trades)} = {wins/len(trades)*100:.0f}%")
print(f"Start: $10,000 -> Final: ${equity:,.0f} ({(equity/10000-1)*100:+.1f}%)")
print(f"\nMonthly:")
for m in sorted(monthly_pnl.keys()):
    print(f"  {m}: ${monthly_pnl[m]:+,.0f}")
print(f"  Total: ${total_pnl:+,.0f}")
