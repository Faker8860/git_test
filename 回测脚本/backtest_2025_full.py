"""
2025年完整回测 — Volty Expan Close 策略
========================================
精确匹配 strategy_bot.py execute_volty_signal() 实盘逻辑：
- Volty Expan Close: stop = close ± ATR(5)×0.75
- EMA50 趋势过滤
- 100%仓位 × 3倍杠杆
- 5% 止损
- 起始 $700
- 输出：1周 / 1月 / 1年 收益和收益率
"""

import ccxt
import pandas as pd
import numpy as np
import time as _time
from datetime import datetime, timezone, timedelta

# ── 策略参数（与实盘完全一致） ──
VLEN = 5
VMULT = 0.75
PCT = 100       # 仓位百分比
LEV = 3         # 杠杆
SL_PCT = 5      # 止损百分比
TREND_FILTER = True
TREND_TYPE = "EMA"
TREND_LEN = 50
INIT_BALANCE = 700.0
SYMBOL = "ETH/USDT:USDT"
TIMEFRAME = "15m"

print("=" * 70)
print("  2025年 Volty Expan Close 策略完整回测")
print("=" * 70)
print(f"  起始资金: ${INIT_BALANCE:.0f}")
print(f"  策略: VLEN={VLEN}, MULT={VMULT}, EMA50趋势过滤={'开' if TREND_FILTER else '关'}")
print(f"  仓位: {PCT}% × {LEV}x杠杆, 止损: {SL_PCT}%")
print(f"  标的: ETHUSDT 永续合约, {TIMEFRAME} K线")
print()

# ═══════════════════════════════════════════════════════════
# 1. 下载数据
# ═══════════════════════════════════════════════════════════
print(">>> 下载 2025年 ETHUSDT 15分钟K线数据...")

ex = ccxt.binance({"enableRateLimit": True, "timeout": 30000})

since = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
end_ts = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

all_klines = []
chunk = 0
while since < end_ts:
    try:
        klines = ex.fetch_ohlcv(SYMBOL, TIMEFRAME, since=since, limit=1000)
        if not klines:
            break
        all_klines.extend(klines)
        since = klines[-1][0] + 1
        chunk += 1
        if chunk % 5 == 0:
            print(f"  已下载 {len(all_klines)} 根K线... ({datetime.fromtimestamp(klines[-1][0]/1000).strftime('%Y-%m-%d')})")
    except Exception as e:
        print(f"  重试: {e}")
        _time.sleep(1)

print(f"  总计: {len(all_klines)} 根K线")
print()

# ═══════════════════════════════════════════════════════════
# 2. 准备数据
# ═══════════════════════════════════════════════════════════
df = pd.DataFrame(all_klines, columns=["ts", "open", "high", "low", "close", "volume"])
df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
df.set_index("ts", inplace=True)

# 只保留 2025 年数据
df = df[(df.index >= "2025-01-01") & (df.index < "2026-01-01")]
print(f"  2025年数据: {len(df)} 根K线, {df.index[0]} → {df.index[-1]}")

# ── ATR 计算 ──
tr = pd.concat([
    df["high"] - df["low"],
    (df["high"] - df["close"].shift(1)).abs(),
    (df["low"] - df["close"].shift(1)).abs(),
], axis=1).max(axis=1)
atr = tr.rolling(window=VLEN).mean() * VMULT

# ── EMA50 趋势过滤 ──
ema50 = df["close"].ewm(span=TREND_LEN, adjust=False).mean()

print(f"  ATR 均值: {atr.mean():.2f}, EMA50 就绪: {ema50.notna().sum()} 根")
print()

# ═══════════════════════════════════════════════════════════
# 3. 回测引擎
# ═══════════════════════════════════════════════════════════
print(">>> 开始回测...")

bal = INIT_BALANCE
pos = None          # None / "LONG" / "SHORT"
ep = 0.0            # entry price
cts = 0.0           # contracts
uv = 0.0            # usdt value
trades = []
equity_curve = [{"time": df.index[0], "equity": bal}]
last_trade_bar = None  # 防同根K线重复开仓

START_IDX = max(VLEN + 3, TREND_LEN + 3)  # 等指标就绪

for i in range(START_IDX, len(df)):
    hi = df["high"].iloc[i]
    lo = df["low"].iloc[i]
    op = df["open"].iloc[i]
    cl = df["close"].iloc[i]
    tm = df.index[i]
    bar_time = tm

    # ── 止损检查（盘中触发） ──
    if pos == "LONG":
        sl_price = ep * (1 - SL_PCT / 100)
        if lo <= sl_price:
            pnl = (sl_price - ep) / ep * uv
            bal += pnl
            trades.append({
                "time": tm, "action": "止损平多", "dir": "LONG",
                "entry": round(ep, 2), "exit": round(sl_price, 2),
                "contracts": round(cts, 4), "value": round(uv, 2),
                "pnl": round(pnl, 2), "balance": round(bal, 2)
            })
            equity_curve.append({"time": tm, "equity": bal})
            pos = None
            last_trade_bar = bar_time
            continue

    if pos == "SHORT":
        sl_price = ep * (1 + SL_PCT / 100)
        if hi >= sl_price:
            pnl = (ep - sl_price) / ep * uv
            bal += pnl
            trades.append({
                "time": tm, "action": "止损平空", "dir": "SHORT",
                "entry": round(ep, 2), "exit": round(sl_price, 2),
                "contracts": round(cts, 4), "value": round(uv, 2),
                "pnl": round(pnl, 2), "balance": round(bal, 2)
            })
            equity_curve.append({"time": tm, "equity": bal})
            pos = None
            last_trade_bar = bar_time
            continue

    # ── Volty 信号（上一根已完成K线计算 stop 价） ──
    prev_atr = atr.iloc[i - 1]
    if pd.isna(prev_atr) or prev_atr <= 0:
        continue

    prev_close = df["close"].iloc[i - 1]
    long_level = prev_close + prev_atr
    short_level = prev_close - prev_atr

    # ── 趋势过滤 ──
    prev_ema = ema50.iloc[i - 1]
    trend_bull = not TREND_FILTER or df["close"].iloc[i - 1] > prev_ema
    trend_bear = not TREND_FILTER or df["close"].iloc[i - 1] < prev_ema

    # ── 信号触发 ──
    long_trig = hi >= long_level and trend_bull and pos != "LONG"
    short_trig = lo <= short_level and trend_bear and pos != "SHORT"

    # 同向双触发：取更靠近 open 的一侧
    if long_trig and short_trig:
        if abs(op - long_level) > abs(op - short_level):
            long_trig = False
        else:
            short_trig = False

    # 同根K线冷却
    if bar_time == last_trade_bar and pos is not None:
        continue

    # ── 执行信号 ──
    if long_trig:
        if pos == "SHORT":
            # 翻转：平空 + 开多
            xp = long_level
            pl = (ep - xp) / ep * uv
            bal += pl
            trades.append({
                "time": tm, "action": "翻转平空→多", "dir": "SHORT",
                "entry": round(ep, 2), "exit": round(xp, 2),
                "contracts": round(cts, 4), "value": round(uv, 2),
                "pnl": round(pl, 2), "balance": round(bal, 2)
            })
            equity_curve.append({"time": tm, "equity": bal})
            pos = None

        if bal > 10:
            cap = bal * PCT / 100.0
            uv = cap * LEV
            ep = long_level
            cts = uv / ep
            pos = "LONG"
            trades.append({
                "time": tm, "action": "开多", "dir": "LONG",
                "entry": round(ep, 2), "exit": 0,
                "contracts": round(cts, 4), "value": round(uv, 2),
                "pnl": 0, "balance": round(bal, 2)
            })
            equity_curve.append({"time": tm, "equity": bal})
            last_trade_bar = bar_time

    elif short_trig:
        if pos == "LONG":
            # 翻转：平多 + 开空
            xp = short_level
            pl = (xp - ep) / ep * uv
            bal += pl
            trades.append({
                "time": tm, "action": "翻转平多→空", "dir": "LONG",
                "entry": round(ep, 2), "exit": round(xp, 2),
                "contracts": round(cts, 4), "value": round(uv, 2),
                "pnl": round(pl, 2), "balance": round(bal, 2)
            })
            equity_curve.append({"time": tm, "equity": bal})
            pos = None

        if bal > 10:
            cap = bal * PCT / 100.0
            uv = cap * LEV
            ep = short_level
            cts = uv / ep
            pos = "SHORT"
            trades.append({
                "time": tm, "action": "开空", "dir": "SHORT",
                "entry": round(ep, 2), "exit": 0,
                "contracts": round(cts, 4), "value": round(uv, 2),
                "pnl": 0, "balance": round(bal, 2)
            })
            equity_curve.append({"time": tm, "equity": bal})
            last_trade_bar = bar_time

# 最终平仓
if pos is not None:
    lp = df["close"].iloc[-1]
    pl = (lp - ep) / ep * uv if pos == "LONG" else (ep - lp) / ep * uv
    bal += pl
    trades.append({
        "time": df.index[-1], "action": "最终平仓",
        "dir": pos, "entry": round(ep, 2), "exit": round(lp, 2),
        "contracts": round(cts, 4), "value": round(uv, 2),
        "pnl": round(pl, 2), "balance": round(bal, 2)
    })
    equity_curve.append({"time": df.index[-1], "equity": bal})

# ═══════════════════════════════════════════════════════════
# 4. 结果计算
# ═══════════════════════════════════════════════════════════

eq_df = pd.DataFrame(equity_curve)
eq_df.set_index("time", inplace=True)

trades_df = pd.DataFrame(trades)
closed_trades = [t for t in trades if t["pnl"] != 0]
total_closed = len(closed_trades)

wins = [t for t in closed_trades if t["pnl"] > 0]
losses = [t for t in closed_trades if t["pnl"] < 0]
win_rate = len(wins) / total_closed * 100 if total_closed else 0
avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

# 按区间计算
def calc_period(start_date, end_date, label):
    """计算指定区间的收益."""
    before = eq_df[eq_df.index < start_date]
    period_eq = eq_df[(eq_df.index >= start_date) & (eq_df.index <= end_date)]

    if before.empty:
        sb = INIT_BALANCE
    else:
        sb = before["equity"].iloc[-1]

    if period_eq.empty:
        eb = sb
    else:
        eb = period_eq["equity"].iloc[-1]

    # 该区间内的成交
    period_trades = [t for t in trades if str(t["time"])[:10] >= start_date and str(t["time"])[:10] <= end_date]
    closed = [t for t in period_trades if t["pnl"] != 0]
    pnl = sum(t["pnl"] for t in closed)

    return {
        "label": label,
        "start_balance": round(sb, 2),
        "end_balance": round(eb, 2),
        "pnl": round(pnl, 2),
        "return_pct": round((eb - sb) / sb * 100, 2) if sb > 0 else 0,
        "trades": len(period_trades),
        "closed": len(closed),
    }

# ── 计算各时间段 ──
periods = [
    ("2025-01-01", "2025-01-07", "第1周 (1/1-1/7)"),
    ("2025-01-01", "2025-01-31", "第1个月 (1月)"),
    ("2025-01-01", "2025-12-31", "全年 (2025)"),
]

results = [calc_period(sd, ed, lb) for sd, ed, lb in periods]

# 月度明细
monthly = []
for m in range(1, 13):
    sd = f"2025-{m:02d}-01"
    if m == 12:
        ed = "2025-12-31"
    else:
        ed = f"2025-{m+1:02d}-01"
    r = calc_period(sd, ed, f"{m}月")
    monthly.append(r)

# ═══════════════════════════════════════════════════════════
# 5. 输出报告
# ═══════════════════════════════════════════════════════════

print()
print("=" * 75)
print("                         📊 2025年 回测结果报告")
print("=" * 75)
print()

# 核心结果
print("┌─────────────────────────────────────────────────────┐")
print("│  核心指标                                            │")
print("├─────────────────────────────────────────────────────┤")
for r in results:
    emoji = "🟢" if r["return_pct"] > 0 else "🔴"
    print(f"│ {r['label']:<18s}                                │")
    print(f"│   起始: ${r['start_balance']:>10,.2f}                            │")
    print(f"│   结束: ${r['end_balance']:>10,.2f}                            │")
    print(f"│   收益: {r['pnl']:>+12,.2f} USDT  ({r['return_pct']:+.1f}%) {emoji}        │")
    print(f"│   成交: {r['trades']} 笔 (平仓 {r['closed']} 次)                      │")
    print(f"│                                                      │")

# 年化收益
final_bal = results[-1]["end_balance"]
total_return = (final_bal - INIT_BALANCE) / INIT_BALANCE * 100
print(f"│ 📈 全年总收益: {total_return:+.1f}%  (${INIT_BALANCE:.0f} → ${final_bal:,.0f})    │")
print(f"│ 📈 年化收益率（复利）: {total_return:+.1f}%                              │")
print("└─────────────────────────────────────────────────────┘")
print()

# 月度明细
print("┌──────────────────────────────────────────────────────┐")
print("│  月度收益明细                                          │")
print("├──────┬──────────┬──────────┬──────────┬──────────────┤")
print("│ 月份 │ 起始($)  │ 结束($)  │ 盈亏($)  │ 收益率       │")
print("├──────┼──────────┼──────────┼──────────┼──────────────┤")
for m in monthly:
    emoji = "🟢" if m["return_pct"] > 0 else "🔴"
    print(f"│ {m['label']:4s} │ ${m['start_balance']:>7,.0f}  │ ${m['end_balance']:>7,.0f}  │ {m['pnl']:>+8,.0f} │ {m['return_pct']:>+6.1f}% {emoji}  │")
print("└──────┴──────────┴──────────┴──────────┴──────────────┘")
print()

# 交易统计
print("┌──────────────────────────────────────────────────────┐")
print("│  交易统计                                              │")
print("├──────────────────────────────────────────────────────┤")
print(f"│  总成交笔数:     {len(trades):>5d}                                    │")
print(f"│  平仓次数:       {total_closed:>5d}                                    │")
print(f"│  盈利次数:       {len(wins):>5d}                                    │")
print(f"│  亏损次数:       {len(losses):>5d}                                    │")
print(f"│  胜率:           {win_rate:>5.1f}%                                   │")
print(f"│  平均盈利:       ${avg_win:>+8,.0f}                                  │")
print(f"│  平均亏损:       ${avg_loss:>+8,.0f}                                  │")
if avg_win > 0 and avg_loss < 0:
    print(f"│  盈亏比:         {abs(avg_win/avg_loss):>5.2f}                                    │")

# 最大回撤
eq_df["peak"] = eq_df["equity"].cummax()
eq_df["drawdown"] = (eq_df["equity"] - eq_df["peak"]) / eq_df["peak"] * 100
max_dd = eq_df["drawdown"].min()
max_dd_date = eq_df["drawdown"].idxmin()
print(f"│  最大回撤:       {max_dd:>+.1f}%  ({str(max_dd_date)[:10]})          │")
print("└──────────────────────────────────────────────────────┘")
print()

# 最终总结
print("=" * 75)
print(f"  🏁 最终结果: ${INIT_BALANCE:.0f} → ${final_bal:,.0f}")
print(f"  📊 总收益率: {total_return:+.1f}%")
print(f"  📊 胜率: {win_rate:.1f}% | 盈亏比: {abs(avg_win/avg_loss):.2f}" if avg_loss != 0 else "")
print(f"  📊 最大回撤: {max_dd:+.1f}%")
print("=" * 75)

# 保存结果到文件
output_dir = "C:/Users/周勇/Desktop/量化交易/回测结果"
import os as _os
_os.makedirs(output_dir, exist_ok=True)

# 保存交易明细
trades_df.to_csv(f"{output_dir}/2025_backtest_trades.csv", index=False, encoding="utf-8-sig")
print(f"\n交易明细已保存: {output_dir}/2025_backtest_trades.csv")
