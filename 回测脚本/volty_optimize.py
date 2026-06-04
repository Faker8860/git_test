"""Test Volty optimizations side by side."""
import os, sys
sys.path.insert(0, '/opt/trading')

import pandas as pd
import numpy as np
from backtest_engine import fetch_binance_history

df = fetch_binance_history('ETHUSDT', 90, '15m')

def backtest_volty(df, length=5, atr_mult=0.75, trend_filter=False, atr_min=0, time_filter=False):
    tr = pd.concat([
        df['high']-df['low'],
        (df['high']-df['close'].shift(1)).abs(),
        (df['low']-df['close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    atrs = tr.rolling(length).mean() * atr_mult

    long_stop = (df['close'] + atrs).shift(1)
    short_stop = (df['close'] - atrs).shift(1)

    # Trend filter: SMA (boolean)
    if trend_filter:
        sma50 = df['close'].rolling(50).mean().shift(1)
        trend_up = (df['close'] > sma50).astype(bool)
    else:
        trend_up = None

    long_trig = df['high'] >= long_stop
    short_trig = df['low'] <= short_stop

    # Min ATR filter
    if atr_min > 0:
        no_trade = atrs.shift(1) < atr_min
        long_trig[no_trade] = False
        short_trig[no_trade] = False

    # Trend filter: only trade in trend direction
    if trend_filter and trend_up is not None:
        long_trig = long_trig & trend_up      # only long if above SMA50
        short_trig = short_trig & (~trend_up)  # only short if below SMA50

    # Both triggered
    both = long_trig & short_trig
    long_trig[both & (abs(df['open']-long_stop) > abs(df['open']-short_stop))] = False
    short_trig[both & (abs(df['open']-short_stop) >= abs(df['open']-long_stop))] = False

    # Time filter: skip 00:00-06:00 UTC (Asia low vol)
    if time_filter:
        hour = df.index.hour
        skip = (hour >= 0) & (hour < 6)
        long_trig[skip] = False
        short_trig[skip] = False

    position = None; entry_price = 0
    pnl = 0; trades = 0; wins = 0
    max_dd = 0; peak = 0

    for i in range(max(length+2, 52), len(df)):
        if position is None:
            if long_trig.iloc[i]: position='LONG'; entry_price=long_stop.iloc[i]
            elif short_trig.iloc[i]: position='SHORT'; entry_price=short_stop.iloc[i]
        elif position == 'LONG':
            if short_trig.iloc[i]:
                p = short_stop.iloc[i]-entry_price
                pnl += p; trades += 1
                if p > 0: wins += 1
                if pnl > peak: peak = pnl
                dd = peak - pnl
                if dd > max_dd: max_dd = dd
                position='SHORT'; entry_price=short_stop.iloc[i]
        elif position == 'SHORT':
            if long_trig.iloc[i]:
                p = entry_price-long_stop.iloc[i]
                pnl += p; trades += 1
                if p > 0: wins += 1
                if pnl > peak: peak = pnl
                dd = peak - pnl
                if dd > max_dd: max_dd = dd
                position='LONG'; entry_price=long_stop.iloc[i]

    return {'pnl': pnl, 'trades': trades, 'wins': wins, 'max_dd': max_dd}

# Test different configurations
configs = [
    ("原始 (5, 0.75)",        5, 0.75, False, 0, False),
    ("(8, 0.75) ATR加长",     8, 0.75, False, 0, False),
    ("(5, 1.0) 倍数加大",     5, 1.0,  False, 0, False),
    ("(3, 0.5) 更敏感",       3, 0.5,  False, 0, False),
    ("(5, 0.75) +趋势过滤",   5, 0.75, True,  0, False),
    ("(5, 0.75) +低波动过滤", 5, 0.75, False, 3, False),
    ("(5, 0.75) +时间过滤",   5, 0.75, False, 0, True),
    ("(5, 0.75) +趋势+波动",  5, 0.75, True,  3, False),
    ("(8, 1.0) +趋势+波动",   8, 1.0,  True,  3, False),
]

print(f"{'Config':<25} {'PnL':>8} {'Trades':>7} {'Win':>6} {'$/trade':>8} {'MaxDD':>8}")
print("-" * 70)

best = None
best_pnl = -999
for name, length, mult, trend, atr_min, time_f in configs:
    r = backtest_volty(df, length, mult, trend, atr_min, time_f)
    avg = r['pnl']/r['trades'] if r['trades'] > 0 else 0
    wr = r['wins']/r['trades']*100 if r['trades'] > 0 else 0
    marker = " <--" if r['pnl'] > best_pnl else ""
    if r['pnl'] > best_pnl:
        best_pnl = r['pnl']
        best = (name, r)
    print(f"{name:<25} ${r['pnl']:>7.0f} {r['trades']:>7} {wr:>5.0f}% ${avg:>7.2f} ${r['max_dd']:>7.0f}{marker}")

print(f"\nBest: {best[0]} — ${best[1]['pnl']:.0f} profit, {best[1]['trades']} trades")
