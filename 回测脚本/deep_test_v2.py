"""Comprehensive retest - proper vectorized, all promising combos"""
import pandas as pd, numpy as np, time as _time

t0=_time.time()
print("Loading 6.5yr data...")
df=pd.read_csv("/opt/trading/ETHUSDT_15m_full.csv",parse_dates=["ts"],index_col="ts")
n=len(df); INIT=700; TP=7; SL=5; PCT=60; LEV=3

# Vectorized indicators
H,L,C,O=df["high"].values,df["low"].values,df["close"].values,df["open"].values
tr=pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift(1)).abs(),(df["low"]-df["close"].shift(1)).abs()],axis=1).max(axis=1)
atr5=tr.rolling(5).mean()*0.75
ema50=df["close"].ewm(span=50,adjust=False).mean()
atr14=tr.rolling(14).mean()
mid_atr=atr14.median()

# RSI
delta=df["close"].diff(); gain=delta.clip(lower=0); loss=(-delta).clip(lower=0)
rsi14=(100-(100/(1+gain.rolling(14).mean()/loss.rolling(14).mean()))).values

# BB
bb_mid=df["close"].rolling(20).mean().values; bb_std=df["close"].rolling(20).std().values
bb_upper=bb_mid+2*bb_std; bb_lower=bb_mid-2*bb_std

# Volty signals (pre-computed)
pc_arr=C; pa_arr=atr5.values; pe_arr=ema50.values
vl=np.where(np.arange(n)>=200,H>=np.roll(pc_arr+pa_arr,1),False)&(np.roll(pc_arr,1)>np.roll(pe_arr,1))
vs=np.where(np.arange(n)>=200,L<=np.roll(pc_arr-pa_arr,1),False)&(np.roll(pc_arr,1)<np.roll(pe_arr,1))

# Filter arrays
rsi_mid=(rsi14>40)&(rsi14<60)  # RSI in middle zone
rsi_ex=(rsi14>70)|(rsi14<30)   # RSI extreme
bb_near=(C<bb_upper*1.002)&(C>bb_lower*0.998)  # Price near BB
vol_ok=(atr14.values>atr14.median()*0.3)  # Volatility OK
trend_strong=(np.abs(C-np.roll(ema50.values,1))/np.roll(ema50.values,1)*100>0.3)  # >0.3% from EMA

def backtest(name,filt_l=None,filt_s=None,pct=PCT,lev=LEV,tp=TP,sl=SL):
    bal=INIT; pos=None; ep=uv=0.0; eq=[INIT]; trades=0; tps=0; sls=0
    for i in range(200,n):
        hi,lo,cl=H[i],L[i],C[i]
        if pos==1:
            if lo<=ep*(1-sl/100): bal+=(ep*(1-sl/100)-ep)/ep*uv; pos=0; sls+=1; eq.append(bal); continue
            if hi>=ep*(1+tp/100): bal+=(ep*(1+tp/100)-ep)/ep*uv; pos=0; tps+=1; eq.append(bal); continue
        if pos==-1:
            if hi>=ep*(1+sl/100): bal+=(ep-ep*(1+sl/100))/ep*uv; pos=0; sls+=1; eq.append(bal); continue
            if lo<=ep*(1-tp/100): bal+=(ep-ep*(1-tp/100))/ep*uv; pos=0; tps+=1; eq.append(bal); continue
        lt=vl[i] and pos!=1; st=vs[i] and pos!=-1
        if filt_l is not None: lt=lt and bool(filt_l[i])
        if filt_s is not None: st=st and bool(filt_s[i])
        if lt and st:
            if abs(O[i]-(C[i-1]+atr5.iloc[i-1]))>abs(O[i]-(C[i-1]-atr5.iloc[i-1])): lt=False
            else: st=False
        if lt:
            if pos==-1: bal+=(ep-(C[i-1]+atr5.iloc[i-1]))/ep*uv; pos=0; eq.append(bal)
            if bal>10:
                cap=bal*pct/100
                if not pd.isna(atr14.iloc[i]): cap*=min(1.5,max(0.5,mid_atr/max(atr14.iloc[i],0.01)))
                cap=min(cap,bal*0.98); uv=cap*lev; ep=C[i-1]+atr5.iloc[i-1]; pos=1; trades+=1; eq.append(bal)
        elif st:
            if pos==-1: bal+=((C[i-1]-atr5.iloc[i-1])-ep)/ep*uv; pos=0; eq.append(bal)
            if bal>10:
                cap=bal*pct/100
                if not pd.isna(atr14.iloc[i]): cap*=min(1.5,max(0.5,mid_atr/max(atr14.iloc[i],0.01)))
                cap=min(cap,bal*0.98); uv=cap*lev; ep=C[i-1]-atr5.iloc[i-1]; pos=-1; trades+=1; eq.append(bal)
    if pos: bal+=((C[-1]-ep)/ep*uv if pos==1 else (ep-C[-1])/ep*uv); eq.append(bal)
    eqs=np.array(eq); peak=np.maximum.accumulate(eqs); dd=np.min((eqs-peak)/peak*100)
    return {"name":name,"final":round(bal,2),"ret":round((bal-INIT)/INIT*100,2),"dd":round(dd,2),"trades":trades,"tp":tps,"sl":sls}

results=[]

print("1. Baseline (no filter, TP7%, dynamic size)...")
results.append(backtest("A: Baseline Volty+TP7%+Dyn"))

print("2. RSI 40-60 middle zone only...")
results.append(backtest("B: RSI 40-60 filter",filt_l=rsi_mid,filt_s=rsi_mid))

print("3. RSI avoid extremes (<30 or >70)...")
results.append(backtest("C: RSI non-extreme",filt_l=~rsi_ex,filt_s=~rsi_ex))

print("4. RSI confirm: long>50 short<50...")
results.append(backtest("D: RSI confirm direction",filt_l=rsi14>50,filt_s=rsi14<50))

print("5. BB near bands only...")
results.append(backtest("E: BB near bands",filt_l=bb_near,filt_s=bb_near))

print("6. Volatility OK only...")
results.append(backtest("F: Volatility OK",filt_l=vol_ok,filt_s=vol_ok))

print("7. Trend strong filter...")
results.append(backtest("G: Trend strong",filt_l=trend_strong,filt_s=trend_strong))

print("8. RSI+BB combo...")
results.append(backtest("H: RSI+BB combo",filt_l=rsi_mid&bb_near,filt_s=rsi_mid&bb_near))

print("9. RSI+Vol combo...")
results.append(backtest("I: RSI+Vol combo",filt_l=rsi_mid&vol_ok,filt_s=rsi_mid&vol_ok))

print("10. No DynSize, pure TP7%...")
results.append(backtest("J: TP7% only",pct=60,lev=3))

print("11. Lower lev 40%x4x Dyn...")
results.append(backtest("K: 40%x4x Dyn",pct=40,lev=4))

print("12. Higher TP 10% Dyn...")
results.append(backtest("L: TP10% Dyn",tp=10))

results.sort(key=lambda r:r["ret"],reverse=True)
print("\n"+"="*95)
print("  COMPREHENSIVE FILTER TEST ({:.0f}s runtime)".format(_time.time()-t0))
print("="*95)
print("{:<4s} {:<28s} {:>10s} {:>8s} {:>7s} {:>6s}".format("Rk","Strategy","Final","Return","MaxDD","Trades"))
print("-"*95)
for i,r in enumerate(results):
    m="BEST" if i==0 else ""
    print("{:<4d} {:<28s} ${:>8,.0f} {:>+7.1f}% {:>+6.1f}% {:>5d} {}".format(i+1,r["name"],r["final"],r["ret"],r["dd"],r["trades"],m))
