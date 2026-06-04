"""
精确对比脚本 — Python vs TradingView 信号逐笔验证
================================================

用币安 API 拉取与 TV 回测同时段的K线数据，
逐笔对比 Python compute_signals() 的输出与 TV CSV 的交易时间。

用法：
    python compare_tv.py OCC-v8.8_BINANCE_ETHUSDT_2026-05-25.csv

或直接运行（自动选择最新的CSV）：
    python compare_tv.py
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pandas as pd
import numpy as np
import ccxt
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))
load_dotenv(BASE_DIR / ".env")

from strategy_bot import calc_ma, compute_signals, compute_signals_volty, minutes_to_interval


def fetch_binance_klines(symbol: str, since: datetime, until: datetime, timeframe: str = "1m"):
    """从币安拉取历史K线数据."""
    binance = ccxt.binance({"enableRateLimit": True})

    # 去掉 USDT 后缀中的 USDT，换成 /USDT:USDT
    if symbol.endswith("USDT"):
        sym = f"{symbol[:-4]}/USDT:USDT"
    elif symbol.endswith("USD"):
        sym = f"{symbol[:-3]}/USDT:USDT"
    else:
        sym = symbol

    since_ms = int(since.timestamp() * 1000)
    until_ms = int(until.timestamp() * 1000)

    all_candles = []
    current = since_ms
    limit = 1000

    print(f"  拉取 {sym} {timeframe} K线: {since} → {until}")

    while current < until_ms:
        try:
            candles = binance.fetch_ohlcv(sym, timeframe, since=current, limit=limit)
        except Exception as e:
            print(f"  拉取失败: {e}")
            break

        if not candles:
            break

        all_candles.extend(candles)
        current = candles[-1][0] + 1  # 下一条K线的时间戳

        # 如果最新数据已覆盖 until，停止
        if candles[-1][0] >= until_ms:
            break

        if len(candles) < limit:
            break

    if not all_candles:
        print("  未获取到任何K线数据")
        return None

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)

    return df


def load_tv_trades(csv_path: str) -> pd.DataFrame:
    """加载 TV CSV，提取交易时间和方向."""
    df = pd.read_csv(csv_path)

    # TV CSV 格式：每笔交易两行（进场+出场），用"交易 #"分组
    # 提取进场时间（日期和时间列）
    time_col = None
    for col in df.columns:
        if "日期" in col or "时间" in col or "Date" in col or "Time" in col:
            time_col = col
            break

    if time_col is None:
        print(f"  找不到时间列，可用列: {list(df.columns)}")
        return None

    entries = []
    for _, row in df.iterrows():
        trade_type = str(row.iloc[1]) if len(row) > 1 else ""
        if "进场" in trade_type or "Entry" in trade_type:
            time_str = str(row[time_col])
            try:
                # 格式: "2025-10-29 13:00"
                t = datetime.strptime(time_str.strip(), "%Y-%m-%d %H:%M")
                direction = "LONG" if ("多头" in trade_type or "long" in trade_type.lower()) else "SHORT"
                entries.append({"time": t, "direction": direction, "type": "entry"})
            except Exception:
                pass
        elif "出场" in trade_type or "Exit" in trade_type or "Close" in trade_type:
            time_str = str(row[time_col])
            try:
                t = datetime.strptime(time_str.strip(), "%Y-%m-%d %H:%M")
                entries.append({"time": t, "direction": "EXIT", "type": "exit"})
            except Exception:
                pass

    df_entries = pd.DataFrame(entries)
    print(f"  提取到 {len(df_entries)} 条交易记录")
    return df_entries


def find_nearest_signal(signals, target_time, direction):
    """在信号列表中找最接近目标时间的信号."""
    if direction == "LONG":
        mask = signals["long_cond"]
    else:
        mask = signals["short_cond"]

    signal_times = signals[mask].index

    if len(signal_times) == 0:
        return None, None

    # 找最接近的时间
    diffs = abs(signal_times - target_time)
    nearest_idx = diffs.argmin()
    nearest_time = signal_times[nearest_idx]
    diff_minutes = (nearest_time - target_time).total_seconds() / 60

    return nearest_time, diff_minutes


def main():
    csv_dir = BASE_DIR / "策略代码"
    csv_files = sorted(csv_dir.glob("OCC-v8.8*.csv"))

    if len(sys.argv) > 1:
        csv_path = BASE_DIR / "策略代码" / sys.argv[1]
        if not csv_path.exists():
            csv_path = Path(sys.argv[1])
    elif csv_files:
        csv_path = csv_files[-1]
    else:
        csv_path = None
        # fallback to any CSV
        all_csv = sorted(csv_dir.glob("*.csv"))
        if all_csv:
            csv_path = all_csv[-1]

    if csv_path is None or not csv_path.exists():
        print("未找到 TV CSV 文件")
        return

    print("=" * 70)
    print(f"对比文件: {csv_path.name}")
    print("=" * 70)

    # 1. 加载 TV 交易记录
    tv_entries = load_tv_trades(str(csv_path))
    if tv_entries is None or tv_entries.empty:
        print("无法解析 TV 交易记录")
        return

    # 提取时间范围
    tv_times = tv_entries["time"]
    start_time = tv_times.min() - timedelta(days=2)
    end_time = tv_times.max() + timedelta(days=2)

    # 识别交易对（从文件名）
    fname = csv_path.name
    if "ETHUSDT" in fname:
        symbol = "ETHUSDT"
    elif "BTCUSDT" in fname:
        symbol = "BTCUSDT"
    elif "ETHUSD" in fname:
        symbol = "ETHUSDT"
    else:
        symbol = "ETHUSDT"

    # 2. 拉取币安K线数据
    df = fetch_binance_klines(symbol, start_time, end_time, "1m")
    if df is None or df.empty:
        print("无法获取K线数据，可能历史数据太远")
        return

    # 3. 计算 Python 信号
    # 参数从当前 .env + 默认值（匹配 OCC v8.8: TEMA, 8, 3x, 5min delay）
    ma_type = os.getenv("MA_TYPE", "TEMA")
    ma_len = int(os.getenv("MA_LEN", "8"))
    cross_mult = int(os.getenv("CROSS_MULT", "3"))
    delay_min = int(os.getenv("DELAY_MINUTES", "5"))

    print(f"\nPython 信号参数: {ma_type}({ma_len}), {cross_mult}x 跨周期, {delay_min}min 延迟")
    print(f"K线数量: {len(df)}, 时间范围: {df.index[0]} → {df.index[-1]}")

    sig = compute_signals(df, ma_type=ma_type, ma_len=ma_len,
                          cross_mult=cross_mult, tf_minutes=1,
                          delay_offset=0, delay_minutes=delay_min,
                          )

    # 4. 逐笔对比
    print("\n" + "=" * 70)
    print("逐笔对比")
    print("=" * 70)
    print(f"{'TV进场时间':<22} {'方向':<8} {'Python信号时间':<22} {'偏差(分钟)':<12} {'匹配':<6}")
    print("-" * 70)

    results = []
    for _, entry in tv_entries.iterrows():
        tv_time = entry["time"]
        direction = entry["direction"]

        if direction == "EXIT":
            continue  # 跳过出场，只看进场信号

        nearest, diff = find_nearest_signal(sig, pd.Timestamp(tv_time), direction)
        if nearest is None:
            match = "❌ 无信号"
            diff_str = "-"
        elif abs(diff) <= 5:
            match = "✅"
            diff_str = f"{diff:+.0f}"
        elif abs(diff) <= 30:
            match = "⚠️"
            diff_str = f"{diff:+.0f}"
        else:
            match = "❌"
            diff_str = f"{diff:+.0f}"

        results.append({
            "tv_time": tv_time,
            "direction": direction,
            "python_time": nearest,
            "diff_min": diff if nearest is not None else None,
            "match": match,
        })

        py_time_str = str(nearest)[:19] if nearest is not None else "-"
        print(f"{str(tv_time):<22} {direction:<8} {py_time_str:<22} {diff_str:<12} {match:<6}")

    # 5. 统计
    total = len(results)
    matched = sum(1 for r in results if r["match"] == "✅")
    close = sum(1 for r in results if r["match"] == "⚠️")
    failed = sum(1 for r in results if r["match"] == "❌")

    print("\n" + "=" * 70)
    print("统计结果")
    print("=" * 70)
    print(f"  总信号数: {total}")
    print(f"  ✅ 精确匹配 (≤5分钟偏差): {matched}")
    print(f"  ⚠️ 接近 (≤30分钟偏差): {close}")
    print(f"  ❌ 未匹配 (>30分钟偏差或无信号): {failed}")
    if total > 0:
        print(f"  匹配率: {matched/total*100:.0f}%")

    # 6. 显示 Python 独有的信号（TV 未记录的）
    py_long = sig[sig["long_cond"]].index
    py_short = sig[sig["short_cond"]].index

    tv_entry_times = set()
    for _, entry in tv_entries.iterrows():
        if entry["direction"] != "EXIT":
            tv_entry_times.add(entry["time"])

    unmatched_long = []
    for t in py_long:
        is_matched = any(abs((t - pd.Timestamp(tv_t)).total_seconds()) <= 30
                         for tv_t in tv_entry_times)
        if not is_matched:
            unmatched_long.append(t)

    unmatched_short = []
    for t in py_short:
        is_matched = any(abs((t - pd.Timestamp(tv_t)).total_seconds()) <= 30
                         for tv_t in tv_entry_times)
        if not is_matched:
            unmatched_short.append(t)

    if unmatched_long:
        print(f"\nPython 独有做多信号 ({len(unmatched_long)}个):")
        for t in unmatched_long[:10]:
            print(f"  {t}")

    if unmatched_short:
        print(f"\nPython 独有做空信号 ({len(unmatched_short)}个):")
        for t in unmatched_short[:10]:
            print(f"  {t}")


if __name__ == "__main__":
    main()
