"""Test risk reduction approaches"""
import pandas as pd, numpy as np

df=pd.read_csv("/opt/trading/ETHUSDT_15m_full.csv",parse_dates=["ts"],index_col="ts")
n=len(df); INIT=700; TP=7; SL=5
H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
tr=pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift(1)).abs(),(df["low"]-df["close"].shift(1)).abs()],axis=1).max(axis=1)
atr5_v=tr.rolling(5).mean()*0.75; ema50_v=df["close"].ewm(span=50,adjust=False).mean()
atr14_v=tr.rolling(14).mean(); mid_atr=atr14_v.median()
d=df["close"].diff(); g=d.clip(lower=0); l=(-d).clip(lower=0)
rsi_v=(100-(100/(1+g.rolling(14).mean()/l.rolling(14).mean()))).values

def run(name, cap_mult=1.5, dd_protect=False, lev=3, sl=5):
    bal=INIT; pos=None; ep=uv=0.0; eq=[INIT]; peak=INIT
    trades=0; tp_c=0; sl_c=0
    for i in range(200,n):
        hi,lo,cl=H[i],L[i],C[i]
        # Track peak and DD
        if bal>peak: peak=bal
        dd_pct=(peak-bal)/peak*100
        # SL
        if pos==1 and lo<=ep*(1-sl/100): bal+=(ep*(1-sl/100)-ep)/ep*uv; pos=None; sl_c+=1; eq.append(bal); continue
        if pos==-1 and hi>=ep*(1+sl/100): bal+=(ep-ep*(1+sl/100))/ep*uv; pos=None; sl_c+=1; eq.append(bal); continue
        # TP
        if pos==1 and hi>=ep*(1+TP/100): bal+=(ep*(1+TP/100)-ep)/ep*uv; pos=None; tp_c+=1; eq.append(bal); continue
        if pos==-1 and lo<=ep*(1-TP/100): bal+=(ep-ep*(1-TP/100))/ep*uv; pos=None; tp_c+=1; eq.append(bal); continue
        # Signal
        pc=C[i-1]; pa=atr5_v.iloc[i-1]; pe=ema50_v.iloc[i-1]
        if pd.isna(pa) or pa<=0: continue
        rb=rsi_v[i-1]>50 if not pd.isna(rsi_v[i-1]) else True
        rbe=rsi_v[i-1]<50 if not pd.isna(rsi_v[i-1]) else True
        lt=hi>=pc+pa and pc>pe and rb and pos!=1
        st=lo<=pc-pa and pc<pe and rbe and pos!=-1
        if lt and st:
            if abs(O[i]-(pc+pa))>abs(O[i]-(pc-pa)): lt=False
            else: st=False
        if lt:
            if pos==-1: bal+=(ep-(pc+pa))/ep*uv; pos=None; eq.append(bal)
            if bal>10:
                pct=60
                # DD protection: halve position if in drawdown > 30%
                if dd_protect and dd_pct>30: pct=pct/2
                cap=bal*pct/100
                av=atr14_v.iloc[i] if not pd.isna(atr14_v.iloc[i]) else mid_atr
                if av>0: cap*=min(cap_mult,max(0.3,mid_atr/av))
                uv=min(cap,bal*0.98)*lev; ep=pc+pa; pos=1; trades+=1; eq.append(bal)
        elif st:
            if pos==1: bal+=((pc-pa)-ep)/ep*uv; pos=None; eq.append(bal)
            if bal>10:
                pct=60
                if dd_protect and dd_pct>30: pct=pct/2
                cap=bal*pct/100
                av=atr14_v.iloc[i] if not pd.isna(atr14_v.iloc[i]) else mid_atr
                if av>0: cap*=min(cap_mult,max(0.3,mid_atr/av))
                uv=min(cap,bal*0.98)*lev; ep=pc-pa; pos=-1; trades+=1; eq.append(bal)
    if pos: bal+=((C[-1]-ep)/ep*uv if pos==1 else (ep-C[-1])/ep*uv); eq.append(bal)
    eqs=np.array(eq); peak_arr=np.maximum.accumulate(eqs); dd=np.min((eqs-peak_arr)/peak_arr*100)
    ret=(bal-INIT)/INIT*100
    return {"name":name,"final":round(bal,2),"ret":round(ret,2),"dd":round(dd,2),"trades":trades}

results=[]
results.append(run("A: Baseline (cap1.5x, lev3x)", cap_mult=1.5, lev=3))
results.append(run("B: Cap1.2x lower risk", cap_mult=1.2, lev=3))
results.append(run("C: Cap1.0x conservative", cap_mult=1.0, lev=3))
results.append(run("D: Cap1.2x + DD protect", cap_mult=1.2, dd_protect=True, lev=3))
results.append(run("E: Cap1.2x + lev2x", cap_mult=1.2, lev=2))
results.append(run("F: Cap1.5x + DD protect", cap_mult=1.5, dd_protect=True, lev=3))
results.append(run("G: SL3% tighter", cap_mult=1.5, lev=3, sl=3))
results.append(run("H: SL3% + Cap1.2x", cap_mult=1.2, lev=3, sl=3))

results.sort(key=lambda r:r["ret"],reverse=True)
print("Risk Reduction Test (6.5yr)\n")
print("{:<35s} {:>10s} {:>8s} {:>7s} {:>6s}".format("Strategy","Final","Return","MaxDD","Trades"))
print("-"*70)
for r in results:
    print("{:<35s} ${:>8,.0f} {:>+7.1f}% {:>+6.1f}% {:>5d} {}".format(
        r["name"],r["final"],r["ret"],r["dd"],r["trades"],"BEST" if r==results[0] else ""))

# Also compute profit/drawdown ratio
print("\nRisk-adjusted (Return / |MaxDD|):")
for r in sorted(results,key=lambda r:r["ret"]/abs(r["dd"]) if r["dd"]!=0 else 0,reverse=True):
    if r["dd"]!=0:
        print("  {:<35s} {:+.1f}".format(r["name"],r["ret"]/abs(r["dd"])))
