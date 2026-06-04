import os, sys
sys.path.insert(0, '/opt/trading')
os.environ['TIMEFRAME_MINUTES'] = '15'
from strategy_bot import compute_signals
from backtest_engine import fetch_binance_history, extract_trades_from_signals

df = fetch_binance_history('ETHUSDT', 15, '15m')
sig = compute_signals(df, 'TEMA', 8, 3, 15, 0, 5)
trades = extract_trades_from_signals(df, sig, 'TEMA')

print(f"K-lines: {len(df)}")
print(f"Period: {df.index[0]} -> {df.index[-1]}")
print()

for i, t in enumerate(trades):
    om = " [open]" if t.get("open") else ""
    print(f"{i+1}. {t['direction']:>5}  {t['entry_time']} -> {t['exit_time']}  "
          f"in={t['entry_price']}  out={t['exit_price']}  PnL={t['pnl_pct']}%{om}")

closed = [t for t in trades if not t.get("open")]
if closed:
    wins = sum(1 for t in closed if t["pnl_pct"] > 0)
    total = sum(t["pnl_pct"] for t in closed)
    print(f"\n{'='*50}")
    print(f"Trades: {len(closed)} closed + {len(trades)-len(closed)} open")
    print(f"Win: {wins}/{len(closed)} = {wins/len(closed)*100:.0f}%")
    print(f"Total PnL: {total:+.2f}%")
    print(f"Avg: {total/len(closed):+.2f}% per trade")
