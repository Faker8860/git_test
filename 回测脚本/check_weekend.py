"""Verify weekend performance + TP=1.5% by year"""
import ccxt, pandas as pd, numpy as np, time as _time
from datetime import datetime, timezone

VLEN, VMULT, PCT, LEV, SL_PCT, TP_PCT = 5, 0.75, 60, 3, 5, 1.5
INIT = 700.0

print("Downloading 2024-2025...")
ex = ccxt.binance({"enableRateLimit": True, "timeout": 30000})
since = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
end_ts = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
all_klines = []
while since < end_ts:
    try:
        klines = ex.fetch_ohlcv("ETH/USDT:USDT", "15m", since=since, limit=1000)
        if not klines: break
        all_klines.extend(klines); since = klines[-1][0] + 1
    except: _time.sleep(1)

df = pd.DataFrame(all_klines, columns=["ts","open","high","low","close","volume"])
df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True); df.set_index("ts", inplace=True)
df = df[(df.index >= "2024-01-01") & (df.index < "2026-01-01")]

tr = pd.concat([df["high"]-df["low"], (df["high"]-df["close"].shift(1)).abs(), (df["low"]-df["close"].shift(1)).abs()], axis=1).max(axis=1)
atr_sig = tr.rolling(VLEN).mean() * VMULT
ema50 = df["close"].ewm(span=50, adjust=False).mean()
START = max(VLEN+3, 53)

trades = []
bal = INIT; pos = None; ep = uv = 0.0
for i in range(START, len(df)):
    hi, lo = df["high"].iloc[i], df["low"].iloc[i]; tm = df.index[i]
    if pos == "LONG":
        sp = ep*(1-SL_PCT/100); tp = ep*(1+TP_PCT/100)
        if lo <= sp: bal += (sp-ep)/ep*uv; trades.append({"pnl":(sp-ep)/ep*uv,"dow":entry_dow,"year":tm.year,"exit":"SL"}); pos=None; continue
        if hi >= tp: bal += (tp-ep)/ep*uv; trades.append({"pnl":(tp-ep)/ep*uv,"dow":entry_dow,"year":tm.year,"exit":"TP"}); pos=None; continue
    if pos == "SHORT":
        sp = ep*(1+SL_PCT/100); tp = ep*(1-TP_PCT/100)
        if hi >= sp: bal += (ep-sp)/ep*uv; trades.append({"pnl":(ep-sp)/ep*uv,"dow":entry_dow,"year":tm.year,"exit":"SL"}); pos=None; continue
        if lo <= tp: bal += (ep-tp)/ep*uv; trades.append({"pnl":(ep-tp)/ep*uv,"dow":entry_dow,"year":tm.year,"exit":"TP"}); pos=None; continue
    pa = atr_sig.iloc[i-1]
    if pd.isna(pa) or pa<=0: continue
    pc = df["close"].iloc[i-1]; ll, sl = pc+pa, pc-pa
    tb = df["close"].iloc[i-1] > ema50.iloc[i-1]; tbe = df["close"].iloc[i-1] < ema50.iloc[i-1]
    lt = hi>=ll and tb and pos!="LONG"; st = lo<=sl and tbe and pos!="SHORT"
    if lt and st:
        if abs(df["open"].iloc[i]-ll) > abs(df["open"].iloc[i]-sl): lt=False
        else: st=False
    if lt:
        if pos=="SHORT": bal += (ep-ll)/ep*uv; trades.append({"pnl":(ep-ll)/ep*uv,"dow":entry_dow,"year":tm.year,"exit":"FLIP"}); pos=None
        if bal>10: cap=bal*PCT/100; uv=cap*LEV; ep=ll; pos="LONG"; entry_dow=tm.dayofweek
    elif st:
        if pos=="LONG": bal += (sl-ep)/ep*uv; trades.append({"pnl":(sl-ep)/ep*uv,"dow":entry_dow,"year":tm.year,"exit":"FLIP"}); pos=None
        if bal>10: cap=bal*PCT/100; uv=cap*LEV; ep=sl; pos="SHORT"; entry_dow=tm.dayofweek

tdf = pd.DataFrame(trades)
closed = tdf[tdf["pnl"]!=0]
dow_names = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]

print("\n=== Weekend Analysis by Year (with TP=1.5%) ===")
for year in [2024, 2025]:
    print("\n--- {} ---".format(year))
    for dow in range(7):
        dt = closed[(closed["dow"]==dow) & (closed["year"]==year)]
        if dt.empty: continue
        pnl = dt["pnl"].sum()
        wins = len(dt[dt["pnl"]>0])
        total = len(dt)
        avg = dt["pnl"].mean()
        print("  {}: {:+.0f}U  WR={:.0f}%  n={}  avgPnL={:+.0f}U".format(
            dow_names[dow], pnl, wins/total*100, total, avg))

print("\n=== Per-trade avg PnL (both years) ===")
for dow in range(7):
    dt = closed[closed["dow"]==dow]
    if dt.empty: continue
    print("  {}: avg={:+.0f}U  median={:+.0f}U  std={:.0f}U  n={}".format(
        dow_names[dow], dt["pnl"].mean(), dt["pnl"].median(), dt["pnl"].std(), len(dt)))

print("\n=== Saturday vs Non-Saturday T-test ===")
sat = closed[closed["dow"]==5]["pnl"]
non_sat = closed[closed["dow"]!=5]["pnl"]
from scipy import stats as sp_stats
t_stat, p_val = sp_stats.ttest_ind(sat, non_sat)
print("  Sat avg: {:+.2f}U, Non-Sat avg: {:+.2f}U".format(sat.mean(), non_sat.mean()))
print("  T-statistic: {:.2f}, P-value: {:.4f}".format(t_stat, p_val))
print("  Statistically significant? {}".format("YES (p<0.05)" if p_val < 0.05 else "NO (p>=0.05)"))

print("\n=== What if we skip Saturday? ===")
bal2 = INIT; pos2 = None; ep2 = uv2 = 0.0
for i in range(START, len(df)):
    hi, lo = df["high"].iloc[i], df["low"].iloc[i]; tm = df.index[i]
    is_sat = tm.dayofweek == 5
    if pos2 == "LONG":
        sp = ep2*(1-SL_PCT/100); tp = ep2*(1+TP_PCT/100)
        if lo <= sp: bal2 += (sp-ep2)/ep2*uv2; pos2=None; continue
        if hi >= tp: bal2 += (tp-ep2)/ep2*uv2; pos2=None; continue
    if pos2 == "SHORT":
        sp = ep2*(1+SL_PCT/100); tp = ep2*(1-TP_PCT/100)
        if hi >= sp: bal2 += (ep2-sp)/ep2*uv2; pos2=None; continue
        if lo <= tp: bal2 += (ep2-tp)/ep2*uv2; pos2=None; continue
    if is_sat: continue
    pa = atr_sig.iloc[i-1]
    if pd.isna(pa) or pa<=0: continue
    pc = df["close"].iloc[i-1]; ll, sl = pc+pa, pc-pa
    tb = df["close"].iloc[i-1] > ema50.iloc[i-1]; tbe = df["close"].iloc[i-1] < ema50.iloc[i-1]
    lt = hi>=ll and tb and pos2!="LONG"; st = lo<=sl and tbe and pos2!="SHORT"
    if lt and st:
        if abs(df["open"].iloc[i]-ll) > abs(df["open"].iloc[i]-sl): lt=False
        else: st=False
    if lt:
        if pos2=="SHORT": bal2 += (ep2-ll)/ep2*uv2; pos2=None
        if bal2>10: cap=bal2*PCT/100; uv2=cap*LEV; ep2=ll; pos2="LONG"
    elif st:
        if pos2=="LONG": bal2 += (sl-ep2)/ep2*uv2; pos2=None
        if bal2>10: cap=bal2*PCT/100; uv2=cap*LEV; ep2=sl; pos2="SHORT"
if pos2: lp=df["close"].iloc[-1]; bal2 += (lp-ep2)/ep2*uv2 if pos2=="LONG" else (ep2-lp)/ep2*uv2

ret2 = (bal2-INIT)/INIT*100
print("  TP=1.5% + No Sat: ${:,.0f} ({:+.1f}%)".format(bal2, ret2))
print("  TP=1.5% only:    ${:,.0f} ({:+.1f}%)".format(bal, (bal-INIT)/INIT*100))
