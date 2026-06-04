"""Volty: SMA50 vs TEMA50 trend filter comparison."""
import os, sys, json, urllib.request, time
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
import pandas as pd, numpy as np

BINANCE_API = "https://data-api.binance.vision/api/v3/klines"

def fetch(symbol, interval, limit=1000, end=None):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if end: params["endTime"] = end
    url = f"{BINANCE_API}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as r:
        return json.loads(r.read().decode())

def fetch_all(symbol, interval, days):
    all_k, end_ts = [], None
    for i in range(60):
        b = fetch(symbol, interval, 1000, end_ts)
        if not b: break
        all_k = b + all_k
        if all_k:
            e = pd.to_datetime(all_k[0][0], unit="ms")
            if (pd.to_datetime(all_k[-1][0], unit="ms") - e).days >= days: break
        if len(b) < 1000: break
        end_ts = b[0][0] - 1; time.sleep(0.08)
    return all_k

def to_df(klines):
    df = pd.DataFrame(klines, columns=["t","o","h","l","c","v","ct","qv","n","tbv","tbqv","ig"])
    df["t"] = pd.to_datetime(df["t"], unit="ms")
    df.set_index("t", inplace=True)
    for c in ["o","h","l","c","v"]: df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["o","h","l","c","v"]].rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})[~df.index.duplicated()].sort_index()

def calc_tema(series, length):
    e1 = series.ewm(span=length, adjust=False).mean()
    e2 = e1.ewm(span=length, adjust=False).mean()
    e3 = e2.ewm(span=length, adjust=False).mean()
    return 3.0 * e1 - 3.0 * e2 + e3

def run_volty(df, trend_filter="SMA50"):
    """Volty backtest with specified trend filter. 1 ETH fixed."""
    from strategy_bot import compute_signals_volty

    sig = compute_signals_volty(df, 5, 0.75)

    if trend_filter == "TEMA50":
        trend_ma = calc_tema(df["close"], 50).shift(1)
    elif trend_filter == "EMA50":
        trend_ma = df["close"].ewm(span=50, adjust=False).mean().shift(1)
    else:  # SMA50
        trend_ma = df["close"].rolling(50).mean().shift(1)

    trend_up = df["close"] > trend_ma

    # ATR stop levels
    tr = pd.concat([df["high"]-df["low"],
                    (df["high"]-df["close"].shift(1)).abs(),
                    (df["low"]-df["close"].shift(1)).abs()], axis=1).max(axis=1)
    atrs = tr.rolling(5).mean() * 0.75
    lsl = (df["close"]+atrs).shift(1); ssl = (df["close"]-atrs).shift(1)

    equity = 10000.0; pos = None; ep = 0.0; trades = []
    peak = equity; max_dd = 0.0

    for i in range(57, len(df)):
        lt = sig["long_cond"].iloc[i] and trend_up.iloc[i]
        st = sig["short_cond"].iloc[i] and (not trend_up.iloc[i])
        if pos is None:
            if lt: pos, ep = "L", lsl.iloc[i]
            elif st: pos, ep = "S", ssl.iloc[i]
        elif pos == "L":
            if st:
                pnl = (ssl.iloc[i] - ep) * 1.0
                equity += pnl; trades.append(pnl)
                peak = max(peak, equity); max_dd = max(max_dd, peak-equity)
                pos, ep = "S", ssl.iloc[i]
        elif pos == "S":
            if lt:
                pnl = (ep - lsl.iloc[i]) * 1.0
                equity += pnl; trades.append(pnl)
                peak = max(peak, equity); max_dd = max(max_dd, peak-equity)
                pos, ep = "L", lsl.iloc[i]

    wins = sum(1 for t in trades if t > 0); n = len(trades)
    return {
        "final": equity, "trades": n, "wins": wins,
        "wr": wins/n*100 if n else 0, "max_dd": max_dd,
        "pnl": equity-10000, "ret": (equity/10000-1)*100,
        "avg_win": np.mean([t for t in trades if t>0]) if wins else 0,
        "avg_loss": np.mean([t for t in trades if t<0]) if n-wins else 0,
        "trades_list": trades,
        "equity_curve": [10000] + list(np.cumsum(trades)+10000),
    }

print("=" * 70)
print("  Volty Trend Filter Comparison: SMA50 vs EMA50 vs TEMA50")
print("=" * 70)

print("\n[1] Fetching ETHUSDT 15m data (365 days)...")
df = to_df(fetch_all("ETHUSDT", "15m", 370))
print(f"  {len(df)} bars, {df.index[0]} -> {df.index[-1]}")

now = df.index[-1]
periods = [
    ("Past Week",     now - pd.Timedelta(days=7)),
    ("Past Month",    now - pd.Timedelta(days=30)),
    ("Past 3 Months", now - pd.Timedelta(days=90)),
    ("Past Year",     now - pd.Timedelta(days=365)),
]

results = {}
for filt in ["SMA50", "EMA50", "TEMA50"]:
    print(f"\n[2] Running Volty with {filt} filter...")
    df_full = df[df.index >= now - pd.Timedelta(days=370)]
    r = run_volty(df_full, filt)
    r["filter"] = filt

    # Monthly breakdown
    monthly = {}
    cum = 10000.0
    for i, t in enumerate(r["trades_list"]):
        m = df_full.index[57+i].strftime("%Y-%m") if 57+i < len(df_full) else "?"
        monthly[m] = monthly.get(m, 0) + t

    r["monthly"] = monthly
    results[filt] = r

# Period comparison
print(f"\n{'='*75}")
print(f"  PERIOD COMPARISON (fixed 1 ETH)")
print(f"{'='*75}")
print(f"  {'Period':<16} {'SMA50':>12} {'EMA50':>12} {'TEMA50':>12}")
print(f"  {'-'*54}")

for period_name, cutoff in periods:
    df_p = df[df.index >= cutoff]
    print(f"  {period_name:<16}", end="")
    for filt in ["SMA50", "EMA50", "TEMA50"]:
        r = run_volty(df_p, filt)
        print(f" USD{r['pnl']:>+8.0f} ", end="")
    print()

# Monthly breakdown
print(f"\n{'='*75}")
print(f"  MONTHLY BREAKDOWN (past year)")
print(f"{'='*75}")
print(f"  {'Month':<10} {'SMA50':>10} {'EMA50':>10} {'TEMA50':>10} {'Best':>8}")
print(f"  {'-'*50}")

all_months = sorted(set().union(*[results[f]["monthly"].keys() for f in results]))
sma_tot = ema_tot = tema_tot = 0
for m in all_months:
    s = results["SMA50"]["monthly"].get(m, 0)
    e = results["EMA50"]["monthly"].get(m, 0)
    t = results["TEMA50"]["monthly"].get(m, 0)
    sma_tot += s; ema_tot += e; tema_tot += t
    best = max(s, e, t)
    best_name = "SMA" if best==s else ("EMA" if best==e else "TEMA")
    print(f"  {m:<10} USD {s:>+7.0f}  USD {e:>+7.0f}  USD {t:>+7.0f}  {best_name:>6}")
    if best == t: print(f"           ^^^ TEMA wins!")

# Final summary
print(f"\n{'='*75}")
print(f"  FULL YEAR SUMMARY")
print(f"{'='*75}")
for filt in ["SMA50", "EMA50", "TEMA50"]:
    r = results[filt]
    print(f"\n  [{filt}]")
    print(f"    Final:   USD {r['final']:,.0f}  (return {r['ret']:+.1f}%)")
    print(f"    Trades:  {r['trades']}  |  WinRate: {r['wr']:.1f}%")
    print(f"    AvgW/L:  USD {r['avg_win']:+.0f} / USD {r['avg_loss']:+.0f}")
    print(f"    MaxDD:   USD {r['max_dd']:,.0f}")
    print(f"    PnL:     USD {r['pnl']:+,.0f}")

# Winner
best_filt = max(results, key=lambda f: results[f]["pnl"])
print(f"\n  WINNER: {best_filt} (USD {results[best_filt]['pnl']:+,.0f})")

# Compare signal frequency
for filt in ["SMA50", "EMA50", "TEMA50"]:
    r = results[filt]
    print(f"  {filt}: {r['trades']} trades (~{r['trades']/12:.0f}/month)")

print(f"\n  Note: TEMA50 is more responsive than SMA50, may reduce lag")
print(f"  but can also introduce more noise/false signals.")
