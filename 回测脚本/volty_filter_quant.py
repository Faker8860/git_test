"""Volty filter comparison - quantitative metrics with fees."""
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

def run_volty_quant(df, trend_filter="SMA50", fee_pct=0.04):
    """Volty backtest with fees, per-trade tracking."""
    from strategy_bot import compute_signals_volty
    sig = compute_signals_volty(df, 5, 0.75)

    if trend_filter == "TEMA50":
        trend_ma = calc_tema(df["close"], 50).shift(1)
    elif trend_filter == "EMA50":
        trend_ma = df["close"].ewm(span=50, adjust=False).mean().shift(1)
    else:
        trend_ma = df["close"].rolling(50).mean().shift(1)

    trend_up = df["close"] > trend_ma

    tr = pd.concat([df["high"]-df["low"],
                    (df["high"]-df["close"].shift(1)).abs(),
                    (df["low"]-df["close"].shift(1)).abs()], axis=1).max(axis=1)
    atrs = tr.rolling(5).mean() * 0.75
    lsl = (df["close"]+atrs).shift(1); ssl = (df["close"]-atrs).shift(1)

    equity = 10000.0; pos = None; ep = 0.0; trades = []
    daily_equity = {}  # for sharpe
    peak = equity; max_dd = 0.0
    total_fees = 0.0

    for i in range(57, len(df)):
        lt = sig["long_cond"].iloc[i] and trend_up.iloc[i]
        st = sig["short_cond"].iloc[i] and (not trend_up.iloc[i])

        if pos is None:
            if lt: pos, ep = "L", lsl.iloc[i]
            elif st: pos, ep = "S", ssl.iloc[i]
        elif pos == "L":
            if st:
                exit_px = ssl.iloc[i]
                gross_pnl = (exit_px - ep) * 1.0
                fee = (ep + exit_px) * fee_pct / 100  # fee on entry+exit notional
                net_pnl = gross_pnl - fee
                total_fees += fee
                equity += net_pnl; trades.append(net_pnl)
                peak = max(peak, equity); max_dd = max(max_dd, peak-equity)
                pos, ep = "S", exit_px
        elif pos == "S":
            if lt:
                exit_px = lsl.iloc[i]
                gross_pnl = (ep - exit_px) * 1.0
                fee = (ep + exit_px) * fee_pct / 100
                net_pnl = gross_pnl - fee
                total_fees += fee
                equity += net_pnl; trades.append(net_pnl)
                peak = max(peak, equity); max_dd = max(max_dd, peak-equity)
                pos, ep = "L", exit_px

        # Track daily equity
        day = df.index[i].date()
        if day not in daily_equity:
            daily_equity[day] = equity

    # Calculate daily returns for Sharpe
    daily_vals = sorted(daily_equity.items())
    daily_rets = []
    for i in range(1, len(daily_vals)):
        daily_rets.append((daily_vals[i][1] / daily_vals[i-1][1] - 1) * 100)

    daily_rets = np.array(daily_rets)
    if len(daily_rets) > 0 and daily_rets.std() > 0:
        sharpe = np.sqrt(365) * daily_rets.mean() / daily_rets.std()
    else:
        sharpe = 0

    # Calmar ratio
    calmar = (equity/10000 - 1) * 100 / (max_dd/10000 * 100) if max_dd > 0 else float('inf')

    wins = sum(1 for t in trades if t > 0); n = len(trades)
    return {
        "filter": trend_filter, "final": equity, "trades": n, "wins": wins,
        "wr": wins/n*100 if n else 0, "max_dd": max_dd, "max_dd_pct": max_dd/peak*100,
        "pnl_net": equity-10000, "ret": (equity/10000-1)*100,
        "avg_win": np.mean([t for t in trades if t>0]) if wins else 0,
        "avg_loss": np.mean([t for t in trades if t<0]) if n-wins else 0,
        "total_fees": total_fees, "fee_per_trade": total_fees/n if n else 0,
        "sharpe": sharpe, "calmar": calmar,
        "daily_ret_mean": daily_rets.mean() if len(daily_rets) else 0,
        "daily_ret_std": daily_rets.std() if len(daily_rets) else 0,
        "profit_factor": sum(t for t in trades if t>0)/abs(sum(t for t in trades if t<0)) if sum(t for t in trades if t<0)!=0 else float('inf'),
    }

print("=" * 72)
print("  Volty Filter - Quantitative Analysis (with 0.04% fees)")
print("=" * 72)

print("\n[1] Fetching full year data...")
df = to_df(fetch_all("ETHUSDT", "15m", 370))
print(f"  {len(df)} bars")

# Test all filters
filters = ["SMA50", "EMA50", "TEMA50"]
results = {}
for f in filters:
    print(f"  Running {f}...")
    results[f] = run_volty_quant(df[df.index >= df.index[-1] - pd.Timedelta(days=370)], f)
    print(f"    {results[f]['trades']} trades, net PnL=USD {results[f]['pnl_net']:+,.0f}")

# Period comparison with fees
now = df.index[-1]
periods = [
    ("Past Week",     now - pd.Timedelta(days=7)),
    ("Past Month",    now - pd.Timedelta(days=30)),
    ("Past 3 Months", now - pd.Timedelta(days=90)),
]
period_results = {}
for period_name, cutoff in periods:
    df_p = df[df.index >= cutoff]
    period_results[period_name] = {}
    for f in filters:
        period_results[period_name][f] = run_volty_quant(df_p, f)

# ============== REPORT ==============
print(f"\n{'='*85}")
print(f"  QUANTITATIVE COMPARISON - All values include 0.04% fees")
print(f"{'='*85}")

# Main metrics table
print(f"\n  {'Metric':<22} {'SMA50':>14} {'EMA50':>14} {'TEMA50':>14} {'Best':>8}")
print(f"  {'-'*74}")
metrics = [
    ("Net PnL (USD)",        "pnl_net",       "+.0f"),
    ("Return %",             "ret",           "+.1f"),
    ("Total Fees (USD)",     "total_fees",    ".0f"),
    ("Trades",               "trades",        ".0f"),
    ("Win Rate %",           "wr",            ".1f"),
    ("Profit Factor",        "profit_factor", ".2f"),
    ("Avg Win (USD)",        "avg_win",       "+.0f"),
    ("Avg Loss (USD)",       "avg_loss",      "+.0f"),
    ("Max DD (USD)",         "max_dd",        ".0f"),
    ("Max DD %",             "max_dd_pct",    ".2f"),
    ("Sharpe Ratio",         "sharpe",        ".2f"),
    ("Calmar Ratio",         "calmar",        ".2f"),
    ("Daily Ret Mean %",     "daily_ret_mean", ".3f"),
    ("Daily Ret Std %",      "daily_ret_std",  ".3f"),
    ("PnL per Trade (USD)",  None,            ".2f"),
]

for label, key, fmt in metrics:
    print(f"  {label:<22}", end="")
    best_val = -999999
    best_name = ""
    vals = {}
    for f in filters:
        if key is None:  # computed metric
            v = results[f]["pnl_net"] / results[f]["trades"]
        else:
            v = results[f][key]
        vals[f] = v
        # Higher is better for most metrics, except DD/std
        if key in ("max_dd", "max_dd_pct", "daily_ret_std", "total_fees", "trades"):
            if best_val == -999999 or v < best_val:
                best_val = v; best_name = f
        else:
            if v > best_val:
                best_val = v; best_name = f

    for f in filters:
        print(f" {vals[f]:>{fmt.replace('+','').replace('.','').replace('f','')}}", end="")
        # Actually format properly
        v = vals[f]
        if fmt == "+.0f": s = f"USD {v:>+7.0f}"
        elif fmt == "+.1f": s = f"{v:>+11.1f}%"
        elif fmt == ".0f": s = f"USD {v:>7.0f}"
        elif fmt == ".1f": s = f"{v:>11.1f}%"
        elif fmt == ".2f": s = f"{v:>11.2f}"
        elif fmt == ".3f": s = f"{v:>11.3f}%"
        else: s = f"{v:>11}"
        # Redo properly
    # Let me just print simply

print()
# Simpler print
for f in filters:
    r = results[f]
    print(f"  [{f}]")
    print(f"    Net PnL:        USD {r['pnl_net']:>+10,.0f}  ({r['ret']:>+.1f}%)")
    print(f"    Trades:         {r['trades']:>10,}  (fees: USD {r['total_fees']:,.0f})")
    print(f"    Win Rate:       {r['wr']:>10.1f}%")
    print(f"    Profit Factor:  {r['profit_factor']:>10.2f}")
    print(f"    Avg Win/Loss:   USD {r['avg_win']:>+8.0f} / USD {r['avg_loss']:>+8.0f}")
    print(f"    Max DD:         USD {r['max_dd']:>10,.0f}  ({r['max_dd_pct']:.2f}%)")
    print(f"    Sharpe:         {r['sharpe']:>10.2f}")
    print(f"    Calmar:         {r['calmar']:>10.2f}")
    print(f"    PnL/Trade:      USD {r['pnl_net']/r['trades']:>+10.2f}")
    print(f"    Daily Ret:      {r['daily_ret_mean']:>+10.3f}% mean, {r['daily_ret_std']:.3f}% std")
    print()

# Period table
print(f"  {'='*70}")
print(f"  PERIOD RETURNS (net of fees)")
print(f"  {'Period':<16} {'SMA50':>14} {'EMA50':>14} {'TEMA50':>14}")
print(f"  {'-'*60}")
for period_name in ["Past Week", "Past Month", "Past 3 Months"]:
    print(f"  {period_name:<16}", end="")
    for f in filters:
        pr = period_results[period_name][f]
        print(f" USD {pr['pnl_net']:>+7.0f} ", end="")
    print()

# Overall ranking
print(f"\n  {'='*70}")
print(f"  RANKING (weighted score)")
print(f"  {'='*70}")

# Score: normalize each metric 0-100, weight: PnL 30%, Sharpe 25%, Calmar 20%, PF 15%, Fees -10%
weights = {"pnl_net": 0.30, "sharpe": 0.25, "calmar": 0.20, "profit_factor": 0.15,
           "max_dd_pct": -0.10}
scores = {}
for f in filters:
    scores[f] = 0

for metric, weight in weights.items():
    vals = {f: results[f][metric] for f in filters}
    # For negative weight (lower is better), invert
    if weight < 0:
        best = min(vals.values()); worst = max(vals.values())
    else:
        best = max(vals.values()); worst = min(vals.values())
    spread = best - worst
    for f in filters:
        if spread > 0:
            if weight < 0:
                normalized = (worst - vals[f]) / spread  # lower = better
            else:
                normalized = (vals[f] - worst) / spread  # higher = better
        else:
            normalized = 0.5
        scores[f] += normalized * abs(weight) * 100

for f in filters:
    print(f"  {f}: score = {scores[f]:.1f}")
best = max(scores, key=scores.get)
print(f"\n  >>> BEST for quantitative trading: {best} <<<")
