"""Thorough retest of promising strategies and filters"""
import pandas as pd, numpy as np

print("Loading 6.5yr data...")
df=pd.read_csv("/opt/trading/ETHUSDT_15m_full.csv",parse_dates=["ts"],index_col="ts")
n=len(df); INIT=700; TP=7

tr=pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift(1)).abs(),(df["low"]-df["close"].shift(1)).abs()],axis=1).max(axis=1)
atr5=tr.rolling(5).mean()*0.75; ema50=df["close"].ewm(span=50,adjust=False).mean()
atr14=tr.rolling(14).mean()
H,L,C,O=df["high"].values,df["low"].values,df["close"].values,df["open"].values

# Pre-compute indicators for filters (vectorized)
delta=pd.Series(C).diff()
gain=delta.clip(lower=0)
loss=(-delta).clip(lower=0)
avg_gain=gain.rolling(14).mean()
avg_loss=loss.rolling(14).mean()
rs=avg_gain/avg_loss
rsi14=(100-(100/(1+rs))).values

bb_mid=pd.Series(C).rolling(20).mean().values
bb_std=pd.Series(C).rolling(20).std().values
bb_upper=bb_mid+2*bb_std; bb_lower=bb_mid-2*bb_std

# PSAR with gentler params
def calc_psar(af_start=0.01, af_inc=0.01, af_max=0.1):
    psar=np.zeros(n); ep_arr=np.zeros(n); trend=np.ones(n)
    psar[0]=L[0]; ep_arr[0]=H[0]; trend[0]=1; af=af_start
    for i in range(1,n):
        psar[i]=psar[i-1]+af*(ep_arr[i-1]-psar[i-1])
        if trend[i-1]==1:
            psar[i]=min(psar[i],L[i-1],L[i-2] if i>1 else L[i-1])
            if H[i]>ep_arr[i-1]: ep_arr[i]=H[i]; af=min(af+af_inc,af_max)
            else: ep_arr[i]=ep_arr[i-1]
            if L[i]<psar[i]: trend[i]=-1; psar[i]=ep_arr[i]; ep_arr[i]=L[i]; af=af_start
            else: trend[i]=1
        else:
            psar[i]=max(psar[i],H[i-1],H[i-2] if i>1 else H[i-1])
            if L[i]<ep_arr[i-1]: ep_arr[i]=L[i]; af=min(af+af_inc,af_max)
            else: ep_arr[i]=ep_arr[i-1]
            if H[i]>psar[i]: trend[i]=1; psar[i]=ep_arr[i]; ep_arr[i]=H[i]; af=af_start
            else: trend[i]=-1
    return psar, trend

psar1,psar_trend1=calc_psar(0.01,0.01,0.1)  # Gentle PSAR
psar2,psar_trend2=calc_psar(0.005,0.005,0.05)  # Very gentle

def run(name, pct=60, lev=3, sl=5, tp=TP, use_filter=None, use_psar=None, use_dyn=False):
    bal=INIT; pos=None; ep=uv=0.0; eq=[INIT]; trades=0; tp_count=0; sl_count=0
    for i in range(200,n):
        hi,lo,cl=H[i],L[i],C[i]
        # SL
        sl_price=0
        if pos=="LONG":
            if use_psar is not None: sl_price=use_psar[i]  # PSAR trailing
            else: sl_price=ep*(1-sl/100)
            if lo<=sl_price: bal+=(sl_price-ep)/ep*uv; pos=None; sl_count+=1; eq.append(bal); continue
        if pos=="SHORT":
            if use_psar is not None: sl_price=use_psar[i]
            else: sl_price=ep*(1+sl/100)
            if hi>=sl_price: bal+=(ep-sl_price)/ep*uv; pos=None; sl_count+=1; eq.append(bal); continue
        # TP
        if pos=="LONG" and hi>=ep*(1+tp/100): bal+=(ep*(1+tp/100)-ep)/ep*uv; pos=None; tp_count+=1; eq.append(bal); continue
        if pos=="SHORT" and lo<=ep*(1-tp/100): bal+=(ep-ep*(1-tp/100))/ep*uv; pos=None; tp_count+=1; eq.append(bal); continue
        # Signal
        pc=C[i-1]; pa=atr5.iloc[i-1]; pe=ema50.iloc[i-1]
        if pd.isna(pa) or pa<=0: continue
        lt=hi>=pc+pa and pc>pe; st=lo<=pc-pa and pc<pe
        if lt and st:
            if abs(O[i]-(pc+pa))>abs(O[i]-(pc-pa)): lt=False
            else: st=False
        # Apply filter (use_filter is a numpy array)
        if use_filter is not None:
            if lt and not bool(use_filter[i]): lt=False
            if st and not bool(use_filter[i]): st=False
        if lt and pos!="LONG":
            if pos=="SHORT": bal+=(ep-(pc+pa))/ep*uv; pos=None; eq.append(bal)
            if bal>10:
                cap=bal*pct/100
                if use_dyn and not pd.isna(atr14.iloc[i]): cap*=min(1.5,max(0.5,10/max(atr14.iloc[i],0.01)))
                cap=min(cap,bal*0.98); uv=cap*lev; ep=pc+pa; pos="LONG"; trades+=1; eq.append(bal)
        elif st and pos!="SHORT":
            if pos=="LONG": bal+=((pc-pa)-ep)/ep*uv; pos=None; eq.append(bal)
            if bal>10:
                cap=bal*pct/100
                if use_dyn and not pd.isna(atr14.iloc[i]): cap*=min(1.5,max(0.5,10/max(atr14.iloc[i],0.01)))
                cap=min(cap,bal*0.98); uv=cap*lev; ep=pc-pa; pos="SHORT"; trades+=1; eq.append(bal)
    if pos: bal+=((C[-1]-ep)/ep*uv if pos=="LONG" else (ep-C[-1])/ep*uv); eq.append(bal)
    eqs=np.array(eq); peak=np.maximum.accumulate(eqs); dd=np.min((eqs-peak)/peak*100)
    ret=(bal-INIT)/INIT*100
    return {"name":name,"final":round(bal,2),"ret":round(ret,2),"dd":round(dd,2),"trades":trades,"tp":tp_count,"sl":sl_count}

results=[]

# 1. Baseline with dynamic sizing
print("Volty TP7% DynSize (baseline)...")
results.append(run("Volty TP7% DynSize",use_dyn=True))

# 2. + RSI filter (only trade when 30<RSI<70, avoid extremes)
print("+RSI filter (30-70)...")
rsi_filter=(rsi14>30)&(rsi14<70)
results.append(run("+RSI 30-70 filter",use_dyn=True,use_filter=rsi_filter))

# 3. + BB filter (only trade near Bollinger bands)
print("+BB filter...")
bb_filter=(C>bb_lower*0.995)|(C<bb_upper*1.005)  # near bands
results.append(run("+BB near bands",use_dyn=True,use_filter=bb_filter))

# 4. RSI confirmation: only long if RSI>50, only short if RSI<50
print("+RSI confirmation...")
rsi_long_ok=rsi14>50; rsi_short_ok=rsi14<50
# We'll use these inside run() via use_filter - but need separate long/short filters
# For now test with simple approach: skip both filters, add more useful tests instead

# 5. Gentle PSAR trailing
print("+Gentle PSAR...")
results.append(run("+Gentle PSAR(0.01/0.1)",use_dyn=True,use_psar=psar1))

# 6. Very gentle PSAR
print("+Very Gentle PSAR...")
results.append(run("+VeryGentle PSAR(0.005/0.05)",use_dyn=True,use_psar=psar2))

# 7. TP sweep with dynamic sizing
for tp_val in [3,5,10]:
    print("  DynSize TP={}%...".format(tp_val))
    results.append(run("DynSize TP={}%".format(tp_val),use_dyn=True,tp=tp_val))

# 8. Lower leverage with dynamic sizing
print("DynSize 30%x5x...")
results.append(run("DynSize 30%x5x",pct=30,lev=5,use_dyn=True))

results.append(run("DynSize 40%x4x",pct=40,lev=4,use_dyn=True))

# Sort
results.sort(key=lambda r:r["ret"],reverse=True)
print("\n"+"="*90)
print("  DEEP RETEST RESULTS (6.5yr, $700 start)")
print("="*90)
print("{:<30s} {:>10s} {:>8s} {:>7s} {:>6s} {:>5s} {:>5s}".format("Strategy","Final","Return","MaxDD","Trades","TPs","SLs"))
print("-"*90)
for r in results:
    m="BEST" if r==results[0] else ""
    print("{:<30s} ${:>8,.0f} {:>+7.1f}% {:>+6.1f}% {:>5d} {:>4d} {:>4d} {}".format(
        r["name"],r["final"],r["ret"],r["dd"],r["trades"],r["tp"],r["sl"],m))
