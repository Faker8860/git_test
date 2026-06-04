"""
Parse TradingView exported CSV backtest data for both strategies.
Calculate PnL for past week, month, 3 months, and year.
"""
import os
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def parse_occ_csv(filepath):
    """Parse OCC v8.13 TradingView CSV."""
    df = pd.read_csv(filepath)
    # Columns: Trade number, Type, Date and time, Signal, Price USDT, Size (qty),
    #          Size (value), Net PnL USDT, Net PnL %, Favorable excursion USDT,
    #          Favorable excursion %, Adverse excursion USDT, Adverse excursion %,
    #          Cumulative PnL USDT, Cumulative PnL %

    # Only exit rows have real PnL
    exits = df[df.iloc[:, 1].str.contains("Exit", case=False, na=False)].copy()
    exits["datetime"] = pd.to_datetime(exits.iloc[:, 2])
    exits["pnl_usdt"] = pd.to_numeric(exits.iloc[:, 7], errors="coerce")
    exits["pnl_pct"] = pd.to_numeric(exits.iloc[:, 8], errors="coerce")
    exits["cum_pnl_usdt"] = pd.to_numeric(exits.iloc[:, 13], errors="coerce")
    exits["cum_pnl_pct"] = pd.to_numeric(exits.iloc[:, 14], errors="coerce")
    exits["direction"] = exits.iloc[:, 1].apply(
        lambda x: "LONG" if "long" in str(x).lower() else "SHORT"
    )
    exits = exits.sort_values("datetime").reset_index(drop=True)
    return exits[["datetime", "direction", "pnl_usdt", "pnl_pct", "cum_pnl_usdt", "cum_pnl_pct"]]


def parse_volty_csv(filepath):
    """Parse Volty Expan Close TradingView CSV (Chinese headers)."""
    df = pd.read_csv(filepath)

    # Columns: Trade number, 类型, 日期和时间, 信号, 价格 USDT, 大小(数量),
    #          大小(价值), Net PnL USDT, Net PnL %, 有利波动 USDT, 有利波动 %,
    #          不利波动 USDT, 不利波动 %, Cumulative PnL USDT, Cumulative PnL %

    # Only exit rows
    exits = df[df.iloc[:, 1].str.contains("出场", na=False)].copy()
    exits["datetime"] = pd.to_datetime(exits.iloc[:, 2])
    exits["pnl_usdt"] = pd.to_numeric(exits.iloc[:, 7], errors="coerce")
    exits["pnl_pct"] = pd.to_numeric(exits.iloc[:, 8], errors="coerce")
    exits["cum_pnl_usdt"] = pd.to_numeric(exits.iloc[:, 13], errors="coerce")
    exits["cum_pnl_pct"] = pd.to_numeric(exits.iloc[:, 14], errors="coerce")
    exits["direction"] = exits.iloc[:, 1].apply(
        lambda x: "LONG" if "多头" in str(x) else "SHORT"
    )
    exits = exits.sort_values("datetime").reset_index(drop=True)
    return exits[["datetime", "direction", "pnl_usdt", "pnl_pct", "cum_pnl_usdt", "cum_pnl_pct"]]


def analyze_period(trades_df, period_name, cutoff_date):
    """Analyze performance for a specific time period."""
    period_trades = trades_df[trades_df["datetime"] >= cutoff_date]

    if len(period_trades) == 0:
        return None

    num_trades = len(period_trades)
    wins = (period_trades["pnl_usdt"] > 0).sum()
    losses = (period_trades["pnl_usdt"] < 0).sum()
    total_pnl = period_trades["pnl_usdt"].sum()
    total_pnl_pct = period_trades["pnl_pct"].sum()
    win_rate = wins / num_trades * 100 if num_trades > 0 else 0

    avg_win = period_trades[period_trades["pnl_usdt"] > 0]["pnl_usdt"].mean() if wins > 0 else 0
    avg_loss = period_trades[period_trades["pnl_usdt"] < 0]["pnl_usdt"].mean() if losses > 0 else 0

    cumsum = period_trades["pnl_usdt"].cumsum()
    running_max = cumsum.cummax()
    drawdown = running_max - cumsum
    max_dd = drawdown.max()

    total_positive = period_trades[period_trades["pnl_usdt"] > 0]["pnl_usdt"].sum()
    total_negative = abs(period_trades[period_trades["pnl_usdt"] < 0]["pnl_usdt"].sum())
    profit_factor = total_positive / total_negative if total_negative > 0 else float('inf')

    return {
        "num_trades": num_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_dd": max_dd,
        "profit_factor": profit_factor,
        "first_date": period_trades["datetime"].min(),
        "last_date": period_trades["datetime"].max(),
        "trades": period_trades,
    }


def print_analysis(name, analysis):
    if analysis is None:
        print("  No trades in this period.")
        return

    a = analysis
    print(f"\n  Period: {a['first_date']} -> {a['last_date']}")
    print(f"  Trades:        {a['num_trades']}")
    print(f"  Wins/Losses:   {a['wins']}/{a['losses']}")
    print(f"  Win Rate:      {a['win_rate']:.1f}%")
    print(f"  Total PnL:     ${a['total_pnl']:+,.2f}  ({a['total_pnl_pct']:+.2f}%)")
    print(f"  Avg Win:       ${a['avg_win']:+,.2f}")
    print(f"  Avg Loss:      ${a['avg_loss']:+,.2f}")
    print(f"  Max DD:        ${a['max_dd']:,.2f}")
    print(f"  Profit Factor: {a['profit_factor']:.2f}")

    # Last 5 trades
    print(f"  Last 5 trades:")
    for _, t in a['trades'].tail(5).iterrows():
        m = "[WIN]" if t['pnl_usdt'] > 0 else "[LOSS]"
        print(f"    {m} {t['datetime']} {t['direction']:>5} PnL=${t['pnl_usdt']:+,.2f} ({t['pnl_pct']:+.2f}%)")


def main():
    now = pd.Timestamp.now()
    # Note: TV CSV times may be in different timezone
    # Let's just use date-based filtering

    periods = {
        "Past Week":       now - pd.Timedelta(days=7),
        "Past Month":      now - pd.Timedelta(days=30),
        "Past 3 Months":   now - pd.Timedelta(days=90),
        "Past Year":       now - pd.Timedelta(days=365),
    }

    # ── OCC v8.13 ──
    occ_path = os.path.join(BASE_DIR, "OCC-v8.13_BINANCE_ETHUSDT_2026-05-30 15分钟.csv")
    print("=" * 80)
    print("  STRATEGY 1: OCC v8.13 (TEMA Trend Following)")
    print(f"  Data: {occ_path}")
    print("=" * 80)

    try:
        occ_trades = parse_occ_csv(occ_path)
        print(f"  Total trades in CSV: {len(occ_trades)}")
        print(f"  Date range: {occ_trades['datetime'].min()} -> {occ_trades['datetime'].max()}")
        print(f"  Cumulative PnL (entire CSV): ${occ_trades['pnl_usdt'].sum():+,.2f}")

        for period_name, cutoff in periods.items():
            print(f"\n{'─'*60}")
            print(f"  [{period_name}]")
            print(f"{'─'*60}")
            analysis = analyze_period(occ_trades, period_name, cutoff)
            print_analysis("OCC v8.13", analysis)

    except Exception as e:
        print(f"  ERROR parsing OCC CSV: {e}")
        import traceback
        traceback.print_exc()

    # ── Volty ──
    volty_path = os.path.join(BASE_DIR, "验证数据",
                              "Volty_Expan_Close_Strategy_BINANCE_ETHUSDT_2026-05-29 (3).csv")
    print(f"\n\n{'='*80}")
    print("  STRATEGY 2: Volty Expan Close (ATR Breakout)")
    print(f"  Data: {volty_path}")
    print("=" * 80)

    try:
        volty_trades = parse_volty_csv(volty_path)
        print(f"  Total trades in CSV: {len(volty_trades)}")
        print(f"  Date range: {volty_trades['datetime'].min()} -> {volty_trades['datetime'].max()}")
        print(f"  Cumulative PnL (entire CSV): ${volty_trades['pnl_usdt'].sum():+,.2f}")

        for period_name, cutoff in periods.items():
            print(f"\n{'─'*60}")
            print(f"  [{period_name}]")
            print(f"{'─'*60}")
            analysis = analyze_period(volty_trades, period_name, cutoff)
            print_analysis("Volty", analysis)

    except Exception as e:
        print(f"  ERROR parsing Volty CSV: {e}")
        import traceback
        traceback.print_exc()

    # ── Summary Table ──
    print(f"\n\n{'='*90}")
    print(f"  FINAL SUMMARY - Real K-line Signal Performance (TradingView Backtest)")
    print(f"  Based on Binance ETHUSDT 15-minute data")
    print(f"{'='*90}")
    print(f"  {'Period':<18} {'Strategy':<24} {'PnL($)':>10} {'PnL(%)':>8} {'Trades':>7} {'Win%':>8} {'Max DD($)':>10} {'PF':>6}")
    print(f"  {'-'*90}")

    for period_name, cutoff in periods.items():
        # OCC
        occ_analysis = analyze_period(occ_trades, period_name, cutoff)
        if occ_analysis:
            a = occ_analysis
            print(f"  {period_name:<18} {'OCC v8.13':<24} ${a['total_pnl']:>9,.2f} "
                  f"{a['total_pnl_pct']:>7.2f}% {a['num_trades']:>6}  "
                  f"{a['win_rate']:>7.1f}% ${a['max_dd']:>9,.2f} {a['profit_factor']:>5.2f}")

        # Volty
        volty_analysis = analyze_period(volty_trades, period_name, cutoff)
        if volty_analysis:
            a = volty_analysis
            print(f"  {period_name:<18} {'Volty Expan Close':<24} ${a['total_pnl']:>9,.2f} "
                  f"{a['total_pnl_pct']:>7.2f}% {a['num_trades']:>6}  "
                  f"{a['win_rate']:>7.1f}% ${a['max_dd']:>9,.2f} {a['profit_factor']:>5.2f}")

        print(f"  {'-'*90}")

    # ========================================
    # Compounding simulation based on TV trades
    # ========================================
    print(f"\n\n{'='*90}")
    print(f"  COMPOUNDING SIMULATION (Starting $10,000, reinvesting profits)")
    print(f"  Based on per-trade PnL% from TV CSV data, applied sequentially")
    print(f"{'='*90}")

    for strat_name, trades_df in [("OCC v8.13", occ_trades), ("Volty", volty_trades)]:
        print(f"\n  [{strat_name}] Compounding Performance:")
        for period_name, cutoff in periods.items():
            period_trades = trades_df[trades_df["datetime"] >= cutoff]
            if len(period_trades) == 0:
                print(f"    {period_name:<18}: No data")
                continue

            equity = 10000.0
            for _, t in period_trades.iterrows():
                # Each trade uses 100% equity and returns PnL%
                equity *= (1 + t["pnl_pct"] / 100)

            total_ret = (equity / 10000 - 1) * 100
            print(f"    {period_name:<18}: ${10_000:,.0f} -> ${equity:,.0f}  "
                  f"Return: {total_ret:+.2f}%  ({len(period_trades)} trades)")


if __name__ == "__main__":
    main()
