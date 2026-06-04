"""Smart sizing: ATR + RSI combined"""
import pandas as pd, numpy as np

df=pd.read_csv("/opt/trading/ETHUSDT_15m_full.csv",parse_dates=["ts"],index_col="ts")
n=len(df); INIT=700
H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
tr=pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift(1)).abs(),(df["low"]-df["close"].shift(1)).abs()],axis=1).max(axis=1)
atr5_v=tr.rolling(5).mean()*0.75; ema50_v=df["close"].ewm(span=50,adjust=False).mean()
atr14_v=tr.rolling(14).mean(); mid_atr=atr14_v.median()
d=df["close"].diff(); g=d.clip(lower=0); l=(-d).clip(lower=0)
rsi_v=(100-(100/(1+g.rolling(14).mean()/l.rolling(14).mean()))).values

def run(label, cap_fn):
    bal=INIT; pos=None; ep=uv=0.0; eq=[INIT]
    for i in range(200,n):
        hi,lo,cl=H[i],L[i],C[i]
        if pos==1 and lo<=ep*0.95: bal+=(ep*0.95-ep)/ep*uv; pos=None; eq.append(bal); continue
        if pos==-1 and hi>=ep*1.05: bal+=(ep-ep*1.05)/ep*uv; pos=None; eq.append(bal); continue
        if pos==1 and hi>=ep*1.07: bal+=(ep*1.07-ep)/ep*uv; pos=None; eq.append(bal); continue
        if pos==-1 and lo<=ep*0.93: bal+=(ep-ep*0.93)/ep*uv; pos=None; eq.append(bal); continue
        pc=C[i-1]; pa=atr5_v.iloc[i-1]; pe=ema50_v.iloc[i-1]
        if pd.isna(pa) or pa<=0: continue
        rb=rsi_v[i-1]>50 if not pd.isna(rsi_v[i-1]) else True
        rbe=rsi_v[i-1]<50 if not pd.isna(rsi_v[i-1]) else True
        lt=hi>=pc+pa and pc>pe and rb and pos!=1
        st=lo<=pc-pa and pc<pe and rbe and pos!=-1
        if lt:
            if pos==-1: bal+=(ep-(pc+pa))/ep*uv; pos=None; eq.append(bal)
            if bal>10:
                cap=bal*0.6; av=atr14_v.iloc[i] if not pd.isna(atr14_v.iloc[i]) else mid_atr
                rs=rsi_v[i-1] if not pd.isna(rsi_v[i-1]) else 50
                cap*=cap_fn(av,rs) if av>0 else 1.0
                uv=min(cap,bal*0.98)*3; ep=pc+pa; pos=1; eq.append(bal)
        elif st:
            if pos==1: bal+=((pc-pa)-ep)/ep*uv; pos=None; eq.append(bal)
            if bal>10:
                cap=bal*0.6; av=atr14_v.iloc[i] if not pd.isna(atr14_v.iloc[i]) else mid_atr
                rs=rsi_v[i-1] if not pd.isna(rsi_v[i-1]) else 50
                cap*=cap_fn(av,rs) if av>0 else 1.0
                uv=min(cap,bal*0.98)*3; ep=pc-pa; pos=-1; eq.append(bal)
    if pos: bal+=((C[-1]-ep)/ep*uv if pos==1 else (ep-C[-1])/ep*uv); eq.append(bal)
    eqs=np.array(eq); peak=np.maximum.accumulate(eqs); dd=np.min((eqs-peak)/peak*100)
    print("{}: \${:,.0f} ({:+.1f}%) DD:{:+.1f}%".format(label,bal,(bal-INIT)/INIT*100,dd))
    return bal

print("=== Smart Sizing Test ===\n")
# A: Current (cap=1.0, neutral)
run("A: Cap1.0 neutral       ",lambda av,rs: min(1.0,max(0.5,mid_atr/av) if av>0 else 1))
# B: Smart: stronger trend -> bigger bet
run("B: Trend up to 1.5x     ",lambda av,rs: min(1.5,max(0.4,mid_atr/av*(0.7+abs(rs-50)/50*0.8) if av>0 else 1)))
# C: Conservative in chop, aggressive in trend
run("C: 0.5x chop, 1.2x trend",lambda av,rs: min(1.2,max(0.5,mid_atr/av*(0.5 if 40<rs<60 else 1.2) if av>0 else 1)))
# D: Pure ATR (no RSI)
run("D: Pure ATR (no RSI)    ",lambda av,rs: max(0.5,min(1.2,mid_atr/av) if av>0 else 1))
