"""Fast regime-switching: RSI-based regimes, no slow ADX"""
import pandas as pd, numpy as np, time as _time
t0=_time.time()

print("Loading...")
df=pd.read_csv("/opt/trading/ETHUSDT_15m_full.csv",parse_dates=["ts"],index_col="ts")
n=len(df); INIT=700; TP=7; SL=5; PCT=60; LEV=3

H,L,C,O=df["high"].values,df["low"].values,df["close"].values,df["open"].values
tr=pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift(1)).abs(),(df["low"]-df["close"].shift(1)).abs()],axis=1).max(axis=1)
atr5_v=tr.rolling(5).mean()*0.75
ema50_v=df["close"].ewm(span=50,adjust=False).mean()
atr14_v=tr.rolling(14).mean(); mid_atr=atr14_v.median()

# RSI
delta=df["close"].diff(); gain=delta.clip(lower=0); loss=(-delta).clip(lower=0)
rsi14=(100-(100/(1+gain.rolling(14).mean()/loss.rolling(14).mean()))).values

# BB
bb_mid=df["close"].rolling(20).mean().values; bb_std=df["close"].rolling(20).std().values
bb_upper=bb_mid+2*bb_std; bb_lower=bb_mid-2*bb_std

# Volty signals
vl=np.where(np.arange(n)>=200,H>=np.roll(C+atr5_v.values,1),False)&(np.roll(C,1)>np.roll(ema50_v.values,1))
vs=np.where(np.arange(n)>=200,L<=np.roll(C-atr5_v.values,1),False)&(np.roll(C,1)<np.roll(ema50_v.values,1))

# Regimes (fast, no ADX)
# Trending: RSI strongly directional. Ranging: RSI near 50.
is_trending = (rsi14>55)|(rsi14<45)
is_ranging = ~is_trending
is_bull = rsi14>50
is_bear = rsi14<50

# BB signals for ranging
bb_long = (C<bb_lower)&(rsi14<45)  # oversold bounce
bb_short = (C>bb_upper)&(rsi14>55) # overbought reversal

def dyn_cap(bal,i):
    cap=bal*PCT/100
    if not pd.isna(atr14_v.iloc[i]): cap*=min(1.5,max(0.5,mid_atr/max(atr14_v.iloc[i],0.01)))
    return min(cap,bal*0.98)

def run(name,volty_filt=None,use_bb_ranging=False,use_rsi=False,force_bb=False):
    bal=INIT; pos=None; ep=uv=0.0; eq=[INIT]; trades=0; tp_c=0; sl_c=0; bb_trades=0; volty_trades=0
    for i in range(200,n):
        hi,lo,cl=H[i],L[i],C[i]
        if pos==1:
            if lo<=ep*(1-SL/100): bal+=(ep*(1-SL/100)-ep)/ep*uv; pos=0; sl_c+=1; eq.append(bal); continue
            if hi>=ep*(1+TP/100): bal+=(ep*(1+TP/100)-ep)/ep*uv; pos=0; tp_c+=1; eq.append(bal); continue
        if pos==-1:
            if hi>=ep*(1+SL/100): bal+=(ep-ep*(1+SL/100))/ep*uv; pos=0; sl_c+=1; eq.append(bal); continue
            if lo<=ep*(1-TP/100): bal+=(ep-ep*(1-TP/100))/ep*uv; pos=0; tp_c+=1; eq.append(bal); continue

        in_range=is_ranging[i]; in_trend=is_trending[i]

        # Strategy selection
        if force_bb or (use_bb_ranging and in_range):
            # Use BB mean reversion
            lt=bb_long[i] and pos!=1; st=bb_short[i] and pos!=-1
            if lt and st:
                if abs(O[i]-bb_upper[i])>abs(O[i]-bb_lower[i]): lt=False
                else: st=False
            if lt and pos!=1:
                if pos==-1: bal+=(ep-bb_lower[i])/ep*uv; pos=0; eq.append(bal)
                if bal>10: cap=dyn_cap(bal,i); uv=cap*LEV; ep=bb_lower[i]; pos=1; trades+=1; bb_trades+=1; eq.append(bal)
            elif st and pos!=-1:
                if pos==1: bal+=(bb_upper[i]-ep)/ep*uv; pos=0; eq.append(bal)
                if bal>10: cap=dyn_cap(bal,i); uv=cap*LEV; ep=bb_upper[i]; pos=-1; trades+=1; bb_trades+=1; eq.append(bal)
        elif in_trend or not use_bb_ranging:
            # Use Volty
            lt=vl[i] and pos!=1; st=vs[i] and pos!=-1
            if use_rsi: lt=lt and is_bull[i]; st=st and is_bear[i]
            if volty_filt is not None: lt=lt and volty_filt[i]; st=st and volty_filt[i]
            if lt and st:
                if abs(O[i]-(C[i-1]+atr5_v.iloc[i-1]))>abs(O[i]-(C[i-1]-atr5_v.iloc[i-1])): lt=False
                else: st=False
            if lt and pos!=1:
                if pos==-1: bal+=(ep-(C[i-1]+atr5_v.iloc[i-1]))/ep*uv; pos=0; eq.append(bal)
                if bal>10: cap=dyn_cap(bal,i); uv=cap*LEV; ep=C[i-1]+atr5_v.iloc[i-1]; pos=1; trades+=1; volty_trades+=1; eq.append(bal)
            elif st and pos!=-1:
                if pos==1: bal+=((C[i-1]-atr5_v.iloc[i-1])-ep)/ep*uv; pos=0; eq.append(bal)
                if bal>10: cap=dyn_cap(bal,i); uv=cap*LEV; ep=C[i-1]-atr5_v.iloc[i-1]; pos=-1; trades+=1; volty_trades+=1; eq.append(bal)

    if pos: bal+=((C[-1]-ep)/ep*uv if pos==1 else (ep-C[-1])/ep*uv); eq.append(bal)
    eqs=np.array(eq); peak=np.maximum.accumulate(eqs); dd=np.min((eqs-peak)/peak*100)
    return {"name":name,"final":round(bal,2),"ret":round((bal-INIT)/INIT*100,2),"dd":round(dd,2),
            "trades":trades,"bb":bb_trades,"volty":volty_trades,"tp":tp_c,"sl":sl_c}

results=[]

print("A: Baseline (Volty+Dyn+TP7)...")
results.append(run("A: Baseline"))

print("B: +RSI direction...")
results.append(run("B: +RSI direction",use_rsi=True))

print("C: Regime switch (trend=Volty, range=BB)...")
results.append(run("C: Regime switch",use_bb_ranging=True))

print("D: Regime + RSI combo...")
results.append(run("D: Regime+RSI",use_bb_ranging=True,use_rsi=True))

print("E: Pure BB always...")
results.append(run("E: Pure BB always",force_bb=True))

print("F: Volty only in trending, skip ranging...")
results.append(run("F: Volty trend-only",use_bb_ranging=True)) # no BB, just skip ranging

results.sort(key=lambda r:r["ret"],reverse=True)
print("\n"+"="*95)
print("  REGIME-SWITCHING RESULTS ({:.0f}s, 6.5yr)".format(_time.time()-t0))
print("="*95)
print("{:<4s} {:<30s} {:>10s} {:>8s} {:>7s} {:>6s} {:>5s}".format("Rk","Strategy","Final","Return","MaxDD","Trades","BB/V"))
print("-"*95)
for i,r in enumerate(results):
    m="BEST" if i==0 else ""
    print("{:<4d} {:<30s} ${:>8,.0f} {:>+7.1f}% {:>+6.1f}% {:>5d} {:>3d}/{} {}".format(
        i+1,r["name"],r["final"],r["ret"],r["dd"],r["trades"],r["bb"],r["volty"],m))
