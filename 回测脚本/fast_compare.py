"""Fast vectorized strategy comparison on 6.5yr data"""
import pandas as pd, numpy as np, json, time as _time

INIT=700; PCT=60; LEV=3; SL=5; TP=1.5

print("Loading data...")
df=pd.read_csv("/opt/trading/ETHUSDT_15m_full.csv",parse_dates=["ts"],index_col="ts")
H,L,C,O=df["high"].values,df["low"].values,df["close"].values,df["open"].values
n=len(df); print("{} candles, {} -> {}".format(n,str(df.index[0])[:10],str(df.index[-1])[:10]))

# Pre-compute indicators (vectorized, fast)
print("Computing indicators...")
TR=np.maximum(H-L,np.maximum(np.abs(H-np.roll(C,1)),np.abs(L-np.roll(C,1))))
EMA50=pd.Series(C).ewm(span=50,adjust=False).mean().to_numpy()
ATR14=pd.Series(TR).rolling(14).mean().to_numpy()
ATR5=pd.Series(TR).rolling(5).mean().to_numpy()

def backtest(signals_long,signals_short,name):
    """signals: boolean arrays of entry triggers"""
    bal=INIT; pos=0; ep=uv=0.0; eq=[INIT]; trades=0
    for i in range(200,n):
        hi,lo,cl=H[i],L[i],C[i]
        # SL
        if pos==1 and lo<=ep*(1-SL/100):
            bal+=(ep*(1-SL/100)-ep)/ep*uv; pos=0; eq.append(bal); continue
        if pos==-1 and hi>=ep*(1+SL/100):
            bal+=(ep-ep*(1+SL/100))/ep*uv; pos=0; eq.append(bal); continue
        # TP
        if pos==1 and cl>=ep*(1+TP/100):
            bal+=(ep*(1+TP/100)-ep)/ep*uv; pos=0; eq.append(bal); continue
        if pos==-1 and cl<=ep*(1-TP/100):
            bal+=(ep-ep*(1-TP/100))/ep*uv; pos=0; eq.append(bal); continue
        # Signals
        lt=signals_long[i] and pos!=1
        st=signals_short[i] and pos!=-1
        if lt and st:
            if abs(O[i]-(C[i-1]+ATR5[i-1]*0.75))>abs(O[i]-(C[i-1]-ATR5[i-1]*0.75)): lt=False
            else: st=False
        if lt:
            if pos==-1:
                xp=C[i-1]+ATR5[i-1]*0.75; bal+=(ep-xp)/ep*uv; pos=0; eq.append(bal)
            if bal>10:
                cap=bal*PCT/100; uv=cap*LEV; ep=C[i-1]+ATR5[i-1]*0.75; pos=1
                eq.append(bal); trades+=1
        elif st:
            if pos==1:
                xp=C[i-1]-ATR5[i-1]*0.75; bal+=(xp-ep)/ep*uv; pos=0; eq.append(bal)
            if bal>10:
                cap=bal*PCT/100; uv=cap*LEV; ep=C[i-1]-ATR5[i-1]*0.75; pos=-1
                eq.append(bal); trades+=1
    if pos:
        clf=C[-1]; bal+=((clf-ep)/ep*uv if pos==1 else (ep-clf)/ep*uv); eq.append(bal)
    eqs=np.array(eq); peak=np.maximum.accumulate(eqs)
    dd=np.min((eqs-peak)/peak*100); ret=(bal-INIT)/INIT*100
    return {"name":name,"final":round(bal,2),"ret":round(ret,2),"max_dd":round(dd,2),
            "trades":trades,"pain":round(ret/abs(dd),2) if dd!=0 else 0}

t0=_time.time()
results=[]

# 1. Volty EMA50 (current)
print("Volty EMA50...")
pc=C; pa=ATR5*0.75; pe=EMA50
valid=np.ones(n,dtype=bool); valid[:5]=False
vl=np.where(valid,H>=np.roll(pc+pa,1),False) & (np.roll(pc,1)>np.roll(pe,1))
vs=np.where(valid,L<=np.roll(pc-pa,1),False) & (np.roll(pc,1)<np.roll(pe,1))
vl[:200]=False; vs[:200]=False
results.append(backtest(vl,vs,"Volty EMA50"))

# 2. SuperTrend
print("SuperTrend...")
st_period=10; st_mult=3.0
src=(H+L)/2; atr_st=pd.Series(TR).rolling(st_period).mean().values
up=src-st_mult*atr_st; dn=src+st_mult*atr_st
st_bull=np.zeros(n,dtype=bool); st_bear=np.zeros(n,dtype=bool)
for i in range(st_period+5,n):
    if C[i-1]>up[i-1]: st_bull[i]=True
    if C[i-1]<dn[i-1]: st_bear[i]=True
prev_bull=np.roll(st_bull,1); prev_bear=np.roll(st_bear,1)
st_l=st_bull & ~prev_bull; st_s=st_bear & ~prev_bear
st_l[:200]=False; st_s[:200]=False
results.append(backtest(st_l,st_s,"SuperTrend"))

# 3. SuperTrend + EMA50
print("SuperTrend+EMA50...")
st_l2=st_l & (np.roll(C,1)>np.roll(EMA50,1))
st_s2=st_s & (np.roll(C,1)<np.roll(EMA50,1))
results.append(backtest(st_l2,st_s2,"SuperTrend+EMA50"))

# 4. PMax
print("PMax...")
ma_ema=pd.Series(C).ewm(span=10).mean().values; atr_p=ATR14
long_stop=ma_ema-3*atr_p; short_stop=ma_ema+3*atr_p
pm_bull=np.zeros(n,dtype=bool); pm_bear=np.zeros(n,dtype=bool)
for i in range(20,n):
    if ma_ema[i-1]>long_stop[i-1]: pm_bull[i]=True
    if ma_ema[i-1]<short_stop[i-1]: pm_bear[i]=True
prev_pb=np.roll(pm_bull,1); prev_ps=np.roll(pm_bear,1)
pm_l=pm_bull & ~prev_pb; pm_s=pm_bear & ~prev_ps
pm_l[:200]=False; pm_s[:200]=False
results.append(backtest(pm_l,pm_s,"PMax"))

# 5. Volty + SuperTrend combo
print("Volty+SuperTrend...")
vst_l=vl & st_bull; vst_s=vs & st_bear
vst_l[:200]=False; vst_s[:200]=False
results.append(backtest(vst_l,vst_s,"Volty+SuperTrend"))

# 6. Volty + ATR14 filter (only trade when ATR > 0.5% of price)
print("Volty+ATRfilter...")
atr_pct=ATR14/C*100
vl_a=vl & (np.roll(atr_pct,1)>0.5); vs_a=vs & (np.roll(atr_pct,1)>0.5)
results.append(backtest(vl_a,vs_a,"Volty+ATR>0.5%"))

# 7. Volty + TP=3%
print("Volty TP=3%...")
orig_tp_val = TP
TP_VAL = 3
# Re-run with modified TP
def backtest_tp(signals_long,signals_short,name,tp_val):
    bal=INIT; pos=0; ep=uv=0.0; eq=[INIT]; trades=0
    for i in range(200,n):
        hi,lo,cl=H[i],L[i],C[i]
        if pos==1 and lo<=ep*(1-SL/100):
            bal+=(ep*(1-SL/100)-ep)/ep*uv; pos=0; eq.append(bal); continue
        if pos==-1 and hi>=ep*(1+SL/100):
            bal+=(ep-ep*(1+SL/100))/ep*uv; pos=0; eq.append(bal); continue
        if pos==1 and cl>=ep*(1+tp_val/100):
            bal+=(ep*(1+tp_val/100)-ep)/ep*uv; pos=0; eq.append(bal); continue
        if pos==-1 and cl<=ep*(1-tp_val/100):
            bal+=(ep-ep*(1-tp_val/100))/ep*uv; pos=0; eq.append(bal); continue
        lt=signals_long[i] and pos!=1; st=signals_short[i] and pos!=-1
        if lt and st:
            if abs(O[i]-(C[i-1]+ATR5[i-1]*0.75))>abs(O[i]-(C[i-1]-ATR5[i-1]*0.75)): lt=False
            else: st=False
        if lt:
            if pos==-1: xp=C[i-1]+ATR5[i-1]*0.75; bal+=(ep-xp)/ep*uv; pos=0; eq.append(bal)
            if bal>10: cap=bal*PCT/100; uv=cap*LEV; ep=C[i-1]+ATR5[i-1]*0.75; pos=1; eq.append(bal); trades+=1
        elif st:
            if pos==1: xp=C[i-1]-ATR5[i-1]*0.75; bal+=(xp-ep)/ep*uv; pos=0; eq.append(bal)
            if bal>10: cap=bal*PCT/100; uv=cap*LEV; ep=C[i-1]-ATR5[i-1]*0.75; pos=-1; eq.append(bal); trades+=1
    if pos: clf=C[-1]; bal+=((clf-ep)/ep*uv if pos==1 else (ep-clf)/ep*uv); eq.append(bal)
    eqs=np.array(eq); peak=np.maximum.accumulate(eqs)
    dd=np.min((eqs-peak)/peak*100); ret=(bal-INIT)/INIT*100
    return {"name":name,"final":round(bal,2),"ret":round(ret,2),"max_dd":round(dd,2),"trades":trades,"pain":round(ret/abs(dd),2) if dd!=0 else 0}
results.append(backtest_tp(vl,vs,"Volty TP=3%",3))

results.sort(key=lambda r:r["ret"],reverse=True)
print("\n"+"="*80)
print("  STRATEGY COMPARISON ({:.0f}s runtime)".format(_time.time()-t0))
print("="*80)
print("{:<5s} {:<25s} {:>10s} {:>8s} {:>8s} {:>8s}".format("Rank","Strategy","Final","Return","MaxDD","Trades"))
print("-"*80)
for i,r in enumerate(results):
    m="[WIN]" if r["ret"]>0 else "[LOSS]"
    print("{:<5d} {:<25s} ${:>8,.0f} {:>+7.1f}% {:>+7.1f}% {:>6d} {}".format(i+1,r["name"],r["final"],r["ret"],r["max_dd"],r["trades"],m))

with open("/opt/trading/strategy_comparison.json","w") as f:
    json.dump({"config":"{}%x{}x SL:{}% TP:{}%".format(PCT,LEV,SL,orig_tp),
               "data":"{}->{}".format(str(df.index[0])[:10],str(df.index[-1])[:10]),
               "rankings":[{"rank":i+1,"name":r["name"],"final":r["final"],"return":r["ret"],"max_dd":r["max_dd"],"trades":r["trades"]} for i,r in enumerate(results)]},f,indent=2)
print("\nSaved to strategy_comparison.json")
