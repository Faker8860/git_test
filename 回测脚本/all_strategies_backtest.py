"""
ALL Strategies Backtest on 6.5 Years ETHUSDT 15m Data
=====================================================
Tests: Volty EMA50, SuperTrend, Hull Suite, PMax, OCC, Stealth Pivots
Each with standard params, 60% x 3x, $700 start
"""
import pandas as pd
import numpy as np
import time as _time
import os, json

INIT = 700.0
PCT, LEV, SL_PCT, TP_PCT = 60, 3, 5, 1.5  # standard config

# === Load Data ===
DATA_FILE = "/opt/trading/ETHUSDT_15m_full.csv"
print("Loading data from {}...".format(DATA_FILE))
df = pd.read_csv(DATA_FILE, parse_dates=["ts"], index_col="ts")
print("Loaded: {:,} candles, {} -> {}".format(len(df), str(df.index[0])[:10], str(df.index[-1])[:10]))

# Pre-compute common indicators
high, low, close, open_ = df["high"], df["low"], df["close"], df["open"]
tr = pd.concat([high-low, (high-close.shift(1)).abs(), (low-close.shift(1)).abs()], axis=1).max(axis=1)
atr14 = tr.rolling(14).mean()
ema50 = close.ewm(span=50, adjust=False).mean()

def run_backtest(name, get_signal_fn, extra_params=None):
    """Generic backtest engine. get_signal_fn(df, i) returns (long_trigger, short_trigger)"""
    bal = INIT
    pos = None  # None, "LONG", "SHORT"
    ep = uv = 0.0
    trades = []
    eq = [INIT]
    START = 200  # Allow enough bars for all indicators to warm up

    for i in range(START, len(df)):
        hi, lo, cl = high.iloc[i], low.iloc[i], close.iloc[i]
        tm = df.index[i]

        # Stop loss
        if pos == "LONG" and lo <= ep * (1 - SL_PCT/100):
            pnl = (ep*(1-SL_PCT/100) - ep) / ep * uv
            bal += pnl; pos = None; eq.append(bal); continue
        if pos == "SHORT" and hi >= ep * (1 + SL_PCT/100):
            pnl = (ep - ep*(1+SL_PCT/100)) / ep * uv
            bal += pnl; pos = None; eq.append(bal); continue

        # Take profit
        if pos == "LONG" and cl >= ep * (1 + TP_PCT/100):
            pnl = (ep*(1+TP_PCT/100) - ep) / ep * uv
            bal += pnl; pos = None; eq.append(bal); continue
        if pos == "SHORT" and cl <= ep * (1 - TP_PCT/100):
            pnl = (ep - ep*(1-TP_PCT/100)) / ep * uv
            bal += pnl; pos = None; eq.append(bal); continue

        # Get signal
        try:
            lt, st = get_signal_fn(df, i, extra_params)
        except:
            continue

        if lt and st:
            if abs(open_.iloc[i] - (close.iloc[i-1] + tr.rolling(5).mean().iloc[i-1]*0.75)) > \
               abs(open_.iloc[i] - (close.iloc[i-1] - tr.rolling(5).mean().iloc[i-1]*0.75)):
                lt = False
            else:
                st = False

        if lt and pos != "LONG":
            if pos == "SHORT":
                bal += (ep - lt) / ep * uv if isinstance(lt, (int,float)) else 0
                pos = None; eq.append(bal)
            if bal > 10:
                cap = bal * PCT / 100; uv = cap * LEV
                ep = close.iloc[i-1] + tr.rolling(5).mean().iloc[i-1]*0.75 if isinstance(lt, bool) else lt
                if isinstance(lt, bool):
                    ep = close.iloc[i-1] + tr.rolling(5).mean().iloc[i-1]*0.75
                pos = "LONG"; eq.append(bal); trades.append(1)
        elif st and pos != "SHORT":
            if pos == "LONG":
                bal += (ep - st) / ep * uv if isinstance(st, (int,float)) else 0
                pos = None; eq.append(bal)
            if bal > 10:
                cap = bal * PCT / 100; uv = cap * LEV
                if isinstance(st, bool):
                    ep = close.iloc[i-1] - tr.rolling(5).mean().iloc[i-1]*0.75
                pos = "SHORT"; eq.append(bal); trades.append(1)

    if pos:
        lp = close.iloc[-1]
        bal += (lp-ep)/ep*uv if pos=="LONG" else (ep-lp)/ep*uv
        eq.append(bal)

    eq_s = pd.Series(eq)
    peak = eq_s.cummax()
    dd = (eq_s - peak) / peak * 100
    max_dd = dd.min()
    ret = (bal - INIT) / INIT * 100

    return {"name": name, "final": round(bal,2), "ret": round(ret,2),
            "max_dd": round(max_dd,2), "trades": len(trades),
            "pain": round(ret/abs(max_dd),2) if max_dd != 0 else 0}

# ============================================================
# Strategy 1: Volty EMA50 (CURRENT)
# ============================================================
def volty_signal(df, i, _):
    VLEN, VMULT = 5, 0.75
    if i < VLEN + 3: return False, False
    pc = df["close"].iloc[i-1]
    pa = tr.rolling(VLEN).mean().iloc[i-1] * VMULT
    if pd.isna(pa) or pa <= 0: return False, False
    pe = ema50.iloc[i-1]
    tb = pc > pe; tbe = pc < pe
    lt = df["high"].iloc[i] >= pc + pa and tb
    st = df["low"].iloc[i] <= pc - pa and tbe
    return lt, st

# ============================================================
# Strategy 2: SuperTrend
# ============================================================
def supertrend_signal(df, i, params):
    period, mult = params if params else (10, 3.0)
    if i < period + 5: return False, False
    src = (df["high"].iloc[i-1] + df["low"].iloc[i-1]) / 2
    atr_val = tr.rolling(period).mean().iloc[i-1]
    if pd.isna(atr_val): return False, False
    # SuperTrend logic (simplified but matches core concept)
    up = src - mult * atr_val
    dn = src + mult * atr_val
    prev_close = df["close"].iloc[i-1]
    # Trend change detection
    prev_up = (df["high"].iloc[i-2] + df["low"].iloc[i-2])/2 - mult * tr.rolling(period).mean().iloc[i-2]
    prev_dn = (df["high"].iloc[i-2] + df["low"].iloc[i-2])/2 + mult * tr.rolling(period).mean().iloc[i-2]
    was_bearish = df["close"].iloc[i-2] < prev_up if not pd.isna(prev_up) else False
    was_bullish = df["close"].iloc[i-2] > prev_dn if not pd.isna(prev_dn) else False
    lt = df["high"].iloc[i] > dn and was_bearish
    st = df["low"].iloc[i] < up and was_bullish
    return lt, st

# ============================================================
# Strategy 3: Hull Suite
# ============================================================
def hull_signal(df, i, params):
    length = params if params else 55
    if i < length + 5: return False, False
    def wma(s, l):
        w = np.arange(1, l+1)
        return (s[-l:] * w).sum() / w.sum()
    closes = df["close"].values
    if i < length*2: return False, False
    # HMA calculation for current and 2 bars ago
    def calc_hma(idx):
        if idx < length: return np.nan
        half = length // 2
        sqrt_len = int(np.sqrt(length))
        wma_half = sum(closes[idx-half+1:idx+1] * np.arange(1, half+1)) / sum(np.arange(1, half+1))
        wma_full = sum(closes[idx-length+1:idx+1] * np.arange(1, length+1)) / sum(np.arange(1, length+1))
        raw = 2*wma_half - wma_full
        if idx < sqrt_len: return np.nan
        return sum(raw * np.arange(1, sqrt_len+1)) / sum(np.arange(1, sqrt_len+1)) if isinstance(raw, np.ndarray) else np.nan
    h0 = calc_hma(i)
    h2 = calc_hma(i-2)
    if pd.isna(h0) or pd.isna(h2): return False, False
    lt = h0 > h2
    st = h0 < h2
    return lt, st

# ============================================================
# Strategy 4: PMax (MA + ATR trailing)
# ============================================================
def pmax_signal(df, i, params):
    period, mult = params if params else (10, 3.0)
    if i < period + 20: return False, False
    # EMA of source
    src = (df["high"].iloc[i-1] + df["low"].iloc[i-1]) / 2
    ma = df["close"].ewm(span=period).mean().iloc[i-1]
    atr_val = tr.rolling(period).mean().iloc[i-1]
    if pd.isna(atr_val) or pd.isna(ma): return False, False
    long_stop = ma - mult * atr_val
    short_stop = ma + mult * atr_val
    prev_long = df["close"].ewm(span=period).mean().iloc[i-2] - mult * tr.rolling(period).mean().iloc[i-2]
    prev_short = df["close"].ewm(span=period).mean().iloc[i-2] + mult * tr.rolling(period).mean().iloc[i-2]
    if pd.isna(prev_long) or pd.isna(prev_short): return False, False
    was_up = df["close"].ewm(span=period).mean().iloc[i-2] > prev_long
    was_down = df["close"].ewm(span=period).mean().iloc[i-2] < prev_short
    lt = df["close"].ewm(span=period).mean().iloc[i-1] > short_stop and was_down
    st = df["close"].ewm(span=period).mean().iloc[i-1] < long_stop and was_up
    return lt, st

# ============================================================
# Strategy 5: OCC v8.13 (MA crossover on HTF)
# ============================================================
def occ_signal(df, i, _):
    # Simplified OCC: TEMA(8) crossover on 3x timeframe
    length, cross_mult = 8, 3
    if i < length * cross_mult + 10: return False, False
    def tema(s, l):
        e1 = s.ewm(span=l, adjust=False).mean()
        e2 = e1.ewm(span=l, adjust=False).mean()
        e3 = e2.ewm(span=l, adjust=False).mean()
        return 3*e1 - 3*e2 + e3
    close_ma = tema(df["close"], length)
    open_ma = tema(df["open"], length)
    # Resample to 3x timeframe
    ht_freq = 3
    epoch = np.array([int(t.timestamp()//60) for t in df.index])
    ht = epoch // (15 * ht_freq)
    is_close = np.zeros(len(df), dtype=bool)
    is_close[:-1] = ht[:-1] != ht[1:]; is_close[-1] = True
    close_ht = pd.Series(np.where(is_close, close_ma.values, np.nan), index=df.index).ffill()
    open_ht = pd.Series(np.where(is_close, open_ma.values, np.nan), index=df.index).ffill()
    if pd.isna(close_ht.iloc[i]) or pd.isna(open_ht.iloc[i]): return False, False
    prev_c = close_ht.iloc[i-1]; prev_o = open_ht.iloc[i-1]
    curr_c = close_ht.iloc[i]; curr_o = open_ht.iloc[i]
    lt = curr_c > curr_o and prev_c <= prev_o
    st = curr_c < curr_o and prev_c >= prev_o
    return lt, st

# ============================================================
# Strategy 6: SuperTrend + EMA50 (Hybrid)
# ============================================================
def supertrend_ema_signal(df, i, _):
    period, mult = 10, 3.0
    if i < period + 50 + 5: return False, False
    src = (df["high"].iloc[i-1] + df["low"].iloc[i-1]) / 2
    atr_val = tr.rolling(period).mean().iloc[i-1]
    if pd.isna(atr_val): return False, False
    up = src - mult * atr_val
    dn = src + mult * atr_val
    prev_up = (df["high"].iloc[i-2]+df["low"].iloc[i-2])/2 - mult*tr.rolling(period).mean().iloc[i-2]
    prev_dn = (df["high"].iloc[i-2]+df["low"].iloc[i-2])/2 + mult*tr.rolling(period).mean().iloc[i-2]
    if pd.isna(prev_up): return False, False
    was_bearish = df["close"].iloc[i-2] < prev_up
    was_bullish = df["close"].iloc[i-2] > prev_dn
    # EMA50 trend filter
    pe = ema50.iloc[i-1]
    tb = df["close"].iloc[i-1] > pe
    tbe = df["close"].iloc[i-1] < pe
    lt = df["high"].iloc[i] > dn and was_bearish and tb
    st = df["low"].iloc[i] < up and was_bullish and tbe
    return lt, st

# ============================================================
# Strategy 7: Volty + SuperTrend Confirmation (Combined)
# ============================================================
def volty_st_combo(df, i, _):
    VLEN, VMULT = 5, 0.75
    st_period, st_mult = 10, 3.0
    if i < max(VLEN, st_period) + 55: return False, False
    # Volty signal
    pc = df["close"].iloc[i-1]
    pa = tr.rolling(VLEN).mean().iloc[i-1] * VMULT
    if pd.isna(pa) or pa <= 0: return False, False
    # SuperTrend for confirmation
    src = (df["high"].iloc[i-1] + df["low"].iloc[i-1]) / 2
    atr_st = tr.rolling(st_period).mean().iloc[i-1]
    if pd.isna(atr_st): return False, False
    up = src - st_mult * atr_st
    dn = src + st_mult * atr_st
    prev_up = (df["high"].iloc[i-2]+df["low"].iloc[i-2])/2 - st_mult*tr.rolling(st_period).mean().iloc[i-2]
    prev_dn = (df["high"].iloc[i-2]+df["low"].iloc[i-2])/2 + st_mult*tr.rolling(st_period).mean().iloc[i-2]
    if pd.isna(prev_up): return False, False
    st_bull = df["close"].iloc[i-1] > prev_up  # SuperTrend says bullish
    st_bear = df["close"].iloc[i-1] < prev_dn  # SuperTrend says bearish
    # EMA50
    pe = ema50.iloc[i-1]
    tb = pc > pe; tbe = pc < pe
    # Only enter when both Volty AND SuperTrend agree + EMA50 confirms
    lt = df["high"].iloc[i] >= pc+pa and tb and st_bull
    st_sig = df["low"].iloc[i] <= pc-pa and tbe and st_bear
    return lt, st_sig

# ============================================================
# Strategy 8: OCC + EMA50 Filter
# ============================================================
def occ_ema_signal(df, i, _):
    length, cross_mult = 8, 3
    if i < length * cross_mult + 55: return False, False
    def tema(s, l):
        e1 = s.ewm(span=l, adjust=False).mean(); e2 = e1.ewm(span=l, adjust=False).mean()
        e3 = e2.ewm(span=l, adjust=False).mean(); return 3*e1 - 3*e2 + e3
    close_ma = tema(df["close"], length); open_ma = tema(df["open"], length)
    epoch = np.array([int(t.timestamp()//60) for t in df.index])
    ht = epoch // (15 * 3)
    is_close = np.zeros(len(df), dtype=bool); is_close[:-1] = ht[:-1] != ht[1:]; is_close[-1] = True
    close_ht = pd.Series(np.where(is_close, close_ma.values, np.nan), index=df.index).ffill()
    open_ht = pd.Series(np.where(is_close, open_ma.values, np.nan), index=df.index).ffill()
    if pd.isna(close_ht.iloc[i]): return False, False
    prev_c, prev_o = close_ht.iloc[i-1], open_ht.iloc[i-1]
    curr_c, curr_o = close_ht.iloc[i], open_ht.iloc[i]
    pe = ema50.iloc[i-1]; tb = df["close"].iloc[i-1] > pe; tbe = df["close"].iloc[i-1] < pe
    lt = curr_c > curr_o and prev_c <= prev_o and tb
    st = curr_c < curr_o and prev_c >= prev_o and tbe
    return lt, st

# ============================================================
# RUN ALL
# ============================================================
strategies = [
    ("Volty EMA50 (Current)", volty_signal, None),
    ("SuperTrend", supertrend_signal, (10, 3.0)),
    ("SuperTrend Tight", supertrend_signal, (10, 2.0)),
    ("Hull Suite", hull_signal, 55),
    ("PMax", pmax_signal, (10, 3.0)),
    ("OCC v8.13", occ_signal, None),
    ("SuperTrend+EMA50", supertrend_ema_signal, None),
    ("Volty+SuperTrend Combo", volty_st_combo, None),
    ("OCC+EMA50", occ_ema_signal, None),
]

print("\n" + "=" * 80)
print("  RUNNING {:d} STRATEGIES ON 6.5 YEARS OF DATA".format(len(strategies)))
print("=" * 80)

results = []
for name, fn, params in strategies:
    print("  Testing: {}...".format(name))
    r = run_backtest(name, fn, params)
    results.append(r)
    marker = "[WIN]" if r["ret"] > 0 else "[LOSS]"
    print("    {} ${:,.0f} ({:+.1f}%) DD:{:+.1f}% trades:{}".format(
        marker, r["final"], r["ret"], r["max_dd"], r["trades"]))

# Sort by return
results.sort(key=lambda r: r["ret"], reverse=True)

print("\n" + "=" * 85)
print("  FINAL RANKINGS (2019-2026, 6.5 years, $700 start, 60%x3x)")
print("=" * 85)
print("{:<5s} {:<30s} {:>10s} {:>8s} {:>8s} {:>8s} {:>6s}".format(
    "Rank", "Strategy", "Final", "Return", "MaxDD", "Pain", "Trades"))
print("-" * 85)
for i, r in enumerate(results):
    print("{:<5d} {:<30s} ${:>8,.0f} {:>+7.1f}% {:>+7.1f}% {:>7.2f} {:>6d}".format(
        i+1, r["name"], r["final"], r["ret"], r["max_dd"], r["pain"], r["trades"]))

# Save results
out = {
    "data_range": "{} -> {}".format(str(df.index[0])[:10], str(df.index[-1])[:10]),
    "total_candles": len(df),
    "config": "{}% x {}x, SL:{}%, TP:{}%".format(PCT, LEV, SL_PCT, TP_PCT),
    "rankings": [{"rank": i+1, "name": r["name"], "final": r["final"],
                  "return": r["ret"], "max_dd": r["max_dd"], "pain": r["pain"],
                  "trades": r["trades"]} for i, r in enumerate(results)]
}
with open("/opt/trading/strategy_comparison.json", "w") as f:
    json.dump(out, f, indent=2)
print("\nResults saved to /opt/trading/strategy_comparison.json")
print("=" * 85)
