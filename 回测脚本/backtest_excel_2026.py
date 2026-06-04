"""2026年 Volty 策略回测 → Excel | 仓位: 50% × 3x | 起始: $700"""
import ccxt, pandas as pd, numpy as np, time as _time, os, platform
from datetime import datetime, timezone
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

VLEN, VMULT, PCT, LEV, SL_PCT = 5, 0.75, 50, 3, 5
INIT = 700.0
SYMBOL = "ETH/USDT:USDT"
TIMEFRAME = "15m"

print("=" * 60)
print("  2026年 Volty 策略回测 -> Excel")
print(f"  仓位: {PCT}% x {LEV}x | 起始: ${INIT}")
print("=" * 60)

# === Download data ===
print(">>> Downloading 2026 ETHUSDT 15m...")
ex = ccxt.binance({"enableRateLimit": True, "timeout": 30000})
since = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
end_ts = int(datetime(2026, 6, 4, tzinfo=timezone.utc).timestamp() * 1000)

all_klines = []
while since < end_ts:
    try:
        klines = ex.fetch_ohlcv(SYMBOL, TIMEFRAME, since=since, limit=1000)
        if not klines: break
        all_klines.extend(klines)
        since = klines[-1][0] + 1
        if len(all_klines) % 3000 == 0:
            print(f"  {len(all_klines)} candles...")
    except Exception as e:
        _time.sleep(1)

df = pd.DataFrame(all_klines, columns=["ts", "open", "high", "low", "close", "volume"])
df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
df.set_index("ts", inplace=True)
df = df[(df.index >= "2026-01-01") & (df.index < "2026-06-04")]
print(f"  Total: {len(df)} candles")

# === Indicators ===
tr = pd.concat([df["high"] - df["low"], (df["high"] - df["close"].shift(1)).abs(), (df["low"] - df["close"].shift(1)).abs()], axis=1).max(axis=1)
atr_sig = tr.rolling(VLEN).mean() * VMULT
ema50 = df["close"].ewm(span=50, adjust=False).mean()

# === Backtest ===
print(">>> Backtesting...")
bal = INIT; pos = None; ep = cts = uv = 0.0
trades = []
equity_daily = {}
START = max(VLEN + 3, 53)

for i in range(START, len(df)):
    hi, lo, op = df["high"].iloc[i], df["low"].iloc[i], df["open"].iloc[i]
    tm = df.index[i]
    date_key = str(tm.date())

    # Stop loss
    if pos == "LONG":
        sp = ep * (1 - SL_PCT / 100)
        if lo <= sp:
            pnl = (sp - ep) / ep * uv; bal += pnl
            trades.append([len(trades)+1, str(tm.date()), str(tm.time())[:5], "做多", "止损平仓",
                          round(ep,2), round(sp,2), round(uv,2), round(pnl,2), round(bal,2),
                          int(tm.isocalendar()[1]), int(tm.month)])
            pos = None; equity_daily[date_key] = bal; continue
    if pos == "SHORT":
        sp = ep * (1 + SL_PCT / 100)
        if hi >= sp:
            pnl = (ep - sp) / ep * uv; bal += pnl
            trades.append([len(trades)+1, str(tm.date()), str(tm.time())[:5], "做空", "止损平仓",
                          round(ep,2), round(sp,2), round(uv,2), round(pnl,2), round(bal,2),
                          int(tm.isocalendar()[1]), int(tm.month)])
            pos = None; equity_daily[date_key] = bal; continue

    pa = atr_sig.iloc[i - 1]
    if pd.isna(pa) or pa <= 0: continue
    pc = df["close"].iloc[i - 1]
    ll, sl = pc + pa, pc - pa

    pe = ema50.iloc[i - 1]
    tb = df["close"].iloc[i - 1] > pe
    tbe = df["close"].iloc[i - 1] < pe

    lt = hi >= ll and tb and pos != "LONG"
    st = lo <= sl and tbe and pos != "SHORT"
    if lt and st:
        if abs(op - ll) > abs(op - sl): lt = False
        else: st = False

    if lt:
        if pos == "SHORT":
            xp = ll; pl = (ep - xp) / ep * uv; bal += pl
            trades.append([len(trades)+1, str(tm.date()), str(tm.time())[:5], "做空->做多", "翻转平仓",
                          round(ep,2), round(xp,2), round(uv,2), round(pl,2), round(bal,2),
                          int(tm.isocalendar()[1]), int(tm.month)])
            pos = None; equity_daily[date_key] = bal
        if bal > 10:
            cap = bal * PCT / 100.0; uv = cap * LEV; ep = ll; cts = uv / ep; pos = "LONG"
            trades.append([len(trades)+1, str(tm.date()), str(tm.time())[:5], "做多", "开仓",
                          round(ep,2), 0, round(uv,2), 0, round(bal,2),
                          int(tm.isocalendar()[1]), int(tm.month)])
            equity_daily[date_key] = bal
    elif st:
        if pos == "LONG":
            xp = sl; pl = (xp - ep) / ep * uv; bal += pl
            trades.append([len(trades)+1, str(tm.date()), str(tm.time())[:5], "做多->做空", "翻转平仓",
                          round(ep,2), round(xp,2), round(uv,2), round(pl,2), round(bal,2),
                          int(tm.isocalendar()[1]), int(tm.month)])
            pos = None; equity_daily[date_key] = bal
        if bal > 10:
            cap = bal * PCT / 100.0; uv = cap * LEV; ep = sl; cts = uv / ep; pos = "SHORT"
            trades.append([len(trades)+1, str(tm.date()), str(tm.time())[:5], "做空", "开仓",
                          round(ep,2), 0, round(uv,2), 0, round(bal,2),
                          int(tm.isocalendar()[1]), int(tm.month)])
            equity_daily[date_key] = bal

if pos:
    lp = df["close"].iloc[-1]
    pl = (lp - ep) / ep * uv if pos == "LONG" else (ep - lp) / ep * uv
    bal += pl
    trades.append([len(trades)+1, str(df.index[-1].date()), str(df.index[-1].time())[:5],
                  "做多" if pos=="LONG" else "做空", "最终平仓",
                  round(ep,2), round(lp,2), round(uv,2), round(pl,2), round(bal,2),
                  int(df.index[-1].isocalendar()[1]), int(df.index[-1].month)])

cols = ["序号","日期","时间","方向","动作","入场价","出场价","持仓价值U","盈亏U","余额U","周","月"]
trades_df = pd.DataFrame(trades, columns=cols)
final_bal = round(bal, 2)
total_ret = round((final_bal - INIT) / INIT * 100, 2)

# === Stats ===
print(">>> Computing stats...")
closed = trades_df[trades_df.iloc[:, 8] != 0].copy()
wins = closed[closed.iloc[:, 8] > 0]
losses = closed[closed.iloc[:, 8] < 0]

# Daily equity
eq_df = pd.DataFrame(list(equity_daily.items()), columns=["date", "equity"])
eq_df["date"] = pd.to_datetime(eq_df["date"]); eq_df = eq_df.sort_values("date"); eq_df.set_index("date", inplace=True)
eq_df["peak"] = eq_df["equity"].cummax()
eq_df["dd"] = (eq_df["equity"] - eq_df["peak"]) / eq_df["peak"] * 100
max_dd = round(eq_df["dd"].min(), 2)
max_dd_date = str(eq_df["dd"].idxmin())[:10]

# Weekly
weekly_data = []
for w in sorted(trades_df.iloc[:, 10].unique()):
    wt = closed[closed.iloc[:, 10] == w]
    if wt.empty: continue
    wpnl = round(wt.iloc[:, 8].sum(), 2)
    prev = closed[closed.iloc[:, 10] < w]
    ws = prev.iloc[-1, 9] if not prev.empty else INIT
    we = wt.iloc[-1, 9]
    wr = round((we - ws) / ws * 100, 2) if ws > 0 else 0
    weekly_data.append([f"第{int(w)}周", len(wt), len(wt[wt.iloc[:,8]>0]), len(wt[wt.iloc[:,8]<0]), wpnl, wr, round(ws,2), round(we,2)])

weekly_df = pd.DataFrame(weekly_data, columns=["周","交易次数","盈利次数","亏损次数","盈亏U","收益率%","周初余额U","周末余额U"])

# Monthly (Jan-Jun 2026)
monthly_data = []
for m in range(1, 7):
    mt = closed[closed.iloc[:, 11] == m]
    if mt.empty:
        monthly_data.append([f"{m}月", 0, 0, 0, 0, 0, 0, 0]); continue
    mpnl = round(mt.iloc[:, 8].sum(), 2)
    prev = closed[closed.iloc[:, 11] < m]
    ms = prev.iloc[-1, 9] if not prev.empty else INIT
    me = mt.iloc[-1, 9]
    mr = round((me - ms) / ms * 100, 2) if ms > 0 else 0
    monthly_data.append([f"{m}月", len(mt), len(mt[mt.iloc[:,8]>0]), len(mt[mt.iloc[:,8]<0]), mpnl, mr, round(ms,2), round(me,2)])

monthly_df = pd.DataFrame(monthly_data, columns=["月份","交易次数","盈利次数","亏损次数","盈亏U","收益率%","月初余额U","月末余额U"])

# === Excel ===
print(">>> Generating Excel...")
wb = Workbook()

# Styles
hf = Font(name="Microsoft YaHei", size=11, bold=True, color="FFFFFF")
hfill = PatternFill(start_color="1a1a2e", end_color="1a1a2e", fill_type="solid")
tf = Font(name="Microsoft YaHei", size=16, bold=True, color="1a1a2e")
sf = Font(name="Microsoft YaHei", size=10, color="666666")
df_ = Font(name="Microsoft YaHei", size=10)
gf = Font(name="Microsoft YaHei", size=10, color="006100")
rf_ = Font(name="Microsoft YaHei", size=10, color="9C0006")
gfill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
rfill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
yfill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
lfill = PatternFill(start_color="F5F5F5", end_color="F5F5F5", fill_type="solid")
tb = Border(left=Side(style="thin",color="D0D0D0"),right=Side(style="thin",color="D0D0D0"),
            top=Side(style="thin",color="D0D0D0"),bottom=Side(style="thin",color="D0D0D0"))

def sh(ws, row, n):
    for c in range(1, n+1):
        cl = ws.cell(row=row, column=c); cl.font = hf; cl.fill = hfill
        cl.alignment = Alignment(horizontal="center", vertical="center"); cl.border = tb

def sd(ws, row, n, even=False):
    for c in range(1, n+1):
        cl = ws.cell(row=row, column=c); cl.font = df_; cl.border = tb
        cl.alignment = Alignment(horizontal="center" if c > 2 else "left")
        if even: cl.fill = lfill

# Sheet 1: Trade Details
ws1 = wb.active; ws1.title = "Transaction Details"
ws1.merge_cells("A1:L1")
ws1.cell(row=1, column=1, value="2026 Volty Expan Close Strategy - Trade Details").font = tf
ws1.merge_cells("A2:L2")
ws1.cell(row=2, column=1, value=f"Position: {PCT}% x {LEV}x | Start: ${INIT} | End: ${final_bal:,.0f} | Return: {total_ret:+.1f}%").font = sf

h1 = ["Number","Date","Time","Direction","Action","Entry(USDT)","Exit(USDT)","Value(U)","PnL(U)","Balance(U)","Week","Month"]
for c, h in enumerate(h1, 1): ws1.cell(row=4, column=c, value=h)
sh(ws1, 4, len(h1))

for i in range(len(trades_df)):
    r = i + 5; row = trades_df.iloc[i]
    for c in range(len(h1)): ws1.cell(row=r, column=c+1, value=row.iloc[c] if c < 10 else int(row.iloc[c]))
    sd(ws1, r, len(h1), i % 2 == 0)
    pc = ws1.cell(row=r, column=9)
    if float(row.iloc[8]) > 0: pc.font = gf; pc.fill = gfill
    elif float(row.iloc[8]) < 0: pc.font = rf_; pc.fill = rfill

for c in range(1, len(h1)+1):
    mx = max(len(str(ws1.cell(row=r, column=c).value or "")) for r in range(4, len(trades_df)+5))
    ws1.column_dimensions[get_column_letter(c)].width = min(mx + 4, 22)
ws1.freeze_panes = "A5"

# Sheet 2: Monthly
ws2 = wb.create_sheet("Monthly Summary")
ws2.merge_cells("A1:H1"); ws2.cell(row=1, column=1, value="2026 Monthly PnL Summary").font = tf
h2 = ["Month","Trades","Wins","Losses","PnL(U)","Return%","Start Balance(U)","End Balance(U)"]
for c, h in enumerate(h2, 1): ws2.cell(row=3, column=c, value=h)
sh(ws2, 3, len(h2))

for i in range(len(monthly_df)):
    r = i + 4; row = monthly_df.iloc[i]
    for c in range(len(h2)): ws2.cell(row=r, column=c+1, value=row.iloc[c])
    sd(ws2, r, len(h2), i % 2 == 0)
    rc = ws2.cell(row=r, column=6)
    if float(row.iloc[5]) > 0: rc.font = gf; rc.fill = gfill
    elif float(row.iloc[5]) < 0: rc.font = rf_; rc.fill = rfill

sr = len(monthly_df) + 5
ws2.cell(row=sr, column=1, value="Total").font = Font(name="Microsoft YaHei", size=10, bold=True)
for c in range(1, 5): ws2.cell(row=sr, column=c+1, value=int(monthly_df.iloc[:, c].sum()))
ws2.cell(row=sr, column=5, value=round(monthly_df.iloc[:, 4].sum(), 2))
ws2.cell(row=sr, column=6, value=total_ret)
ws2.cell(row=sr, column=7, value=INIT)
ws2.cell(row=sr, column=8, value=final_bal)
for c in range(1, 9): ws2.cell(row=sr, column=c).font = Font(name="Microsoft YaHei", size=10, bold=True); ws2.cell(row=sr, column=c).border = tb; ws2.cell(row=sr, column=c).fill = yfill

for c in range(1, 9):
    mx = max(len(str(ws2.cell(row=r, column=c).value or "")) for r in range(3, sr+1))
    ws2.column_dimensions[get_column_letter(c)].width = min(mx + 4, 22)
ws2.freeze_panes = "A4"

# Sheet 3: Weekly
ws3 = wb.create_sheet("Weekly Summary")
ws3.merge_cells("A1:H1"); ws3.cell(row=1, column=1, value="2026 Weekly PnL Summary").font = tf
h3 = ["Week","Trades","Wins","Losses","PnL(U)","Return%","Start Balance(U)","End Balance(U)"]
for c, h in enumerate(h3, 1): ws3.cell(row=3, column=c, value=h)
sh(ws3, 3, len(h3))

for i in range(len(weekly_df)):
    r = i + 4; row = weekly_df.iloc[i]
    for c in range(len(h3)): ws3.cell(row=r, column=c+1, value=row.iloc[c])
    sd(ws3, r, len(h3), i % 2 == 0)
    rc = ws3.cell(row=r, column=6)
    if float(row.iloc[5]) > 0: rc.font = gf; rc.fill = gfill
    elif float(row.iloc[5]) < 0: rc.font = rf_; rc.fill = rfill

for c in range(1, 9):
    mx = max(len(str(ws3.cell(row=r, column=c).value or "")) for r in range(3, len(weekly_df)+4))
    ws3.column_dimensions[get_column_letter(c)].width = min(mx + 4, 22)
ws3.freeze_panes = "A4"

# Sheet 4: Statistics
ws4 = wb.create_sheet("Statistics")
ws4.merge_cells("A1:B1"); ws4.cell(row=1, column=1, value="2026 Strategy Statistics").font = tf

stats = [
    ("Strategy", "Volty Expan Close"), ("Symbol", "ETHUSDT Perpetual"),
    ("Timeframe", "15min"), ("Params", f"VLEN={VLEN}, MULT={VMULT}"),
    ("Trend Filter", "EMA50"), ("Position", f"{PCT}% x {LEV}x"),
    ("Stop Loss", f"{SL_PCT}%"), ("", ""),
    ("Start Balance", f"${INIT:,.2f}"), ("End Balance", f"${final_bal:,.2f}"),
    ("Total PnL", f"${final_bal-INIT:+,.2f}"), ("Total Return", f"{total_ret:+.2f}%"),
    ("", ""), ("Total Trades", len(trades_df)),
    ("Closed Trades", len(closed)), ("Wins", len(wins)),
    ("Losses", len(losses)),
    ("Win Rate", f"{len(wins)/len(closed)*100:.1f}%" if len(closed) > 0 else "N/A"),
    ("Avg Win", f"${wins.iloc[:,8].mean():+,.0f}" if len(wins) > 0 else "N/A"),
    ("Avg Loss", f"${losses.iloc[:,8].mean():+,.0f}" if len(losses) > 0 else "N/A"),
    ("Profit Factor", f"{abs(wins.iloc[:,8].mean()/losses.iloc[:,8].mean()):.2f}" if len(wins) > 0 and len(losses) > 0 else "N/A"),
    ("Max Win", f"${wins.iloc[:,8].max():+,.0f}" if len(wins) > 0 else "N/A"),
    ("Max Loss", f"${losses.iloc[:,8].min():+,.0f}" if len(losses) > 0 else "N/A"),
    ("", ""), ("Max Drawdown", f"{max_dd:+.2f}%"),
    ("Max DD Date", max_dd_date),
    ("Profitable Months", f"{len([m for m in monthly_data if m[5] > 0])}/6"),
    ("Profitable Weeks", f"{len([w for w in weekly_data if w[5] > 0])}/{len(weekly_data)}"),
    ("Best Month", f"{max(monthly_data, key=lambda x: x[5])[0]} ({max(monthly_data, key=lambda x: x[5])[5]:+.1f}%)"),
    ("Worst Month", f"{min(monthly_data, key=lambda x: x[5])[0]} ({min(monthly_data, key=lambda x: x[5])[5]:+.1f}%)"),
]

for i, (label, value) in enumerate(stats):
    r = i + 3
    ws4.cell(row=r, column=1, value=label).font = Font(name="Microsoft YaHei", size=10, bold=True)
    ws4.cell(row=r, column=2, value=value).font = df_
    ws4.cell(row=r, column=1).border = tb; ws4.cell(row=r, column=2).border = tb
    if i % 2: ws4.cell(row=r, column=1).fill = lfill; ws4.cell(row=r, column=2).fill = lfill

ws4.column_dimensions["A"].width = 22; ws4.column_dimensions["B"].width = 30

# Save
if platform.system() == "Windows":
    out = r"C:\Users\周勇\Desktop\量化交易\回测结果\2026年Volty策略回测报表_50pct_3x.xlsx"
else:
    out = "/opt/trading/2026年Volty策略回测报表_50pct_3x.xlsx"
od = os.path.dirname(out)
if od: os.makedirs(od, exist_ok=True)
wb.save(out)

print(f"\n{'='*60}")
print(f"  Excel saved: {out}")
print(f"{'='*60}")
print(f"  Final: ${INIT:,} -> ${final_bal:,.0f} ({total_ret:+.1f}%)")
print(f"  {len(trades_df)} trades, {len(closed)} closed")
print(f"  {len(wins)}W/{len(losses)}L, WR {len(wins)/len(closed)*100:.1f}%")
print(f"  Max DD: {max_dd:+.1f}%")
print(f"  Best: {max(monthly_data, key=lambda x: x[5])[0]}, Worst: {min(monthly_data, key=lambda x: x[5])[0]}")
