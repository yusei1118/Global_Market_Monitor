from __future__ import annotations

ASSETS = [
    # Japan equities
    {"symbol": "7203.T", "name": "Toyota Motor", "category": "Japan Equity", "country": "Japan", "stale_minutes": 15},
    {"symbol": "8306.T", "name": "Mitsubishi UFJ Financial Group", "category": "Japan Equity", "country": "Japan", "stale_minutes": 15},
    {"symbol": "6758.T", "name": "Sony Group", "category": "Japan Equity", "country": "Japan", "stale_minutes": 15},
    {"symbol": "7974.T", "name": "Nintendo", "category": "Japan Equity", "country": "Japan", "stale_minutes": 15},
    {"symbol": "6501.T", "name": "Hitachi", "category": "Japan Equity", "country": "Japan", "stale_minutes": 15},
    {"symbol": "7011.T", "name": "Mitsubishi Heavy Industries", "category": "Japan Equity", "country": "Japan", "stale_minutes": 15},
    {"symbol": "9983.T", "name": "Fast Retailing", "category": "Japan Equity", "country": "Japan", "stale_minutes": 15},
    {"symbol": "8035.T", "name": "Tokyo Electron", "category": "Japan Equity", "country": "Japan", "stale_minutes": 15},
    {"symbol": "9984.T", "name": "SoftBank Group", "category": "Japan Equity", "country": "Japan", "stale_minutes": 15},
    {"symbol": "6861.T", "name": "Keyence", "category": "Japan Equity", "country": "Japan", "stale_minutes": 15},

    # US equities
    {"symbol": "JPM", "name": "JPMorgan Chase", "category": "US Equity", "country": "US", "stale_minutes": 15},
    {"symbol": "BAC", "name": "Bank of America", "category": "US Equity", "country": "US", "stale_minutes": 15},
    {"symbol": "XOM", "name": "Exxon Mobil", "category": "US Equity", "country": "US", "stale_minutes": 15},
    {"symbol": "CVX", "name": "Chevron", "category": "US Equity", "country": "US", "stale_minutes": 15},
    {"symbol": "WMT", "name": "Walmart", "category": "US Equity", "country": "US", "stale_minutes": 15},
    {"symbol": "COST", "name": "Costco", "category": "US Equity", "country": "US", "stale_minutes": 15},
    {"symbol": "LLY", "name": "Eli Lilly", "category": "US Equity", "country": "US", "stale_minutes": 15},
    {"symbol": "JNJ", "name": "Johnson & Johnson", "category": "US Equity", "country": "US", "stale_minutes": 15},
    {"symbol": "CAT", "name": "Caterpillar", "category": "US Equity", "country": "US", "stale_minutes": 15},
    {"symbol": "GE", "name": "GE Aerospace", "category": "US Equity", "country": "US", "stale_minutes": 15},

    # Tech
    {"symbol": "NVDA", "name": "NVIDIA", "category": "Tech", "country": "US", "stale_minutes": 15},
    {"symbol": "MSFT", "name": "Microsoft", "category": "Tech", "country": "US", "stale_minutes": 15},
    {"symbol": "AAPL", "name": "Apple", "category": "Tech", "country": "US", "stale_minutes": 15},
    {"symbol": "GOOGL", "name": "Alphabet", "category": "Tech", "country": "US", "stale_minutes": 15},
    {"symbol": "META", "name": "Meta Platforms", "category": "Tech", "country": "US", "stale_minutes": 15},

    # Indices / FX / commodity / crypto
    {"symbol": "^GSPC", "name": "S&P 500", "category": "Index", "country": "US", "stale_minutes": 15},
    {"symbol": "^IXIC", "name": "Nasdaq Composite", "category": "Index", "country": "US", "stale_minutes": 15},
    {"symbol": "^N225", "name": "Nikkei 225", "category": "Index", "country": "Japan", "stale_minutes": 15},
    {"symbol": "^VIX", "name": "CBOE Volatility Index", "category": "Volatility", "country": "US", "stale_minutes": 15},
    {"symbol": "JPY=X", "name": "USD/JPY", "category": "FX", "country": "Global", "stale_minutes": 10},
    {"symbol": "GC=F", "name": "Gold Futures", "category": "Commodity", "country": "Global", "stale_minutes": 15},
    {"symbol": "BTC-USD", "name": "Bitcoin", "category": "Crypto", "country": "Global", "stale_minutes": 10},
]

ASSET_BY_SYMBOL = {row["symbol"]: row for row in ASSETS}

FRED_SERIES = [
    {
        "series_id": "DFF",
        "country": "US",
        "indicator": "Effective Federal Funds Rate",
        "unit": "Percent",
        "frequency": "Daily",
        "transform": "level",
    },
    {
        "series_id": "DGS10",
        "country": "US",
        "indicator": "US 10Y Treasury Yield",
        "unit": "Percent",
        "frequency": "Daily",
        "transform": "level",
    },
    {
        "series_id": "CPIAUCSL",
        "country": "US",
        "indicator": "US CPI",
        "unit": "Index",
        "frequency": "Monthly",
        "transform": "yoy",
    },
    {
        "series_id": "IRLTLT01JPM156N",
        "country": "Japan",
        "indicator": "Japan 10Y Government Bond Yield",
        "unit": "Percent",
        "frequency": "Monthly",
        "transform": "level",
    },
    {
        "series_id": "IRSTCI01JPM156N",
        "country": "Japan",
        "indicator": "Japan Overnight Call / Interbank Rate",
        "unit": "Percent",
        "frequency": "Monthly",
        "transform": "level",
    },
]
