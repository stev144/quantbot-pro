"""
test_data.py

This file is used to test our data fetching functions.
"""

#from data_fetcher import get_price, get_klines
from bot.data_fetcher import get_price, get_klines

# Test price fetch
price = get_price("BTCUSDT")
print("BTC Price:", price)

# Test candlestick fetch
df = get_klines("BTCUSDT")

# Print last 5 rows
print(df.tail())