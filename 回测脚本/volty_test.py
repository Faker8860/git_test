import os, sys
sys.path.insert(0, '/opt/trading')
os.environ['TIMEFRAME_MINUTES'] = '15'
from strategy_bot import compute_signals_volty
from backtest_engine import fetch_binance_history, extract_trades_from_signals

df = fetch_binance_history('ETHUSDT', 15, '15m')
sig = compute_signals_volty(df, 5, 0.75)
trades = extract_trades_from_signals(df, sig, 'VOLTY')

print(f"Volty: {len(df)} bars, {df.index[0]} -> {df.index[-1]}")
print(f"long_cond={sig['long_cond'].sum()}, short_cond={sig['short_cond'].sum()}")
print(f"Trades: {len(trades)}")
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
    print(f"Closed: {len(closed)} + {len(trades)-len(closed)} open")
    print(f"Win: {wins}/{len(closed)} = {wins/len(closed)*100:.0f}%")
    print(f"Total PnL: {total:+.2f}%")
    print(f"Avg: {total/len(closed):+.2f}%")
