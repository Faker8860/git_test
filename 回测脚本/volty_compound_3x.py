"""
Volty EMA50 — 100%仓位 3x杠杆 复利回测
含手续费+滑点
"""
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
    for c in ["o","h","l","c","v"]: df[c] = pd.to_numeric(df[c])
    return df[["o","h","l","c","v"]].rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})[~df.index.duplicated()].sort_index()

from strategy_bot import compute_signals_volty, calc_ma

def run_volty_compound(df, initial=1000, leverage=3, slippage=0.02, fee=0.04):
    """100%仓位 3x杠杆 复利 — 含滑点和手续费"""
    sig = compute_signals_volty(df, 5, 0.75)
    trend_ma = calc_ma(df["close"], "EMA", 50).shift(1)
    trend_up = df["close"] > trend_ma

    tr = pd.concat([df["high"]-df["low"],
                    (df["high"]-df["close"].shift(1)).abs(),
                    (df["low"]-df["close"].shift(1)).abs()], axis=1).max(axis=1)
    atrs = tr.rolling(5).mean() * 0.75
    lsl = (df["close"]+atrs).shift(1); ssl = (df["close"]-atrs).shift(1)

    equity = float(initial)
    pos = None; ep = 0.0; trades = []
    peak = equity; max_dd = 0.0
    total_fees = 0.0; total_slip = 0.0

    for i in range(57, len(df)):
        lt = sig["long_cond"].iloc[i] and trend_up.iloc[i]
        st = sig["short_cond"].iloc[i] and (not trend_up.iloc[i])

        if pos is None:
            if lt:
                fill = lsl.iloc[i] * (1 + slippage/100)
                pos, ep = "L", fill
            elif st:
                fill = ssl.iloc[i] * (1 - slippage/100)
                pos, ep = "S", fill
        elif pos == "L":
            if st:
                exit_fill = ssl.iloc[i] * (1 - slippage/100)
                pnl_pct = (exit_fill - ep) / ep  # price change %
                gross_pnl = equity * pnl_pct * leverage  # leveraged PnL
                notional = equity * leverage  # position size
                fee_cost = notional * fee / 100 * 2  # fee on entry+exit notional
                net_pnl = gross_pnl - fee_cost
                # Liquidation check
                if equity + net_pnl <= 0:
                    net_pnl = -equity  # liquidated
                equity += net_pnl; trades.append(net_pnl)
                total_slip += abs((ssl.iloc[i] - exit_fill))
                total_fees += fee_cost
                peak = max(peak, equity); max_dd = max(max_dd, peak-equity)
                pos, ep = "S", ssl.iloc[i] * (1 - slippage/100)
        elif pos == "S":
            if lt:
                exit_fill = lsl.iloc[i] * (1 + slippage/100)
                pnl_pct = (ep - exit_fill) / ep
                gross_pnl = equity * pnl_pct * leverage
                notional = equity * leverage
                fee_cost = notional * fee / 100 * 2  # fee on entry+exit notional
                net_pnl = gross_pnl - fee_cost
                if equity + net_pnl <= 0:
                    net_pnl = -equity
                equity += net_pnl; trades.append(net_pnl)
                total_slip += abs((exit_fill - lsl.iloc[i]))
                total_fees += fee_cost
                peak = max(peak, equity); max_dd = max(max_dd, peak-equity)
                pos, ep = "L", lsl.iloc[i] * (1 + slippage/100)

    wins = sum(1 for t in trades if t > 0)
    n = len(trades)
    return {
        "final": equity, "trades": n, "wins": wins,
        "wr": wins/n*100 if n else 0,
        "pnl": equity-initial, "ret": (equity/initial-1)*100,
        "max_dd": max_dd, "max_dd_pct": max_dd/peak*100 if peak>0 else 0,
        "liquidated": equity <= 0,
        "total_fees": total_fees, "total_slip": total_slip,
    }

print("=" * 72)
print("  Volty EMA50 — 100%仓位 3x杠杆 复利")
print("  (含0.02%滑点 + 0.04%手续费)")
print("=" * 72)

print("\n[1] Fetching data...")
df = to_df(fetch_all("ETHUSDT", "15m", 370))
print(f"  {len(df)} bars, {df.index[0]} -> {df.index[-1]}")

now = df.index[-1]
periods = [
    ("一周 (7天)",   now - pd.Timedelta(days=7)),
    ("一月 (30天)",  now - pd.Timedelta(days=30)),
    ("三月 (90天)",  now - pd.Timedelta(days=90)),
    ("一年 (365天)", now - pd.Timedelta(days=365)),
]

# Test 3 initial capital levels
capitals = [100, 1000, 10000]

for initial in capitals:
    print(f"\n{'#'*72}")
    print(f"# 初始本金: USD {initial:,}")
    print(f"{'#'*72}")
    print(f"  {'Period':<18} {'Final':>12} {'PnL':>12} {'Return':>10} {'Trades':>7} {'Win%':>7} {'MaxDD%':>8} {'Status':>10}")
    print(f"  {'-'*76}")

    for period_name, cutoff in periods:
        df_p = df[df.index >= cutoff]
        if len(df_p) < 100: continue

        r = run_volty_compound(df_p, initial=initial, leverage=3, slippage=0.02, fee=0.04)
        status = "LIQUIDATED!" if r["liquidated"] else "OK"
        print(f"  {period_name:<18} USD {r['final']:>9,.0f} USD {r['pnl']:>+9,.0f} "
              f"{r['ret']:>+9.1f}% {r['trades']:>6}  {r['wr']:>5.1f}% {r['max_dd_pct']:>7.1f}% {status:>10}")

# Detailed print for USD 1,000 starting capital
print(f"\n\n{'='*72}")
print(f"  详细报告 — 初始 USD 1,000")
print(f"{'='*72}")

for period_name, cutoff in periods:
    df_p = df[df.index >= cutoff]
    if len(df_p) < 100: continue

    r_ideal = run_volty_compound(df_p, initial=1000, leverage=3, slippage=0.0, fee=0.0)
    r_real = run_volty_compound(df_p, initial=1000, leverage=3, slippage=0.02, fee=0.04)

    print(f"\n  [{period_name}]")
    print(f"    理想 (无费无滑点): USD {r_ideal['final']:,.0f} ({r_ideal['ret']:+.1f}%)  {r_ideal['trades']}笔")
    print(f"    真实 (含费+滑点):  USD {r_real['final']:,.0f} ({r_real['ret']:+.1f}%)  {r_real['trades']}笔")
    print(f"    利润保留率:        {r_real['pnl']/r_ideal['pnl']*100:.0f}%" if r_ideal['pnl'] > 0 else f"    利润保留率: N/A")
    print(f"    最大回撤:          {r_real['max_dd_pct']:.1f}%")
    print(f"    手续费合计:        USD {r_real['total_fees']:,.0f}")
    print(f"    滑点合计:          USD {r_real['total_slip']:,.0f}")

# Liquidation warning
print(f"\n\n{'='*72}")
print(f"  [!] 风险提示")
print(f"{'='*72}")
print(f"  3x杠杆 + 100%仓位复利 = 极高风险")
print(f"  单笔不利交易可能导致账户大幅回撤")
print(f"  建议实盘使用: 20-50%仓位 + 2-3x杠杆")
print(f"  或保留固定仓位不复利，控制风险")
