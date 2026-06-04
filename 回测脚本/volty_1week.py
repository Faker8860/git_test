"""Volty 1-week prediction: $700 account, 1 ETH per trade, 3x leverage."""
import os, sys
sys.path.insert(0, '/opt/trading')
os.environ['TIMEFRAME_MINUTES'] = '15'

import pandas as pd
import numpy as np
from backtest_engine import fetch_binance_history

df = fetch_binance_history('ETHUSDT', 7, '15m')

length, atr_mult = 5, 0.75
tr = pd.concat([
    df["high"] - df["low"],
    (df["high"] - df["close"].shift(1)).abs(),
    (df["low"] - df["close"].shift(1)).abs(),
], axis=1).max(axis=1)
atrs = tr.rolling(window=length).mean() * atr_mult

long_stop = (df["close"] + atrs).shift(1)
short_stop = (df["close"] - atrs).shift(1)

long_trig = df["high"] >= long_stop
short_trig = df["low"] <= short_stop
both = long_trig & short_trig
dist_long = abs(df["open"] - long_stop)
dist_short = abs(df["open"] - short_stop)
long_trig[both & (dist_long > dist_short)] = False
short_trig[both & (dist_short >= dist_long)] = False

# Fixed: 1 ETH per trade, 3x leverage, $700 account
account = 700.0
leverage = 3
contracts_per_trade = 1.0  # fixed 1 ETH
position = None
entry_price = 0
trades = []

for i in range(length + 2, len(df)):
    if position is None:
        if long_trig.iloc[i]:
            position = "LONG"
            entry_price = long_stop.iloc[i]
        elif short_trig.iloc[i]:
            position = "SHORT"
            entry_price = short_stop.iloc[i]
    elif position == "LONG":
        if short_trig.iloc[i]:
            exit_price = short_stop.iloc[i]
            pnl = (exit_price - entry_price) * contracts_per_trade
            account += pnl
            trades.append({'dir': 'LONG', 'pnl': pnl, 'entry': entry_price, 'exit': exit_price})
            position = "SHORT"
            entry_price = short_stop.iloc[i]
    elif position == "SHORT":
        if long_trig.iloc[i]:
            exit_price = long_stop.iloc[i]
            pnl = (entry_price - exit_price) * contracts_per_trade
            account += pnl
            trades.append({'dir': 'SHORT', 'pnl': pnl, 'entry': entry_price, 'exit': exit_price})
            position = "LONG"
            entry_price = long_stop.iloc[i]

wins = sum(1 for t in trades if t['pnl'] > 0)
total_pnl = sum(t['pnl'] for t in trades)
final = 700 + total_pnl

print(f"Period: {df.index[0]} -> {df.index[-1]}")
print(f"Account: $700, 1 ETH/trade, 3x leverage")
print(f"Trades: {len(trades)}")
print(f"Win: {wins}/{len(trades)} = {wins/len(trades)*100:.0f}%")
print(f"Total PnL: ${total_pnl:+.2f}")
print(f"Final: ${final:.2f} ({(final/700-1)*100:+.1f}%)")
print()

# Last 5 trades
print("Last 5 trades:")
for t in trades[-5:]:
    s = "+" if t['pnl'] >= 0 else ""
    print(f"  {t['dir']:>5}  entry={t['entry']:.2f}  exit={t['exit']:.2f}  PnL={s}{t['pnl']:.2f}")

# Daily breakdown
t_df = pd.DataFrame(trades)
t_df['day'] = [df.index[0] for _ in range(len(trades))]  # placeholder
daily = {}
for i, t in enumerate(trades):
    # approximate day from trade index
    day_idx = i * len(df) // len(trades)
    if day_idx < len(df):
        d = df.index[day_idx].strftime('%m-%d')
        daily[d] = daily.get(d, 0) + t['pnl']

print(f"\nApprox daily:")
for d in sorted(daily.keys()):
    print(f"  {d}: ${daily[d]:+.2f}")
