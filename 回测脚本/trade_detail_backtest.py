"""
Trade-by-trade detail backtest - Fixed 1 ETH, both strategies.
Outputs every trade to CSV for verification.
"""
import os, sys, json, urllib.request, time
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import pandas as pd
import numpy as np
from datetime import datetime

BINANCE_API = "https://data-api.binance.vision/api/v3/klines"

def fetch_klines(symbol, interval, limit=1000, end_time=None):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if end_time: params["endTime"] = end_time
    url = f"{BINANCE_API}?{urllib.parse.urlencode(params)}"
    for _ in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as r:
                return json.loads(r.read().decode())
        except: time.sleep(1)
    return []

def fetch_all(symbol, interval, days):
    all_k, end_ts = [], None
    for i in range(60):
        b = fetch_klines(symbol, interval, 1000, end_ts)
        if not b: break
        all_k = b + all_k
        if all_k:
            e = pd.to_datetime(all_k[0][0], unit="ms")
            l = pd.to_datetime(all_k[-1][0], unit="ms")
            if (l - e).days >= days: break
        if len(b) < 1000: break
        end_ts = b[0][0] - 1; time.sleep(0.08)
    return all_k

def to_df(klines):
    df = pd.DataFrame(klines, columns=["t","o","h","l","c","v","ct","qv","n","tbv","tbqv","ig"])
    df["t"] = pd.to_datetime(df["t"], unit="ms")
    df.set_index("t", inplace=True)
    for c in ["o","h","l","c","v"]: df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["o","h","l","c","v"]].rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})[~df.index.duplicated()].sort_index()

# ============================================================
# OCC DETAILED TRADES
# ============================================================
def occ_trades_detail(df):
    from strategy_bot import compute_signals
    sig = compute_signals(df, "TEMA", 8, 3, 15, 0, 5)
    pos, ep, trades = None, 0.0, []
    for i in range(80, len(sig)):
        lt = sig["long_cond"].iloc[i]; st = sig["short_cond"].iloc[i]
        cp = df["close"].iloc[i]; ts = df.index[i]
        if pos is None:
            if lt: pos, ep, ets = "LONG", cp, ts
            elif st: pos, ep, ets = "SHORT", cp, ts
        elif pos == "LONG":
            if st:
                pnl = (cp - ep) * 1.0
                trades.append({"entry_time": ets, "exit_time": ts, "dir": "LONG",
                               "entry_px": round(ep,2), "exit_px": round(cp,2), "pnl": round(pnl,2)})
                pos, ep, ets = "SHORT", cp, ts
        elif pos == "SHORT":
            if lt:
                pnl = (ep - cp) * 1.0
                trades.append({"entry_time": ets, "exit_time": ts, "dir": "SHORT",
                               "entry_px": round(ep,2), "exit_px": round(cp,2), "pnl": round(pnl,2)})
                pos, ep, ets = "LONG", cp, ts
    return trades

# ============================================================
# VOLTY DETAILED TRADES
# ============================================================
def volty_trades_detail(df):
    from strategy_bot import compute_signals_volty
    sig = compute_signals_volty(df, 5, 0.75)
    sma50 = df["close"].rolling(50).mean().shift(1)
    trend_up = df["close"] > sma50
    tr = pd.concat([df["high"]-df["low"],
                    (df["high"]-df["close"].shift(1)).abs(),
                    (df["low"]-df["close"].shift(1)).abs()], axis=1).max(axis=1)
    atrs = tr.rolling(5).mean() * 0.75
    lsl = (df["close"]+atrs).shift(1); ssl = (df["close"]-atrs).shift(1)
    pos, ep, trades = None, 0.0, []
    for i in range(57, len(df)):
        lt = sig["long_cond"].iloc[i] and trend_up.iloc[i]
        st = sig["short_cond"].iloc[i] and (not trend_up.iloc[i])
        ts = df.index[i]
        if pos is None:
            if lt: pos, ep, ets = "LONG", lsl.iloc[i], ts
            elif st: pos, ep, ets = "SHORT", ssl.iloc[i], ts
        elif pos == "LONG":
            if st:
                pnl = (ssl.iloc[i] - ep) * 1.0
                trades.append({"entry_time": ets, "exit_time": ts, "dir": "LONG",
                               "entry_px": round(ep,2), "exit_px": round(ssl.iloc[i],2), "pnl": round(pnl,2)})
                pos, ep, ets = "SHORT", ssl.iloc[i], ts
        elif pos == "SHORT":
            if lt:
                pnl = (ep - lsl.iloc[i]) * 1.0
                trades.append({"entry_time": ets, "exit_time": ts, "dir": "SHORT",
                               "entry_px": round(ep,2), "exit_px": round(lsl.iloc[i],2), "pnl": round(pnl,2)})
                pos, ep, ets = "LONG", lsl.iloc[i], ts
    return trades

# ============================================================
print("="*70)
print("  TRADE-BY-TRADE BACKTEST - Fixed 1 ETH per trade")
print("="*70)

print("\n[1] Fetching Binance ETHUSDT 15m data...")
klines = fetch_all("ETHUSDT", "15m", 370)
df = to_df(klines)
print(f"  {len(df)} bars, {df.index[0]} -> {df.index[-1]}")

now = df.index[-1]
periods = {
    "Past_Week": now - pd.Timedelta(days=7),
    "Past_Month": now - pd.Timedelta(days=30),
    "Past_3Months": now - pd.Timedelta(days=90),
    "Past_Year": now - pd.Timedelta(days=365),
}

# Generate trades for full year
print("\n[2] Computing OCC trades...")
occ_all = occ_trades_detail(df[df.index >= now - pd.Timedelta(days=370)])
print(f"  OCC: {len(occ_all)} total trades")

print("\n[3] Computing Volty trades...")
volty_all = volty_trades_detail(df[df.index >= now - pd.Timedelta(days=370)])
print(f"  Volty: {len(volty_all)} total trades")

# Export all trades to CSV
occ_df = pd.DataFrame(occ_all)
occ_df.insert(0, "trade_no", range(1, len(occ_df)+1))
occ_df.to_csv(os.path.join(BASE_DIR, "OCC_v8.13_每笔交易明细.csv"), index=False, encoding="utf-8-sig")

volty_df = pd.DataFrame(volty_all)
volty_df.insert(0, "trade_no", range(1, len(volty_df)+1))
volty_df.to_csv(os.path.join(BASE_DIR, "Volty_每笔交易明细.csv"), index=False, encoding="utf-8-sig")

print(f"\n[4] Exported:")
print(f"  OCC_v8.13_每笔交易明细.csv  ({len(occ_df)} trades)")
print(f"  Volty_每笔交易明细.csv      ({len(volty_df)} trades)")

# ============================================================
# Print period-by-period with trade details
# ============================================================
for period_name, cutoff in periods.items():
    print(f"\n{'#'*85}")
    print(f"# {period_name.replace('_',' ')}")
    print(f"{'#'*85}")

    for strat_name, all_trades in [("OCC v8.13 (TEMA)", occ_all), ("Volty Expan Close", volty_all)]:
        period_label = period_name.replace("_", " ")
        pt = [t for t in all_trades if t["exit_time"] >= cutoff]
        if not pt:
            print(f"\n  [{strat_name}]: No trades")
            continue

        cum = 0.0; wins = sum(1 for t in pt if t["pnl"] > 0)
        total = sum(t["pnl"] for t in pt)

        print(f"\n  [{strat_name}]")
        print(f"  {'─'*80}")
        print(f"  {'#':<6} {'入场时间':<20} {'出场时间':<20} {'方向':<7} {'入场价':>9} {'出场价':>9} {'PnL($)':>9} {'累计($)':>10}")
        print(f"  {'─'*80}")

        for i, t in enumerate(pt):
            cum += t["pnl"]
            print(f"  {i+1:<6} {str(t['entry_time'])[:19]:<20} {str(t['exit_time'])[:19]:<20} "
                  f"{t['dir']:<7} {t['entry_px']:>9.2f} {t['exit_px']:>9.2f} "
                  f"{t['pnl']:>+9.2f} {cum:>+10.2f}")

        print(f"  {'─'*80}")
        print(f"  TOTAL: {len(pt)} trades, Wins={wins}/{len(pt)} ({wins/len(pt)*100:.1f}%), "
              f"PnL=${total:+,.2f}, MaxWin=${max(t['pnl'] for t in pt):+,.2f}, "
              f"MaxLoss=${min(t['pnl'] for t in pt):+,.2f}")

    print()

print(f"\n{'='*70}")
print(f"  Done! Check CSV files for complete trade history.")
print(f"{'='*70}")
