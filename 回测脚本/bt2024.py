import ccxt, pandas as pd, numpy as np, time
from strategy_bot import compute_signals_volty, calc_ma

ex = ccxt.binance({'enableRateLimit': True})
print("Fetching 2024 ETHUSDT 15m data...")
all_k = []
since = int(pd.Timestamp('2024-01-01').timestamp() * 1000)

for _ in range(40):
    try:
        batch = ex.fetch_ohlcv('ETH/USDT:USDT', '15m', since=since, limit=1000)
    except Exception as e:
        print(f'Error: {e}')
        break
    if not batch: break
    all_k.extend(batch)
    if batch[-1][0] >= int(pd.Timestamp('2025-01-01').timestamp() * 1000): break
    since = batch[-1][0] + 1
    if len(batch) < 1000: break
    time.sleep(0.05)

ncols = len(all_k[0])
cols = ['t','o','h','l','c','v'][:ncols]
df = pd.DataFrame(all_k, columns=cols)
df['t'] = pd.to_datetime(df['t'], unit='ms')
df.set_index('t', inplace=True)
for col in ['o','h','l','c','v']:
    if col in df.columns: df[col] = pd.to_numeric(df[col])
df = df.rename(columns={'o':'open','h':'high','l':'low','c':'close','v':'volume'})
df = df[~df.index.duplicated()].sort_index()

df_2024 = df[(df.index >= '2024-01-01') & (df.index < '2025-01-01')]
print(f'2024: {len(df_2024)} bars, {df_2024.index[0]} -> {df_2024.index[-1]}')
print(f'ETH: {df_2024.close.min():.0f} - {df_2024.close.max():.0f} ({(df_2024.close.iloc[-1]/df_2024.close.iloc[0]-1)*100:+.1f}%)')

INITIAL, LEVERAGE = 680, 3
sig = compute_signals_volty(df_2024, 5, 0.75)
trend_ma = calc_ma(df_2024['close'], 'EMA', 50).shift(1)
trend_up = df_2024['close'] > trend_ma
tr = pd.concat([df_2024['high']-df_2024['low'], (df_2024['high']-df_2024['close'].shift(1)).abs(),
                (df_2024['low']-df_2024['close'].shift(1)).abs()], axis=1).max(axis=1)
atrs = tr.rolling(5).mean() * 0.75
lsl = (df_2024['close']+atrs).shift(1)
ssl = (df_2024['close']-atrs).shift(1)

equity = float(INITIAL)
pos = None; ep = 0.0; trades = []
peak = equity; max_dd = 0.0

for i in range(57, len(df_2024)):
    lt = sig['long_cond'].iloc[i] and trend_up.iloc[i]
    st = sig['short_cond'].iloc[i] and (not trend_up.iloc[i])
    if pos is None:
        if lt: pos, ep = 'L', lsl.iloc[i]
        elif st: pos, ep = 'S', ssl.iloc[i]
    elif pos == 'L':
        if st:
            pnl_pct = (ssl.iloc[i] - ep) / ep
            fee = equity * LEVERAGE * 0.0008
            pnl = equity * pnl_pct * LEVERAGE - fee
            equity += pnl; trades.append(pnl)
            peak = max(peak, equity); max_dd = max(max_dd, peak-equity)
            pos, ep = 'S', ssl.iloc[i]
    elif pos == 'S':
        if lt:
            pnl_pct = (ep - lsl.iloc[i]) / ep
            fee = equity * LEVERAGE * 0.0008
            pnl = equity * pnl_pct * LEVERAGE - fee
            equity += pnl; trades.append(pnl)
            peak = max(peak, equity); max_dd = max(max_dd, peak-equity)
            pos, ep = 'L', lsl.iloc[i]

n = len(trades); w = sum(1 for t in trades if t > 0)
ws = []; ls = []; curr = 0
for t in trades:
    if t > 0:
        if curr < 0: ls.append(curr); curr = 0
        curr += 1
    else:
        if curr > 0: ws.append(curr); curr = 0
        curr -= 1
if curr > 0: ws.append(curr)
if curr < 0: ls.append(curr)

monthly = {}
for i, t in enumerate(trades):
    m = df_2024.index[57+i].strftime('%Y-%m') if 57+i < len(df_2024) else '?'
    monthly[m] = monthly.get(m, 0) + t

print(f'\n=== 2024 FULL YEAR (starting USD {INITIAL}) ===')
print(f'Trades:        {n}')
print(f'Wins:          {w} ({w/n*100:.1f}%)')
print(f'Losses:        {n-w} ({(n-w)/n*100:.1f}%)')
print(f'Final:         USD {equity:,.0f}')
print(f'PnL:           USD {equity-INITIAL:+,.0f} ({(equity/INITIAL-1)*100:+.1f}%)')
print(f'Max DD:        {max_dd/peak*100:.1f}%')
print(f'Max Win Streak:  {max(ws) if ws else 0}')
print(f'Max Loss Streak: {abs(min(ls)) if ls else 0}')
print()
print('Monthly PnL:')
for m in sorted(monthly.keys()):
    print(f'  {m}: USD {monthly[m]:>+12,.0f}')
