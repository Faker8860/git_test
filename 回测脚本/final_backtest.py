import sys
sys.path.insert(0, '/opt/trading')
import pandas as pd, numpy as np
from backtest_engine import fetch_binance_history

for days in [7, 30, 90]:
    df = fetch_binance_history('ETHUSDT', days, '15m')

    tr = pd.concat([df['high']-df['low'], (df['high']-df['close'].shift(1)).abs(), (df['low']-df['close'].shift(1)).abs()], axis=1).max(axis=1)
    atrs = tr.rolling(5).mean() * 0.75
    long_stop = (df['close']+atrs).shift(1)
    short_stop = (df['close']-atrs).shift(1)
    long_trig = df['high'] >= long_stop
    short_trig = df['low'] <= short_stop
    both = long_trig & short_trig
    long_trig[both & (abs(df['open']-long_stop)>abs(df['open']-short_stop))] = False
    short_trig[both & (abs(df['open']-short_stop)>=abs(df['open']-long_stop))] = False

    sma50 = df['close'].rolling(50).mean().shift(1)
    trend_up = (df['close'] > sma50).astype(bool)
    long_trig = long_trig & trend_up
    short_trig = short_trig & (~trend_up)

    account = 700.0
    contracts = 0.5  # 0.5 ETH per trade
    pos = None; ep = 0; trades = 0; wins = 0; maxdd = 0; peak = 700

    for i in range(52, len(df)):
        if pos is None:
            if long_trig.iloc[i]: pos='L'; ep=long_stop.iloc[i]
            elif short_trig.iloc[i]: pos='S'; ep=short_stop.iloc[i]
        elif pos == 'L':
            if short_trig.iloc[i]:
                pnl = (short_stop.iloc[i] - ep) * contracts
                account += pnl; trades += 1
                if pnl > 0: wins += 1
                if account > peak: peak = account
                if peak - account > maxdd: maxdd = peak - account
                pos='S'; ep=short_stop.iloc[i]
        elif pos == 'S':
            if long_trig.iloc[i]:
                pnl = (ep - long_stop.iloc[i]) * contracts
                account += pnl; trades += 1
                if pnl > 0: wins += 1
                if account > peak: peak = account
                if peak - account > maxdd: maxdd = peak - account
                pos='L'; ep=long_stop.iloc[i]

    wr = wins/trades*100 if trades else 0
    total_pnl = account - 700
    print(f"--- {days} days ({df.index[0].strftime('%m/%d')} - {df.index[-1].strftime('%m/%d')}) ---")
    print(f"  Trades: {trades}  Win: {wr:.0f}%")
    print(f"  Start: $700 -> Final: ${account:.0f}  PnL: ${total_pnl:+.0f} ({(account/700-1)*100:+.1f}%)")
    print(f"  Max DD: ${maxdd:.0f}")
    print()
