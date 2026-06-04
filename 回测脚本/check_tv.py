import pandas as pd
import sys

df = pd.read_csv("/opt/trading/tv_v813.csv")

entries = df[df["Type"].str.contains("Entry", na=False)].copy()
entries["dt"] = pd.to_datetime(entries["Date and time"])
entries = entries.sort_values("dt")

print("First 25 TV entries:")
for i, (_, row) in enumerate(entries.head(25).iterrows()):
    print(f"  {i+1}. {row['dt']}  {row['Type']:<20} signal={row['Signal']}")

diffs = entries["dt"].diff().dropna()
diffs_min = diffs.dt.total_seconds() / 60
print(f"\nInterval stats: min={diffs_min.min():.0f}m  max={diffs_min.max():.0f}m  avg={diffs_min.mean():.0f}m  median={diffs_min.median():.0f}m")

short = diffs_min[diffs_min < 10]
print(f"Intervals < 10min: {len(short)}")
print(f"Intervals < 30min: {len(diffs_min[diffs_min < 30])}")

# Check entry direction distribution
longs = (entries["Type"].str.contains("long", case=False)).sum()
shorts = (entries["Type"].str.contains("short", case=False)).sum()
print(f"\nEntries: LONG={longs}  SHORT={shorts}  TOTAL={len(entries)}")
print(f"Time range: {entries['dt'].min()} to {entries['dt'].max()}")

# First trade detail
print("\nFirst trade (both rows):")
trade1 = df[df["Trade number"] == 1]
for _, r in trade1.iterrows():
    print(f"  {r['Type']}: {r['Date and time']} @ {r['Price USD']}")
