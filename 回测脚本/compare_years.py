import ccxt, pandas as pd, numpy as np, time
from strategy_bot import compute_signals_volty, calc_ma

ex = ccxt.binance({"enableRateLimit": True})

# Fetch all data
all_k = []
since = int(pd.Timestamp("2024-01-01").timestamp() * 1000)
for _ in range(60):
    try: batch = ex.fetch_ohlcv("ETH/USDT:USDT", "15m", since=since, limit=1000)
    except: break
    if not batch: break
    all_k.extend(batch)
    if batch[-1][0] >= int(pd.Timestamp("2026-06-01").timestamp() * 1000): break
    since = batch[-1][0] + 1
    if len(batch) < 1000: break
    time.sleep(0.05)

ncols = len(all_k[0])
df = pd.DataFrame(all_k, columns=["t","o","h","l","c","v"][:ncols])
df["t"] = pd.to_datetime(df["t"], unit="ms"); df.set_index("t", inplace=True)
for c in ["o","h","l","c","v"]:
    if c in df.columns: df[c] = pd.to_numeric(df[c])
df = df.rename(columns={"o":"open","h":"high","l":"low","c":"close"})
df = df[~df.index.duplicated()].sort_index()

INITIAL, LEV = 680, 3

for year, start, end in [("2024", "2024-01-01", "2025-01-01"),
                           ("2025-2026", "2025-05-01", "2026-06-01")]:
    dy = df[(df.index >= start) & (df.index < end)]
    if len(dy) < 100: continue

    # Volatility
    daily_ret = dy["close"].resample("D").last().pct_change().dropna()
    avg_atr = ((dy["high"]-dy["low"]).rolling(5).mean()*0.75).mean()
    eth_chg = (dy["close"].iloc[-1]/dy["close"].iloc[0]-1)*100

    # Backtest
    sig = compute_signals_volty(dy, 5, 0.75)
    trend_ma = calc_ma(dy["close"], "EMA", 50).shift(1)
    trend_up = dy["close"] > trend_ma
    tr = pd.concat([dy["high"]-dy["low"], (dy["high"]-dy["close"].shift(1)).abs(),
                    (dy["low"]-dy["close"].shift(1)).abs()], axis=1).max(axis=1)
    atrs = tr.rolling(5).mean() * 0.75
    lsl = (dy["close"]+atrs).shift(1); ssl = (dy["close"]-atrs).shift(1)

    equity = float(INITIAL); pos = None; ep = 0.0
    monthly = {}; trades = []; peak = equity; max_dd = 0.0

    for i in range(57, len(dy)):
        lt = sig["long_cond"].iloc[i] and trend_up.iloc[i]
        st = sig["short_cond"].iloc[i] and (not trend_up.iloc[i])
        m = dy.index[i].strftime("%Y-%m")
        if pos is None:
            if lt: pos, ep = "L", lsl.iloc[i]
            elif st: pos, ep = "S", ssl.iloc[i]
        elif pos == "L":
            if st:
                pnl_pct = (ssl.iloc[i] - ep) / ep
                pnl = equity * pnl_pct * LEV - equity * LEV * 0.0008
                equity += pnl; trades.append(pnl)
                peak = max(peak, equity); max_dd = max(max_dd, peak-equity)
                monthly[m] = monthly.get(m, 0) + pnl
                pos, ep = "S", ssl.iloc[i]
        elif pos == "S":
            if lt:
                pnl_pct = (ep - lsl.iloc[i]) / ep
                pnl = equity * pnl_pct * LEV - equity * LEV * 0.0008
                equity += pnl; trades.append(pnl)
                peak = max(peak, equity); max_dd = max(max_dd, peak-equity)
                monthly[m] = monthly.get(m, 0) + pnl
                pos, ep = "L", lsl.iloc[i]

    n = len(trades); w = sum(1 for t in trades if t > 0)
    ret = (equity/INITIAL-1)*100

    # Per-trade stats as % of equity
    win_pcts = []
    loss_pcts = []
    eq = INITIAL
    for t in trades:
        if t > 0: win_pcts.append(t/eq*100)
        else: loss_pcts.append(abs(t)/eq*100)
        eq += t

    print("="*55)
    print(f"  {year} ({len(dy)} bars, {dy.index[0].date()} -> {dy.index[-1].date()})")
    print("="*55)
    print(f"  ETH:     {dy.close.iloc[0]:.0f} -> {dy.close.iloc[-1]:.0f}  ({eth_chg:+.1f}%)")
    print(f"  Vol:     daily std {daily_ret.std()*100:.2f}%")
    print(f"  ATR*0.75: avg USD {avg_atr:.1f}")
    print(f"  ---")
    print(f"  Trades:  {n}  |  Wins: {w} ({w/n*100:.1f}%)")
    print(f"  Final:   USD {equity:,.0f}  ({ret:+.1f}%)")
    print(f"  Max DD:  {max_dd/peak*100:.1f}%")
    print(f"  Avg Win/Loss per trade (as %% of equity):")
    print(f"    Win:  +{np.mean(win_pcts):.2f}%  |  Loss: -{np.mean(loss_pcts):.2f}%")
    print(f"  ---")
    print(f"  Monthly:")
    for m in sorted(monthly.keys()):
        pnl = monthly[m]
        print(f"    {m}: USD {pnl:>+12,.0f}")
    print()
