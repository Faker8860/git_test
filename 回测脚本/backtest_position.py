"""2025年 Volty 仓位优化回测 — 5组仓位×杠杆对比"""
import ccxt, pandas as pd, numpy as np, time as _time
from datetime import datetime, timezone

VLEN, VMULT, SL_PCT = 5, 0.75, 5
TREND_LEN = 50
INIT = 700.0

# ── 5组仓位参数 ──
COMBOS = [
    {"name": "保守 33%×2x",  "pct": 33, "lev": 2},
    {"name": "稳健 33%×3x",  "pct": 33, "lev": 3},
    {"name": "均衡 50%×2x",  "pct": 50, "lev": 2},
    {"name": "进取 50%×3x",  "pct": 50, "lev": 3},
    {"name": "激进 100%×3x", "pct": 100, "lev": 3},
]

print("=" * 70)
print("  2025年 Volty 仓位优化回测 — 5组对比")
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
ema50 = df["close"].ewm(span=TREND_LEN, adjust=False).mean()

# ═══════════════ 回测函数 ═══════════════
def run_backtest(pct, lev):
    bal = INIT; pos = None; ep = cts = uv = 0.0
    trades = 0; eq_curve = [{"t": df.index[0], "e": bal}]
    START = max(VLEN + 3, TREND_LEN + 3, 30)
    stopped = 0  # count stop losses

    for i in range(START, len(df)):
        hi, lo, op = df["high"].iloc[i], df["low"].iloc[i], df["open"].iloc[i]
        tm = df.index[i]

        # Stop loss
        if pos == "LONG":
            sp = ep * (1 - SL_PCT/100)
            if lo <= sp:
                bal += (sp - ep) / ep * uv
                pos = None; stopped += 1
                eq_curve.append({"t": tm, "e": bal})
                continue
        if pos == "SHORT":
            sp = ep * (1 + SL_PCT/100)
            if hi >= sp:
                bal += (ep - sp) / ep * uv
                pos = None; stopped += 1
                eq_curve.append({"t": tm, "e": bal})
                continue

        pa = atr_sig.iloc[i-1]
        if pd.isna(pa) or pa <= 0: continue
        pc = df["close"].iloc[i-1]
        ll, sl = pc + pa, pc - pa

        # Trend filter
        pe = ema50.iloc[i-1]
        tb = df["close"].iloc[i-1] > pe
        tbe = df["close"].iloc[i-1] < pe

        lt = hi >= ll and tb and pos != "LONG"
        st = lo <= sl and tbe and pos != "SHORT"
        if lt and st:
            if abs(op - ll) > abs(op - sl): lt = False
            else: st = False

        if lt:
            if pos == "SHORT":
                xp = ll; bal += (ep - xp) / ep * uv
                pos = None; trades += 1
                eq_curve.append({"t": tm, "e": bal})
            if bal > 10:
                cap = bal * pct / 100.0
                uv = cap * lev
                ep = ll; cts = uv / ep; pos = "LONG"
                trades += 1; eq_curve.append({"t": tm, "e": bal})
        elif st:
            if pos == "LONG":
                xp = sl; bal += (xp - ep) / ep * uv
                pos = None; trades += 1
                eq_curve.append({"t": tm, "e": bal})
            if bal > 10:
                cap = bal * pct / 100.0
                uv = cap * lev
                ep = sl; cts = uv / ep; pos = "SHORT"
                trades += 1; eq_curve.append({"t": tm, "e": bal})

    if pos:
        lp = df["close"].iloc[-1]
        bal += (lp - ep) / ep * uv if pos == "LONG" else (ep - lp) / ep * uv
        trades += 1

    # Analysis
    eqs = pd.DataFrame(eq_curve); eqs.set_index("t", inplace=True)
    eqs["peak"] = eqs["e"].cummax()
    eqs["dd"] = (eqs["e"] - eqs["peak"]) / eqs["peak"] * 100
    max_dd = eqs["dd"].min()
    max_dd_date = str(eqs["dd"].idxmin())[:10]

    # How many times did balance drop below certain thresholds
    below_500 = (eqs["e"] < 500).sum()
    below_300 = (eqs["e"] < 300).sum()
    below_100 = (eqs["e"] < 100).sum()

    def calc_pd(sd, ed):
        pe = eqs[(eqs.index >= sd) & (eqs.index <= ed)]
        be = eqs[eqs.index < sd]
        sb = be["e"].iloc[-1] if not be.empty else INIT
        eb = pe["e"].iloc[-1] if not pe.empty else sb
        return [round(sb, 2), round(eb, 2), round((eb - sb) / sb * 100, 2) if sb > 0 else 0]

    # Monthly breakdown
    monthly = []
    for m in range(1, 13):
        sd = f"2025-{m:02d}-01"; ed = f"2025-{m+1:02d}-01" if m < 12 else "2025-12-31"
        monthly.append(calc_pd(sd, ed))

    # Win/Loss analysis
    eq_changes = eqs["e"].diff().dropna()
    periods_up = (eq_changes > 0).sum()
    periods_total = len(eq_changes)
    up_ratio = periods_up / periods_total * 100 if periods_total > 0 else 0

    # Sharpe-like: return / max_dd (absolute)
    total_ret = (bal - INIT) / INIT * 100
    pain_ratio = total_ret / abs(max_dd) if max_dd != 0 else 0

    return {
        "final": round(bal, 2),
        "ret": round(total_ret, 2),
        "max_dd": round(max_dd, 2),
        "max_dd_date": max_dd_date,
        "trades": trades,
        "stopped": stopped,
        "week1": calc_pd("2025-01-01", "2025-01-07"),
        "month1": calc_pd("2025-01-01", "2025-01-31"),
        "monthly": monthly,
        "below_500": below_500,
        "below_300": below_300,
        "below_100": below_100,
        "pain_ratio": round(pain_ratio, 2),
    }

# ═══════════════ 跑5组 ═══════════════
results = []
for combo in COMBOS:
    print(f">>> {combo['name']}...")
    r = run_backtest(combo["pct"], combo["lev"])
    r["name"] = combo["name"]
    results.append(r)
    print(f"    最终: ${r['final']:,.0f} ({r['ret']:+.1f}%)  |  回撤: {r['max_dd']:+.1f}%  |  成交: {r['trades']}")

# ═══════════════ 输出 ═══════════════
print()
print("=" * 95)
print("                        📊 仓位优化对比 (2025年)")
print("=" * 95)

# Headers
print(f"\n{'指标':<22s}", end="")
for r in results:
    print(f"{r['name']:>16s}", end="")
print()
print("-" * 95)

# Core metrics
rows = [
    ("全年收益", lambda r: f"${r['final']:,.0f} ({r['ret']:+.1f}%)"),
    ("第1周", lambda r: f"${r['week1'][1]:,.0f} ({r['week1'][2]:+.1f}%)"),
    ("第1月", lambda r: f"${r['month1'][1]:,.0f} ({r['month1'][2]:+.1f}%)"),
    ("最大回撤", lambda r: f"{r['max_dd']:+.1f}%"),
    ("回撤日期", lambda r: r['max_dd_date']),
    ("痛感指数(收益/回撤)", lambda r: f"{r['pain_ratio']:+.2f}"),
    ("成交次数", lambda r: str(r['trades'])),
    ("止损次数", lambda r: str(r['stopped'])),
    ("跌破$500(K线数)", lambda r: str(r['below_500'])),
    ("跌破$300(K线数)", lambda r: str(r['below_300'])),
    ("跌破$100(K线数)", lambda r: str(r['below_100'])),
]

for label, func in rows:
    print(f"{label:<22s}", end="")
    for r in results:
        val = func(r)
        # Color based on comparison
        print(f"{val:>16s}", end="")
    print()

# Monthly
print(f"\n{'月份':<6s}", end="")
for r in results:
    print(f"{r['name']:>16s}", end="")
print(f"\n{'-'*95}")

for m in range(12):
    print(f"{m+1:>2}月  ", end="")
    for r in results:
        mo = r["monthly"][m]
        emoji = "UP" if mo[2] > 0 else "DN"
        print(f"  ${mo[1]:>7,.0f} ({mo[2]:+.0f}%)".rjust(16), end="")
    print()

# Winner
best_ret = max(results, key=lambda r: r["ret"])
best_pain = max(results, key=lambda r: r["pain_ratio"])

print(f"\n{'='*95}")
print(f"  🏆 最高收益: {best_ret['name']}  →  ${best_ret['final']:,.0f} ({best_ret['ret']:+.1f}%)  回撤 {best_ret['max_dd']:+.1f}%")
print(f"  🏆 最佳风险收益比: {best_pain['name']}  →  {best_pain['pain_ratio']:.2f} (收益{best_pain['ret']:+.1f}% / 回撤{abs(best_pain['max_dd']):.1f}%)")
print(f"{'='*95}")
