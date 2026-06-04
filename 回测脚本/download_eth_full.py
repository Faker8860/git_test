"""Download ALL ETHUSDT 15m from Binance"""
import ccxt
import pandas as pd
import time as _time
import os
from datetime import datetime, timezone

SYMBOL = "ETH/USDT:USDT"
TF = "15m"
OUT = "/opt/trading/ETHUSDT_15m_full.csv"

print("Downloading ALL ETHUSDT 15m from Binance...")
ex = ccxt.binance({"enableRateLimit": True, "timeout": 30000})

since = int(datetime(2019, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
end = int(datetime(2026, 6, 5, tzinfo=timezone.utc).timestamp() * 1000)

all_klines = []
last_count = 0
retry = 0
while since < end:
    try:
        klines = ex.fetch_ohlcv(SYMBOL, TF, since=since, limit=1000)
        if not klines:
            since += 1000 * 60 * 15 * 1000
            _time.sleep(0.5)
            continue
        all_klines.extend(klines)
        since = klines[-1][0] + 1
        retry = 0
        if len(all_klines) - last_count >= 5000:
            last_count = len(all_klines)
            last_date = datetime.fromtimestamp(klines[-1][0]/1000).strftime("%Y-%m-%d")
            print("  {} candles... (up to {})".format(len(all_klines), last_date))
    except Exception as e:
        retry += 1
        if retry > 5:
            print("  Too many retries, skipping chunk")
            since += 1000 * 60 * 15 * 1000
            retry = 0
        _time.sleep(2)

print("\nTotal: {:,} candles".format(len(all_klines)))

df = pd.DataFrame(all_klines, columns=["ts","open","high","low","close","volume"])
df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
df.set_index("ts", inplace=True)
df = df[~df.index.duplicated(keep="first")]
df = df.sort_index()

print("After dedup: {:,} candles".format(len(df)))
print("Range: {} -> {}".format(df.index[0], df.index[-1]))

df.to_csv(OUT)
size_mb = os.path.getsize(OUT) / 1024 / 1024
years = (df.index[-1] - df.index[0]).days / 365.25
print("Saved: {} ({:.1f} MB, {:.1f} years)".format(OUT, size_mb, years))
