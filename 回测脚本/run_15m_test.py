"""Run backtest with 15-minute K-lines matching server config."""
import os, sys
sys.path.insert(0, '/opt/trading')
os.environ['TIMEFRAME_MINUTES'] = '15'

import pandas as pd
import numpy as np
from strategy_bot import compute_signals, calc_ma
from backtest_engine import fetch_binance_history, extract_trades_from_signals

print("Fetching 15-min ETHUSDT data...")
df = fetch_binance_history('ETHUSDT', 30, '15m')
if df is None:
    print("Failed to fetch data")
    exit()

print(f"Got {len(df)} bars: {df.index[0]} -> {df.index[-1]}")

# Run OCC v8.13: TEMA(8), 3x HTF, 5min delay, 15-min base
sig = compute_signals(df, 'TEMA', 8, 3, 15, 0, 5)

print(f"Signals: xlong={sig['xlong'].sum()}, xshort={sig['xshort'].sum()}")
print(f"Entries: long_cond={sig['long_cond'].sum()}, short_cond={sig['short_cond'].sum()}")

trades = extract_trades_from_signals(df, sig, 'TEMA')
print(f"Trades: {len(trades)}")

print()
print("First 20 trades:")
for i, t in enumerate(trades[:20]):
    om = ' [open]' if t.get('open') else ''
    print(f"  {i+1}. {t['direction']:>5}  {t['entry_time']} -> {t['exit_time']}  PnL={t['pnl_pct']}%{om}")

closed = [t for t in trades if not t.get('open')]
if closed:
    wins = sum(1 for t in closed if t['pnl_pct'] > 0)
    total_pnl = sum(t['pnl_pct'] for t in closed)
    print(f"\nStats: {len(closed)} closed | Win={wins}/{len(closed)} ({wins/len(closed)*100:.0f}%) | Total PnL={total_pnl:.2f}% | Avg={total_pnl/len(closed):.2f}%")
