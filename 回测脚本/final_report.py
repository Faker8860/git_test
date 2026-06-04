"""
Complete Backtest Report - Volty EMA50 | Always-in-market | 3x
Starting capital USD 680 (user's actual balance)
"""
import json, urllib.request, time, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd, numpy as np
from strategy_bot import compute_signals_volty, calc_ma

BINANCE_API = "https://data-api.binance.vision/api/v3/klines"
INITIAL = 680
LEVERAGE = 3
SLIPPAGE = 0.02
FEE = 0.04

def fetch_all(symbol, interval, days):
    all_k, end_ts = [], None
    for i in range(50):
        params = {"symbol": symbol, "interval": interval, "limit": 1000}
        if end_ts: params["endTime"] = end_ts
        url = f"{BINANCE_API}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as r:
            batch = json.loads(r.read().decode())
        if not batch: break
        all_k = batch + all_k
        if all_k and (pd.to_datetime(all_k[-1][0], unit="ms") - pd.to_datetime(all_k[0][0], unit="ms")).days >= days:
            break
        if len(batch) < 1000: break
        end_ts = batch[0][0] - 1; time.sleep(0.08)
    return all_k

def to_df(klines):
    df = pd.DataFrame(klines, columns=["t","o","h","l","c","v","ct","qv","n","tbv","tbqv","ig"])
    df["t"] = pd.to_datetime(df["t"], unit="ms"); df.set_index("t", inplace=True)
    for c in ["o","h","l","c","v"]: df[c] = pd.to_numeric(df[c])
    return df[["o","h","l","c","v"]].rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})[~df.index.duplicated()].sort_index()

def run_backtest(df):
    """Volty EMA50 always-in-market compound 3x"""
    sig = compute_signals_volty(df, 5, 0.75)
    trend_ma = calc_ma(df["close"], "EMA", 50).shift(1)
    trend_up = df["close"] > trend_ma
    tr = pd.concat([df["high"]-df["low"], (df["high"]-df["close"].shift(1)).abs(),
                    (df["low"]-df["close"].shift(1)).abs()], axis=1).max(axis=1)
    atrs = tr.rolling(5).mean() * 0.75
    lsl = (df["close"]+atrs).shift(1); ssl = (df["close"]-atrs).shift(1)

    equity = float(INITIAL)
    pos, ep = None, 0.0
    trades = []; peak = equity; max_dd = 0.0
    total_fees = 0.0

    for i in range(57, len(df)):
        lt = sig["long_cond"].iloc[i] and trend_up.iloc[i]
        st = sig["short_cond"].iloc[i] and (not trend_up.iloc[i])
        if pos is None:
            if lt: pos, ep = "L", lsl.iloc[i] * (1 + SLIPPAGE/100)
            elif st: pos, ep = "S", ssl.iloc[i] * (1 - SLIPPAGE/100)
        elif pos == "L":
            if st:
                exit_fill = ssl.iloc[i] * (1 - SLIPPAGE/100)
                pnl_pct = (exit_fill - ep) / ep
                gross = equity * pnl_pct * LEVERAGE
                fee_cost = equity * LEVERAGE * FEE / 100 * 2
                net = gross - fee_cost
                if equity + net <= 0: net = -equity
                equity += net; trades.append(net)
                total_fees += fee_cost
                peak = max(peak, equity); max_dd = max(max_dd, peak-equity)
                pos, ep = "S", ssl.iloc[i] * (1 - SLIPPAGE/100)
        elif pos == "S":
            if lt:
                exit_fill = lsl.iloc[i] * (1 + SLIPPAGE/100)
                pnl_pct = (ep - exit_fill) / ep
                gross = equity * pnl_pct * LEVERAGE
                fee_cost = equity * LEVERAGE * FEE / 100 * 2
                net = gross - fee_cost
                if equity + net <= 0: net = -equity
                equity += net; trades.append(net)
                total_fees += fee_cost
                peak = max(peak, equity); max_dd = max(max_dd, peak-equity)
                pos, ep = "L", lsl.iloc[i] * (1 + SLIPPAGE/100)

    return trades, equity, max_dd, total_fees, peak

print("="*65)
print("  FINAL BACKTEST: Volty EMA50 | 100% 3x Compound")
print(f"  Starting: USD {INITIAL}")
print("="*65)

print("\n[1] Fetching Binance data...")
df = to_df(fetch_all("ETHUSDT", "15m", 370))
print(f"  {len(df)} bars, {df.index[0]} -> {df.index[-1]}")

now = df.index[-1]
periods = [
    ("Past Week",     now - pd.Timedelta(days=7)),
    ("Past Month",    now - pd.Timedelta(days=30)),
    ("Past 3 Months", now - pd.Timedelta(days=90)),
    ("Past Year",     now - pd.Timedelta(days=365)),
]

for period_name, cutoff in periods:
    df_p = df[df.index >= cutoff]
    if len(df_p) < 100: continue
    trades, eq, dd, fees, peak = run_backtest(df_p)
    wins = sum(1 for t in trades if t > 0)
    n = len(trades)

    # Streaks
    streaks_win = []; streaks_loss = []; curr = 0
    for t in trades:
        if t > 0:
            if curr < 0: streaks_loss.append(curr); curr = 0
            curr += 1
        else:
            if curr > 0: streaks_win.append(curr); curr = 0
            curr -= 1
    if curr > 0: streaks_win.append(curr)
    if curr < 0: streaks_loss.append(curr)

    max_win_streak = max(streaks_win) if streaks_win else 0
    max_loss_streak = abs(min(streaks_loss)) if streaks_loss else 0
    avg_win_streak = np.mean(streaks_win) if streaks_win else 0
    avg_loss_streak = abs(np.mean(streaks_loss)) if streaks_loss else 0

    # Win/Loss by count
    w = sum(1 for t in trades if t > 0)
    l = sum(1 for t in trades if t <= 0)
    avg_w = np.mean([t for t in trades if t > 0]) if w else 0
    avg_l = np.mean([t for t in trades if t <= 0]) if l else 0
    best_t = max(trades) if trades else 0
    worst_t = min(trades) if trades else 0

    # Monthly breakdown if period >= 30 days
    monthly_str = ""
    if "Month" in period_name or "3 Months" in period_name or "Year" in period_name:
        monthly = {}
        cum = INITIAL
        for i, t in enumerate(trades):
            m = df_p.index[57+i].strftime("%Y-%m") if 57+i < len(df_p) else "?"
            monthly[m] = monthly.get(m, 0) + t
        monthly_str = "  Monthly PnL:\n"
        for m in sorted(monthly.keys()):
            monthly_str += f"    {m}: USD {monthly[m]:>+10,.0f}\n"

    print(f"\n{'#'*65}")
    print(f"# {period_name} ({len(df_p)} bars)")
    print(f"{'#'*65}")
    print(f"  Trades:      {n}")
    print(f"  Wins:        {w} ({w/n*100:.1f}%)")
    print(f"  Losses:      {l} ({l/n*100:.1f}%)")
    print(f"  Final:       USD {eq:,.0f}")
    print(f"  PnL:         USD {eq-INITIAL:+,.0f} ({(eq/INITIAL-1)*100:+.1f}%)")
    print(f"  Max DD:      USD {dd:,.0f} ({dd/peak*100:.1f}%)")
    print(f"  Fees:        USD {fees:,.0f}")
    print(f"  Avg Win:     USD {avg_w:+,.0f}")
    print(f"  Avg Loss:    USD {avg_l:+,.0f}")
    print(f"  Best Trade:  USD {best_t:+,.0f}")
    print(f"  Worst Trade: USD {worst_t:+,.0f}")
    print(f"  Max Win Streak:  {max_win_streak} trades")
    print(f"  Max Loss Streak: {max_loss_streak} trades")
    print(f"  Avg Win Streak:  {avg_win_streak:.1f}")
    print(f"  Avg Loss Streak: {avg_loss_streak:.1f}")
    if monthly_str:
        print(monthly_str.rstrip())

print(f"\n{'='*65}")
print(f"  DONE - All numbers based on Binance real 15m K-lines")
print(f"  Strategy: Volty EMA50, always-in-market flip, 3x compound")
print(f"  Starting capital: USD {INITIAL}")
print(f"{'='*65}")
