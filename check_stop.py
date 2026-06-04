import ccxt, pandas as pd, numpy as np
ex = ccxt.binance({"enableRateLimit":True})
klines = ex.fetch_ohlcv("ETH/USDT:USDT", "15m", limit=100)
df = pd.DataFrame(klines, columns=["t","o","h","l","c","v"])
df["t"] = pd.to_datetime(df["t"], unit="ms")
tr = pd.concat([df["h"]-df["l"], (df["h"]-df["c"].shift(1)).abs(), (df["l"]-df["c"].shift(1)).abs()], axis=1).max(axis=1)
atrs = tr.rolling(5).mean() * 0.75
ema50 = df["c"].ewm(span=50, adjust=False).mean()
prev_close = df["c"].iloc[-2]
prev_atrs = atrs.iloc[-2]
prev_ema = ema50.iloc[-2]
long_stop = prev_close + prev_atrs
ticker = ex.fetch_ticker("ETH/USDT:USDT")
curr = ticker["last"]
print(f"现价: {curr:.2f}")
print(f"做多触发价: {long_stop:.2f}")
print(f"距触发: {long_stop - curr:.2f}, 需趋势BULL")
print(f"EMA50: {prev_ema:.2f}")
print(f"趋势: {'BULL' if prev_close > prev_ema else 'BEAR'}")
