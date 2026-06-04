"""Volty backtest for ETHUSDT last year"""
import ccxt, pandas as pd, numpy as np, json, time as _time
from datetime import datetime, timezone, timedelta

print('=== Volty Strategy Backtest ===')
print('Params: 15min, VLEN=5, MULT=0.75, 100pct, 3x, 5pct SL, $700 start')
print()

ex = ccxt.binance({'enableRateLimit': True, 'timeout': 30000})
print('Downloading ETHUSDT 15m klines (1 year)...')

all_klines = []
since = int(datetime(2025, 6, 3, tzinfo=timezone.utc).timestamp() * 1000)
end_time = int(datetime(2026, 6, 3, tzinfo=timezone.utc).timestamp() * 1000)

chunks = 0
while since < end_time:
    try:
        klines = ex.fetch_ohlcv('ETH/USDT:USDT', '15m', since=since, limit=1000)
        if not klines: break
        all_klines.extend(klines)
        since = klines[-1][0] + 1
        chunks += 1
        if chunks % 5 == 0:
            print('  Downloaded %d candles...' % len(all_klines))
    except Exception as e:
        print('  Retry... %s' % e)
        _time.sleep(1)

print('Total: %d candles' % len(all_klines))

df = pd.DataFrame(all_klines, columns=['ts','open','high','low','close','vol'])
df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
df.set_index('ts', inplace=True)

VLEN, VMULT = 5, 0.75
PCT, LEV, SL = 100, 3, 5
INIT = 700

tr = pd.concat([df['high']-df['low'], (df['high']-df['close'].shift(1)).abs(), (df['low']-df['close'].shift(1)).abs()], axis=1).max(axis=1)
atr = tr.rolling(VLEN).mean() * VMULT
ll = df['close'].shift(1) + atr.shift(1)
sl = df['close'].shift(1) - atr.shift(1)

bal = INIT; pos = None; ep = 0; cts = 0; uv = 0
trades = []; eq = [bal]

for i in range(VLEN+3, len(df)):
    hi, lo, cl = df['high'].iloc[i], df['low'].iloc[i], df['close'].iloc[i]
    tm = str(df.index[i])[:16]

    if pos == 'LONG':
        sp = ep * (1 - SL/100/LEV)
        if lo <= sp:
            pnl = (sp - ep)/ep * uv; bal += pnl
            trades.append({'t':tm,'a':'SL_L','s':'LONG','e':round(ep,2),'x':round(sp,2),'c':round(cts,4),'uv':round(uv,2),'pnl':round(pnl,2),'bal':round(bal,2)})
            eq.append(bal); pos = None; continue

    if pos == 'SHORT':
        sp = ep * (1 + SL/100/LEV)
        if hi >= sp:
            pnl = (ep - sp)/ep * uv; bal += pnl
            trades.append({'t':tm,'a':'SL_S','s':'SHORT','e':round(ep,2),'x':round(sp,2),'c':round(cts,4),'uv':round(uv,2),'pnl':round(pnl,2),'bal':round(bal,2)})
            eq.append(bal); pos = None; continue

    lt = not pd.isna(ll.iloc[i]) and hi >= ll.iloc[i]
    st = not pd.isna(sl.iloc[i]) and lo <= sl.iloc[i]

    if lt and pos != 'LONG':
        if pos == 'SHORT':
            xp = ll.iloc[i]; pl = (ep - xp)/ep * uv; bal += pl
            trades.append({'t':tm,'a':'FLIP_L','s':'SHORT','e':round(ep,2),'x':round(xp,2),'c':round(cts,4),'uv':round(uv,2),'pnl':round(pl,2),'bal':round(bal,2)})
            eq.append(bal)
        cap = bal * PCT/100; uv = cap * LEV; ep = ll.iloc[i]; cts = uv/ep; pos = 'LONG'
        trades.append({'t':tm,'a':'OPEN_L','s':'LONG','e':round(ep,2),'x':0,'c':round(cts,4),'uv':round(uv,2),'pnl':0,'bal':round(bal,2)})
    elif st and pos != 'SHORT':
        if pos == 'LONG':
            xp = sl.iloc[i]; pl = (xp - ep)/ep * uv; bal += pl
            trades.append({'t':tm,'a':'FLIP_S','s':'LONG','e':round(ep,2),'x':round(xp,2),'c':round(cts,4),'uv':round(uv,2),'pnl':round(pl,2),'bal':round(bal,2)})
            eq.append(bal)
        cap = bal * PCT/100; uv = cap * LEV; ep = sl.iloc[i]; cts = uv/ep; pos = 'SHORT'
        trades.append({'t':tm,'a':'OPEN_S','s':'SHORT','e':round(ep,2),'x':0,'c':round(cts,4),'uv':round(uv,2),'pnl':0,'bal':round(bal,2)})

if pos:
    lp = df['close'].iloc[-1]
    pl = (lp-ep)/ep*uv if pos=='LONG' else (ep-lp)/ep*uv
    bal += pl
    trades.append({'t':str(df.index[-1])[:16],'a':'FINAL','s':pos,'e':round(ep,2),'x':round(lp,2),'c':round(cts,4),'uv':round(uv,2),'pnl':round(pl,2),'bal':round(bal,2)})
    eq.append(bal)

tdf = pd.DataFrame(trades)

def metrics(sd, ed, label):
    pt = tdf[(tdf['t']>=sd)&(tdf['t']<=ed)]
    cl = [t for t in pt.to_dict('records') if t['pnl']!=0]
    if not cl: return None
    ws = [t for t in cl if t['pnl']>0]
    ls = [t for t in cl if t['pnl']<0]
    wr = len(ws)/len(cl)*100
    tp = sum(t['pnl'] for t in cl)
    ep_t = tdf[tdf['t']<sd]
    sb = ep_t.iloc[-1]['bal'] if len(ep_t)>0 else INIT
    eb = cl[-1]['bal']
    return {'p':label,'sb':sb,'eb':eb,'pnl':tp,'ret':(eb-sb)/sb*100,'trades':len(pt),'closed':len(cl),'wins':len(ws),'wr':wr,'aw':sum(t['pnl'] for t in ws)/len(ws) if ws else 0,'al':sum(t['pnl'] for t in ls)/len(ls) if ls else 0}

now = df.index[-1].strftime('%Y-%m-%d')
results = []
for d, l in [(7,'1 Week'), (30,'1 Month'), (90,'3 Months'), (365,'1 Year')]:
    sd = (pd.Timestamp(now)-timedelta(days=d)).strftime('%Y-%m-%d')
    r = metrics(sd, now, l)
    if r: results.append(r)

print()
print('='*65)
print('%-10s %8s %8s %10s %8s %6s %7s %8s %8s' % ('Period','Start','End','PnL','Return','Trades','WinRt','AvgWin','AvgLoss'))
print('='*65)
for r in results:
    print('%-10s %6.0fU %6.0fU %+8.0fU %+7.1f%% %4d笔 %6.1f%% %+6.0fU %+6.0fU' % (r['p'], r['sb'], r['eb'], r['pnl'], r['ret'], r['trades'], r['wr'], r['aw'], r['al']))
print('='*65)
print()
for r in results:
    print('%s: %d closed, %dW/%dL, avgWin=%.0fU, avgLoss=%.0fU' % (r['p'], r['closed'], r['wins'], r['closed']-r['wins'], r['aw'], r['al']))
print()
print('Final: $%.0f -> $%.0f (%.1f%%)' % (INIT, bal, (bal-INIT)/INIT*100))
