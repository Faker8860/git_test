"""Compare TV BINANCE backtest with Python signals (both 15-min)."""
import os, sys
sys.path.insert(0, '/opt/trading')
os.environ['TIMEFRAME_MINUTES'] = '15'

import pandas as pd
import numpy as np
from datetime import timedelta
from strategy_bot import compute_signals
from backtest_engine import fetch_binance_history, extract_trades_from_signals

# Load TV CSV
tv = pd.read_csv('/opt/trading/tv_binance_15m.csv')
tv_times = pd.to_datetime(tv['Date and time'])
print(f"TV: {len(tv)} rows, {tv_times.min()} -> {tv_times.max()}")

# Extract entries from TV
tv_entries = []
for i, row in tv.iterrows():
    ttype = str(row['Type'])
    if 'Entry' in ttype:
        dt = pd.Timestamp(row['Date and time'])
        direction = 'LONG' if 'long' in ttype.lower() else 'SHORT'
        tv_entries.append({'time': dt, 'direction': direction})
print(f"TV entries: {len(tv_entries)}")

# Fetch Binance data
start = tv_times.min() - timedelta(days=2)
end = tv_times.max() + timedelta(days=1)
df = fetch_binance_history('ETHUSDT', 85, '15m')

if df is None:
    print("Failed to fetch data")
    exit()

mask = (df.index >= start) & (df.index <= end)
df_f = df[mask]
print(f"Binance: {len(df_f)} bars, {df_f.index[0]} -> {df_f.index[-1]}")

# Run signals
sig = compute_signals(df_f, 'TEMA', 8, 3, 15, 0, 5)

# Extract Python entries
trades = extract_trades_from_signals(df_f, sig, 'TEMA')
py_entries = [{'time': t['entry_time'], 'direction': t['direction']} for t in trades]
print(f"Python entries: {len(py_entries)}")

# Only compare overlapping period
overlap_start = max(tv_entries[0]['time'], df_f.index[0])
overlap_end = min(tv_entries[-1]['time'], df_f.index[-1])

tv_overlap = [e for e in tv_entries if overlap_start <= e['time'] <= overlap_end]
py_overlap = [e for e in py_entries if overlap_start <= e['time'] <= overlap_end]
print(f"Overlap period: {overlap_start} -> {overlap_end}")
print(f"TV in overlap: {len(tv_overlap)}, Python in overlap: {len(py_overlap)}")

# Match each TV entry to nearest Python entry
print()
print(f"{'TV entry':<22} {'Dir':<6} {'Python entry':<22} {'Diff':>6}  Match")
print("-" * 75)

matched_1m = 0
matched_15m = 0
total = 0
diffs = []

for tv_e in tv_overlap:
    total += 1
    tv_t = tv_e['time']
    best_diff = float('inf')
    best_py = None

    for py_e in py_overlap:
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
