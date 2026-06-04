"""2025年 Volty 过滤器参数优化 — 3组对比"""
import ccxt, pandas as pd, numpy as np, time as _time
from datetime import datetime, timezone

VLEN, VMULT, PCT, LEV, SL_PCT = 5, 0.75, 100, 3, 5
TREND_LEN = 50
INIT = 700.0

# ── 3组参数 ──
COMBOS = [
    {"name": "A: ATR≥0.3% 无ADX",   "atr": 0.3, "adx": 0},
    {"name": "B: ATR≥0.5% 无ADX",   "atr": 0.5, "adx": 0},
    {"name": "C: ATR≥0.5% ADX≥15",  "atr": 0.5, "adx": 15},
]

print("=" * 70)
print("  2025年 Volty 过滤器参数优化 — 3组对比")
print("=" * 70)

# ═══════════════ 下载数据 ═══════════════
print(">>> 下载 2025年 ETHUSDT 15m 数据...")
ex = ccxt.binance({"enableRateLimit": True, "timeout": 30000})
since = int(datetime(2025,1,1,tzinfo=timezone.utc).timestamp()*1000)
end_ts = int(datetime(2026,1,1,tzinfo=timezone.utc).timestamp()*1000)

all_klines = []
while since < end_ts:
    try:
        klines = ex.fetch_ohlcv("ETH/USDT:USDT", "15m", since=since, limit=1000)
        if not klines: break
        all_klines.extend(klines)
        since = klines[-1][0] + 1
        if len(all_klines) % 5000 == 0:
            print(f"  {len(all_klines)} 根...")
    except Exception as e:
        _time.sleep(1)

df = pd.DataFrame(all_klines, columns=["ts","open","high","low","close","volume"])
df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
df.set_index("ts", inplace=True)
df = df[(df.index >= "2025-01-01") & (df.index < "2026-01-01")]
print(f"  总计: {len(df)} 根K线\n")

# ═══════════════ 预计算指标 ═══════════════
tr = pd.concat([df["high"]-df["low"], (df["high"]-df["close"].shift(1)).abs(), (df["low"]-df["close"].shift(1)).abs()], axis=1).max(axis=1)
atr_sig = tr.rolling(VLEN).mean() * VMULT
atr14 = tr.rolling(14).mean()
ema50 = df["close"].ewm(span=TREND_LEN, adjust=False).mean()

# ADX(14)
def calc_adx(d, p=14):
    h,l,c = d["high"],d["low"],d["close"]
    tr_ = pd.concat([h-l,(h-c.shift(1)).abs(),(l-c.shift(1)).abs()],axis=1).max(axis=1)
    pdm = h.diff(); mdm = l.shift(1)-l
    pdm[(pdm<=mdm)|(pdm<=0)] = 0
    mdm[(mdm<=h.diff())|(mdm<=0)] = 0
    atr_ = tr_.ewm(alpha=1/p, adjust=False).mean()
    pdi = 100*pdm.ewm(alpha=1/p,adjust=False).mean()/atr_
    mdi = 100*mdm.ewm(alpha=1/p,adjust=False).mean()/atr_
    return 100*(pdi-mdi).abs()/(pdi+mdi).ewm(alpha=1/p,adjust=False).mean()
adx14 = calc_adx(df, 14)

# ═══════════════ 回测函数 ═══════════════
def run_backtest(min_atr_pct, min_adx):
    bal = INIT; pos = None; ep = cts = uv = 0.0
    trades = []; eq_curve = [{"t":df.index[0],"e":bal}]
    last_bar = None; filtered = 0
    START = max(VLEN+3, TREND_LEN+3, 30)

    for i in range(START, len(df)):
        hi, lo, op, cl = df["high"].iloc[i], df["low"].iloc[i], df["open"].iloc[i], df["close"].iloc[i]
        tm = df.index[i]

        # Stop loss
        if pos=="LONG" and lo <= ep*(1-SL_PCT/100):
            bal += (ep*(1-SL_PCT/100)-ep)/ep*uv; pos=None; eq_curve.append({"t":tm,"e":bal}); continue
        if pos=="SHORT" and hi >= ep*(1+SL_PCT/100):
            bal += (ep-ep*(1+SL_PCT/100))/ep*uv; pos=None; eq_curve.append({"t":tm,"e":bal}); continue

        pa = atr_sig.iloc[i-1]
        if pd.isna(pa) or pa<=0: continue
        pc = df["close"].iloc[i-1]
        ll, sl = pc+pa, pc-pa

        # Trend
        pe = ema50.iloc[i-1]
        tb = df["close"].iloc[i-1] > pe
        tbe = df["close"].iloc[i-1] < pe

        # ATR filter
        if min_atr_pct>0:
            pa14 = atr14.iloc[i-1]
            if not pd.isna(pa14) and pa14/pc*100 < min_atr_pct:
                filtered+=1; continue

        # ADX filter
        if min_adx>0:
            padx = adx14.iloc[i-1]
            if not pd.isna(padx) and padx < min_adx:
                filtered+=1; continue

        lt = hi>=ll and tb and pos!="LONG"
        st = lo<=sl and tbe and pos!="SHORT"
        if lt and st:
            if abs(op-ll) > abs(op-sl): lt=False
            else: st=False
        if tm==last_bar and pos: continue

        if lt:
            if pos=="SHORT":
                xp=ll; bal+=(ep-xp)/ep*uv; pos=None; eq_curve.append({"t":tm,"e":bal})
            if bal>10:
                cap=bal*PCT/100; uv=cap*LEV; ep=ll; cts=uv/ep; pos="LONG"
                trades.append({"t":tm,"a":"LONG","e":round(ep,2),"pnl":0}); eq_curve.append({"t":tm,"e":bal}); last_bar=tm
        elif st:
            if pos=="LONG":
                xp=sl; bal+=(xp-ep)/ep*uv; pos=None; eq_curve.append({"t":tm,"e":bal})
            if bal>10:
                cap=bal*PCT/100; uv=cap*LEV; ep=sl; cts=uv/ep; pos="SHORT"
                trades.append({"t":tm,"a":"SHORT","e":round(ep,2),"pnl":0}); eq_curve.append({"t":tm,"e":bal}); last_bar=tm

    if pos:
        lp=df["close"].iloc[-1]; bal+=(lp-ep)/ep*uv if pos=="LONG" else (ep-lp)/ep*uv

    eqs = pd.DataFrame(eq_curve); eqs.set_index("t",inplace=True)
    eqs["peak"] = eqs["e"].cummax()
    eqs["dd"] = (eqs["e"]-eqs["peak"])/eqs["peak"]*100
    max_dd = eqs["dd"].min()

    # Period results
    def calc(sd, ed):
        pe = eqs[(eqs.index>=sd)&(eqs.index<=ed)]
        be = eqs[eqs.index<sd]
        sb = be["e"].iloc[-1] if not be.empty else INIT
        eb = pe["e"].iloc[-1] if not pe.empty else sb
        return [round(sb,2), round(eb,2), round((eb-sb)/sb*100,2) if sb>0 else 0]

    week1 = calc("2025-01-01","2025-01-07")
    month1 = calc("2025-01-01","2025-01-31")
    year = calc("2025-01-01","2025-12-31")
    final_bal = round(bal, 2)

    # 月度
    monthly = []
    for m in range(1,13):
        sd=f"2025-{m:02d}-01"; ed=f"2025-{m+1:02d}-01" if m<12 else "2025-12-31"
        monthly.append(calc(sd,ed))

    return {"final":final_bal,"ret":round((final_bal-INIT)/INIT*100,2),
            "week1":week1,"month1":month1,"year":year,"monthly":monthly,
            "trades":len(trades),"filtered":filtered,"max_dd":round(max_dd,2)}

# ═══════════════ 跑3组 ═══════════════
results = []
for combo in COMBOS:
    print(f">>> 回测: {combo['name']}...")
    r = run_backtest(combo["atr"], combo["adx"])
    r["name"] = combo["name"]
    results.append(r)

# ═══════════════ 输出对比 ═══════════════
print()
print("=" * 85)
print("                     📊 三组参数对比 (2025年)")
print("=" * 85)

# Summary table
print(f"\n{'指标':<20s}", end="")
for r in results:
    print(f"{r['name']:>20s}", end="")
print()
print("-"*85)

print(f"{'全年收益':<20s}", end="")
for r in results:
    print(f"${r['final']:>8,.0f} ({r['ret']:+.1f}%)".rjust(20), end="")
print()

print(f"{'第1周':<20s}", end="")
for r in results:
    print(f"${r['week1'][1]:>7,.0f} ({r['week1'][2]:+.1f}%)".rjust(20), end="")
print()

print(f"{'第1月':<20s}", end="")
for r in results:
    print(f"${r['month1'][1]:>7,.0f} ({r['month1'][2]:+.1f}%)".rjust(20), end="")
print()

print(f"{'最大回撤':<20s}", end="")
for r in results:
    print(f"{r['max_dd']:+.1f}%".rjust(20), end="")
print()

print(f"{'成交次数':<20s}", end="")
for r in results:
    print(f"{r['trades']}".rjust(20), end="")
print()

print(f"{'被过滤':<20s}", end="")
for r in results:
    print(f"{r['filtered']:,}次".rjust(20), end="")
print()

# Monthly comparison
print(f"\n{'月份':<6s}", end="")
for r in results: print(f"{r['name']:>22s}", end="")
print()
print("-"*85)
for m in range(12):
    print(f"{m+1:>2}月  ", end="")
    for r in results:
        mo = r["monthly"][m]
        e = "🟢" if mo[2]>0 else "🔴"
        print(f"  ${mo[1]:>7,.0f} ({mo[2]:+.1f}%) {e}  ".rjust(22), end="")
    print()

# Original (no filter) comparison
print(f"\n{'='*85}")
print("  与原始策略（无过滤器）对比:")
print(f"  原始: $700 → $1,620 (+131.5%)  回撤 -75.1%")
print(f"{'='*85}")

# Winner
best = max(results, key=lambda r: r["final"])
print(f"\n🏆 最优参数: {best['name']}")
print(f"   最终: ${best['final']:,.0f}  ({best['ret']:+.1f}%)")
print(f"   最大回撤: {best['max_dd']:+.1f}%")
print(f"   过滤了 {best['filtered']:,} 次无效信号")
