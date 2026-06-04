"""Regime-switching: different strategies for different markets"""
import pandas as pd, numpy as np, time as _time

t0=_time.time()
print("Loading data + computing indicators...")
df=pd.read_csv("/opt/trading/ETHUSDT_15m_full.csv",parse_dates=["ts"],index_col="ts")
n=len(df); INIT=700; TP=7; SL=5; PCT=60; LEV=3

H,L,C,O=df["high"].values,df["low"].values,df["close"].values,df["open"].values
tr=pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift(1)).abs(),(df["low"]-df["close"].shift(1)).abs()],axis=1).max(axis=1)
atr5_v=tr.rolling(5).mean()*0.75
ema50_v=df["close"].ewm(span=50,adjust=False).mean()
atr14_v=tr.rolling(14).mean()
mid_atr=atr14_v.median()

# RSI
delta=df["close"].diff(); gain=delta.clip(lower=0); loss=(-delta).clip(lower=0)
rsi14=(100-(100/(1+gain.rolling(14).mean()/loss.rolling(14).mean()))).values

# ADX for regime detection
def calc_adx(p=14):
    tr_adx=tr.values
    pdm=np.zeros(n); mdm=np.zeros(n)
    for i in range(1,n):
        up=H[i]-H[i-1]; dn=L[i-1]-L[i]
        pdm[i]=up if (up>dn and up>0) else 0
        mdm[i]=dn if (dn>up and dn>0) else 0
    atr_adx=pd.Series(tr_adx).ewm(alpha=1/p,adjust=False).mean().values
    pdi=100*pd.Series(pdm).ewm(alpha=1/p,adjust=False).mean().values/atr_adx
    mdi=100*pd.Series(mdm).ewm(alpha=1/p,adjust=False).mean().values/atr_adx
    dx=100*np.abs(pdi-mdi)/(pdi+mdi+0.0001)
    return pd.Series(dx).ewm(alpha=1/p,adjust=False).mean().values
adx14=calc_adx(14)

# Volty signals
vl=np.where(np.arange(n)>=200,H>=np.roll(C+atr5_v.values,1),False)&(np.roll(C,1)>np.roll(ema50_v.values,1))
vs=np.where(np.arange(n)>=200,L<=np.roll(C-atr5_v.values,1),False)&(np.roll(C,1)<np.roll(ema50_v.values,1))

# BB signals (mean reversion for ranging markets)
bb_mid=df["close"].rolling(20).mean().values; bb_std=df["close"].rolling(20).std().values
bb_upper=bb_mid+2*bb_std; bb_lower=bb_mid-2*bb_std
bb_long=(C<bb_lower)&(rsi14<40); bb_short=(C>bb_upper)&(rsi14>60)  # Mean reversion at extremes

def dyn_cap(bal,i):
    cap=bal*PCT/100
    if not pd.isna(atr14_v.iloc[i]): cap*=min(1.5,max(0.5,mid_atr/max(atr14_v.iloc[i],0.01)))
    return min(cap,bal*0.98)

def backtest(name, use_rsi=False, use_regime=False, use_bb=False):
    bal=INIT; pos=None; ep=uv=0.0; eq=[INIT]; trades=0; tps=0; sls=0; regime_switches=0
    for i in range(200,n):
        hi,lo,cl=H[i],L[i],C[i]
        # SL
        if pos==1:
            if lo<=ep*(1-SL/100): bal+=(ep*(1-SL/100)-ep)/ep*uv; pos=0; sls+=1; eq.append(bal); continue
            if hi>=ep*(1+TP/100): bal+=(ep*(1+TP/100)-ep)/ep*uv; pos=0; tps+=1; eq.append(bal); continue
        if pos==-1:
            if hi>=ep*(1+SL/100): bal+=(ep-ep*(1+SL/100))/ep*uv; pos=0; sls+=1; eq.append(bal); continue
            if lo<=ep*(1-TP/100): bal+=(ep-ep*(1-TP/100))/ep*uv; pos=0; tps+=1; eq.append(bal); continue

        # Determine regime
        is_trending=adx14[i]>20 if i>=200 else True
        is_ranging=adx14[i]<=20 if i>=200 else False

        lt=False; st=False
        if use_regime and is_ranging and use_bb:
            # Ranging: use BB mean reversion
            lt=bb_long[i] and pos!=1; st=bb_short[i] and pos!=-1
        else:
            # Trending/default: use Volty
            lt=vl[i] and pos!=1; st=vs[i] and pos!=-1
            # RSI confirmation
            if use_rsi:
                lt=lt and rsi14[i]>50
                st=st and rsi14[i]<50

        if lt and st:
            if abs(O[i]-(C[i-1]+atr5_v.iloc[i-1]))>abs(O[i]-(C[i-1]-atr5_v.iloc[i-1])): lt=False
            else: st=False

        if lt:
            if pos==-1: bal+=(ep-(C[i-1]+atr5_v.iloc[i-1]))/ep*uv; pos=0; eq.append(bal)
            if bal>10:
                cap=dyn_cap(bal,i); uv=cap*LEV; ep=C[i-1]+atr5_v.iloc[i-1]; pos=1; trades+=1; eq.append(bal)
                if is_ranging: regime_switches+=1
        elif st:
            if pos==1: bal+=((C[i-1]-atr5_v.iloc[i-1])-ep)/ep*uv; pos=0; eq.append(bal)
            if bal>10:
                cap=dyn_cap(bal,i); uv=cap*LEV; ep=C[i-1]-atr5_v.iloc[i-1]; pos=-1; trades+=1; eq.append(bal)
                if is_ranging: regime_switches+=1
    if pos: bal+=((C[-1]-ep)/ep*uv if pos==1 else (ep-C[-1])/ep*uv); eq.append(bal)
    eqs=np.array(eq); peak=np.maximum.accumulate(eqs); dd=np.min((eqs-peak)/peak*100)
    return {"name":name,"final":round(bal,2),"ret":round((bal-INIT)/INIT*100,2),"dd":round(dd,2),"trades":trades,"tp":tps,"sl":sls,"rsw":regime_switches}

results=[]

print("1. Volty baseline (TP7%+Dyn)...")
results.append(backtest("A: Volty baseline"))

print("2. Volty+RSI confirmation...")
results.append(backtest("B: Volty+RSI confirm",use_rsi=True))

print("3. Regime: ADX>20=Volty, ADX<20=BB mean-revert...")
results.append(backtest("C: Regime switch",use_regime=True,use_bb=True))

print("4. Regime+RSI combo...")
results.append(backtest("D: Regime+RSI",use_rsi=True,use_regime=True,use_bb=True))

print("5. Pure BB mean revert (always)...")
results.append(backtest("E: Pure BB mean-revert",use_regime=True,use_bb=True))  # force always BB

# Also test different ADX thresholds
print("6. Regime ADX>25 Volty, <25 BB...")
results.append(backtest("F: Regime ADX25",use_regime=True,use_bb=True))  # same function, but need custom ADX logic

# Force always BB
def backtest_bb(name):
    bal=INIT; pos=None; ep=uv=0.0; eq=[INIT]; trades=0
    for i in range(200,n):
        hi,lo,cl=H[i],L[i],C[i]
        if pos==1:
            if lo<=ep*(1-SL/100): bal+=(ep*(1-SL/100)-ep)/ep*uv; pos=0; eq.append(bal); continue
            if hi>=ep*(1+TP/100): bal+=(ep*(1+TP/100)-ep)/ep*uv; pos=0; eq.append(bal); continue
        if pos==-1:
            if hi>=ep*(1+SL/100): bal+=(ep-ep*(1+SL/100))/ep*uv; pos=0; eq.append(bal); continue
            if lo<=ep*(1-TP/100): bal+=(ep-ep*(1-TP/100))/ep*uv; pos=0; eq.append(bal); continue
        lt=bb_long[i] and pos!=1; st=bb_short[i] and pos!=-1
        if lt and st:
            if abs(O[i]-bb_upper[i])>abs(O[i]-bb_lower[i]): lt=False
            else: st=False
        if lt:
            if pos==-1: bal+=(ep-bb_lower[i])/ep*uv; pos=0; eq.append(bal)
            if bal>10: cap=dyn_cap(bal,i); uv=cap*LEV; ep=bb_lower[i]; pos=1; trades+=1; eq.append(bal)
        elif st:
            if pos==1: bal+=(bb_upper[i]-ep)/ep*uv; pos=0; eq.append(bal)
            if bal>10: cap=dyn_cap(bal,i); uv=cap*LEV; ep=bb_upper[i]; pos=-1; trades+=1; eq.append(bal)
    if pos: bal+=((C[-1]-ep)/ep*uv if pos==1 else (ep-C[-1])/ep*uv); eq.append(bal)
    eqs=np.array(eq); peak=np.maximum.accumulate(eqs); dd=np.min((eqs-peak)/peak*100)
    return {"name":name,"final":round(bal,2),"ret":round((bal-INIT)/INIT*100,2),"dd":round(dd,2),"trades":trades}

results.append(backtest_bb("G: Pure BB mean-revert (always)"))

results.sort(key=lambda r:r["ret"],reverse=True)
print("\n"+"="*90)
print("  REGIME-SWITCHING TEST ({:.0f}s)".format(_time.time()-t0))
print("="*90)
print("{:<35s} {:>10s} {:>8s} {:>7s} {:>6s}".format("Strategy","Final","Return","MaxDD","Trades"))
print("-"*90)
for i,r in enumerate(results):
    m="BEST" if i==0 else ""
    print("{:<35s} ${:>8,.0f} {:>+7.1f}% {:>+6.1f}% {:>5d} {}".format(r["name"],r["final"],r["ret"],r["dd"],r["trades"],m))
