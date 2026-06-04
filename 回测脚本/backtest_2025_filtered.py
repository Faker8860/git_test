"""
2025年完整回测 — Volty + ATR/ADX 过滤器版
==========================================
与实盘 filter 完全一致：
- ATR(14) < 价格的 0.5% → 不交易
- ADX(14) < 20 → 不交易
"""

import ccxt
import pandas as pd
import numpy as np
import time as _time
from datetime import datetime, timezone, timedelta

VLEN = 5
VMULT = 0.75
PCT = 100
LEV = 3
SL_PCT = 5
TREND_FILTER = True
TREND_TYPE = "EMA"
TREND_LEN = 50
INIT_BALANCE = 700.0

# ── 新增过滤器参数 ──
MIN_ATR_PCT = 0.5   # ATR(14)/价格 低于此%不交易
MIN_ADX = 20        # ADX(14) 低于此值不交易

SYMBOL = "ETH/USDT:USDT"
TIMEFRAME = "15m"

print("=" * 70)
print("  2025年 Volty + ATR/ADX过滤器 回测")
print("=" * 70)
print(f"  起始: ${INIT_BALANCE} | ATR≥{MIN_ATR_PCT}% | ADX≥{MIN_ADX} | EMA50趋势")
print()

# ═══════════════════════════════════ 下载数据 ═══════════════════════════════════
print(">>> 下载数据...")
ex = ccxt.binance({"enableRateLimit": True, "timeout": 30000})
since = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
end_ts = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

all_klines = []
while since < end_ts:
    try:
        klines = ex.fetch_ohlcv(SYMBOL, TIMEFRAME, since=since, limit=1000)
        if not klines: break
        all_klines.extend(klines)
        since = klines[-1][0] + 1
        if len(all_klines) % 5000 == 0:
            print(f"  {len(all_klines)} 根...")
    except Exception as e:
        print(f"  重试: {e}")
        _time.sleep(1)

print(f"  总计: {len(all_klines)} 根")

# ═══════════════════════════════════ 数据准备 ═══════════════════════════════════
df = pd.DataFrame(all_klines, columns=["ts","open","high","low","close","volume"])
df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
df.set_index("ts", inplace=True)
df = df[(df.index >= "2025-01-01") & (df.index < "2026-01-01")]

# ATR
tr = pd.concat([
    df["high"] - df["low"],
    (df["high"] - df["close"].shift(1)).abs(),
    (df["low"] - df["close"].shift(1)).abs(),
], axis=1).max(axis=1)
atr_signal = tr.rolling(window=VLEN).mean() * VMULT

# ATR(14) for volatility filter
atr14 = tr.rolling(window=14).mean()

# ADX(14)
def calc_adx(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    tr_adx = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    plus_dm = high.diff()
    minus_dm = low.shift(1) - low
    plus_dm[(plus_dm <= minus_dm) | (plus_dm <= 0)] = 0
    minus_dm[(minus_dm <= high.diff()) | (minus_dm <= 0)] = 0
    atr_adx = tr_adx.ewm(alpha=1.0/period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1.0/period, adjust=False).mean() / atr_adx
    minus_di = 100 * minus_dm.ewm(alpha=1.0/period, adjust=False).mean() / atr_adx
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1.0/period, adjust=False).mean()

adx14 = calc_adx(df, 14)

# EMA50
ema50 = df["close"].ewm(span=TREND_LEN, adjust=False).mean()

# ═══════════════════════════════════ 回测引擎 ═══════════════════════════════════
print(">>> 开始回测...")

bal = INIT_BALANCE
pos = None
ep = cts = uv = 0.0
trades = []
equity_curve = [{"time": df.index[0], "equity": bal}]
last_trade_bar = None
filtered_count = 0  # 被过滤器拦截的次数

START_IDX = max(VLEN + 3, TREND_LEN + 3, 30)

for i in range(START_IDX, len(df)):
    hi = df["high"].iloc[i]
    lo = df["low"].iloc[i]
    op = df["open"].iloc[i]
    cl = df["close"].iloc[i]
    tm = df.index[i]

    # ── 止损 ──
    if pos == "LONG":
        sl_price = ep * (1 - SL_PCT/100)
        if lo <= sl_price:
            pnl = (sl_price - ep) / ep * uv
            bal += pnl
            trades.append({"time":tm,"action":"止损平多","dir":"LONG","entry":round(ep,2),"exit":round(sl_price,2),"pnl":round(pnl,2),"bal":round(bal,2)})
            equity_curve.append({"time":tm,"equity":bal})
            pos = None; last_trade_bar = tm; continue
    if pos == "SHORT":
        sl_price = ep * (1 + SL_PCT/100)
        if hi >= sl_price:
            pnl = (ep - sl_price) / ep * uv
            bal += pnl
            trades.append({"time":tm,"action":"止损平空","dir":"SHORT","entry":round(ep,2),"exit":round(sl_price,2),"pnl":round(pnl,2),"bal":round(bal,2)})
            equity_curve.append({"time":tm,"equity":bal})
            pos = None; last_trade_bar = tm; continue

    # ── 信号计算 ──
    prev_atr = atr_signal.iloc[i-1]
    if pd.isna(prev_atr) or prev_atr <= 0:
        continue

    prev_close = df["close"].iloc[i-1]
    long_level = prev_close + prev_atr
    short_level = prev_close - prev_atr

    # ── 趋势过滤 ──
    prev_ema = ema50.iloc[i-1]
    trend_bull = not TREND_FILTER or df["close"].iloc[i-1] > prev_ema
    trend_bear = not TREND_FILTER or df["close"].iloc[i-1] < prev_ema

    # ── 🆕 ATR波动率过滤器 ──
    prev_atr14 = atr14.iloc[i-1]
    if MIN_ATR_PCT > 0 and not pd.isna(prev_atr14):
        atr_pct = prev_atr14 / prev_close * 100
        if atr_pct < MIN_ATR_PCT:
            filtered_count += 1
            continue

    # ── 🆕 ADX趋势强度过滤器 ──
    prev_adx = adx14.iloc[i-1]
    if MIN_ADX > 0 and not pd.isna(prev_adx):
        if prev_adx < MIN_ADX:
            filtered_count += 1
            continue

    # ── 信号触发 ──
    long_trig = hi >= long_level and trend_bull and pos != "LONG"
    short_trig = lo <= short_level and trend_bear and pos != "SHORT"

    if long_trig and short_trig:
        if abs(op - long_level) > abs(op - short_level):
            long_trig = False
        else:
            short_trig = False

    if tm == last_trade_bar and pos is not None:
        continue

    # ── 执行 ──
    if long_trig:
        if pos == "SHORT":
            xp = long_level; pl = (ep - xp)/ep*uv; bal += pl
            trades.append({"time":tm,"action":"翻转平空","dir":"SHORT","entry":round(ep,2),"exit":round(xp,2),"pnl":round(pl,2),"bal":round(bal,2)})
            equity_curve.append({"time":tm,"equity":bal}); pos = None
        if bal > 10:
            cap = bal*PCT/100; uv = cap*LEV; ep = long_level; cts = uv/ep; pos = "LONG"
            trades.append({"time":tm,"action":"开多","dir":"LONG","entry":round(ep,2),"exit":0,"pnl":0,"bal":round(bal,2)})
            equity_curve.append({"time":tm,"equity":bal}); last_trade_bar = tm

    elif short_trig:
        if pos == "LONG":
            xp = short_level; pl = (xp - ep)/ep*uv; bal += pl
            trades.append({"time":tm,"action":"翻转平多","dir":"LONG","entry":round(ep,2),"exit":round(xp,2),"pnl":round(pl,2),"bal":round(bal,2)})
            equity_curve.append({"time":tm,"equity":bal}); pos = None
        if bal > 10:
            cap = bal*PCT/100; uv = cap*LEV; ep = short_level; cts = uv/ep; pos = "SHORT"
            trades.append({"time":tm,"action":"开空","dir":"SHORT","entry":round(ep,2),"exit":0,"pnl":0,"bal":round(bal,2)})
            equity_curve.append({"time":tm,"equity":bal}); last_trade_bar = tm

# 最终平仓
if pos:
    lp = df["close"].iloc[-1]
    pl = (lp-ep)/ep*uv if pos=="LONG" else (ep-lp)/ep*uv
    bal += pl
    trades.append({"time":df.index[-1],"action":"最终平仓","dir":pos,"entry":round(ep,2),"exit":round(lp,2),"pnl":round(pl,2),"bal":round(bal,2)})

# ═══════════════════════════════════ 结果 ═══════════════════════════════════
eq_df = pd.DataFrame(equity_curve)
eq_df.set_index("time", inplace=True)

closed = [t for t in trades if t["pnl"] != 0]
wins = [t for t in closed if t["pnl"] > 0]
losses = [t for t in closed if t["pnl"] < 0]
total_closed = len(closed)
win_rate = len(wins)/total_closed*100 if total_closed else 0
avg_win = sum(t["pnl"] for t in wins)/len(wins) if wins else 0
avg_loss = sum(t["pnl"] for t in losses)/len(losses) if losses else 0

def calc_period(sd, ed, label):
    period_eq = eq_df[(eq_df.index >= sd) & (eq_df.index <= ed)]
    before = eq_df[eq_df.index < sd]
    sb = before["equity"].iloc[-1] if not before.empty else INIT_BALANCE
    eb = period_eq["equity"].iloc[-1] if not period_eq.empty else sb
    pt = [t for t in trades if str(t["time"])[:10] >= sd and str(t["time"])[:10] <= ed]
    cl = [t for t in pt if t["pnl"]!=0]
    return {"label":label,"start":round(sb,2),"end":round(eb,2),"pnl":round(sum(t["pnl"] for t in cl),2),"ret":round((eb-sb)/sb*100,2) if sb>0 else 0,"trades":len(pt),"closed":len(cl)}

# 核心时间段
periods = [
    ("2025-01-01","2025-01-07","第1周"),
    ("2025-01-01","2025-01-31","第1个月"),
    ("2025-01-01","2025-12-31","全年"),
]
results = [calc_period(sd,ed,lb) for sd,ed,lb in periods]

# 月度
monthly = []
for m in range(1,13):
    sd = f"2025-{m:02d}-01"
    ed = f"2025-{m+1:02d}-01" if m<12 else "2025-12-31"
    monthly.append(calc_period(sd, ed, f"{m}月"))

# 最大回撤
eq_df["peak"] = eq_df["equity"].cummax()
eq_df["dd"] = (eq_df["equity"] - eq_df["peak"]) / eq_df["peak"] * 100
max_dd = eq_df["dd"].min()
max_dd_date = eq_df["dd"].idxmin()

print()
print("=" * 75)
print("            📊 2025年回测: Volty + ATR/ADX过滤器")
print("=" * 75)

print("┌─────────────────────────────────────────────────────┐")
print("│  核心指标（带过滤器）                                  │")
print("├─────────────────────────────────────────────────────┤")
for r in results:
    e = "🟢" if r["ret"]>0 else "🔴"
    print(f"│ {r['label']:12s}  ${r['start']:>8,.0f} → ${r['end']:>8,.0f}  |  {r['pnl']:>+8,.0f}U  ({r['ret']:+.1f}%) {e}  │")
final_bal = results[-1]["end"]
total_ret = (final_bal-INIT_BALANCE)/INIT_BALANCE*100
print(f"│ 全年总收益: {total_ret:+.1f}%  (${INIT_BALANCE:.0f} → ${final_bal:,.0f})                │")
print("└─────────────────────────────────────────────────────┘")
print()

print("┌──────────────────────────────────────────────────────┐")
print("│  月度明细                                              │")
print("├──────┬──────────┬──────────┬──────────┬──────────────┤")
print("│ 月份 │ 起始($)  │ 结束($)  │ 盈亏($)  │ 收益率       │")
print("├──────┼──────────┼──────────┼──────────┼──────────────┤")
for m in monthly:
    e = "🟢" if m["ret"]>0 else "🔴"
    print(f"│ {m['label']:4s} │ ${m['start']:>7,.0f}  │ ${m['end']:>7,.0f}  │ {m['pnl']:>+8,.0f} │ {m['ret']:>+6.1f}% {e}  │")
print("└──────┴──────────┴──────────┴──────────┴──────────────┘")
print()

print("┌──────────────────────────────────────────────────────┐")
print("│  交易统计                                              │")
print("├──────────────────────────────────────────────────────┤")
print(f"│  总成交笔数:     {len(trades):>5d}                                    │")
print(f"│  平仓次数:       {total_closed:>5d}                                    │")
print(f"│  被过滤跳过:     {filtered_count:>5d} 次                                  │")
print(f"│  盈利次数:       {len(wins):>5d}                                    │")
print(f"│  亏损次数:       {len(losses):>5d}                                    │")
print(f"│  胜率:           {win_rate:>5.1f}%                                   │")
print(f"│  平均盈利:       ${avg_win:>+8,.0f}                                  │")
print(f"│  平均亏损:       ${avg_loss:>+8,.0f}                                  │")
if avg_win>0 and avg_loss<0:
    print(f"│  盈亏比:         {abs(avg_win/avg_loss):>5.2f}                                    │")
print(f"│  最大回撤:       {max_dd:>+.1f}%  ({str(max_dd_date)[:10]})          │")
print("└──────────────────────────────────────────────────────┘")
print()
print("=" * 75)
print(f"  🏁 最终: ${INIT_BALANCE:.0f} → ${final_bal:,.0f}  ({total_ret:+.1f}%)")
print(f"  胜率: {win_rate:.1f}% | 盈亏比: {abs(avg_win/avg_loss):.2f}" if avg_loss!=0 else "")
print(f"  最大回撤: {max_dd:+.1f}% | 过滤: {filtered_count}次")
print("=" * 75)
