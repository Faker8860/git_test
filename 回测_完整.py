"""Exact Volty strategy backtest — matches strategy_bot.py execute_volty_signal()"""
import ccxt, pandas as pd, numpy as np, time as _time
from datetime import datetime, timezone, timedelta

print('=== Volty Strategy Backtest (Exact strategy_bot.py logic) ===')
print('ETHUSDT 15min | VLEN=5 MULT=0.75 | 100% 3x | 5% SL | $700 start')
print('Period: 2025-01-01 to 2026-06-03')
print()

ex = ccxt.binance({'enableRateLimit': True, 'timeout': 30000})

# Download from Jan 2025
since = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
end = int(datetime(2026, 6, 4, tzinfo=timezone.utc).timestamp() * 1000)

all_klines = []
while since < end:
    try:
        klines = ex.fetch_ohlcv('ETH/USDT:USDT', '15m', since=since, limit=1000)
        if not klines: break
        all_klines.extend(klines)
        since = klines[-1][0] + 1
        if len(all_klines) % 5000 == 0:
            print('  Downloaded %d candles...' % len(all_klines))
    except Exception as e:
        print('  Retry: %s' % e)
        _time.sleep(1)

print('Total: %d candles' % len(all_klines))

df = pd.DataFrame(all_klines, columns=['ts','open','high','low','close','vol'])
df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
df.set_index('ts', inplace=True)

# === EXACT strategy_bot.py Volty logic ===
VLEN = 5
VMULT = 0.75
PCT = 100
LEV = 3
SL = 5
INIT = 700

# ATR calculation (matches strategy_bot.py exactly)
tr = pd.concat([
    df['high'] - df['low'],
    (df['high'] - df['close'].shift(1)).abs(),
    (df['low'] - df['close'].shift(1)).abs(),
], axis=1).max(axis=1)
atr = tr.rolling(window=VLEN).mean() * VMULT

bal = INIT; pos = None; ep = 0; cts = 0; uv = 0
trades = []

start_idx = VLEN + 3
for i in range(start_idx, len(df)):
    hi = df['high'].iloc[i]
    lo = df['low'].iloc[i]
    cl = df['close'].iloc[i]
    tm = df.index[i]

    # === Stop Loss (matches place_stop_loss + exchange trigger) ===
    if pos == 'LONG':
        sp = ep * (1 - SL/100/LEV)
        if lo <= sp:
            pnl = (sp - ep) / ep * uv
            bal += pnl
            trades.append({'t':tm,'a':'STOP_L','s':'LONG','e':round(ep,2),'x':round(sp,2),'c':round(cts,4),'v':round(uv,2),'pnl':round(pnl,2),'bal':round(bal,2)})
            pos = None; continue

    if pos == 'SHORT':
        sp = ep * (1 + SL/100/LEV)
        if hi >= sp:
            pnl = (ep - sp) / ep * uv
            bal += pnl
            trades.append({'t':tm,'a':'STOP_S','s':'SHORT','e':round(ep,2),'x':round(sp,2),'c':round(cts,4),'v':round(uv,2),'pnl':round(pnl,2),'bal':round(bal,2)})
            pos = None; continue

    # === Volty Signal (prev completed bar) ===
    prev_atr = atr.iloc[i-1]
    if pd.isna(prev_atr) or prev_atr <= 0:
        continue

    prev_close = df['close'].iloc[i-1]
    long_level = prev_close + prev_atr
    short_level = prev_close - prev_atr

    long_trigger = hi >= long_level
    short_trigger = lo <= short_level

    # === Execute signals (matches execute_order with flip logic) ===
    if long_trigger and pos != 'LONG':
        if pos == 'SHORT':
            xp = long_level
            pl = (ep - xp) / ep * uv
            bal += pl
            trades.append({'t':tm,'a':'FLIP_L','s':'SHORT','e':round(ep,2),'x':round(xp,2),'c':round(cts,4),'v':round(uv,2),'pnl':round(pl,2),'bal':round(bal,2)})
            pos = None
        if bal > 0:
            cap = bal * PCT / 100
            uv = cap * LEV
            ep = long_level
            cts = uv / ep
            pos = 'LONG'
            trades.append({'t':tm,'a':'OPEN_L','s':'LONG','e':round(ep,2),'x':0,'c':round(cts,4),'v':round(uv,2),'pnl':0,'bal':round(bal,2)})

    elif short_trigger and pos != 'SHORT':
        if pos == 'LONG':
            xp = short_level
            pl = (xp - ep) / ep * uv
            bal += pl
            trades.append({'t':tm,'a':'FLIP_S','s':'LONG','e':round(ep,2),'x':round(xp,2),'c':round(cts,4),'v':round(uv,2),'pnl':round(pl,2),'bal':round(bal,2)})
            pos = None
        if bal > 0:
            cap = bal * PCT / 100
            uv = cap * LEV
            ep = short_level
            cts = uv / ep
            pos = 'SHORT'
            trades.append({'t':tm,'a':'OPEN_S','s':'SHORT','e':round(ep,2),'x':0,'c':round(cts,4),'v':round(uv,2),'pnl':0,'bal':round(bal,2)})

# Close final position
if pos and bal > 0:
    lp = df['close'].iloc[-1]
    pl = (lp-ep)/ep*uv if pos=='LONG' else (ep-lp)/ep*uv
    bal += pl
    trades.append({'t':df.index[-1],'a':'CLOSE','s':pos,'e':round(ep,2),'x':round(lp,2),'c':round(cts,4),'v':round(uv,2),'pnl':round(pl,2),'bal':round(bal,2)})

tdf = pd.DataFrame(trades)
closes = [t for t in trades if t['pnl'] != 0]
total_trades = len(trades)
total_closed = len(closes)

# Results by period
def calc(sd, ed, label):
    pt = [t for t in trades if str(t['t'])[:10] >= sd and str(t['t'])[:10] <= ed]
    cl = [t for t in pt if t['pnl'] != 0]
    if not cl: return None
    ws = [t for t in cl if t['pnl'] > 0]
    ls = [t for t in cl if t['pnl'] < 0]
    wr = len(ws)/len(cl)*100 if cl else 0
    tp = sum(t['pnl'] for t in cl)
    # Find balance at period start
    before = [t for t in trades if str(t['t'])[:10] < sd]
    sb = before[-1]['bal'] if before else INIT
    eb = cl[-1]['bal'] if cl else sb
    aw = sum(t['pnl'] for t in ws)/len(ws) if ws else 0
    al = sum(t['pnl'] for t in ls)/len(ls) if ls else 0
    return {'p':label,'sb':sb,'eb':eb,'pnl':tp,'ret':(eb-sb)/sb*100,'trades':len(pt),'closed':len(cl),'w':len(ws),'l':len(ls),'wr':wr,'aw':aw,'al':al}

results = []
for sd, ed, label in [
    ('2025-01-01','2025-01-07','Week 1'),
    ('2025-01-01','2025-01-31','Month 1'),
    ('2025-01-01','2025-03-31','Q1'),
    ('2025-01-01','2025-06-02','6 Months'),
    ('2025-01-01','2025-12-31','1 Year'),
]:
    r = calc(sd, ed, label)
    if r: results.append(r)

print()
print('='*75)
print('%-12s %8s %8s %10s %8s %6s %6s %7s %8s %8s' % ('Period','Start','End','PnL','Return','Trades','Closed','WinRt','AvgWin','AvgLoss'))
print('='*75)
for r in results:
    print('%-12s %6.0fU %6.0fU %+8.0fU %+7.1f%% %5d %5d %6.1f%% %+6.0fU %+6.0fU' % (r['p'],r['sb'],r['eb'],r['pnl'],r['ret'],r['trades'],r['closed'],r['wr'],r['aw'],r['al']))
print('='*75)
print()
for r in results:
    print('%s: %d trades, %d closed (%dW/%dL), avgWin=%.1fU, avgLoss=%.1fU, return=%.1f%%' % (r['p'],r['trades'],r['closed'],r['w'],r['l'],r['aw'],r['al'],r['ret']))

print()
print('='*50)
print('FINAL: $%.0f -> $%.0f (%.1f%% over %d months)' % (INIT, bal, (bal-INIT)/INIT*100, 17))
print('Total records: %d trades, %d closures' % (total_trades, total_closed))
print('='*50)
