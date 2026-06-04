"""Clean backtest - proven engine, just test multiple signal sources"""
import pandas as pd, numpy as np, json

INIT=700; PCT=60; LEV=3; SL=5; TP=1.5

print("Loading...")
df=pd.read_csv("/opt/trading/ETHUSDT_15m_full.csv",parse_dates=["ts"],index_col="ts")
n=len(df); print("{} candles".format(n))

# Pre-compute common
tr=pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift(1)).abs(),(df["low"]-df["close"].shift(1)).abs()],axis=1).max(axis=1)
atr5=tr.rolling(5).mean()*0.75
ema50=df["close"].ewm(span=50,adjust=False).mean()
atr14=tr.rolling(14).mean()

# === PROVEN backtest function (exact same as earlier successful backtests) ===
def run(name, get_signals_fn):
    bal=INIT; pos=None; ep=uv=0.0; trades=0; eq=[INIT]; stopped=0; tp_count=0
    for i in range(200,n):
        hi,lo,op,cl=df["high"].iloc[i],df["low"].iloc[i],df["open"].iloc[i],df["close"].iloc[i]
        # SL
        if pos=="LONG" and lo<=ep*(1-SL/100):
            bal+=(ep*(1-SL/100)-ep)/ep*uv; pos=None; stopped+=1; eq.append(bal); continue
        if pos=="SHORT" and hi>=ep*(1+SL/100):
            bal+=(ep-ep*(1+SL/100))/ep*uv; pos=None; stopped+=1; eq.append(bal); continue
        # TP
        if pos=="LONG" and hi>=ep*(1+TP/100):
            bal+=(ep*(1+TP/100)-ep)/ep*uv; pos=None; tp_count+=1; eq.append(bal); continue
        if pos=="SHORT" and lo<=ep*(1-TP/100):
            bal+=(ep-ep*(1-TP/100))/ep*uv; pos=None; tp_count+=1; eq.append(bal); continue
        # Signal
        lt,st=get_signals_fn(df,i)
        if lt and st:
            if abs(op-(df["close"].iloc[i-1]+atr5.iloc[i-1]))>abs(op-(df["close"].iloc[i-1]-atr5.iloc[i-1])): lt=False
            else: st=False
        if lt and pos!="LONG":
            if pos=="SHORT":
                xp=df["close"].iloc[i-1]+atr5.iloc[i-1]
                bal+=(ep-xp)/ep*uv; pos=None; eq.append(bal)
            if bal>10:
                cap=bal*PCT/100; uv=cap*LEV
                ep=df["close"].iloc[i-1]+atr5.iloc[i-1]; pos="LONG"; trades+=1; eq.append(bal)
        elif st and pos!="SHORT":
            if pos=="LONG":
                xp=df["close"].iloc[i-1]-atr5.iloc[i-1]
                bal+=(xp-ep)/ep*uv; pos=None; eq.append(bal)
            if bal>10:
                cap=bal*PCT/100; uv=cap*LEV
                ep=df["close"].iloc[i-1]-atr5.iloc[i-1]; pos="SHORT"; trades+=1; eq.append(bal)
    if pos:
        lp=df["close"].iloc[-1]
        bal+=((lp-ep)/ep*uv if pos=="LONG" else (ep-lp)/ep*uv); eq.append(bal)
    eqs=np.array(eq); peak=np.maximum.accumulate(eqs)
    dd=np.min((eqs-peak)/peak*100); ret=(bal-INIT)/INIT*100
    return {"name":name,"final":round(bal,2),"ret":round(ret,2),"dd":round(dd,2),
            "trades":trades,"stopped":stopped,"tp":tp_count,
            "pain":round(ret/abs(dd),2) if dd!=0 else 0}

# === Signal functions ===

def sig_volty_ema50(df,i):
    if i<6: return False,False
    pc=df["close"].iloc[i-1]; pa=atr5.iloc[i-1]; pe=ema50.iloc[i-1]
    if pd.isna(pa) or pa<=0: return False,False
    tb=pc>pe; tbe=pc<pe
    return df["high"].iloc[i]>=pc+pa and tb, df["low"].iloc[i]<=pc-pa and tbe

def sig_volty_notrend(df,i):
    if i<6: return False,False
    pc=df["close"].iloc[i-1]; pa=atr5.iloc[i-1]
    if pd.isna(pa) or pa<=0: return False,False
    return df["high"].iloc[i]>=pc+pa, df["low"].iloc[i]<=pc-pa

def sig_supertrend(df,i):
    """SuperTrend: ATR(10) x3"""
    if i<15: return False,False
    # Simplified: use rolling ST logic
    src=(df["high"].iloc[i-1]+df["low"].iloc[i-1])/2
    atr10=tr.rolling(10).mean().iloc[i-1]
    if pd.isna(atr10): return False,False
    up=src-3*atr10; dn=src+3*atr10
    # Trend change detection
    prev_src=(df["high"].iloc[i-2]+df["low"].iloc[i-2])/2
    prev_atr10=tr.rolling(10).mean().iloc[i-2]
    if pd.isna(prev_atr10): return False,False
    prev_up=prev_src-3*prev_atr10; prev_dn=prev_src+3*prev_atr10
    was_bear=df["close"].iloc[i-2]<prev_up; was_bull=df["close"].iloc[i-2]>prev_dn
    lt=df["high"].iloc[i]>dn and was_bear
    st=df["low"].iloc[i]<up and was_bull
    return lt,st

def sig_st_ema50(df,i):
    lt,st=sig_supertrend(df,i)
    if i<55: return False,False
    pe=ema50.iloc[i-1]; pc=df["close"].iloc[i-1]
    return lt and pc>pe, st and pc<pe

def sig_volty_st(df,i):
    """Volty + SuperTrend both must agree"""
    vlt,vst=sig_volty_ema50(df,i)
    slt,sst=sig_supertrend(df,i)
    # Both must be true
    return vlt and slt, vst and sst

def sig_volty_atrfilter(df,i):
    """Volty + ATR14 > 0.3% of price"""
    vlt,vst=sig_volty_ema50(df,i)
    if i<16: return False,False
    a14=atr14.iloc[i-1]; pc=df["close"].iloc[i-1]
    if pd.isna(a14) or pc<=0: return False,False
    ok=a14/pc*100>=0.3
    return vlt and ok, vst and ok

# === Run ===
results=[]
print("Volty EMA50...")
results.append(run("Volty EMA50 (current)",sig_volty_ema50))
print("Volty no-trend...")
results.append(run("Volty (no trend filter)",sig_volty_notrend))
print("SuperTrend...")
results.append(run("SuperTrend",sig_supertrend))
print("SuperTrend+EMA50...")
results.append(run("SuperTrend+EMA50",sig_st_ema50))
print("Volty+SuperTrend...")
results.append(run("Volty+SuperTrend combo",sig_volty_st))
print("Volty+ATR>0.3%...")
results.append(run("Volty+ATR>0.3%",sig_volty_atrfilter))

# Sort
results.sort(key=lambda r:r["ret"],reverse=True)
print("\n"+"="*75)
print("  FINAL STRATEGY RANKING (2019-2026, {} candles)".format(n))
print("="*75)
print("{:<3s} {:<28s} {:>8s} {:>7s} {:>6s} {:>6s}".format("Rk","Strategy","Final","Return","MaxDD","Trades"))
print("-"*75)
for i,r in enumerate(results):
    print("{:<3d} {:<28s} ${:>6,.0f} {:>+6.1f}% {:>+5.1f}% {:>5d}  SL:{} TP:{}".format(
        i+1,r["name"],r["final"],r["ret"],r["dd"],r["trades"],r["stopped"],r["tp"]))

print("\nDONE")
