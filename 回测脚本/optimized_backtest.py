"""Volty with PSAR trailing stop + dynamic position sizing"""
import pandas as pd, numpy as np

print("Loading 6.5yr data...")
df=pd.read_csv("/opt/trading/ETHUSDT_15m_full.csv",parse_dates=["ts"],index_col="ts")
n=len(df)

INIT=700; PCT=60; LEV=3; SL=5; TP=7

tr=pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift(1)).abs(),(df["low"]-df["close"].shift(1)).abs()],axis=1).max(axis=1)
atr5=tr.rolling(5).mean()*0.75; ema50=df["close"].ewm(span=50,adjust=False).mean()
box_width=tr.rolling(20).mean()  # 20-bar volatility for dynamic sizing
median_box=box_width.median()

def run_backtest(name, use_psar=False, use_dynamic_size=False):
    bal=INIT; pos=None; ep=uv=0.0; trades=0; eq=[INIT]
    stopped=0; tp_hits=0; psar_exits=0
    # PSAR state
    af=0.02; best_price=0; psar_sl=0

    for i in range(200,n):
        hi,lo,cl=df["high"].iloc[i],df["low"].iloc[i],df["close"].iloc[i]

        # === PSAR trailing stop ===
        if pos=="LONG" and use_psar:
            if cl>best_price: best_price=cl; af=min(af+0.02,0.2)
            psar_sl=ep+af*(best_price-ep) if best_price>ep else ep*(1-SL/100)
            actual_sl=psar_sl  # Use PSAR instead of fixed SL
        else:
            actual_sl=ep*(1-SL/100) if pos=="LONG" else (ep*(1+SL/100) if pos=="SHORT" else 0)

        # SL check
        if pos=="LONG" and lo<=actual_sl:
            bal+=(actual_sl-ep)/ep*uv; pos=None; stopped+=1
            if use_psar: psar_exits+=1; af=0.02; best_price=0
            eq.append(bal); continue
        if pos=="SHORT" and hi>=(ep*(1+SL/100) if not use_psar else psar_sl):
            if not use_psar: sl_p=ep*(1+SL/100)
            else: sl_p=psar_sl
            bal+=(ep-sl_p)/ep*uv; pos=None; stopped+=1
            if use_psar: psar_exits+=1; af=0.02; best_price=0
            eq.append(bal); continue

        # TP (fixed)
        if pos=="LONG" and hi>=ep*(1+TP/100):
            bal+=(ep*(1+TP/100)-ep)/ep*uv; pos=None; tp_hits+=1; af=0.02; best_price=0; eq.append(bal); continue
        if pos=="SHORT" and lo<=ep*(1-TP/100):
            bal+=(ep-ep*(1-TP/100))/ep*uv; pos=None; tp_hits+=1; af=0.02; best_price=0; eq.append(bal); continue

        # Signal
        pc=df["close"].iloc[i-1]; pa=atr5.iloc[i-1]; pe=ema50.iloc[i-1]
        if pd.isna(pa) or pa<=0: continue
        tb=pc>pe; tbe=pc<pe
        lt=hi>=pc+pa and tb; st=lo<=pc-pa and tbe
        if lt and st:
            if abs(df["open"].iloc[i]-(pc+pa))>abs(df["open"].iloc[i]-(pc-pa)): lt=False
            else: st=False

        if lt and pos!="LONG":
            if pos=="SHORT": xp=pc+pa; bal+=(ep-xp)/ep*uv; pos=None; eq.append(bal)
            if bal>10:
                cap=bal*PCT/100
                if use_dynamic_size and not pd.isna(box_width.iloc[i]):
                    # Dynamic: scale by median_box/current_box
                    bw=box_width.iloc[i]
                    if bw>0: cap*=min(1.5,max(0.5,median_box/bw))
                cap=min(cap,bal*0.98)
                uv=cap*LEV; ep=pc+pa; pos="LONG"
                if use_psar: af=0.02; best_price=cl; psar_sl=ep*(1-SL/100)
                trades+=1; eq.append(bal)
        elif st and pos!="SHORT":
            if pos=="LONG": xp=pc-pa; bal+=(xp-ep)/ep*uv; pos=None; eq.append(bal)
            if bal>10:
                cap=bal*PCT/100
                if use_dynamic_size and not pd.isna(box_width.iloc[i]):
                    bw=box_width.iloc[i]
                    if bw>0: cap*=min(1.5,max(0.5,median_box/bw))
                cap=min(cap,bal*0.98)
                uv=cap*LEV; ep=pc-pa; pos="SHORT"
                if use_psar: af=0.02; best_price=cl; psar_sl=ep*(1+SL/100)
                trades+=1; eq.append(bal)

    if pos: lp=df["close"].iloc[-1]; bal+=((lp-ep)/ep*uv if pos=="LONG" else (ep-lp)/ep*uv); eq.append(bal)
    eqs=np.array(eq); peak=np.maximum.accumulate(eqs); dd=np.min((eqs-peak)/peak*100)
    ret=(bal-INIT)/INIT*100
    return {"name":name,"final":round(bal,2),"ret":round(ret,2),"dd":round(dd,2),
            "trades":trades,"stopped":stopped,"tp":tp_hits,"psar":psar_exits,
            "pain":round(ret/abs(dd),2) if dd!=0 else 0}

# Run 4 variants
print("Volty+TP7% (baseline)...")
r1=run_backtest("Volty+TP7%")

print("Volty+TP7%+PSAR trailing...")
r2=run_backtest("Volty+TP7%+PSAR", use_psar=True)

print("Volty+TP7%+DynamicSize...")
r3=run_backtest("Volty+TP7%+DynSize", use_dynamic_size=True)

print("Volty+TP7%+PSAR+DynSize...")
r4=run_backtest("Volty+TP7%+PSAR+DynSize", use_psar=True, use_dynamic_size=True)

# Also add the original Volty from final_compare for reference
print("Volty+TP1.5% (old)...")
orig_tp=TP; TP=1.5
r0=run_backtest("Volty+TP1.5%(old)")
TP=orig_tp

results=[r0,r1,r2,r3,r4]
results.sort(key=lambda r:r["ret"],reverse=True)

print("\n"+"="*90)
print("  OPTIMIZED STRATEGY COMPARISON (6.5 years, $700 start)")
print("="*90)
print("{:<25s} {:>10s} {:>8s} {:>8s} {:>7s} {:>6s} {:>6s}".format(
    "Strategy","Final","Return","MaxDD","Pain","Trades","TPs"))
print("-"*90)
for r in results:
    m="BEST" if r==results[0] else ""
    print("{:<25s} ${:>8,.0f} {:>+7.1f}% {:>+7.1f}% {:>6.1f} {:>5d} {:>5d} {}".format(
        r["name"],r["final"],r["ret"],r["dd"],r["pain"],r["trades"],r["tp"],m))
