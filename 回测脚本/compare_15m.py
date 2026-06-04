"""Compare TV v8.13 15-min backtest with Python signals."""
import os, sys
sys.path.insert(0, '/opt/trading')
os.environ['TIMEFRAME_MINUTES'] = '15'

import pandas as pd
import numpy as np
from datetime import timedelta
from strategy_bot import compute_signals
from backtest_engine import fetch_binance_history, extract_trades_from_signals

# Load TV CSV
tv = pd.read_csv('/opt/trading/tv_15m.csv')
print(f"TV CSV: {len(tv)} rows, cols={list(tv.columns)[:4]}")

# Extract entries
tv_entries = []
for i, row in tv.iterrows():
    ttype = str(row['Type'])
    if 'Entry' in ttype:
        dt = pd.Timestamp(row['Date and time'])
        direction = 'LONG' if 'long' in ttype.lower() else 'SHORT'
        tv_entries.append({'time': dt, 'direction': direction})

tv_times = pd.to_datetime(tv['Date and time'])
print(f"TV range: {tv_times.min()} -> {tv_times.max()}")
print(f"TV entries: {len(tv_entries)} (LONG={sum(1 for e in tv_entries if e['direction']=='LONG')}, SHORT={sum(1 for e in tv_entries if e['direction']=='SHORT')})")

# Fetch Binance 15-min data
start = tv_times.min() - timedelta(days=2)
end = tv_times.max() + timedelta(days=1)
df = fetch_binance_history('ETHUSDT', 30, '15m')
if df is None:
    print("Failed to fetch data")
    exit()

mask = (df.index >= start) & (df.index <= end)
df_f = df[mask]
print(f"Binance bars: {len(df_f)}, {df_f.index[0]} -> {df_f.index[-1]}")

# Run Python signals with same params as TV
sig = compute_signals(df_f, 'TEMA', 8, 3, 15, 0, 5)
print(f"xlong={sig['xlong'].sum()}, xshort={sig['xshort'].sum()}, long_cond={sig['long_cond'].sum()}, short_cond={sig['short_cond'].sum()}")

trades = extract_trades_from_signals(df_f, sig, 'TEMA')
py_entries = [{'time': t['entry_time'], 'direction': t['direction']} for t in trades]
print(f"Python entries: {len(py_entries)}")

# Match
print()
print(f"{'TV entry':<22} {'Dir':<6} {'Python entry':<22} {'Diff':>6}  Match")
print("-" * 75)

matched_1m = 0
matched_15m = 0
total = 0
diffs = []

for tv_e in tv_entries:
    total += 1
    tv_t = tv_e['time']
    best_diff = float('inf')
    best_py = None

    for py_e in py_entries:
        if py_e['direction'] != tv_e['direction']:
            continue
        diff = abs((py_e['time'] - tv_t).total_seconds() / 60)
        if diff < best_diff:
            best_diff = diff
            best_py = py_e

    diffs.append(best_diff)

    if best_diff <= 1:
        status = 'OK'
        matched_1m += 1
        matched_15m += 1
    elif best_diff <= 15:
        status = '~15m'
        matched_15m += 1
    elif best_diff <= 60:
        status = f'{best_diff:.0f}m'
    else:
        status = 'X'

    py_str = str(best_py['time'])[:19] if best_py else '-'
    print(f"{str(tv_t)[:19]:<22} {tv_e['direction']:<6} {py_str:<22} {best_diff:>5.0f}m  {status}")

print()
print(f"Exact match (<=1min): {matched_1m}/{total} = {matched_1m/total*100:.0f}%")
print(f"Within 15min: {matched_15m}/{total} = {matched_15m/total*100:.0f}%")
if diffs:
    print(f"Average diff: {sum(diffs)/len(diffs):.0f} min")
    print(f"Median diff: {sorted(diffs)[len(diffs)//2]:.0f} min")
