import ccxt, os
from dotenv import load_dotenv
load_dotenv('/opt/trading/.env')

b = ccxt.binance({
    "apiKey": os.getenv("BINANCE_API_KEY"),
    "secret": os.getenv("BINANCE_SECRET_KEY"),
    "enableRateLimit": True,
    "options": {"defaultType": "swap"},
})

# Try funding rate methods
for method in ['fetch_funding_rate', 'fetchFundingRate', 'fetch_funding_rate_history', 'publicGetFundingRate']:
    if hasattr(b, method):
        print(f"Has: {method}")
        try:
            if 'history' in method:
                result = getattr(b, method)('ETH/USDT:USDT')
            else:
                result = getattr(b, method)('ETH/USDT:USDT')
            print(f"  Result: {result}")
        except Exception as e:
            print(f"  Error: {e}")

# Also try fapiPublic
try:
    r = b.fapiPublicGetFundingRate({'symbol': 'ETHUSDT'})
    print(f"fapiPublicGetFundingRate: {r}")
except Exception as e:
    print(f"fapiPublicGetFundingRate Error: {e}")

try:
    r = b.fapiPublicGetPremiumIndex({'symbol': 'ETHUSDT'})
    print(f"fapiPublicGetPremiumIndex: {r}")
except Exception as e:
    print(f"fapiPublicGetPremiumIndex Error: {e}")
