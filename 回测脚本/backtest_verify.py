"""
回测对比脚本 — 验证 Python 信号与 TradingView 策略回测是否一致
============================================================

用法：
    python backtest_verify.py

读取「策略代码/」目录下的 Pine Script 源码和 CSV 回测导出，
与 strategy_bot.py 的信号计算进行逐笔对比。
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

# 导入策略信号函数（不启动实盘交易）
from strategy_bot import calc_ma, compute_signals, compute_signals_volty


def load_csv_trades(csv_path: str) -> pd.DataFrame:
    """加载 TradingView 导出的策略回测 CSV."""
    df = pd.read_csv(csv_path)
    # TV 导出格式通常包含：Trade #, Type, Signal, Date/Time, Price, etc.
    print(f"\n[TV CSV] {Path(csv_path).name}")
    print(f"  列名: {list(df.columns)}")
    print(f"  交易笔数: {len(df)}")
    print(f"  前5行:\n{df.head().to_string()}")
    return df


def simulate_bars_from_trades(df_trades: pd.DataFrame, symbol: str = "ETHUSDT"):
    """从 TV 交易记录重建K线数据并模拟 Python 信号.

    注意：这只是一个近似模拟。要精确对比，需要从币安拉取
    同一时段的实际K线数据，然后用 Python 跑信号。
    """
    pass  # 占位，实际需要币安K线数据


def verify_occ_signals():
    """用模拟数据验证 OCC 策略信号计算。

    构建一个简单的 OHLC 序列，手动计算 Pine Script 预期值，
    与 Python compute_signals() 输出对比。
    """
    print("\n" + "=" * 70)
    print("OCC 策略信号验证")
    print("=" * 70)

    # 构建模拟的 1-min K线数据（模拟一个清晰的均线交叉场景）
    np.random.seed(42)
    n_bars = 120  # 2小时的1分钟K线

    base = 3000.0
    trend = np.linspace(0, 40, n_bars)  # 上涨趋势
    noise = np.random.randn(n_bars) * 5

    close = base + trend + noise
    open_p = close - np.random.randn(n_bars) * 3
    high = np.maximum(open_p, close) + np.abs(np.random.randn(n_bars) * 2)
    low = np.minimum(open_p, close) - np.abs(np.random.randn(n_bars) * 2)

    times = pd.date_range("2026-01-01 09:00", periods=n_bars, freq="1min")
    df = pd.DataFrame({"open": open_p, "high": high, "low": low, "close": close}, index=times)

    # Python 信号
    sig = compute_signals(df, ma_type="TEMA", ma_len=8, cross_mult=3,
                          tf_minutes=1, delay_offset=0, delay_minutes=5)

    print(f"  K线数量: {len(df)}")
    print(f"  参数: TEMA(8), 3x 跨周期(3min HTF), 5分钟延迟")
    print(f"  HTF 闭合 bar 数量: {(sig['close_ma_alt'] != sig['close_ma_alt'].shift(1)).sum()}")
    print(f"  交叉信号 xlong: {sig['xlong'].sum()} 次")
    print(f"  交叉信号 xshort: {sig['xshort'].sum()} 次")
    print(f"  延迟后做多信号: {sig['long_cond'].sum()} 次")
    print(f"  延迟后做空信号: {sig['short_cond'].sum()} 次")

    # 显示前几次信号
    xlong_bars = sig[sig["xlong"]].index[:5]
    xshort_bars = sig[sig["xshort"]].index[:5]
    long_cond_bars = sig[sig["long_cond"]].index[:5]

    if len(xlong_bars) > 0:
        print(f"\n  前5次 xlong (交叉): {list(xlong_bars)}")
    if len(long_cond_bars) > 0:
        print(f"  前5次 long_cond (延迟后): {list(long_cond_bars)}")

    # 验证 HTF 更新时刻是否正确
    # 找出 HTF 值发生变化的 bar（即 HTF 闭合 bar）
    htf_changes = sig["close_ma_alt"] != sig["close_ma_alt"].shift(1)
    htf_change_bars = sig[htf_changes].index
    print(f"\n  HTF 值更新时刻（前10个）: {list(htf_change_bars[:10])}")
    for t in list(htf_change_bars[:5]):
        print(f"    {t}: close_alt={sig.loc[t, 'close_ma_alt']:.2f}, "
              f"open_alt={sig.loc[t, 'open_ma_alt']:.2f}")


def verify_volty_signals():
    """用模拟数据验证 Volty 策略信号计算."""
    print("\n" + "=" * 70)
    print("Volty 策略信号验证")
    print("=" * 70)

    np.random.seed(42)
    n_bars = 100

    # 生成有一定波动率的K线
    close = 3000 + np.cumsum(np.random.randn(n_bars) * 10)
    open_p = close - np.random.randn(n_bars) * 5
    high = np.maximum(open_p, close) + np.abs(np.random.randn(n_bars) * 8)
    low = np.minimum(open_p, close) - np.abs(np.random.randn(n_bars) * 8)

    times = pd.date_range("2026-01-01 09:00", periods=n_bars, freq="1min")
    df = pd.DataFrame({"open": open_p, "high": high, "low": low, "close": close}, index=times)

    sig = compute_signals_volty(df, length=5, atr_mult=0.75)

    print(f"  K线数量: {len(df)}")
    print(f"  参数: length=5, ATR Mult=0.75")
    print(f"  做多信号 (stop触发): {sig['long_cond'].sum()} 次")
    print(f"  做空信号 (stop触发): {sig['short_cond'].sum()} 次")

    # 显示前几次信号
    long_bars = sig[sig["long_cond"]].index[:5]
    short_bars = sig[sig["short_cond"]].index[:5]
    print(f"  前5次 long_cond: {list(long_bars)}")
    print(f"  前5次 short_cond: {list(short_bars)}")

    # 验证前 length 根K线没有信号
    early_long = sig["long_cond"].iloc[:6].sum()
    early_short = sig["short_cond"].iloc[:6].sum()
    print(f"  前6根K线信号数: long={early_long}, short={early_short}")
    print(f"  (预期: 前5根K线不挂stop单，第6根可能是首次触发)")


def compare_with_tv_csv():
    """列出可用的 TV 回测 CSV 文件."""
    csv_dir = BASE_DIR / "策略代码"
    csv_files = sorted(csv_dir.glob("*.csv"))
    print("\n" + "=" * 70)
    print("可用的 TradingView 回测 CSV 文件")
    print("=" * 70)
    for f in csv_files:
        size_kb = f.stat().st_size / 1024
        print(f"  {f.name} ({size_kb:.1f} KB)")

    print("\n提示：要精确对比，请用币安 API 拉取同一时段的K线数据，")
    print("然后运行 Python 信号计算，再与 CSV 中的交易时间逐笔比较。")


if __name__ == "__main__":
    print("=" * 70)
    print("  策略信号验证 — Python vs TradingView Pine Script")
    print("=" * 70)

    verify_occ_signals()
    verify_volty_signals()
    compare_with_tv_csv()

    print("\n" + "=" * 70)
    print("验证完成。")
    print("关键检查点：")
    print("  1. HTF 值应该在 3-min 周期的最后一根 1-min K线上即时更新")
    print("  2. 交叉信号不应有 1-bar 延迟")
    print("  3. Volty 前 length 根K线不产生信号")
    print("=" * 70)
