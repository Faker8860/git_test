"""仓位全扫描 — 20组仓位×杠杆，找最优"""
import ccxt, pandas as pd, numpy as np, time as _time
from datetime import datetime, timezone

VLEN, VMULT, SL_PCT = 5, 0.75, 5
INIT = 700.0

# 20 combinations
COMBOS = []
for pct in [10, 20, 25, 30, 33, 40, 50, 60, 66, 75, 80, 100]:
    for lev in [2, 3]:
        if pct == 10 and lev == 3: COMBOS.append((pct, lev))
        elif pct <= 50 and lev == 3: COMBOS.append((pct, lev))
        elif lev == 2: COMBOS.append((pct, lev))
        elif pct >= 50: COMBOS.append((pct, lev))
# Deduplicate
COMBOS = list(set(COMBOS))
COMBOS.sort(key=lambda x: x[0]*x[1])  # sort by effective exposure

print("=" * 70)
print(f"  Position Sizing Sweep — {len(COMBOS)} combinations")
print("=" * 70)

# === Download 2024+2025 data ===
print(">>> Downloading ETHUSDT 15m (2024-2025)...")
ex = ccxt.binance({"enableRateLimit": True, "timeout": 30000})
since = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
end_ts = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

all_klines = []
while since < end_ts:
    try:
        klines = ex.fetch_ohlcv("ETH/USDT:USDT", "15m", since=since, limit=1000)
        if not klines: break
        all_klines.extend(klines)
        since = klines[-1][0] + 1
        if len(all_klines) % 10000 == 0: print(f"  {len(all_klines)}...")
    except: _time.sleep(1)

df = pd.DataFrame(all_klines, columns=["ts","open","high","low","close","volume"])
df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
df.set_index("ts", inplace=True)
df = df[(df.index >= "2024-01-01") & (df.index < "2026-01-01")]
print(f"  Total: {len(df)} candles\n")

# === Pre-compute ===
tr = pd.concat([df["high"]-df["low"], (df["high"]-df["close"].shift(1)).abs(), (df["low"]-df["close"].shift(1)).abs()], axis=1).max(axis=1)
atr_sig = tr.rolling(VLEN).mean() * VMULT
ema50 = df["close"].ewm(span=50, adjust=False).mean()
START = max(VLEN+3, 53)

def run(pct, lev):
    bal = INIT; pos = None; ep = uv = 0.0
    eq_curve = [INIT]; min_bal = INIT
    for i in range(START, len(df)):
        hi, lo = df["high"].iloc[i], df["low"].iloc[i]
        if pos == "LONG" and lo <= ep*(1-SL_PCT/100):
            bal += (ep*(1-SL_PCT/100)-ep)/ep*uv; pos=None; eq_curve.append(bal); continue
        if pos == "SHORT" and hi >= ep*(1+SL_PCT/100):
            bal += (ep-ep*(1+SL_PCT/100))/ep*uv; pos=None; eq_curve.append(bal); continue
        pa = atr_sig.iloc[i-1]
        if pd.isna(pa) or pa<=0: continue
        pc = df["close"].iloc[i-1]; ll, sl = pc+pa, pc-pa
        tb = df["close"].iloc[i-1] > ema50.iloc[i-1]
        tbe = df["close"].iloc[i-1] < ema50.iloc[i-1]
        lt = df["high"].iloc[i]>=ll and tb and pos!="LONG"
        st = df["low"].iloc[i]<=sl and tbe and pos!="SHORT"
        if lt and st:
            if abs(df["open"].iloc[i]-ll) > abs(df["open"].iloc[i]-sl): lt=False
            else: st=False
        if lt:
            if pos=="SHORT": bal+=(ep-ll)/ep*uv; pos=None; eq_curve.append(bal)
            if bal>10: cap=bal*pct/100; uv=cap*lev; ep=ll; pos="LONG"; eq_curve.append(bal)
        elif st:
            if pos=="LONG": bal+=(sl-ep)/ep*uv; pos=None; eq_curve.append(bal)
            if bal>10: cap=bal*pct/100; uv=cap*lev; ep=sl; pos="SHORT"; eq_curve.append(bal)
        if bal < min_bal: min_bal = bal
    if pos:
        lp=df["close"].iloc[-1]; bal+=(lp-ep)/ep*uv if pos=="LONG" else (ep-lp)/ep*uv
    # Calc metrics
    eq = pd.Series(eq_curve); peak = eq.cummax()
    dd = (eq - peak) / peak * 100
    max_dd = dd.min()
    ret = (bal-INIT)/INIT*100
    # Pain ratio: return per unit of max drawdown
    pain = ret/abs(max_dd) if max_dd != 0 else 0
    # Sharpe-like: daily return / daily volatility (approximate)
    daily_ret = pd.Series(eq_curve).pct_change().dropna()
    sharpe = daily_ret.mean()/daily_ret.std()*np.sqrt(365) if daily_ret.std()>0 else 0
    return {"pct":pct,"lev":lev,"final":round(bal,2),"ret":round(ret,2),
            "max_dd":round(max_dd,2),"pain":round(pain,2),"sharpe":round(sharpe,2),
            "min_bal":round(min_bal,2),"exposure":pct*lev}

# === Run all ===
results = []
for pct, lev in COMBOS:
    r = run(pct, lev)
    results.append(r)
    print("  {}%x{}x -> ${:>7,.0f} ({:+.1f}%)  DD:{:+.1f}%  Pain:{:.2f}".format(pct, lev, r['final'], r['ret'], r['max_dd'], r['pain']))

# === Output ===
results.sort(key=lambda r: r["ret"], reverse=True)
print(f"\n{'='*80}")
print(f"  RANKED BY RETURN (2024-2025 combined, $700 start)")
print(f"{'='*80}")
print(f"{'Rank':<5s} {'Config':<12s} {'Final':>10s} {'Return':>8s} {'MaxDD':>8s} {'Pain':>7s} {'Sharpe':>7s} {'MinBal':>10s}")
print("-"*80)
for i, r in enumerate(results):
    cfg = "{}%x{}x".format(r['pct'], r['lev'])
    print("{:<5d} {:<12s} ${:>8,.0f} {:>+7.1f}% {:>+7.1f}% {:>6.2f} {:>6.2f} ${:>8,.0f}".format(
        i+1, cfg, r['final'], r['ret'], r['max_dd'], r['pain'], r['sharpe'], r['min_bal']))

# Best by different metrics
print(f"\n{'='*80}")
print(f"  BEST BY METRIC")
print(f"{'='*80}")
for metric, label in [("ret", "Highest Return"), ("pain", "Best Risk/Reward"), ("sharpe", "Best Sharpe"), ("max_dd", "Lowest Drawdown")]:
    if metric == "max_dd":
        best = max(results, key=lambda r: r[metric])  # least negative
    else:
        best = max(results, key=lambda r: r[metric])
    print("  {}: {}%x{}x -> ${:,.0f} ({:+.1f}%) DD:{:+.1f}%".format(
        label, best['pct'], best['lev'], best['final'], best['ret'], best['max_dd']))

# Current 50%x3x
curr = [r for r in results if r["pct"]==50 and r["lev"]==3][0]
print("\n  Current (50%x3x): Rank #{}, ${:,.0f} ({:+.1f}%)".format(
    results.index(curr)+1, curr['final'], curr['ret']))
