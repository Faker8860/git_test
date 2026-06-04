"""Generate complete trade history table - Volty EMA50, 100% 3x compound."""
import os, sys, json, urllib.request, time
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
import pandas as pd, numpy as np
from strategy_bot import compute_signals_volty, calc_ma

BINANCE_API = "https://data-api.binance.vision/api/v3/klines"

def fetch(symbol, interval, limit=1000, end=None):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if end: params["endTime"] = end
    url = f"{BINANCE_API}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as r:
        return json.loads(r.read().decode())

print("[1] Fetching 1 year of ETHUSDT 15m data...")
all_k, end_ts = [], None
for i in range(40):
    b = fetch("ETHUSDT", "15m", 1000, end_ts)
    if not b: break
    all_k = b + all_k
    if all_k and (pd.to_datetime(all_k[-1][0], unit="ms") - pd.to_datetime(all_k[0][0], unit="ms")).days >= 370:
        break
    if len(b) < 1000: break
    end_ts = b[0][0] - 1; time.sleep(0.08)

df = pd.DataFrame(all_k, columns=["t","o","h","l","c","v","ct","qv","n","tbv","tbqv","ig"])
df["t"] = pd.to_datetime(df["t"], unit="ms"); df.set_index("t", inplace=True)
for c in ["o","h","l","c","v"]: df[c] = pd.to_numeric(df[c])
df = df[["o","h","l","c","v"]].rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
df = df[~df.index.duplicated()].sort_index()
print(f"  {len(df)} bars: {df.index[0]} -> {df.index[-1]}")

# Config
INITIAL = 686.20  # user's actual capital
LEVERAGE = 3
SLIPPAGE = 0.02   # 0.02%
FEE = 0.04        # 0.04%

print("[2] Running Volty EMA50, 100% pos, 3x compound...")
sig = compute_signals_volty(df, 5, 0.75)
trend_ma = calc_ma(df["close"], "EMA", 50).shift(1)
trend_up = df["close"] > trend_ma

tr = pd.concat([df["high"]-df["low"],
                (df["high"]-df["close"].shift(1)).abs(),
                (df["low"]-df["close"].shift(1)).abs()], axis=1).max(axis=1)
atrs = tr.rolling(5).mean() * 0.75
lsl = (df["close"]+atrs).shift(1); ssl = (df["close"]-atrs).shift(1)

equity = float(INITIAL)
pos = None; ep = 0.0; trades = []
peak = equity; max_dd = 0.0
total_fees = 0.0; total_slip = 0.0

for i in range(57, len(df)):
    lt = sig["long_cond"].iloc[i] and trend_up.iloc[i]
    st = sig["short_cond"].iloc[i] and (not trend_up.iloc[i])

    if pos is None:
        if lt:
            fill = lsl.iloc[i] * (1 + SLIPPAGE/100)
            pos, ep = "LONG", fill
        elif st:
            fill = ssl.iloc[i] * (1 - SLIPPAGE/100)
            pos, ep = "SHORT", fill
    elif pos == "LONG":
        if st:
            exit_fill = ssl.iloc[i] * (1 - SLIPPAGE/100)
            pnl_pct = (exit_fill - ep) / ep
            gross = equity * pnl_pct * LEVERAGE
            fee_cost = equity * LEVERAGE * FEE / 100 * 2
            slip_cost = abs(ssl.iloc[i] - exit_fill)
            net = gross - fee_cost
            if equity + net <= 0: net = -equity
            equity += net
            total_fees += fee_cost; total_slip += slip_cost
            peak = max(peak, equity); max_dd = max(max_dd, peak-equity)
            trades.append({
                "trade_no": len(trades)+1,
                "entry_time": str(df.index[i])[:19],
                "exit_time": str(df.index[i])[:19],
                "direction": "LONG",
                "entry_price": round(ep, 2),
                "exit_price": round(exit_fill, 2),
                "ideal_exit": round(ssl.iloc[i], 2),
                "pnl_pct": round(pnl_pct*100, 3),
                "pnl_usd": round(net, 2),
                "fee_usd": round(fee_cost, 2),
                "slip_usd": round(slip_cost, 2),
                "equity": round(equity, 2),
                "leverage_pnl": f"{round(pnl_pct*LEVERAGE*100, 2)}%"
            })
            fill = ssl.iloc[i] * (1 - SLIPPAGE/100)
            pos, ep = "SHORT", fill
    elif pos == "SHORT":
        if lt:
            exit_fill = lsl.iloc[i] * (1 + SLIPPAGE/100)
            pnl_pct = (ep - exit_fill) / ep
            gross = equity * pnl_pct * LEVERAGE
            fee_cost = equity * LEVERAGE * FEE / 100 * 2
            slip_cost = abs(exit_fill - lsl.iloc[i])
            net = gross - fee_cost
            if equity + net <= 0: net = -equity
            equity += net
            total_fees += fee_cost; total_slip += slip_cost
            peak = max(peak, equity); max_dd = max(max_dd, peak-equity)
            trades.append({
                "trade_no": len(trades)+1,
                "entry_time": str(df.index[i])[:19],
                "exit_time": str(df.index[i])[:19],
                "direction": "SHORT",
                "entry_price": round(ep, 2),
                "exit_price": round(exit_fill, 2),
                "ideal_exit": round(lsl.iloc[i], 2),
                "pnl_pct": round(pnl_pct*100, 3),
                "pnl_usd": round(net, 2),
                "fee_usd": round(fee_cost, 2),
                "slip_usd": round(slip_cost, 2),
                "equity": round(equity, 2),
                "leverage_pnl": f"{round(pnl_pct*LEVERAGE*100, 2)}%"
            })
            fill = lsl.iloc[i] * (1 + SLIPPAGE/100)
            pos, ep = "LONG", fill

tdf = pd.DataFrame(trades)
print(f"  {len(tdf)} trades generated")

# Add cumulative and drawdown columns
tdf["cum_pnl"] = tdf["pnl_usd"].cumsum().round(2)
tdf["return_pct"] = ((tdf["equity"] / INITIAL - 1) * 100).round(2)
tdf["dd_pct"] = 0.0
running_peak = INITIAL
for i in range(len(tdf)):
    running_peak = max(running_peak, tdf.iloc[i]["equity"])
    tdf.at[i, "dd_pct"] = round((running_peak - tdf.iloc[i]["equity"]) / running_peak * 100, 2)

# Monthly summary
tdf["month"] = pd.to_datetime(tdf["exit_time"]).dt.strftime("%Y-%m")
monthly = tdf.groupby("month").agg(
    trades=("pnl_usd", "count"),
    pnl=("pnl_usd", "sum"),
    wins=("pnl_usd", lambda x: (x > 0).sum()),
    start_equity=("equity", "first"),
    end_equity=("equity", "last")
).round(2)

# Export
csv_path = os.path.join(BASE_DIR, "回测结果", "Volty_EMA50_实盘模拟交易明细.csv")
tdf.to_csv(csv_path, index=False, encoding="utf-8-sig")
print(f"\n[3] Exported: {csv_path}")

# Print summary
wins = (tdf["pnl_usd"] > 0).sum()
n = len(tdf)
print(f"\n{'='*75}")
print(f"  Volty EMA50 | 100%仓位 3x杠杆 | 初始 USD {INITIAL:,.2f}")
print(f"  {df.index[0]} -> {df.index[-1]}")
print(f"{'='*75}")
print(f"  总交易: {n} 笔")
print(f"  盈利:   {wins} 笔 ({wins/n*100:.1f}%)")
print(f"  亏损:   {n-wins} 笔 ({(n-wins)/n*100:.1f}%)")
print(f"  最终资金: USD {equity:,.2f}")
print(f"  总收益:   {(equity/INITIAL-1)*100:+.2f}%")
print(f"  最大回撤: {max_dd/peak*100:.2f}%")
print(f"  手续费:   USD {total_fees:,.2f}")
print(f"  滑点:     USD {total_slip:,.2f}")

print(f"\n  月度明细:")
print(f"  {'Month':<10} {'Trades':>6} {'PnL($)':>10} {'Wins':>5} {'Win%':>6} {'Start$':>10} {'End$':>10}")
print(f"  {'-'*65}")
for m, row in monthly.iterrows():
    wr = row["wins"]/row["trades"]*100 if row["trades"] else 0
    print(f"  {m:<10} {int(row['trades']):>6} USD {row['pnl']:>+8,.0f} {int(row['wins']):>5} {wr:>5.1f}% USD {row['start_equity']:>8,.0f} USD {row['end_equity']:>8,.0f}")

# Print first 10 and last 10 trades
print(f"\n  FIRST 10 TRADES:")
print(f"  {'#':<5} {'Entry':<20} {'Exit':<20} {'Dir':<7} {'Entry$':>9} {'Exit$':>9} {'PnL($)':>9} {'Equity':>10}")
for _, t in tdf.head(10).iterrows():
    print(f"  {t['trade_no']:<5} {t['entry_time']:<20} {t['exit_time']:<20} {t['direction']:<7} "
          f"{t['entry_price']:>9.2f} {t['exit_price']:>9.2f} {t['pnl_usd']:>+9.2f} {t['equity']:>10.2f}")

print(f"\n  ... ({n-20} trades omitted) ...")

print(f"\n  LAST 10 TRADES:")
for _, t in tdf.tail(10).iterrows():
    print(f"  {t['trade_no']:<5} {t['entry_time']:<20} {t['exit_time']:<20} {t['direction']:<7} "
          f"{t['entry_price']:>9.2f} {t['exit_price']:>9.2f} {t['pnl_usd']:>+9.2f} {t['equity']:>10.2f}")

print(f"\n  完整明细: {csv_path}")
