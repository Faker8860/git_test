"""
精确回测对比引擎 v2
==================
1. 从币安拉取历史K线 → 运行 Python 策略 → 导出 TV 格式 CSV
2. 与 TradingView 回测 CSV 逐根K线对比
3. 支持 OCC (TEMA) 和 Volty 两种策略

用法:
    python backtest_engine.py                          # 使用 .env 配置
    python backtest_engine.py --symbol ETHUSDT --days 30  # 最近30天
    python backtest_engine.py --compare OCC-v8.13.csv  # 对比模式
"""

import sys
import os
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pandas as pd
import numpy as np
import ccxt
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))
load_dotenv(BASE_DIR / ".env")

from strategy_bot import (
    calc_ma, compute_signals, compute_signals_volty,
    minutes_to_interval,
)

# ── 配置 ──────────────────────────────────────────
SYMBOL = os.getenv("SYMBOLS", "ETHUSDT").split(",")[0].strip()
TIMEFRAME_MINUTES = int(os.getenv("TIMEFRAME_MINUTES", "1"))
MA_TYPE = os.getenv("MA_TYPE", "TEMA")
MA_LEN = int(os.getenv("MA_LEN", "8"))
CROSS_MULT = int(os.getenv("CROSS_MULT", "3"))
DELAY_MINUTES = int(os.getenv("DELAY_MINUTES", "5"))
DELAY_OFFSET = int(os.getenv("DELAY_OFFSET", "0"))
STRATEGY_TYPE = os.getenv("STRATEGY_TYPE", "TEMA")
VOLTY_LENGTH = int(os.getenv("VOLTY_LENGTH", "5"))
VOLTY_ATR_MULT = float(os.getenv("VOLTY_ATR_MULT", "0.75"))
TRADE_TYPE = os.getenv("TRADE_TYPE", "BOTH")


def fetch_binance_history(symbol: str, since_days: int = 30, timeframe: str = "1m"):
    """从币安拉取历史K线数据."""
    exchange = ccxt.binance({"enableRateLimit": True})

    if symbol.endswith("USDT"):
        sym = f"{symbol[:-4]}/USDT:USDT"
    else:
        sym = symbol

    since_ms = int((datetime.now(timezone.utc) - timedelta(days=since_days)).timestamp() * 1000)
    all_candles = []
    limit = 1000

    print(f"拉取 {sym} {timeframe} K线, 最近 {since_days} 天...")

    while True:
        try:
            candles = exchange.fetch_ohlcv(sym, timeframe, since=since_ms, limit=limit)
        except Exception as e:
            print(f"  拉取失败: {e}")
            break

        if not candles:
            break

        all_candles.extend(candles)

        if len(candles) < limit:
            break

        since_ms = candles[-1][0] + 1

    if not all_candles:
        print("  未获取到数据")
        return None

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)

    print(f"  获取 {len(df)} 根K线, {df.index[0]} → {df.index[-1]}")
    return df


def run_backtest(df, stype="TEMA"):
    """在历史数据上运行策略，返回信号 DataFrame."""
    if stype == "VOLTY":
        sig = compute_signals_volty(df, VOLTY_LENGTH, VOLTY_ATR_MULT)
    else:
        sig = compute_signals(df, MA_TYPE, MA_LEN, CROSS_MULT,
                              TIMEFRAME_MINUTES, DELAY_OFFSET, DELAY_MINUTES)
    return sig


def extract_trades_from_signals(df, sig, stype="TEMA"):
    """从信号 DataFrame 提取交易记录（匹配 TV 执行逻辑）.

    Returns: [{"entry_time": ts, "exit_time": ts, "direction": "LONG"/"SHORT",
               "entry_price": float, "exit_price": float, "pnl_pct": float}, ...]
    """
    trades = []
    position = None  # "LONG" or "SHORT" or None
    entry_price = 0
    entry_idx = 0

    for i in range(1, len(sig)):
        long_trig = sig["long_cond"].iloc[i]
        short_trig = sig["short_cond"].iloc[i]

        if position is None:
            # 无仓位，检查开仓信号
            if long_trig and TRADE_TYPE in ("LONG", "BOTH"):
                position = "LONG"
                entry_price = df["close"].iloc[i]
                entry_idx = i
            elif short_trig and TRADE_TYPE in ("SHORT", "BOTH"):
                position = "SHORT"
                entry_price = df["close"].iloc[i]
                entry_idx = i
        elif position == "LONG":
            # 持多仓，检查平仓信号
            if short_trig and TRADE_TYPE == "LONG":
                exit_price = df["close"].iloc[i]
                pnl_pct = (exit_price - entry_price) / entry_price * 100
                trades.append({
                    "entry_time": df.index[entry_idx],
                    "exit_time": df.index[i],
                    "direction": "LONG",
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(exit_price, 2),
                    "pnl_pct": round(pnl_pct, 2),
                })
                position = None
            elif short_trig and TRADE_TYPE == "BOTH":
                # 翻转：平多 + 开空
                exit_price = df["close"].iloc[i]
                pnl_pct = (exit_price - entry_price) / entry_price * 100
                trades.append({
                    "entry_time": df.index[entry_idx],
                    "exit_time": df.index[i],
                    "direction": "LONG",
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(exit_price, 2),
                    "pnl_pct": round(pnl_pct, 2),
                })
                position = "SHORT"
                entry_price = exit_price
                entry_idx = i
        elif position == "SHORT":
            if long_trig and TRADE_TYPE == "SHORT":
                exit_price = df["close"].iloc[i]
                pnl_pct = (entry_price - exit_price) / entry_price * 100
                trades.append({
                    "entry_time": df.index[entry_idx],
                    "exit_time": df.index[i],
                    "direction": "SHORT",
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(exit_price, 2),
                    "pnl_pct": round(pnl_pct, 2),
                })
                position = None
            elif long_trig and TRADE_TYPE == "BOTH":
                exit_price = df["close"].iloc[i]
                pnl_pct = (entry_price - exit_price) / entry_price * 100
                trades.append({
                    "entry_time": df.index[entry_idx],
                    "exit_time": df.index[i],
                    "direction": "SHORT",
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(exit_price, 2),
                    "pnl_pct": round(pnl_pct, 2),
                })
                position = "LONG"
                entry_price = exit_price
                entry_idx = i

    # 如果有未平仓位，以最后一根K线收盘价标记
    if position is not None:
        exit_price = df["close"].iloc[-1]
        pnl_pct = (exit_price - entry_price) / entry_price * 100 if position == "LONG" else (entry_price - exit_price) / entry_price * 100
        trades.append({
            "entry_time": df.index[entry_idx],
            "exit_time": df.index[-1],
            "direction": position,
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "pnl_pct": round(pnl_pct, 2),
            "open": True,  # 标记为未平仓
        })

    return trades


def export_tv_csv(trades, filepath):
    """导出为 TradingView 格式的 CSV."""
    rows = []
    trade_num = 1
    for t in trades:
        direction_cn = "多头" if t["direction"] == "LONG" else "空头"
        rows.append({
            "交易 #": trade_num,
            "类型": f"{direction_cn}出场" if not t.get("open") else f"{direction_cn}持仓中",
            "日期和时间": t["exit_time"].strftime("%Y-%m-%d %H:%M"),
            "信号": f"Close entry(s) order {t['direction'].lower()}",
            "价格 USDT": t["exit_price"],
            "净损益 %": t["pnl_pct"],
        })
        rows.append({
            "交易 #": trade_num,
            "类型": f"{direction_cn}进场",
            "日期和时间": t["entry_time"].strftime("%Y-%m-%d %H:%M"),
            "信号": t["direction"].lower(),
            "价格 USDT": t["entry_price"],
        })
        trade_num += 1

    df_out = pd.DataFrame(rows)
    df_out.to_csv(filepath, index=False, encoding="utf-8")
    print(f"导出: {filepath} ({len(trades)} 笔交易)")


def compare_with_tv(py_trades, tv_csv_path):
    """对比 Python 回测与 TV 回测."""
    tv_df = load_tv_csv(tv_csv_path)
    if tv_df is None:
        return

    print(f"\n{'='*70}")
    print(f"对比: Python vs {Path(tv_csv_path).name}")
    print(f"{'='*70}")

    # 提取 TV 交易时间
    tv_entries = []
    for _, row in tv_df.iterrows():
        trade_type = str(row.iloc[1]) if len(row) > 1 else ""
        time_str = str(row.iloc[2]) if len(row) > 2 else ""
        if "进场" in trade_type:
            try:
                t = datetime.strptime(time_str.strip(), "%Y-%m-%d %H:%M")
                direction = "LONG" if "多头" in trade_type else "SHORT"
                tv_entries.append({"time": t, "type": "entry", "direction": direction})
            except Exception:
                pass
        elif "出场" in trade_type:
            try:
                t = datetime.strptime(time_str.strip(), "%Y-%m-%d %H:%M")
                tv_entries.append({"time": t, "type": "exit"})
            except Exception:
                pass

    # 对比进场时间
    py_entries = []
    for t in py_trades:
        py_entries.append({"time": t["entry_time"], "direction": t["direction"]})

    print(f"\n{'TV进场时间':<22} {'方向':<8} {'Python进场时间':<22} {'偏差(分钟)':<12} {'状态'}")
    print("-" * 75)

    matched = 0
    total = 0

    for tv in tv_entries:
        if tv["type"] != "entry":
            continue
        total += 1
        tv_time = pd.Timestamp(tv["time"])

        # 找最近的 Python 信号
        best_diff = float("inf")
        best_py = None
        for py in py_entries:
            if py["direction"] != tv["direction"]:
                continue
            diff = abs((py["time"] - tv_time).total_seconds() / 60)
            if diff < best_diff:
                best_diff = diff
                best_py = py

        if best_py is None:
            print(f"{str(tv_time)[:19]:<22} {tv['direction']:<8} {'无信号':<22} {'-':<12} ❌")
            continue

        if best_diff <= 1:
            status = "✅ 精确"
            matched += 1
        elif best_diff <= 5:
            status = "⚠️ 接近"
        else:
            status = "❌ 偏离"

        print(f"{str(tv_time)[:19]:<22} {tv['direction']:<8} {str(best_py['time'])[:19]:<22} {best_diff:+.1f}分{'':<7} {status}")

    print(f"\n匹配率: {matched}/{total} = {matched/total*100:.0f}%" if total > 0 else "\n无数据")


def load_tv_csv(csv_path):
    try:
        return pd.read_csv(csv_path)
    except Exception as e:
        print(f"读取 TV CSV 失败: {e}")
        return None


# ============================================================
#   主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="策略回测对比引擎")
    parser.add_argument("--symbol", default=SYMBOL, help="交易对")
    parser.add_argument("--days", type=int, default=30, help="回测天数")
    parser.add_argument("--strategy", default=STRATEGY_TYPE, choices=["TEMA", "VOLTY"], help="策略类型")
    parser.add_argument("--compare", type=str, default=None, help="TV 回测 CSV 路径（对比模式）")
    parser.add_argument("--export", type=str, default=None, help="导出 CSV 路径")
    args = parser.parse_args()

    print("=" * 70)
    print("  量化策略回测引擎 v2")
    print("=" * 70)
    print(f"  策略: {args.strategy}")
    if args.strategy == "TEMA":
        print(f"  参数: {MA_TYPE}({MA_LEN}), {CROSS_MULT}x HTF, {DELAY_MINUTES}min 延迟")
    else:
        print(f"  参数: length={VOLTY_LENGTH}, ATR mult={VOLTY_ATR_MULT}")
    print(f"  交易对: {args.symbol}, 回测 {args.days} 天")
    print(f"  方向: {TRADE_TYPE}")

    # 1. 拉取数据
    df = fetch_binance_history(args.symbol, args.days, "1m")
    if df is None or df.empty:
        print("无法获取数据，退出")
        return

    # 2. 计算信号
    print(f"\n计算信号...")
    sig = run_backtest(df, args.strategy)

    print(f"  xlong 信号: {sig['xlong'].sum()} 次")
    print(f"  xshort 信号: {sig['xshort'].sum()} 次")
    if args.strategy == "TEMA":
        print(f"  long_cond (延迟后): {sig['long_cond'].sum()} 次")
        print(f"  short_cond (延迟后): {sig['short_cond'].sum()} 次")

    # 3. 提取交易
    trades = extract_trades_from_signals(df, sig, args.strategy)
    print(f"\n交易记录: {len(trades)} 笔")

    for i, t in enumerate(trades):
        open_mark = " [持仓中]" if t.get("open") else ""
        print(f"  {i+1}. {t['direction']} {t['entry_time']} → {t['exit_time']} "
              f"入场={t['entry_price']} 出场={t['exit_price']} PnL={t['pnl_pct']}%{open_mark}")

    # 统计
    closed_trades = [t for t in trades if not t.get("open")]
    if closed_trades:
        wins = sum(1 for t in closed_trades if t["pnl_pct"] > 0)
        total_pnl = sum(t["pnl_pct"] for t in closed_trades)
        avg_pnl = total_pnl / len(closed_trades)
        print(f"\n统计: {len(closed_trades)}笔已平仓 | 胜率={wins/len(closed_trades)*100:.0f}% | "
              f"总PnL={total_pnl:.2f}% | 平均={avg_pnl:.2f}%")

    # 4. 导出
    if args.export:
        export_tv_csv(trades, args.export)

    # 5. 对比
    if args.compare:
        compare_with_tv(trades, args.compare)


if __name__ == "__main__":
    main()
