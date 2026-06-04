"""Test choppiness filters to avoid whipsaw"""
import pandas as pd, numpy as np

df=pd.read_csv("/opt/trading/ETHUSDT_15m_full.csv",parse_dates=["ts"],index_col="ts")
n=len(df); INIT=700; TP=7; SL=5
H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
tr=pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift(1)).abs(),(df["low"]-df["close"].shift(1)).abs()],axis=1).max(axis=1)
atr5_v=tr.rolling(5).mean()*0.75; ema50_v=df["close"].ewm(span=50,adjust=False).mean()
atr14_v=tr.rolling(14).mean(); mid_atr=atr14_v.median()
d=df["close"].diff(); g=d.clip(lower=0); l=(-d).clip(lower=0)
rsi_v=(100-(100/(1+g.rolling(14).mean()/l.rolling(14).mean()))).values

def run(name, rsi_min=None, rsi_max=None, adx_thresh=None, chop_sleep=False):
    bal=INIT; pos=None; ep=uv=0.0; eq=[INIT]
    trades=0; tp_c=0; sl_c=0; skipped=0
    for i in range(200,n):
        hi,lo,cl=H[i],L[i],C[i]
        if pos==1 and lo<=ep*0.95: bal+=(ep*0.95-ep)/ep*uv; pos=None; sl_c+=1; eq.append(bal); continue
        if pos==-1 and hi>=ep*1.05: bal+=(ep-ep*1.05)/ep*uv; pos=None; sl_c+=1; eq.append(bal); continue
        if pos==1 and hi>=ep*1.07: bal+=(ep*1.07-ep)/ep*uv; pos=None; tp_c+=1; eq.append(bal); continue
        if pos==-1 and lo<=ep*0.93: bal+=(ep-ep*0.93)/ep*uv; pos=None; tp_c+=1; eq.append(bal); continue

        pc=C[i-1]; pa=atr5_v.iloc[i-1]; pe=ema50_v.iloc[i-1]
        if pd.isna(pa) or pa<=0: continue

        # RSI direction filter
        rb=rsi_v[i-1]>50 if not pd.isna(rsi_v[i-1]) else True
        rbe=rsi_v[i-1]<50 if not pd.isna(rsi_v[i-1]) else True

        # Choppiness filter: skip when RSI in middle zone
        rs=rsi_v[i-1]
        in_chop=False
        if chop_sleep and not pd.isna(rs):
            if rsi_min is not None and rsi_max is not None:
                if rsi_min <= rs <= rsi_max:
                    in_chop=True; skipped+=1; continue  # Skip this bar entirely

        lt=hi>=pc+pa and pc>pe and rb and pos!=1
        st=lo<=pc-pa and pc<pe and rbe and pos!=-1
        if lt and st:
            if abs(O[i]-(pc+pa))>abs(O[i]-(pc-pa)): lt=False
            else: st=False

        if lt:
            if pos==-1: bal+=(ep-(pc+pa))/ep*uv; pos=None; eq.append(bal)
            if bal>10:
                cap=bal*0.6; av=atr14_v.iloc[i] if not pd.isna(atr14_v.iloc[i]) else mid_atr
                if av>0: cap*=min(1.0,max(0.5,mid_atr/av))
                uv=min(cap,bal*0.98)*3; ep=pc+pa; pos=1; trades+=1; eq.append(bal)
        elif st:
            if pos==1: bal+=((pc-pa)-ep)/ep*uv; pos=None; eq.append(bal)
            if bal>10:
                cap=bal*0.6; av=atr14_v.iloc[i] if not pd.isna(atr14_v.iloc[i]) else mid_atr
                if av>0: cap*=min(1.0,max(0.5,mid_atr/av))
                uv=min(cap,bal*0.98)*3; ep=pc-pa; pos=-1; trades+=1; eq.append(bal)
    if pos: bal+=((C[-1]-ep)/ep*uv if pos==1 else (ep-C[-1])/ep*uv); eq.append(bal)
    eqs=np.array(eq); peak=np.maximum.accumulate(eqs); dd=np.min((eqs-peak)/peak*100)
    ret=(bal-INIT)/INIT*100
    return {"name":name,"final":round(bal,2),"ret":round(ret,2),"dd":round(dd,2),"trades":trades,"skipped":skipped,"tp":tp_c,"sl":sl_c}

results=[]
results.append(run("A: Current (no chop filter)"))
results.append(run("B: Skip RSI 45-55 (tight)",    rsi_min=45, rsi_max=55, chop_sleep=True))
results.append(run("C: Skip RSI 40-60 (medium)",   rsi_min=40, rsi_max=60, chop_sleep=True))
results.append(run("D: Skip RSI 35-65 (wide)",     rsi_min=35, rsi_max=65, chop_sleep=True))
results.append(run("E: Skip RSI 30-70 (very wide)",rsi_min=30, rsi_max=70, chop_sleep=True))
results.append(run("F: Current + smart sizing (ATR high + RSI ext = 1.2x, ATR high + RSI mid = 0.7x)"))
# F is placeholder for the smart sizing idea

results.sort(key=lambda r:r["ret"],reverse=True)
print("Chop Filter Test (6.5yr)\n")
print("{:<40s} {:>10s} {:>8s} {:>7s} {:>6s} {:>8s}".format("Strategy","Final","Return","MaxDD","Trades","Skipped"))
print("-"*85)
for r in results:
    print("{:<40s} ${:>8,.0f} {:>+7.1f}% {:>+6.1f}% {:>5d} {:>8,}".format(r["name"],r["final"],r["ret"],r["dd"],r["trades"],r["skipped"]))
