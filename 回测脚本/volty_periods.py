import os, sys
sys.path.insert(0, '/opt/trading')
os.environ['TIMEFRAME_MINUTES'] = '15'
from strategy_bot import compute_signals_volty
from backtest_engine import fetch_binance_history, extract_trades_from_signals

for days in [7, 30, 90]:
    print(f"\n{'='*60}")
    print(f"VOLTY 15-min ETHUSDT -- {days} days")
    print(f"{'='*60}")

    df = fetch_binance_history('ETHUSDT', days, '15m')
    sig = compute_signals_volty(df, 5, 0.75)
    trades = extract_trades_from_signals(df, sig, 'VOLTY')

    closed = [t for t in trades if not t.get("open")]
    wins = sum(1 for t in closed if t["pnl_pct"] > 0)
    total_pnl = sum(t["pnl_pct"] for t in closed)

    print(f"Bars: {len(df)}, {df.index[0]} -> {df.index[-1]}")
    print(f"Trades: {len(closed)} closed + {len(trades)-len(closed)} open")
    print(f"Win: {wins}/{len(closed)} = {wins/len(closed)*100:.0f}%")
    print(f"Total PnL: {total_pnl:+.2f}%")
    print(f"Avg/trade: {total_pnl/len(closed):+.3f}%")
    print(f"Max win: {max(t['pnl_pct'] for t in closed):+.2f}%")
    print(f"Max loss: {min(t['pnl_pct'] for t in closed):+.2f}%")
