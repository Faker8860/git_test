"""TP sweep on full 6.5yr data"""
import pandas as pd, numpy as np

print("Loading 228K candles...")
df=pd.read_csv("/opt/trading/ETHUSDT_15m_full.csv",parse_dates=["ts"],index_col="ts")
n=len(df)

INIT=700; PCT=60; LEV=3; SL=5
tr=pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift(1)).abs(),(df["low"]-df["close"].shift(1)).abs()],axis=1).max(axis=1)
atr5=tr.rolling(5).mean()*0.75; ema50=df["close"].ewm(span=50,adjust=False).mean()

# Pre-compute signals (same for all TP levels)
signals_long=[]; signals_short=[]
for i in range(n):
    if i<200:
        signals_long.append(False); signals_short.append(False); continue
    pc=df["close"].iloc[i-1]; pa=atr5.iloc[i-1]; pe=ema50.iloc[i-1]
    if pd.isna(pa) or pa<=0:
        signals_long.append(False); signals_short.append(False); continue
    sl_l=df["high"].iloc[i]>=pc+pa and pc>pe
    sl_s=df["low"].iloc[i]<=pc-pa and pc<pe
    signals_long.append(sl_l); signals_short.append(sl_s)

def run_tp(tp_pct):
    bal=INIT; pos=None; ep=uv=0.0; trades=0; tp_hits=0; sl_hits=0
    eq=[INIT]
    for i in range(200,n):
        hi,lo,cl=df["high"].iloc[i],df["low"].iloc[i],df["close"].iloc[i]
        if pos=="LONG":
            if lo<=ep*(1-SL/100): bal+=(ep*(1-SL/100)-ep)/ep*uv; pos=None; sl_hits+=1; eq.append(bal); continue
            if tp_pct>0 and hi>=ep*(1+tp_pct/100): bal+=(ep*(1+tp_pct/100)-ep)/ep*uv; pos=None; tp_hits+=1; eq.append(bal); continue
        if pos=="SHORT":
            if hi>=ep*(1+SL/100): bal+=(ep-ep*(1+SL/100))/ep*uv; pos=None; sl_hits+=1; eq.append(bal); continue
            if tp_pct>0 and lo<=ep*(1-tp_pct/100): bal+=(ep-ep*(1-tp_pct/100))/ep*uv; pos=None; tp_hits+=1; eq.append(bal); continue
        lt=signals_long[i] and pos!="LONG"; st=signals_short[i] and pos!="SHORT"
        if lt and st:
            if abs(df["open"].iloc[i]-(df["close"].iloc[i-1]+atr5.iloc[i-1]))>abs(df["open"].iloc[i]-(df["close"].iloc[i-1]-atr5.iloc[i-1])): lt=False
            else: st=False
        if lt:
            if pos=="SHORT": xp=df["close"].iloc[i-1]+atr5.iloc[i-1]; bal+=(ep-xp)/ep*uv; pos=None; eq.append(bal)
            if bal>10: cap=bal*PCT/100; uv=cap*LEV; ep=df["close"].iloc[i-1]+atr5.iloc[i-1]; pos="LONG"; trades+=1; eq.append(bal)
        elif st:
            if pos=="LONG": xp=df["close"].iloc[i-1]-atr5.iloc[i-1]; bal+=(xp-ep)/ep*uv; pos=None; eq.append(bal)
            if bal>10: cap=bal*PCT/100; uv=cap*LEV; ep=df["close"].iloc[i-1]-atr5.iloc[i-1]; pos="SHORT"; trades+=1; eq.append(bal)
    if pos: lp=df["close"].iloc[-1]; bal+=((lp-ep)/ep*uv if pos=="LONG" else (ep-lp)/ep*uv); eq.append(bal)
    eqs=np.array(eq); peak=np.maximum.accumulate(eqs); dd=np.min((eqs-peak)/peak*100)
    ret=(bal-INIT)/INIT*100
    return {"tp":tp_pct,"final":round(bal,2),"ret":round(ret,2),"dd":round(dd,2),
            "trades":trades,"tp_hits":tp_hits,"sl_hits":sl_hits,
            "pain":round(ret/abs(dd),2) if dd!=0 else 0}

print("Sweeping TP levels...")
results=[]
for tp in [0,0.5,1,1.5,2,2.5,3,4,5,7,10]:
    r=run_tp(tp); results.append(r)
    print("  TP={}%: ${:,.0f} ({:+.1f}%) DD:{:+.1f}% TP:{} SL:{}".format(tp,r["final"],r["ret"],r["dd"],r["tp_hits"],r["sl_hits"]))

results.sort(key=lambda r:r["ret"],reverse=True)
print("\n"+"="*70)
print("  TP SWEEP RESULTS (6.5 years, Volty EMA50)")
print("="*70)
print("{:<8s} {:>10s} {:>8s} {:>8s} {:>8s} {:>6s}".format("TP","Final","Return","MaxDD","Pain","Trades"))
print("-"*70)
for r in results:
    m="BEST" if r==results[0] else ""
    print("{:<8s} ${:>8,.0f} {:>+7.1f}% {:>+7.1f}% {:>7.2f} {:>5d} {}".format(
        "No TP" if r["tp"]==0 else str(r["tp"])+"%",r["final"],r["ret"],r["dd"],r["pain"],r["trades"],m))
