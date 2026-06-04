import pandas as pd, numpy as np, sys
sys.path.insert(0, '/opt/trading')
from backtest_engine import fetch_binance_history

for tf_min in [15, 30]:
    df = fetch_binance_history('ETHUSDT', 90, f'{tf_min}m')

    tr = pd.concat([df['high']-df['low'], (df['high']-df['close'].shift(1)).abs(), (df['low']-df['close'].shift(1)).abs()], axis=1).max(axis=1)
    atrs = tr.rolling(5).mean() * 0.75
    long_stop = (df['close']+atrs).shift(1)
    short_stop = (df['close']-atrs).shift(1)
    long_trig = df['high'] >= long_stop
    short_trig = df['low'] <= short_stop
    both = long_trig & short_trig
    long_trig[both & (abs(df['open']-long_stop)>abs(df['open']-short_stop))] = False
    short_trig[both & (abs(df['open']-short_stop)>=abs(df['open']-long_stop))] = False

    # Trend filter
    sma50 = df['close'].rolling(50).mean().shift(1)
    trend_up = (df['close'] > sma50).astype(bool)
    long_trig = long_trig & trend_up
    short_trig = short_trig & (~trend_up)

    pos=None; ep=0; pnl=0; trades=0; wins=0; peak=0; maxdd=0
    monthly = {}
    for i in range(52, len(df)):
        if pos is None:
            if long_trig.iloc[i]: pos='L'; ep=long_stop.iloc[i]
            elif short_trig.iloc[i]: pos='S'; ep=short_stop.iloc[i]
        elif pos=='L':
            if short_trig.iloc[i]:
                p=short_stop.iloc[i]-ep; pnl+=p; trades+=1
                if p>0: wins+=1
                if pnl>peak: peak=pnl
                if peak-pnl>maxdd: maxdd=peak-pnl
                m=df.index[i].strftime('%Y-%m'); monthly[m]=monthly.get(m,0)+p
                pos='S'; ep=short_stop.iloc[i]
        elif pos=='S':
            if long_trig.iloc[i]:
                p=ep-long_stop.iloc[i]; pnl+=p; trades+=1
                if p>0: wins+=1
                if pnl>peak: peak=pnl
                if peak-pnl>maxdd: maxdd=peak-pnl
                m=df.index[i].strftime('%Y-%m'); monthly[m]=monthly.get(m,0)+p
                pos='L'; ep=long_stop.iloc[i]

    wr = wins/trades*100 if trades>0 else 0
    print(f'--- {tf_min}min ---')
    print(f'Trades: {trades}  Win: {wr:.0f}%  PnL: ${pnl:+.0f}  MaxDD: ${maxdd:.0f}')
    for m in sorted(monthly.keys()):
        print(f'  {m}: ${monthly[m]:+.0f}')
    print()
