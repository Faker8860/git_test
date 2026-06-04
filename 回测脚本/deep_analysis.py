"""Deep analysis: take-profit sweep + time patterns + trade duration"""
import ccxt, pandas as pd, numpy as np, time as _time
from datetime import datetime, timezone
from collections import Counter

VLEN, VMULT, PCT, LEV, SL_PCT = 5, 0.75, 60, 3, 5
INIT = 700.0

print("=" * 70)
print("  Deep Strategy Analysis — 2024-2025 Data")
print("=" * 70)

# === Download ===
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
        if len(all_klines) % 10000 == 0: print("  {}...".format(len(all_klines)))
    except: _time.sleep(1)

df = pd.DataFrame(all_klines, columns=["ts","open","high","low","close","volume"])
df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
df.set_index("ts", inplace=True)
df = df[(df.index >= "2024-01-01") & (df.index < "2026-01-01")]
print("  Total: {} candles\n".format(len(df)))

# === Pre-compute ===
tr = pd.concat([df["high"]-df["low"], (df["high"]-df["close"].shift(1)).abs(), (df["low"]-df["close"].shift(1)).abs()], axis=1).max(axis=1)
atr_sig = tr.rolling(VLEN).mean() * VMULT
ema50 = df["close"].ewm(span=50, adjust=False).mean()
atr14 = tr.rolling(14).mean()
START = max(VLEN+3, 53)

# ===================================================================
# 1. TAKE-PROFIT SWEEP
# ===================================================================
print("=" * 70)
print("  1. TAKE-PROFIT OPTIMIZATION")
print("=" * 70)

def backtest_with_tp(tp_pct):
    """Run backtest with take-profit at tp_pct% (0 = no TP, same as current)."""
    bal = INIT; pos = None; ep = uv = 0.0
    trades = []
    eq_curve = [INIT]

    for i in range(START, len(df)):
        hi, lo = df["high"].iloc[i], df["low"].iloc[i]
        tm = df.index[i]

        # Stop loss
        if pos == "LONG":
            sp = ep * (1 - SL_PCT/100)
            tp = ep * (1 + tp_pct/100) if tp_pct > 0 else 999999
            if lo <= sp:
                pnl = (sp - ep) / ep * uv; bal += pnl
                trades.append({"pnl": pnl, "exit": "SL", "dur": tm})
                pos = None; eq_curve.append(bal); continue
            if hi >= tp:
                pnl = (tp - ep) / ep * uv; bal += pnl
                trades.append({"pnl": pnl, "exit": "TP", "dur": tm})
                pos = None; eq_curve.append(bal); continue
        if pos == "SHORT":
            sp = ep * (1 + SL_PCT/100)
            tp = ep * (1 - tp_pct/100) if tp_pct > 0 else 0
            if hi >= sp:
                pnl = (ep - sp) / ep * uv; bal += pnl
                trades.append({"pnl": pnl, "exit": "SL", "dur": tm})
                pos = None; eq_curve.append(bal); continue
            if lo <= tp:
                pnl = (ep - tp) / ep * uv; bal += pnl
                trades.append({"pnl": pnl, "exit": "TP", "dur": tm})
                pos = None; eq_curve.append(bal); continue

        pa = atr_sig.iloc[i-1]
        if pd.isna(pa) or pa <= 0: continue
        pc = df["close"].iloc[i-1]; ll, sl = pc + pa, pc - pa
        tb = df["close"].iloc[i-1] > ema50.iloc[i-1]
        tbe = df["close"].iloc[i-1] < ema50.iloc[i-1]
        lt = hi >= ll and tb and pos != "LONG"
        st = lo <= sl and tbe and pos != "SHORT"
        if lt and st:
            if abs(df["open"].iloc[i] - ll) > abs(df["open"].iloc[i] - sl): lt = False
            else: st = False

        if lt:
            if pos == "SHORT":
                xp = ll; bal += (ep - xp) / ep * uv
                trades.append({"pnl": (ep-xp)/ep*uv, "exit": "FLIP", "dur": tm})
                pos = None; eq_curve.append(bal)
            if bal > 10:
                cap = bal * PCT / 100; uv = cap * LEV; ep = ll; pos = "LONG"
                eq_curve.append(bal)
        elif st:
            if pos == "LONG":
                xp = sl; bal += (xp - ep) / ep * uv
                trades.append({"pnl": (xp-ep)/ep*uv, "exit": "FLIP", "dur": tm})
                pos = None; eq_curve.append(bal)
            if bal > 10:
                cap = bal * PCT / 100; uv = cap * LEV; ep = sl; pos = "SHORT"
                eq_curve.append(bal)

    if pos:
        lp = df["close"].iloc[-1]
        bal += (lp - ep) / ep * uv if pos == "LONG" else (ep - lp) / ep * uv
        trades.append({"pnl": (lp-ep)/ep*uv if pos=="LONG" else (ep-lp)/ep*uv, "exit": "END", "dur": df.index[-1]})

    closed = [t for t in trades if t["pnl"] != 0]
    if not closed: return {"tp": tp_pct, "final": INIT, "ret": 0, "dd": 0, "wr": 0, "n": 0}

    eq = pd.Series(eq_curve)
    peak = eq.cummax()
    dd = (eq - peak) / peak * 100
    max_dd = dd.min()
    ret = (bal - INIT) / INIT * 100
    wins = [t for t in closed if t["pnl"] > 0]
    tp_wins = [t for t in closed if t["exit"] == "TP"]
    sl_losses = [t for t in closed if t["exit"] == "SL"]
    wr = len(wins) / len(closed) * 100 if closed else 0
    pain = ret / abs(max_dd) if max_dd != 0 else 0

    return {"tp": tp_pct, "final": round(bal, 2), "ret": round(ret, 2),
            "max_dd": round(max_dd, 2), "wr": round(wr, 1),
            "n_closed": len(closed), "n_wins": len(wins),
            "n_tp": len(tp_wins), "n_sl": len(sl_losses),
            "pain": round(pain, 2),
            "avg_win": round(sum(t["pnl"] for t in wins)/len(wins), 2) if wins else 0,
            "avg_loss": round(sum(t["pnl"] for t in closed if t["pnl"]<0)/len([t for t in closed if t["pnl"]<0]), 2) if [t for t in closed if t["pnl"]<0] else 0}

# Test TP levels
tp_levels = [0, 0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 7, 10]
tp_results = []
for tp in tp_levels:
    r = backtest_with_tp(tp)
    tp_results.append(r)
    label = "No TP" if tp == 0 else "TP={}%".format(tp)
    print("  {} -> ${:,.0f} ({:+.1f}%) DD:{:+.1f}% WR:{:.1f}% Pain:{:.2f} TPwins:{}".format(
        label, r["final"], r["ret"], r["max_dd"], r["wr"], r["pain"], r["n_tp"]))

# Find best
best_tp = max(tp_results, key=lambda r: r["ret"])
best_pain = max(tp_results, key=lambda r: r["pain"])
print("\n  Best return: TP={}% -> ${:,.0f} ({:+.1f}%) DD:{:+.1f}%".format(
    best_tp["tp"], best_tp["final"], best_tp["ret"], best_tp["max_dd"]))
print("  Best pain:   TP={}% -> Pain:{:.2f}".format(best_pain["tp"], best_pain["pain"]))

# ===================================================================
# 2. TIME PATTERNS — Run with best TP and analyze trade timing
# ===================================================================
print("\n" + "=" * 70)
print("  2. TIME PATTERN ANALYSIS (with best TP)")
print("=" * 70)

best_tp_val = best_tp["tp"]

# Re-run with best TP and collect detailed trade data
bal = INIT; pos = None; ep = uv = 0.0
detailed_trades = []

for i in range(START, len(df)):
    hi, lo = df["high"].iloc[i], df["low"].iloc[i]
    tm = df.index[i]

    if pos == "LONG":
        sp = ep * (1 - SL_PCT/100)
        tp = ep * (1 + best_tp_val/100) if best_tp_val > 0 else 999999
        if lo <= sp:
            pnl = (sp - ep) / ep * uv; bal += pnl
            detailed_trades.append({"entry_time": entry_time, "exit_time": tm, "dir": "LONG",
                "entry_px": ep, "exit_px": sp, "pnl": pnl, "exit_type": "SL",
                "entry_hour": entry_time.hour, "entry_dow": entry_time.dayofweek,
                "exit_hour": tm.hour, "exit_dow": tm.dayofweek})
            pos = None; continue
        if hi >= tp:
            pnl = (tp - ep) / ep * uv; bal += pnl
            detailed_trades.append({"entry_time": entry_time, "exit_time": tm, "dir": "LONG",
                "entry_px": ep, "exit_px": tp, "pnl": pnl, "exit_type": "TP",
                "entry_hour": entry_time.hour, "entry_dow": entry_time.dayofweek,
                "exit_hour": tm.hour, "exit_dow": tm.dayofweek})
            pos = None; continue
    if pos == "SHORT":
        sp = ep * (1 + SL_PCT/100)
        tp = ep * (1 - best_tp_val/100) if best_tp_val > 0 else 0
        if hi >= sp:
            pnl = (ep - sp) / ep * uv; bal += pnl
            detailed_trades.append({"entry_time": entry_time, "exit_time": tm, "dir": "SHORT",
                "entry_px": ep, "exit_px": sp, "pnl": pnl, "exit_type": "SL",
                "entry_hour": entry_time.hour, "entry_dow": entry_time.dayofweek,
                "exit_hour": tm.hour, "exit_dow": tm.dayofweek})
            pos = None; continue
        if lo <= tp:
            pnl = (ep - tp) / ep * uv; bal += pnl
            detailed_trades.append({"entry_time": entry_time, "exit_time": tm, "dir": "SHORT",
                "entry_px": ep, "exit_px": tp, "pnl": pnl, "exit_type": "TP",
                "entry_hour": entry_time.hour, "entry_dow": entry_time.dayofweek,
                "exit_hour": tm.hour, "exit_dow": tm.dayofweek})
            pos = None; continue

    pa = atr_sig.iloc[i-1]
    if pd.isna(pa) or pa <= 0: continue
    pc = df["close"].iloc[i-1]; ll, sl = pc + pa, pc - pa
    tb = df["close"].iloc[i-1] > ema50.iloc[i-1]
    tbe = df["close"].iloc[i-1] < ema50.iloc[i-1]
    lt = hi >= ll and tb and pos != "LONG"
    st = lo <= sl and tbe and pos != "SHORT"
    if lt and st:
        if abs(df["open"].iloc[i] - ll) > abs(df["open"].iloc[i] - sl): lt = False
        else: st = False

    if lt:
        if pos == "SHORT":
            xp = ll; bal += (ep - xp) / ep * uv
            detailed_trades.append({"entry_time": entry_time, "exit_time": tm, "dir": "SHORT",
                "entry_px": ep, "exit_px": xp, "pnl": (ep-xp)/ep*uv, "exit_type": "FLIP",
                "entry_hour": entry_time.hour, "entry_dow": entry_time.dayofweek,
                "exit_hour": tm.hour, "exit_dow": tm.dayofweek})
            pos = None
        if bal > 10:
            cap = bal * PCT / 100; uv = cap * LEV; ep = ll; pos = "LONG"; entry_time = tm
    elif st:
        if pos == "LONG":
            xp = sl; bal += (xp - ep) / ep * uv
            detailed_trades.append({"entry_time": entry_time, "exit_time": tm, "dir": "LONG",
                "entry_px": ep, "exit_px": xp, "pnl": (xp-ep)/ep*uv, "exit_type": "FLIP",
                "entry_hour": entry_time.hour, "entry_dow": entry_time.dayofweek,
                "exit_hour": tm.hour, "exit_dow": tm.dayofweek})
            pos = None
        if bal > 10:
            cap = bal * PCT / 100; uv = cap * LEV; ep = sl; pos = "SHORT"; entry_time = tm

tdf = pd.DataFrame(detailed_trades)
closed_t = tdf[tdf["pnl"] != 0].copy()

# By day of week
dow_names = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
print("\n--- PnL by Day of Week ---")
for dow in range(7):
    dt = closed_t[closed_t["entry_dow"] == dow]
    if dt.empty: continue
    pnl = dt["pnl"].sum()
    wins = len(dt[dt["pnl"] > 0])
    total = len(dt)
    print("  {}: {:+.0f}U  WR={:.0f}%  trades={}".format(dow_names[dow], pnl, wins/total*100 if total else 0, total))

# By hour of day (UTC — need to convert to Beijing time = UTC+8)
print("\n--- PnL by Hour (Beijing Time) ---")
closed_t["bj_hour"] = (closed_t["entry_hour"] + 8) % 24
hourly = []
for h in range(24):
    dt = closed_t[closed_t["bj_hour"] == h]
    if dt.empty: continue
    pnl = dt["pnl"].sum()
    wins = len(dt[dt["pnl"] > 0])
    total = len(dt)
    wr = wins/total*100 if total else 0
    hourly.append((h, pnl, wr, total, dt["pnl"].mean()))

# Sort by PnL
hourly.sort(key=lambda x: x[1], reverse=True)
print("  {:>6s} {:>10s} {:>8s} {:>8s} {:>8s}".format("Hour","PnL","WinRate","Trades","AvgPnL"))
for h, pnl, wr, n, avg in hourly[:8]:
    print("  {:>2d}:00   {:>+8.0f}U  {:>5.1f}%  {:>5d}   {:>+6.0f}U".format(h, pnl, wr, n, avg))
print("  ...")
for h, pnl, wr, n, avg in hourly[-4:]:
    print("  {:>2d}:00   {:>+8.0f}U  {:>5.1f}%  {:>5d}   {:>+6.0f}U".format(h, pnl, wr, n, avg))

# By month
print("\n--- PnL by Month ---")
for m in range(1, 13):
    dt = closed_t[closed_t["entry_time"].dt.month == m]
    if dt.empty: continue
    pnl = dt["pnl"].sum()
    wins = len(dt[dt["pnl"] > 0])
    total = len(dt)
    wr = wins/total*100 if total else 0
    print("  Month {:2d}: {:>+8.0f}U  WR={:.0f}%  trades={}".format(m, pnl, wr, total))

# By exit type
print("\n--- Exit Type Analysis ---")
for ext in ["TP", "SL", "FLIP"]:
    dt = closed_t[closed_t["exit_type"] == ext]
    if dt.empty: continue
    pnl = dt["pnl"].sum()
    avg_pnl = dt["pnl"].mean()
    print("  {}: {:+.0f}U total  avg={:+.0f}U  count={}".format(ext, pnl, avg_pnl, len(dt)))

# Trade duration analysis
print("\n--- Trade Duration (bars held) ---")
closed_t["duration"] = (closed_t["exit_time"] - closed_t["entry_time"]).dt.total_seconds() / 900  # 15min bars
wins_t = closed_t[closed_t["pnl"] > 0]
losses_t = closed_t[closed_t["pnl"] < 0]
print("  Win avg duration:  {:.1f} bars ({:.1f} hours)".format(wins_t["duration"].mean(), wins_t["duration"].mean()*0.25))
print("  Loss avg duration: {:.1f} bars ({:.1f} hours)".format(losses_t["duration"].mean(), losses_t["duration"].mean()*0.25))
print("  Max win duration:  {:.0f} bars".format(wins_t["duration"].max()))
print("  Max loss duration: {:.0f} bars".format(losses_t["duration"].max()))

# ===================================================================
# 3. CONSECUTIVE PATTERNS
# ===================================================================
print("\n" + "=" * 70)
print("  3. CONSECUTIVE WIN/LOSS PATTERNS")
print("=" * 70)

pnls = closed_t["pnl"].values
streaks = []
current_streak = 0
current_sign = 1 if pnls[0] > 0 else -1
for p in pnls:
    sign = 1 if p > 0 else -1
    if sign == current_sign:
        current_streak += 1
    else:
        streaks.append((current_sign, current_streak))
        current_sign = sign; current_streak = 1
streaks.append((current_sign, current_streak))

win_streaks = [s[1] for s in streaks if s[0] > 0]
loss_streaks = [s[1] for s in streaks if s[0] < 0]
print("  Max win streak:  {}".format(max(win_streaks) if win_streaks else 0))
print("  Max loss streak: {}".format(max(loss_streaks) if loss_streaks else 0))
print("  Avg win streak:  {:.1f}".format(np.mean(win_streaks) if win_streaks else 0))
print("  Avg loss streak: {:.1f}".format(np.mean(loss_streaks) if loss_streaks else 0))

# PnL after N consecutive losses
print("\n  PnL of next trade after loss streak:")
for n in range(1, 6):
    next_pnls = []
    for i in range(len(streaks) - 1):
        if streaks[i][0] < 0 and streaks[i][1] >= n:
            # Find the first trade of next streak
            idx = sum(s[1] for s in streaks[:i+1])
            if idx < len(pnls):
                next_pnls.append(pnls[idx])
    if next_pnls:
        print("    After {} losses: next avg PnL = {:+.0f}U ({} trades)".format(
            n, np.mean(next_pnls), len(next_pnls)))

# ===================================================================
# 4. VOLATILITY CONTEXT
# ===================================================================
print("\n" + "=" * 70)
print("  4. VOLATILITY CONTEXT (ATR at entry)")
print("=" * 70)

# Map trades to ATR at entry
entry_atrs = []
for _, t in closed_t.iterrows():
    try:
        idx = df.index.get_loc(t["entry_time"])
        if idx > 0:
            a14 = atr14.iloc[idx-1]
            pc = df["close"].iloc[idx-1]
            if not pd.isna(a14) and pc > 0:
                entry_atrs.append({"pnl": t["pnl"], "atr_pct": a14/pc*100})
    except: pass

if entry_atrs:
    ea = pd.DataFrame(entry_atrs)
    # Split by ATR percentile
    ea["atr_bucket"] = pd.cut(ea["atr_pct"], bins=[0, 0.3, 0.5, 0.8, 1.2, 2.0, 100], labels=["<0.3%","0.3-0.5%","0.5-0.8%","0.8-1.2%","1.2-2.0%",">2.0%"])
    print("  PnL by ATR range at entry:")
    for bucket in ["<0.3%","0.3-0.5%","0.5-0.8%","0.8-1.2%","1.2-2.0%",">2.0%"]:
        bt = ea[ea["atr_bucket"] == bucket]
        if bt.empty: continue
        avg_pnl = bt["pnl"].mean()
        wr = len(bt[bt["pnl"]>0])/len(bt)*100
        print("    ATR {}: avg PnL={:+.0f}U  WR={:.0f}%  n={}".format(bucket, avg_pnl, wr, len(bt)))

print("\n" + "=" * 70)
print("  ANALYSIS COMPLETE")
print("=" * 70)
