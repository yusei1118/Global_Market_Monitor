from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

from config import ASSETS, ASSET_BY_SYMBOL, FRED_SERIES

load_dotenv()

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "market_monitor.db"

DASHBOARD_DIR = ROOT / "dashboard_data"
QUALITY_HISTORY_EXPORT_LIMIT = 10000

MARKET_INTERVAL = os.getenv("MARKET_INTERVAL", "5m")
MARKET_PERIOD = os.getenv("MARKET_PERIOD", "5d")

FRED_API_KEY = os.getenv("FRED_API_KEY", "").strip()

# Optional Japan CPI source through the official e-Stat API.
ESTAT_APP_ID = os.getenv("ESTAT_APP_ID", "").strip()
ESTAT_STATS_DATA_ID = os.getenv("ESTAT_STATS_DATA_ID", "0004052037").strip()

# Optional Alpaca Market Data API for best bid/ask quotes.
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "").strip()
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "").strip()

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
ESTAT_URL = "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData"
ALPACA_STOCK_QUOTES_URL = "https://data.alpaca.markets/v2/stocks/quotes/latest"
ALPACA_CRYPTO_QUOTES_URL = "https://data.alpaca.markets/v1beta3/crypto/us/latest/quotes"


# Monitoring thresholds used by the portfolio data-quality layer.
SPREAD_THRESHOLDS_BPS = {
    "US Equity": 100.0,
    "Tech": 100.0,
    "Crypto": 50.0,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value) -> str:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.isoformat()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS raw_market (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            ingested_at_utc TEXT NOT NULL,
            source_timestamp_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            country TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            source TEXT NOT NULL,
            interval TEXT NOT NULL,
            UNIQUE(symbol, source_timestamp_utc, source, interval)
        );

        CREATE TABLE IF NOT EXISTS clean_market (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            ingested_at_utc TEXT NOT NULL,
            source_timestamp_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            country TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            return_1bar REAL,
            zscore_20 REAL,
            missing_flag TEXT NOT NULL,
            duplicate_flag TEXT NOT NULL,
            outlier_flag TEXT NOT NULL,
            source TEXT NOT NULL,
            interval TEXT NOT NULL,
            UNIQUE(symbol, source_timestamp_utc, source, interval)
        );

        CREATE TABLE IF NOT EXISTS market_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            ingested_at_utc TEXT NOT NULL,
            source_timestamp_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            country TEXT NOT NULL,
            close REAL,
            volume REAL,
            return_1bar REAL,
            move_bps REAL,
            zscore_20 REAL,
            age_minutes REAL,
            stale_threshold_minutes REAL,
            market_expected_open TEXT NOT NULL,
            missing_flag TEXT NOT NULL,
            stale_flag TEXT NOT NULL,
            outlier_flag TEXT NOT NULL,
            bid REAL,
            ask REAL,
            mid REAL,
            spread_bps REAL,
            spread_status TEXT NOT NULL,
            crossed_flag TEXT NOT NULL,
            locked_flag TEXT NOT NULL,
            quality_status TEXT NOT NULL,
            source TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_snapshot_symbol_time
        ON market_snapshot(symbol, ingested_at_utc);

        CREATE TABLE IF NOT EXISTS quote_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            ingested_at_utc TEXT NOT NULL,
            quote_timestamp_utc TEXT,
            symbol TEXT NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            country TEXT NOT NULL,
            bid REAL,
            ask REAL,
            bid_size REAL,
            ask_size REAL,
            mid REAL,
            spread REAL,
            spread_bps REAL,
            quote_age_seconds REAL,
            market_expected_open TEXT NOT NULL,
            missing_flag TEXT NOT NULL,
            stale_flag TEXT NOT NULL,
            crossed_flag TEXT NOT NULL,
            locked_flag TEXT NOT NULL,
            quality_status TEXT NOT NULL,
            source TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_quote_snapshot_symbol_time
        ON quote_snapshot(symbol, ingested_at_utc);

        CREATE TABLE IF NOT EXISTS macro_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            ingested_at_utc TEXT NOT NULL,
            source_date TEXT NOT NULL,
            country TEXT NOT NULL,
            indicator TEXT NOT NULL,
            series_id TEXT NOT NULL,
            value REAL,
            yoy_percent REAL,
            unit TEXT,
            frequency TEXT,
            source TEXT NOT NULL,
            UNIQUE(series_id, source_date, source)
        );

        CREATE TABLE IF NOT EXISTS quality_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            ingested_at_utc TEXT NOT NULL,
            dataset TEXT NOT NULL,
            rows_received INTEGER NOT NULL DEFAULT 0,
            rows_inserted INTEGER NOT NULL DEFAULT 0,
            missing_count INTEGER NOT NULL DEFAULT 0,
            duplicate_count INTEGER NOT NULL DEFAULT 0,
            stale_count INTEGER NOT NULL DEFAULT 0,
            outlier_count INTEGER NOT NULL DEFAULT 0,
            crossed_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            notes TEXT
        );


        CREATE TABLE IF NOT EXISTS incident_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_key TEXT NOT NULL UNIQUE,
            dataset TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            issue_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            first_seen_utc TEXT NOT NULL,
            last_seen_utc TEXT NOT NULL,
            resolved_at_utc TEXT,
            opened_run_id TEXT NOT NULL,
            last_run_id TEXT NOT NULL,
            occurrence_count INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'OPEN',
            observed_value REAL,
            threshold_value REAL,
            details TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_incident_status
        ON incident_log(status, severity, last_seen_utc);

        CREATE TABLE IF NOT EXISTS quality_score (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL UNIQUE,
            ingested_at_utc TEXT NOT NULL,
            market_records INTEGER NOT NULL DEFAULT 0,
            quote_records INTEGER NOT NULL DEFAULT 0,
            evaluated_records INTEGER NOT NULL DEFAULT 0,
            completeness_pct REAL NOT NULL,
            timeliness_pct REAL NOT NULL,
            validity_pct REAL NOT NULL,
            consistency_pct REAL NOT NULL,
            overall_score REAL NOT NULL,
            status TEXT NOT NULL,
            open_incident_count INTEGER NOT NULL DEFAULT 0,
            critical_incident_count INTEGER NOT NULL DEFAULT 0,
            high_incident_count INTEGER NOT NULL DEFAULT 0,
            medium_incident_count INTEGER NOT NULL DEFAULT 0,
            notes TEXT
        );

        CREATE VIEW IF NOT EXISTS open_incidents AS
        SELECT *
        FROM incident_log
        WHERE status = 'OPEN';

        CREATE VIEW IF NOT EXISTS latest_quality_score AS
        SELECT *
        FROM quality_score
        WHERE id = (SELECT MAX(id) FROM quality_score);
        """
    )
    conn.commit()


def market_expected_open(asset: dict, now_utc: datetime) -> bool:
    """
    Approximate session-awareness so an equity is not marked STALE overnight.
    This is intentionally simple for the portfolio version.
    """
    category = asset["category"]
    country = asset["country"]

    if category == "Crypto":
        return True

    if category == "FX":
        ny = now_utc.astimezone(ZoneInfo("America/New_York"))
        return ny.weekday() < 5

    if category == "Commodity":
        ny = now_utc.astimezone(ZoneInfo("America/New_York"))
        return ny.weekday() < 5

    if country == "US":
        local = now_utc.astimezone(ZoneInfo("America/New_York"))
        if local.weekday() >= 5:
            return False
        return time(9, 30) <= local.time().replace(tzinfo=None) <= time(16, 0)

    if country == "Japan":
        local = now_utc.astimezone(ZoneInfo("Asia/Tokyo"))
        if local.weekday() >= 5:
            return False
        t = local.time().replace(tzinfo=None)
        morning = time(9, 0) <= t <= time(11, 30)
        afternoon = time(12, 30) <= t <= time(15, 30)
        return morning or afternoon

    return False


def normalize_yfinance_download(data: pd.DataFrame, run_id: str, ingested_at: datetime) -> pd.DataFrame:
    frames = []

    if data is None or data.empty:
        return pd.DataFrame()

    multi = isinstance(data.columns, pd.MultiIndex)

    for asset in ASSETS:
        symbol = asset["symbol"]

        try:
            if multi:
                if symbol not in data.columns.get_level_values(0):
                    continue
                sub = data[symbol].copy()
            else:
                # This path is mostly for a single-ticker download.
                sub = data.copy()

            if sub.empty:
                continue

            sub = sub.reset_index()
            ts_col = "Datetime" if "Datetime" in sub.columns else "Date"
            if ts_col not in sub.columns:
                ts_col = sub.columns[0]

            rename_map = {
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
            sub = sub.rename(columns=rename_map)

            for col in ["open", "high", "low", "close", "volume"]:
                if col not in sub.columns:
                    sub[col] = np.nan

            sub["source_timestamp_utc"] = sub[ts_col].map(iso_utc)
            sub["run_id"] = run_id
            sub["ingested_at_utc"] = ingested_at.isoformat()
            sub["symbol"] = symbol
            sub["name"] = asset["name"]
            sub["category"] = asset["category"]
            sub["country"] = asset["country"]
            sub["source"] = "Yahoo Finance via yfinance"
            sub["interval"] = MARKET_INTERVAL

            frames.append(
                sub[
                    [
                        "run_id",
                        "ingested_at_utc",
                        "source_timestamp_utc",
                        "symbol",
                        "name",
                        "category",
                        "country",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "source",
                        "interval",
                    ]
                ]
            )
        except Exception as exc:
            print(f"[WARN] Could not normalize {symbol}: {exc}", file=sys.stderr)

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result["volume"] = pd.to_numeric(result["volume"], errors="coerce")
    return result


def fetch_market(run_id: str, ingested_at: datetime) -> pd.DataFrame:
    symbols = [a["symbol"] for a in ASSETS]
    print(f"[INFO] Downloading {len(symbols)} market instruments...")
    data = yf.download(
        tickers=symbols,
        period=MARKET_PERIOD,
        interval=MARKET_INTERVAL,
        group_by="ticker",
        auto_adjust=False,
        actions=False,
        threads=True,
        progress=False,
        prepost=False,
        keepna=True,
        timeout=20,
    )
    return normalize_yfinance_download(data, run_id, ingested_at)


def clean_market(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return raw.copy()

    df = raw.copy()
    df["source_timestamp_utc"] = pd.to_datetime(df["source_timestamp_utc"], utc=True)

    df["missing_flag"] = np.where(
        df[["source_timestamp_utc", "symbol", "close"]].isna().any(axis=1),
        "MISSING",
        "OK",
    )

    dup_mask = df.duplicated(
        subset=["symbol", "source_timestamp_utc", "source", "interval"],
        keep=False,
    )
    df["duplicate_flag"] = np.where(dup_mask, "DUPLICATE", "OK")

    df = df.sort_values(["symbol", "source_timestamp_utc"]).copy()
    df["return_1bar"] = df.groupby("symbol")["close"].pct_change(fill_method=None)

    # Pandas 3.x compatibility:
    # avoid groupby.apply(), which can drop grouping columns such as "symbol".
    grouped_returns = df.groupby("symbol")["return_1bar"]

    rolling_mean = grouped_returns.transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )
    rolling_std = grouped_returns.transform(
        lambda s: s.rolling(20, min_periods=10).std(ddof=0)
    )

    df["zscore_20"] = (
        (df["return_1bar"] - rolling_mean)
        / rolling_std.replace(0, np.nan)
    )
    df["outlier_flag"] = np.where(
        df["zscore_20"].abs() >= 3,
        "OUTLIER",
        "OK",
    )

    return df


def make_snapshot(clean: pd.DataFrame, run_id: str, ingested_at: datetime) -> pd.DataFrame:
    if clean.empty:
        return pd.DataFrame()

    rows = []
    for symbol, group in clean.groupby("symbol"):
        group = group.sort_values("source_timestamp_utc")
        asset = ASSET_BY_SYMBOL[symbol]

        # yfinance with keepna=True can include trailing empty bars after a
        # market has closed. Keep those rows in RAW/CLEAN for data-quality
        # analysis, but do not treat an empty bar as the latest market price.
        valid_close = group[group["close"].notna()]

        if valid_close.empty:
            latest = group.iloc[-1]
        else:
            latest = valid_close.iloc[-1]

        source_ts = pd.Timestamp(latest["source_timestamp_utc"]).to_pydatetime()
        age_minutes = max(0.0, (ingested_at - source_ts).total_seconds() / 60.0)
        expected_open = market_expected_open(asset, ingested_at)
        threshold = float(asset["stale_minutes"])

        if expected_open:
            stale_flag = "STALE" if age_minutes > threshold else "OK"
        else:
            stale_flag = "MARKET_CLOSED"

        # yfinance historical bars do not reliably provide executable bid/ask.
        bid = np.nan
        ask = np.nan
        mid = np.nan
        spread_bps = np.nan
        spread_status = "NOT_AVAILABLE"
        crossed_flag = "NOT_AVAILABLE"
        locked_flag = "NOT_AVAILABLE"

        missing_flag = latest["missing_flag"]
        outlier_flag = latest["outlier_flag"]

        if missing_flag != "OK" or stale_flag == "STALE" or outlier_flag != "OK":
            quality_status = "REVIEW"
        else:
            quality_status = "PASS"

        return_1bar = latest["return_1bar"]
        move_bps = return_1bar * 10000 if pd.notna(return_1bar) else np.nan

        rows.append(
            {
                "run_id": run_id,
                "ingested_at_utc": ingested_at.isoformat(),
                "source_timestamp_utc": source_ts.isoformat(),
                "symbol": symbol,
                "name": asset["name"],
                "category": asset["category"],
                "country": asset["country"],
                "close": latest["close"],
                "volume": latest["volume"],
                "return_1bar": return_1bar,
                "move_bps": move_bps,
                "zscore_20": latest["zscore_20"],
                "age_minutes": age_minutes,
                "stale_threshold_minutes": threshold,
                "market_expected_open": "YES" if expected_open else "NO",
                "missing_flag": missing_flag,
                "stale_flag": stale_flag,
                "outlier_flag": outlier_flag,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "spread_bps": spread_bps,
                "spread_status": spread_status,
                "crossed_flag": crossed_flag,
                "locked_flag": locked_flag,
                "quality_status": quality_status,
                "source": latest["source"],
            }
        )

    return pd.DataFrame(rows)



def alpaca_headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }


def _quote_quality_row(
    *,
    run_id: str,
    ingested_at: datetime,
    symbol: str,
    bid,
    ask,
    bid_size,
    ask_size,
    quote_timestamp,
    source: str,
    stale_seconds: int,
) -> dict:
    asset = ASSET_BY_SYMBOL[symbol]

    bid = pd.to_numeric(bid, errors="coerce")
    ask = pd.to_numeric(ask, errors="coerce")
    bid_size = pd.to_numeric(bid_size, errors="coerce")
    ask_size = pd.to_numeric(ask_size, errors="coerce")

    missing = (
        pd.isna(bid)
        or pd.isna(ask)
        or float(bid) <= 0
        or float(ask) <= 0
    )
    missing_flag = "MISSING" if missing else "OK"

    if quote_timestamp:
        quote_ts = pd.Timestamp(quote_timestamp)
        if quote_ts.tzinfo is None:
            quote_ts = quote_ts.tz_localize("UTC")
        else:
            quote_ts = quote_ts.tz_convert("UTC")
        quote_timestamp_utc = quote_ts.isoformat()
        quote_age_seconds = max(
            0.0,
            (ingested_at - quote_ts.to_pydatetime()).total_seconds(),
        )
    else:
        quote_timestamp_utc = None
        quote_age_seconds = np.nan

    expected_open = market_expected_open(asset, ingested_at)

    if asset["category"] == "Crypto":
        stale_flag = (
            "STALE"
            if pd.isna(quote_age_seconds) or quote_age_seconds > stale_seconds
            else "OK"
        )
    elif expected_open:
        stale_flag = (
            "STALE"
            if pd.isna(quote_age_seconds) or quote_age_seconds > stale_seconds
            else "OK"
        )
    else:
        stale_flag = "MARKET_CLOSED"

    if missing:
        mid = np.nan
        spread = np.nan
        spread_bps = np.nan
        crossed_flag = "NOT_AVAILABLE"
        locked_flag = "NOT_AVAILABLE"
    else:
        bid_f = float(bid)
        ask_f = float(ask)
        mid = (bid_f + ask_f) / 2.0
        spread = ask_f - bid_f
        spread_bps = (spread / mid) * 10000 if mid else np.nan
        crossed_flag = "CROSSED" if bid_f > ask_f else "OK"
        locked_flag = "LOCKED" if bid_f == ask_f else "OK"

    if (
        missing_flag != "OK"
        or stale_flag == "STALE"
        or crossed_flag == "CROSSED"
        or locked_flag == "LOCKED"
    ):
        quality_status = "REVIEW"
    else:
        quality_status = "PASS"

    return {
        "run_id": run_id,
        "ingested_at_utc": ingested_at.isoformat(),
        "quote_timestamp_utc": quote_timestamp_utc,
        "symbol": symbol,
        "name": asset["name"],
        "category": asset["category"],
        "country": asset["country"],
        "bid": None if pd.isna(bid) else float(bid),
        "ask": None if pd.isna(ask) else float(ask),
        "bid_size": None if pd.isna(bid_size) else float(bid_size),
        "ask_size": None if pd.isna(ask_size) else float(ask_size),
        "mid": None if pd.isna(mid) else float(mid),
        "spread": None if pd.isna(spread) else float(spread),
        "spread_bps": None if pd.isna(spread_bps) else float(spread_bps),
        "quote_age_seconds": (
            None if pd.isna(quote_age_seconds) else float(quote_age_seconds)
        ),
        "market_expected_open": "YES" if expected_open else "NO",
        "missing_flag": missing_flag,
        "stale_flag": stale_flag,
        "crossed_flag": crossed_flag,
        "locked_flag": locked_flag,
        "quality_status": quality_status,
        "source": source,
    }


def fetch_alpaca_quotes(run_id: str, ingested_at: datetime) -> pd.DataFrame:
    """
    Fetch best bid/ask quotes.

    Version 1.6 intentionally limits this to:
      - US individual equities / tech stocks via Alpaca IEX
      - Bitcoin via Alpaca crypto latest quotes

    Japan equities, indices, FX, and gold remain NOT_AVAILABLE in the quote
    layer until a suitable quote source is added.
    """
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        print("[WARN] Alpaca API keys not set; bid/ask quote download skipped.")
        return pd.DataFrame()

    rows = []
    headers = alpaca_headers()

    # US equities: free/basic-compatible IEX feed.
    us_symbols = [
        a["symbol"]
        for a in ASSETS
        if a["country"] == "US" and a["category"] in {"US Equity", "Tech"}
    ]

    if us_symbols:
        response = requests.get(
            ALPACA_STOCK_QUOTES_URL,
            headers=headers,
            params={
                "symbols": ",".join(us_symbols),
                "feed": "iex",
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        quotes = payload.get("quotes", {})

        for symbol in us_symbols:
            q = quotes.get(symbol, {})
            rows.append(
                _quote_quality_row(
                    run_id=run_id,
                    ingested_at=ingested_at,
                    symbol=symbol,
                    bid=q.get("bp"),
                    ask=q.get("ap"),
                    bid_size=q.get("bs"),
                    ask_size=q.get("as"),
                    quote_timestamp=q.get("t"),
                    source="Alpaca IEX",
                    stale_seconds=300,
                )
            )

    # Bitcoin quote.
    response = requests.get(
        ALPACA_CRYPTO_QUOTES_URL,
        headers=headers,
        params={"symbols": "BTC/USD"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    crypto_quotes = payload.get("quotes", {})
    q = crypto_quotes.get("BTC/USD", {})

    rows.append(
        _quote_quality_row(
            run_id=run_id,
            ingested_at=ingested_at,
            symbol="BTC-USD",
            bid=q.get("bp"),
            ask=q.get("ap"),
            bid_size=q.get("bs"),
            ask_size=q.get("as"),
            quote_timestamp=q.get("t"),
            source="Alpaca Crypto",
            stale_seconds=300,
        )
    )

    result = pd.DataFrame(rows)
    if not result.empty:
        print(
            f"[INFO] Alpaca quotes: {len(result)} symbols, "
            f"missing={(result['missing_flag'] != 'OK').sum()}, "
            f"stale={(result['stale_flag'] == 'STALE').sum()}, "
            f"crossed={(result['crossed_flag'] == 'CROSSED').sum()}"
        )
    return result



def insert_df_ignore(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0

    before = conn.total_changes

    columns = list(df.columns)
    placeholders = ",".join(["?"] * len(columns))
    sql = f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({placeholders})"

    cleaned_rows = []
    for row in df.itertuples(index=False, name=None):
        cleaned = []
        for value in row:
            if pd.isna(value):
                cleaned.append(None)
            elif isinstance(value, pd.Timestamp):
                cleaned.append(value.isoformat())
            elif isinstance(value, datetime):
                cleaned.append(value.isoformat())
            elif isinstance(value, (np.integer,)):
                cleaned.append(int(value))
            elif isinstance(value, (np.floating,)):
                cleaned.append(float(value))
            elif isinstance(value, (np.bool_,)):
                cleaned.append(bool(value))
            else:
                cleaned.append(value)
        cleaned_rows.append(tuple(cleaned))

    conn.executemany(sql, cleaned_rows)
    conn.commit()
    return conn.total_changes - before


def insert_snapshot(conn: sqlite3.Connection, snapshot: pd.DataFrame) -> int:
    if snapshot.empty:
        return 0
    before = conn.total_changes
    snapshot.to_sql("market_snapshot", conn, if_exists="append", index=False)
    conn.commit()
    return conn.total_changes - before


def fred_observations(series_id: str) -> pd.DataFrame:
    if not FRED_API_KEY:
        raise RuntimeError("FRED_API_KEY is not configured.")

    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "asc",
    }
    response = requests.get(FRED_URL, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()

    rows = []
    for obs in payload.get("observations", []):
        value = obs.get("value")
        if value in (None, "."):
            numeric = np.nan
        else:
            numeric = float(value)
        rows.append({"date": obs["date"], "value": numeric})

    return pd.DataFrame(rows)


def fetch_fred_macro(run_id: str, ingested_at: datetime) -> pd.DataFrame:
    rows = []

    if not FRED_API_KEY:
        print("[WARN] FRED_API_KEY not set; FRED macro download skipped.")
        return pd.DataFrame()

    for meta in FRED_SERIES:
        sid = meta["series_id"]
        try:
            df = fred_observations(sid).dropna(subset=["value"])
            if df.empty:
                continue

            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")

            yoy = np.nan
            if meta["transform"] == "yoy":
                # Monthly index -> year-over-year percent change.
                df["yoy_percent"] = df["value"].pct_change(12) * 100.0
                latest = df.iloc[-1]
                yoy = latest["yoy_percent"]
            else:
                latest = df.iloc[-1]

            rows.append(
                {
                    "run_id": run_id,
                    "ingested_at_utc": ingested_at.isoformat(),
                    "source_date": latest["date"].date().isoformat(),
                    "country": meta["country"],
                    "indicator": meta["indicator"],
                    "series_id": sid,
                    "value": float(latest["value"]) if pd.notna(latest["value"]) else np.nan,
                    "yoy_percent": float(yoy) if pd.notna(yoy) else np.nan,
                    "unit": meta["unit"],
                    "frequency": meta["frequency"],
                    "source": "FRED",
                }
            )
            print(f"[INFO] FRED {sid}: {latest['date'].date()} -> {latest['value']}")
        except Exception as exc:
            print(f"[WARN] FRED {sid} failed: {exc}", file=sys.stderr)

    return pd.DataFrame(rows)


def _estat_dimension_maps(statistical_data: dict) -> dict[str, dict[str, str]]:
    maps = {}
    class_inf = statistical_data.get("CLASS_INF", {})
    objs = class_inf.get("CLASS_OBJ", [])
    if isinstance(objs, dict):
        objs = [objs]

    for obj in objs:
        dim_id = obj.get("@id")
        classes = obj.get("CLASS", [])
        if isinstance(classes, dict):
            classes = [classes]
        maps[dim_id] = {
            str(c.get("@code")): str(c.get("@name"))
            for c in classes
            if c.get("@code") is not None
        }
    return maps


def _estat_dimension_meta(statistical_data: dict) -> dict[str, dict[str, dict[str, str]]]:
    """Return e-Stat metadata including class names and units."""
    meta = {}
    class_inf = statistical_data.get("CLASS_INF", {})
    objs = class_inf.get("CLASS_OBJ", [])
    if isinstance(objs, dict):
        objs = [objs]

    for obj in objs:
        dim_id = obj.get("@id")
        classes = obj.get("CLASS", [])
        if isinstance(classes, dict):
            classes = [classes]

        meta[dim_id] = {}
        for c in classes:
            code = c.get("@code")
            if code is None:
                continue
            meta[dim_id][str(code)] = {
                "name": str(c.get("@name", "")),
                "unit": str(c.get("@unit", "")),
            }
    return meta


def fetch_japan_cpi_estat(run_id: str, ingested_at: datetime) -> pd.DataFrame:
    """
    Official Japan CPI YoY adapter using e-Stat.

    The configured DB contains multiple tabulated variables (index level,
    period-over-period change, and year-over-year change), so this function
    explicitly identifies the headline "All items" YoY row.
    """
    if not ESTAT_APP_ID:
        print("[WARN] ESTAT_APP_ID not set; official Japan CPI download skipped.")
        return pd.DataFrame()

    params = {
        "appId": ESTAT_APP_ID,
        "statsDataId": ESTAT_STATS_DATA_ID,
        "lang": "E",
        "metaGetFlg": "Y",
        "cntGetFlg": "N",
        "explanationGetFlg": "N",
    }

    response = requests.get(ESTAT_URL, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()

    result = payload.get("GET_STATS_DATA", {})
    status = str(result.get("RESULT", {}).get("STATUS", ""))
    if status not in ("0", ""):
        raise RuntimeError(
            f"e-Stat returned STATUS={status}: "
            f"{result.get('RESULT', {}).get('ERROR_MSG', '')}"
        )

    statistical_data = result.get("STATISTICAL_DATA", {})
    dim_maps = _estat_dimension_maps(statistical_data)
    dim_meta = _estat_dimension_meta(statistical_data)

    values = statistical_data.get("DATA_INF", {}).get("VALUE", [])
    if isinstance(values, dict):
        values = [values]

    def norm(value: str) -> str:
        return (
            str(value)
            .strip()
            .lower()
            .replace("　", " ")
            .replace("％", "%")
        )

    yoy_phrases = (
        "change over the year",
        "percentage change over the year",
        "year-on-year",
        "year over year",
        "yoy",
        "前年同月比",
        "前年比",
    )
    bad_period_phrases = (
        "change from the previous period",
        "change from previous period",
        "change from the previous month",
        "previous month",
        "month-on-month",
        "mom",
        "前月比",
        "前期比",
    )

    all_item_rows = []
    exact_rows = []

    for item in values:
        raw_value = item.get("$")
        if raw_value in (None, "", "-", "***"):
            continue

        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue

        labels_by_dim = {}
        units = []

        for key, code in item.items():
            if not str(key).startswith("@") or key == "@time":
                continue

            dim_id = str(key)[1:]

            if dim_id == "unit":
                units.append(str(code))
                continue

            meta_row = dim_meta.get(dim_id, {}).get(str(code), {})
            label = meta_row.get("name") or dim_maps.get(dim_id, {}).get(str(code), "")
            unit_from_meta = meta_row.get("unit", "")

            if label:
                labels_by_dim[dim_id] = label
            if unit_from_meta:
                units.append(unit_from_meta)

        label_values = list(labels_by_dim.values())
        label_text = " | ".join(label_values)
        joined = norm(label_text)
        unit_text = norm(" | ".join(units))

        # Headline CPI: exact "All items", but not "All items less ..."
        has_all_items = any(
            norm(lbl) == "all items" or norm(lbl) == "総合"
            for lbl in label_values
        )
        if not has_all_items:
            continue

        if (
            "less " in joined
            or "excluding" in joined
            or "除く" in joined
        ):
            continue

        time_code = str(item.get("@time", ""))
        time_label = dim_maps.get("time", {}).get(time_code, time_code)

        row = {
            "time_code": time_code,
            "time_label": time_label,
            "value": value,
            "labels": label_text,
            "unit": unit_text,
            "tab_code": str(item.get("@tab", "")),
            "tab_label": labels_by_dim.get("tab", ""),
        }
        all_item_rows.append(row)

        is_yoy = any(phrase in joined for phrase in yoy_phrases)
        is_bad_period = any(phrase in joined for phrase in bad_period_phrases)

        # e-Stat can put the unit on VALUE or CLASS metadata.
        is_percent = "%" in unit_text or "percent" in unit_text

        if is_yoy and not is_bad_period:
            exact_rows.append(row)
        elif is_percent and not is_bad_period:
            # Safe fallback only when the row is percentage-like and does not
            # look like period-over-period change. We validate magnitude later.
            row["fallback_percent"] = True

    candidates = exact_rows

    if not candidates:
        percent_fallback = [
            r for r in all_item_rows
            if r.get("fallback_percent")
            and abs(r["value"]) < 20
        ]

        # If metadata does not expose the YoY wording cleanly, only accept the
        # fallback when there is exactly one percentage-like headline row at
        # the latest timestamp. Otherwise print diagnostics rather than guess.
        if percent_fallback:
            latest_time = max(r["time_code"] for r in percent_fallback)
            latest_rows = [r for r in percent_fallback if r["time_code"] == latest_time]
            if len(latest_rows) == 1:
                candidates = latest_rows

    if not candidates:
        diagnostics = []
        for r in sorted(all_item_rows, key=lambda x: x["time_code"], reverse=True)[:12]:
            diagnostics.append(
                f"time={r['time_label']!r}, value={r['value']}, unit={r['unit']!r}, "
                f"tab={r['tab_label']!r}, labels={r['labels']!r}"
            )

        detail = "\n".join(diagnostics) if diagnostics else "No All items rows found."
        raise RuntimeError(
            "Could not uniquely identify Japan headline CPI YoY in e-Stat metadata. "
            "Latest All-items candidates:\n" + detail
        )

    latest = sorted(candidates, key=lambda x: x["time_code"])[-1]

    # Sanity check: a headline CPI YoY rate should not look like an index level.
    if abs(latest["value"]) >= 20:
        raise RuntimeError(
            f"Japan CPI candidate looks like an index level, not YoY: {latest}"
        )

    source_date = latest.get("time_label") or latest["time_code"]
    try:
        parsed_date = pd.to_datetime(
            str(source_date).replace(".", ""),
            errors="raise",
        )
        source_date = parsed_date.strftime("%Y-%m-01")
    except Exception:
        source_date = str(source_date)

    print(
        "[INFO] Japan CPI e-Stat: "
        f"{source_date} -> {latest['value']}% "
        f"({latest.get('tab_label') or latest.get('labels')})"
    )

    return pd.DataFrame(
        [
            {
                "run_id": run_id,
                "ingested_at_utc": ingested_at.isoformat(),
                "source_date": source_date,
                "country": "Japan",
                "indicator": "Japan CPI YoY",
                "series_id": f"eStat:{ESTAT_STATS_DATA_ID}",
                "value": latest["value"],
                "yoy_percent": latest["value"],
                "unit": "Percent change over year",
                "frequency": "Monthly",
                "source": "Japan e-Stat",
            }
        ]
    )



def _is_relevant_now(row: pd.Series) -> bool:
    return (
        str(row.get("market_expected_open", "NO")) == "YES"
        or str(row.get("category", "")) == "Crypto"
    )


def _incident(
    *,
    dataset: str,
    symbol: str,
    name: str,
    issue_type: str,
    severity: str,
    details: str,
    observed_value=None,
    threshold_value=None,
) -> dict:
    return {
        "incident_key": f"{dataset}|{symbol}|{issue_type}",
        "dataset": dataset,
        "symbol": symbol,
        "name": name,
        "issue_type": issue_type,
        "severity": severity,
        "details": details,
        "observed_value": observed_value,
        "threshold_value": threshold_value,
    }


def detect_incidents(
    market_df: pd.DataFrame,
    quote_df: pd.DataFrame,
) -> tuple[list[dict], set[str]]:
    incidents = []
    evaluated_datasets = set()

    if market_df is not None and not market_df.empty:
        evaluated_datasets.add("market_snapshot")

        for _, row in market_df.iterrows():
            if not _is_relevant_now(row):
                continue

            symbol = str(row["symbol"])
            name = str(row.get("name", symbol))

            if row.get("missing_flag") != "OK":
                incidents.append(
                    _incident(
                        dataset="market_snapshot",
                        symbol=symbol,
                        name=name,
                        issue_type="MISSING",
                        severity="HIGH",
                        details="Latest market snapshot is missing a required value.",
                    )
                )

            if row.get("stale_flag") == "STALE":
                age = row.get("age_minutes")
                threshold = row.get("stale_threshold_minutes")
                incidents.append(
                    _incident(
                        dataset="market_snapshot",
                        symbol=symbol,
                        name=name,
                        issue_type="STALE",
                        severity="HIGH",
                        observed_value=age,
                        threshold_value=threshold,
                        details=f"Market data age={age} min; threshold={threshold} min.",
                    )
                )

            if row.get("outlier_flag") == "OUTLIER":
                z = row.get("zscore_20")
                incidents.append(
                    _incident(
                        dataset="market_snapshot",
                        symbol=symbol,
                        name=name,
                        issue_type="OUTLIER",
                        severity="MEDIUM",
                        observed_value=z,
                        threshold_value=3.0,
                        details=f"20-bar absolute return z-score exceeded 3.0; z={z}.",
                    )
                )

    if quote_df is not None and not quote_df.empty:
        evaluated_datasets.add("quote_snapshot")

        for _, row in quote_df.iterrows():
            if not _is_relevant_now(row):
                continue

            symbol = str(row["symbol"])
            name = str(row.get("name", symbol))
            category = str(row.get("category", ""))
            spread_threshold = SPREAD_THRESHOLDS_BPS.get(category)

            if row.get("missing_flag") != "OK":
                incidents.append(
                    _incident(
                        dataset="quote_snapshot",
                        symbol=symbol,
                        name=name,
                        issue_type="MISSING_QUOTE",
                        severity="HIGH",
                        details="Bid and/or ask is missing, zero, or invalid.",
                    )
                )

            if row.get("stale_flag") == "STALE":
                age = row.get("quote_age_seconds")
                incidents.append(
                    _incident(
                        dataset="quote_snapshot",
                        symbol=symbol,
                        name=name,
                        issue_type="STALE_QUOTE",
                        severity="HIGH",
                        observed_value=age,
                        threshold_value=300.0,
                        details=f"Quote age={age} sec; threshold=300 sec.",
                    )
                )

            if row.get("crossed_flag") == "CROSSED":
                incidents.append(
                    _incident(
                        dataset="quote_snapshot",
                        symbol=symbol,
                        name=name,
                        issue_type="CROSSED_MARKET",
                        severity="CRITICAL",
                        observed_value=row.get("spread_bps"),
                        threshold_value=0.0,
                        details="Best bid is above best ask.",
                    )
                )

            if row.get("locked_flag") == "LOCKED":
                incidents.append(
                    _incident(
                        dataset="quote_snapshot",
                        symbol=symbol,
                        name=name,
                        issue_type="LOCKED_MARKET",
                        severity="MEDIUM",
                        observed_value=row.get("spread_bps"),
                        threshold_value=0.0,
                        details="Best bid equals best ask.",
                    )
                )

            spread_bps = row.get("spread_bps")
            if (
                spread_threshold is not None
                and pd.notna(spread_bps)
                and row.get("missing_flag") == "OK"
                and row.get("crossed_flag") != "CROSSED"
                and float(spread_bps) > float(spread_threshold)
            ):
                incidents.append(
                    _incident(
                        dataset="quote_snapshot",
                        symbol=symbol,
                        name=name,
                        issue_type="WIDE_SPREAD",
                        severity="MEDIUM",
                        observed_value=float(spread_bps),
                        threshold_value=float(spread_threshold),
                        details=(
                            f"Spread={float(spread_bps):.2f} bps; "
                            f"threshold={float(spread_threshold):.2f} bps."
                        ),
                    )
                )

    return incidents, evaluated_datasets


def update_incident_log(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    ingested_at: datetime,
    incidents: list[dict],
    evaluated_datasets: set[str],
) -> None:
    now_iso = ingested_at.isoformat()
    active_by_dataset = {}

    for item in incidents:
        active_by_dataset.setdefault(item["dataset"], set()).add(item["incident_key"])

        conn.execute(
            """
            INSERT INTO incident_log (
                incident_key, dataset, symbol, name, issue_type, severity,
                first_seen_utc, last_seen_utc, resolved_at_utc,
                opened_run_id, last_run_id, occurrence_count, status,
                observed_value, threshold_value, details
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 1, 'OPEN', ?, ?, ?)
            ON CONFLICT(incident_key) DO UPDATE SET
                last_seen_utc = excluded.last_seen_utc,
                resolved_at_utc = NULL,
                last_run_id = excluded.last_run_id,
                occurrence_count = incident_log.occurrence_count + 1,
                status = 'OPEN',
                severity = excluded.severity,
                observed_value = excluded.observed_value,
                threshold_value = excluded.threshold_value,
                details = excluded.details
            """,
            (
                item["incident_key"],
                item["dataset"],
                item["symbol"],
                item["name"],
                item["issue_type"],
                item["severity"],
                now_iso,
                now_iso,
                run_id,
                run_id,
                item.get("observed_value"),
                item.get("threshold_value"),
                item.get("details"),
            ),
        )

    for dataset in evaluated_datasets:
        active_keys = active_by_dataset.get(dataset, set())

        if active_keys:
            placeholders = ",".join("?" for _ in active_keys)
            sql = f"""
                UPDATE incident_log
                SET status='RESOLVED',
                    resolved_at_utc=?,
                    last_run_id=?
                WHERE dataset=?
                  AND status='OPEN'
                  AND incident_key NOT IN ({placeholders})
            """
            params = [now_iso, run_id, dataset, *sorted(active_keys)]
        else:
            sql = """
                UPDATE incident_log
                SET status='RESOLVED',
                    resolved_at_utc=?,
                    last_run_id=?
                WHERE dataset=?
                  AND status='OPEN'
            """
            params = [now_iso, run_id, dataset]

        conn.execute(sql, params)

    conn.commit()


def _percent_ok(ok_count: int, total_count: int) -> float:
    if total_count <= 0:
        return 100.0
    return round(100.0 * ok_count / total_count, 2)


def calculate_quality_score(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    ingested_at: datetime,
    market_df: pd.DataFrame,
    quote_df: pd.DataFrame,
) -> dict:
    market_df = market_df if market_df is not None else pd.DataFrame()
    quote_df = quote_df if quote_df is not None else pd.DataFrame()

    market_eval = (
        market_df[market_df.apply(_is_relevant_now, axis=1)].copy()
        if not market_df.empty
        else market_df
    )
    quote_eval = (
        quote_df[quote_df.apply(_is_relevant_now, axis=1)].copy()
        if not quote_df.empty
        else quote_df
    )

    # Completeness
    total = len(market_eval) + len(quote_eval)
    complete_ok = 0
    if not market_eval.empty:
        complete_ok += int((market_eval["missing_flag"] == "OK").sum())
    if not quote_eval.empty:
        complete_ok += int((quote_eval["missing_flag"] == "OK").sum())
    completeness = _percent_ok(complete_ok, total)

    # Timeliness
    timely_ok = 0
    if not market_eval.empty:
        timely_ok += int((market_eval["stale_flag"] != "STALE").sum())
    if not quote_eval.empty:
        timely_ok += int((quote_eval["stale_flag"] != "STALE").sum())
    timeliness = _percent_ok(timely_ok, total)

    # Validity
    valid_ok = 0
    if not market_eval.empty:
        valid_ok += int((market_eval["outlier_flag"] == "OK").sum())
    if not quote_eval.empty:
        q_ok = (
            (quote_eval["missing_flag"] == "OK")
            & (quote_eval["crossed_flag"] != "CROSSED")
            & (quote_eval["locked_flag"] != "LOCKED")
        )
        valid_ok += int(q_ok.sum())
    validity = _percent_ok(valid_ok, total)

    # Consistency
    duplicate_count = 0
    dup_row = conn.execute(
        """
        SELECT duplicate_count
        FROM quality_log
        WHERE run_id=? AND dataset='market'
        ORDER BY id DESC
        LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if dup_row:
        duplicate_count = int(dup_row[0] or 0)

    checks = [1 if duplicate_count == 0 else 0]
    if not quote_eval.empty:
        for _, q in quote_eval.iterrows():
            threshold = SPREAD_THRESHOLDS_BPS.get(str(q.get("category", "")))
            spread = q.get("spread_bps")
            if q.get("missing_flag") != "OK":
                checks.append(0)
            elif threshold is None or pd.isna(spread):
                checks.append(1)
            else:
                checks.append(1 if float(spread) <= float(threshold) else 0)

    consistency = _percent_ok(sum(checks), len(checks))

    overall = round(
        completeness * 0.30
        + timeliness * 0.25
        + validity * 0.25
        + consistency * 0.20,
        2,
    )

    if overall >= 95:
        status = "EXCELLENT"
    elif overall >= 90:
        status = "GOOD"
    elif overall >= 80:
        status = "REVIEW"
    else:
        status = "POOR"

    severity_counts = dict(
        conn.execute(
            """
            SELECT severity, COUNT(*)
            FROM incident_log
            WHERE status='OPEN'
            GROUP BY severity
            """
        ).fetchall()
    )

    result = {
        "run_id": run_id,
        "ingested_at_utc": ingested_at.isoformat(),
        "market_records": len(market_df),
        "quote_records": len(quote_df),
        "evaluated_records": total,
        "completeness_pct": completeness,
        "timeliness_pct": timeliness,
        "validity_pct": validity,
        "consistency_pct": consistency,
        "overall_score": overall,
        "status": status,
        "open_incident_count": sum(int(v) for v in severity_counts.values()),
        "critical_incident_count": int(severity_counts.get("CRITICAL", 0)),
        "high_incident_count": int(severity_counts.get("HIGH", 0)),
        "medium_incident_count": int(severity_counts.get("MEDIUM", 0)),
        "notes": (
            "Weights: completeness 30%, timeliness 25%, validity 25%, "
            "consistency 20%. Closed exchange rows are excluded from live scoring."
        ),
    }

    conn.execute(
        """
        INSERT OR REPLACE INTO quality_score (
            run_id, ingested_at_utc, market_records, quote_records,
            evaluated_records, completeness_pct, timeliness_pct,
            validity_pct, consistency_pct, overall_score, status,
            open_incident_count, critical_incident_count,
            high_incident_count, medium_incident_count, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result["run_id"],
            result["ingested_at_utc"],
            result["market_records"],
            result["quote_records"],
            result["evaluated_records"],
            result["completeness_pct"],
            result["timeliness_pct"],
            result["validity_pct"],
            result["consistency_pct"],
            result["overall_score"],
            result["status"],
            result["open_incident_count"],
            result["critical_incident_count"],
            result["high_incident_count"],
            result["medium_incident_count"],
            result["notes"],
        ),
    )
    conn.commit()
    return result


def run_monitoring_layer(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    ingested_at: datetime,
) -> None:
    market_df = pd.read_sql_query(
        "SELECT * FROM market_snapshot WHERE run_id=?",
        conn,
        params=(run_id,),
    )
    quote_df = pd.read_sql_query(
        "SELECT * FROM quote_snapshot WHERE run_id=?",
        conn,
        params=(run_id,),
    )

    incidents, evaluated_datasets = detect_incidents(market_df, quote_df)
    update_incident_log(
        conn,
        run_id=run_id,
        ingested_at=ingested_at,
        incidents=incidents,
        evaluated_datasets=evaluated_datasets,
    )

    score = calculate_quality_score(
        conn,
        run_id=run_id,
        ingested_at=ingested_at,
        market_df=market_df,
        quote_df=quote_df,
    )

    print(
        "[INFO] Data Quality: "
        f"{score['overall_score']:.2f}/100 {score['status']} | "
        f"Completeness={score['completeness_pct']:.2f}% "
        f"Timeliness={score['timeliness_pct']:.2f}% "
        f"Validity={score['validity_pct']:.2f}% "
        f"Consistency={score['consistency_pct']:.2f}% | "
        f"Open incidents={score['open_incident_count']}"
    )



def _atomic_csv_write(df: pd.DataFrame, path: Path) -> None:
    """Write CSV atomically so Excel/Power Query never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(
        temp_path,
        index=False,
        encoding="utf-8-sig",
        na_rep="",
    )
    temp_path.replace(path)


def _latest_run_id(conn: sqlite3.Connection, table: str) -> str | None:
    row = conn.execute(
        f"SELECT run_id FROM {table} ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def build_market_overview_export(conn: sqlite3.Connection) -> pd.DataFrame:
    market_run_id = _latest_run_id(conn, "market_snapshot")
    if not market_run_id:
        return pd.DataFrame(
            columns=[
                "symbol", "name", "category", "country", "price", "volume",
                "move_bps", "zscore_20", "market_age_minutes",
                "market_expected_open", "market_stale_flag",
                "market_outlier_flag", "market_quality_status",
                "bid", "ask", "mid", "spread_bps", "quote_age_seconds",
                "quote_stale_flag", "crossed_flag", "locked_flag",
                "quote_quality_status", "final_quality_status",
                "market_updated_at_utc", "quote_updated_at_utc",
            ]
        )

    market = pd.read_sql_query(
        """
        SELECT
            symbol,
            name,
            category,
            country,
            close AS price,
            volume,
            move_bps,
            zscore_20,
            age_minutes AS market_age_minutes,
            market_expected_open,
            stale_flag AS market_stale_flag,
            outlier_flag AS market_outlier_flag,
            quality_status AS market_quality_status,
            ingested_at_utc AS market_updated_at_utc
        FROM market_snapshot
        WHERE run_id=?
        """,
        conn,
        params=(market_run_id,),
    )

    quote_run_id = _latest_run_id(conn, "quote_snapshot")
    if quote_run_id:
        quotes = pd.read_sql_query(
            """
            SELECT
                symbol,
                bid,
                ask,
                mid,
                spread_bps,
                quote_age_seconds,
                stale_flag AS quote_stale_flag,
                crossed_flag,
                locked_flag,
                quality_status AS quote_quality_status,
                ingested_at_utc AS quote_updated_at_utc
            FROM quote_snapshot
            WHERE run_id=?
            """,
            conn,
            params=(quote_run_id,),
        )
        result = market.merge(quotes, on="symbol", how="left")
    else:
        result = market.copy()
        for col in [
            "bid", "ask", "mid", "spread_bps", "quote_age_seconds",
            "quote_stale_flag", "crossed_flag", "locked_flag",
            "quote_quality_status", "quote_updated_at_utc",
        ]:
            result[col] = np.nan

    def final_status(row: pd.Series) -> str:
        if row.get("market_quality_status") == "REVIEW":
            return "REVIEW"
        if row.get("quote_quality_status") == "REVIEW":
            return "REVIEW"
        return "PASS"

    result["final_quality_status"] = result.apply(final_status, axis=1)

    category_order = {
        "Index": 1,
        "Volatility": 2,
        "FX": 3,
        "Commodity": 4,
        "Crypto": 5,
        "Japan Equity": 6,
        "US Equity": 7,
        "Tech": 8,
    }
    result["_category_order"] = result["category"].map(category_order).fillna(99)
    result = result.sort_values(
        ["_category_order", "category", "symbol"]
    ).drop(columns=["_category_order"])

    ordered_cols = [
        "symbol", "name", "category", "country", "price", "volume",
        "move_bps", "zscore_20", "market_age_minutes",
        "market_expected_open", "market_stale_flag",
        "market_outlier_flag", "market_quality_status",
        "bid", "ask", "mid", "spread_bps", "quote_age_seconds",
        "quote_stale_flag", "crossed_flag", "locked_flag",
        "quote_quality_status", "final_quality_status",
        "market_updated_at_utc", "quote_updated_at_utc",
    ]
    return result[ordered_cols]


def build_quotes_export(conn: sqlite3.Connection) -> pd.DataFrame:
    run_id = _latest_run_id(conn, "quote_snapshot")
    if not run_id:
        return pd.DataFrame()

    return pd.read_sql_query(
        """
        SELECT
            symbol,
            name,
            category,
            country,
            bid,
            ask,
            bid_size,
            ask_size,
            mid,
            spread,
            spread_bps,
            quote_age_seconds,
            market_expected_open,
            missing_flag,
            stale_flag,
            crossed_flag,
            locked_flag,
            quality_status,
            quote_timestamp_utc,
            ingested_at_utc,
            source
        FROM quote_snapshot
        WHERE run_id=?
        ORDER BY category, symbol
        """,
        conn,
        params=(run_id,),
    )


def build_macro_export(conn: sqlite3.Connection) -> pd.DataFrame:
    macro = pd.read_sql_query(
        """
        SELECT m.*
        FROM macro_data m
        JOIN (
            SELECT series_id, MAX(id) AS max_id
            FROM macro_data
            GROUP BY series_id
        ) latest
          ON m.id = latest.max_id
        ORDER BY
            CASE country WHEN 'US' THEN 1 WHEN 'Japan' THEN 2 ELSE 3 END,
            indicator
        """,
        conn,
    )

    if macro.empty:
        return macro

    macro["dashboard_value"] = np.where(
        macro["yoy_percent"].notna(),
        macro["yoy_percent"],
        macro["value"],
    )
    macro["dashboard_unit"] = np.where(
        macro["yoy_percent"].notna(),
        "Percent YoY",
        macro["unit"],
    )

    cols = [
        "country", "indicator", "series_id", "source_date",
        "dashboard_value", "dashboard_unit",
        "value", "yoy_percent", "unit", "frequency",
        "source", "ingested_at_utc",
    ]
    return macro[cols]


def build_incidents_export(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
            status,
            severity,
            symbol,
            name,
            issue_type,
            occurrence_count,
            first_seen_utc,
            last_seen_utc,
            resolved_at_utc,
            observed_value,
            threshold_value,
            details,
            dataset,
            opened_run_id,
            last_run_id
        FROM incident_log
        ORDER BY
            CASE status WHEN 'OPEN' THEN 1 ELSE 2 END,
            CASE severity
                WHEN 'CRITICAL' THEN 1
                WHEN 'HIGH' THEN 2
                WHEN 'MEDIUM' THEN 3
                ELSE 4
            END,
            last_seen_utc DESC
        """,
        conn,
    )


def build_latest_quality_export(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
            ingested_at_utc,
            overall_score,
            status,
            completeness_pct,
            timeliness_pct,
            validity_pct,
            consistency_pct,
            open_incident_count,
            critical_incident_count,
            high_incident_count,
            medium_incident_count,
            market_records,
            quote_records,
            evaluated_records,
            run_id,
            notes
        FROM quality_score
        ORDER BY id DESC
        LIMIT 1
        """,
        conn,
    )


def build_quality_history_export(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
            ingested_at_utc,
            overall_score,
            status,
            completeness_pct,
            timeliness_pct,
            validity_pct,
            consistency_pct,
            open_incident_count,
            critical_incident_count,
            high_incident_count,
            medium_incident_count,
            evaluated_records,
            run_id
        FROM quality_score
        ORDER BY id DESC
        LIMIT ?
        """,
        conn,
        params=(QUALITY_HISTORY_EXPORT_LIMIT,),
    ).sort_values("ingested_at_utc")


def export_dashboard_data(conn: sqlite3.Connection) -> None:
    """Export Excel/Power Query-ready CSVs from SQLite."""
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)

    exports = {
        "market_overview.csv": build_market_overview_export(conn),
        "quotes.csv": build_quotes_export(conn),
        "macro.csv": build_macro_export(conn),
        "incidents.csv": build_incidents_export(conn),
        "quality_score.csv": build_latest_quality_export(conn),
        "quality_history.csv": build_quality_history_export(conn),
    }

    for filename, df in exports.items():
        _atomic_csv_write(df, DASHBOARD_DIR / filename)

    summary = ", ".join(
        f"{name}={len(df)}"
        for name, df in exports.items()
    )
    print(f"[INFO] Dashboard exports: {summary}")
    print(f"[INFO] Dashboard directory: {DASHBOARD_DIR}")



def write_quality_log(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    ingested_at: datetime,
    dataset: str,
    rows_received: int,
    rows_inserted: int,
    missing_count: int = 0,
    duplicate_count: int = 0,
    stale_count: int = 0,
    outlier_count: int = 0,
    crossed_count: int = 0,
    status: str = "OK",
    notes: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO quality_log (
            run_id, ingested_at_utc, dataset, rows_received, rows_inserted,
            missing_count, duplicate_count, stale_count, outlier_count,
            crossed_count, status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            ingested_at.isoformat(),
            dataset,
            rows_received,
            rows_inserted,
            missing_count,
            duplicate_count,
            stale_count,
            outlier_count,
            crossed_count,
            status,
            notes,
        ),
    )
    conn.commit()


def run(mode: str = "full") -> None:
    if mode not in {"full", "market", "macro"}:
        raise ValueError(f"Unsupported mode: {mode}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    ingested_at = utc_now()

    print(f"[INFO] Run ID: {run_id}")
    print(f"[INFO] UTC ingestion time: {ingested_at.isoformat()}")
    print(f"[INFO] Mode: {mode}")

    conn = sqlite3.connect(DB_PATH)
    try:
        init_db(conn)

        if mode in {"full", "market"}:
            # ---------- Market ----------
            try:
                raw = fetch_market(run_id, ingested_at)
                if raw.empty:
                    write_quality_log(
                        conn,
                        run_id=run_id,
                        ingested_at=ingested_at,
                        dataset="market",
                        rows_received=0,
                        rows_inserted=0,
                        status="ERROR",
                        notes="No market rows were returned.",
                    )
                else:
                    duplicate_count = int(
                        raw.duplicated(
                            subset=["symbol", "source_timestamp_utc", "source", "interval"],
                            keep=False,
                        ).sum()
                    )
                    clean = clean_market(raw)
                    snapshot = make_snapshot(clean, run_id, ingested_at)

                    raw_inserted = insert_df_ignore(conn, "raw_market", raw)
                    clean_inserted = insert_df_ignore(
                        conn,
                        "clean_market",
                        clean[
                            [
                                "run_id",
                                "ingested_at_utc",
                                "source_timestamp_utc",
                                "symbol",
                                "name",
                                "category",
                                "country",
                                "open",
                                "high",
                                "low",
                                "close",
                                "volume",
                                "return_1bar",
                                "zscore_20",
                                "missing_flag",
                                "duplicate_flag",
                                "outlier_flag",
                                "source",
                                "interval",
                            ]
                        ],
                    )
                    snapshot_inserted = insert_snapshot(conn, snapshot)

                    missing_count = int((snapshot["missing_flag"] != "OK").sum())
                    stale_count = int((snapshot["stale_flag"] == "STALE").sum())
                    outlier_count = int((snapshot["outlier_flag"] != "OK").sum())

                    write_quality_log(
                        conn,
                        run_id=run_id,
                        ingested_at=ingested_at,
                        dataset="market",
                        rows_received=len(raw),
                        rows_inserted=raw_inserted,
                        missing_count=missing_count,
                        duplicate_count=duplicate_count,
                        stale_count=stale_count,
                        outlier_count=outlier_count,
                        crossed_count=0,
                        status="OK",
                        notes=(
                            f"clean_inserted={clean_inserted}; "
                            f"snapshot_inserted={snapshot_inserted}; "
                            "bid/ask unavailable in v1 yfinance history, so spread/crossed/locked are NOT_AVAILABLE."
                        ),
                    )

                    print(
                        f"[INFO] Market rows={len(raw)}, raw inserted={raw_inserted}, "
                        f"snapshot rows={len(snapshot)}"
                    )
            except Exception as exc:
                write_quality_log(
                    conn,
                    run_id=run_id,
                    ingested_at=ingested_at,
                    dataset="market",
                    rows_received=0,
                    rows_inserted=0,
                    status="ERROR",
                    notes=str(exc),
                )
                print(f"[ERROR] Market stage failed: {exc}", file=sys.stderr)

            # ---------- Alpaca bid/ask quotes ----------
            try:
                quotes = fetch_alpaca_quotes(run_id, ingested_at)

                if quotes.empty:
                    write_quality_log(
                        conn,
                        run_id=run_id,
                        ingested_at=ingested_at,
                        dataset="quotes_alpaca",
                        rows_received=0,
                        rows_inserted=0,
                        status="SKIPPED" if not (ALPACA_API_KEY and ALPACA_SECRET_KEY) else "OK",
                        notes=(
                            "Alpaca API keys not configured."
                            if not (ALPACA_API_KEY and ALPACA_SECRET_KEY)
                            else "No quotes returned."
                        ),
                    )
                else:
                    before = conn.total_changes
                    quotes.to_sql("quote_snapshot", conn, if_exists="append", index=False)
                    conn.commit()
                    quote_inserted = conn.total_changes - before

                    missing_count = int((quotes["missing_flag"] != "OK").sum())
                    stale_count = int((quotes["stale_flag"] == "STALE").sum())
                    crossed_count = int((quotes["crossed_flag"] == "CROSSED").sum())
                    locked_count = int((quotes["locked_flag"] == "LOCKED").sum())

                    write_quality_log(
                        conn,
                        run_id=run_id,
                        ingested_at=ingested_at,
                        dataset="quotes_alpaca",
                        rows_received=len(quotes),
                        rows_inserted=quote_inserted,
                        missing_count=missing_count,
                        stale_count=stale_count,
                        crossed_count=crossed_count,
                        status="OK",
                        notes=f"locked_count={locked_count}; US stock feed=IEX; BTC quote via Alpaca crypto.",
                    )
            except Exception as exc:
                write_quality_log(
                    conn,
                    run_id=run_id,
                    ingested_at=ingested_at,
                    dataset="quotes_alpaca",
                    rows_received=0,
                    rows_inserted=0,
                    status="ERROR",
                    notes=str(exc),
                )
                print(f"[WARN] Alpaca quote stage failed: {exc}", file=sys.stderr)


            # ---------- Incident Log + Data Quality Score ----------
            try:
                run_monitoring_layer(
                    conn,
                    run_id=run_id,
                    ingested_at=ingested_at,
                )
            except Exception as exc:
                print(f"[WARN] Monitoring layer failed: {exc}", file=sys.stderr)

        if mode in {"full", "macro"}:
            # ---------- FRED macro ----------
            try:
                fred = fetch_fred_macro(run_id, ingested_at)
                inserted = insert_df_ignore(conn, "macro_data", fred) if not fred.empty else 0
                write_quality_log(
                    conn,
                    run_id=run_id,
                    ingested_at=ingested_at,
                    dataset="macro_fred",
                    rows_received=len(fred),
                    rows_inserted=inserted,
                    status="OK" if FRED_API_KEY else "SKIPPED",
                    notes="" if FRED_API_KEY else "FRED_API_KEY not configured.",
                )
            except Exception as exc:
                write_quality_log(
                    conn,
                    run_id=run_id,
                    ingested_at=ingested_at,
                    dataset="macro_fred",
                    rows_received=0,
                    rows_inserted=0,
                    status="ERROR",
                    notes=str(exc),
                )
                print(f"[ERROR] FRED stage failed: {exc}", file=sys.stderr)

            # ---------- Official Japan CPI ----------
            try:
                jp_cpi = fetch_japan_cpi_estat(run_id, ingested_at)
                inserted = insert_df_ignore(conn, "macro_data", jp_cpi) if not jp_cpi.empty else 0
                write_quality_log(
                    conn,
                    run_id=run_id,
                    ingested_at=ingested_at,
                    dataset="japan_cpi_estat",
                    rows_received=len(jp_cpi),
                    rows_inserted=inserted,
                    status="OK" if ESTAT_APP_ID else "SKIPPED",
                    notes="" if ESTAT_APP_ID else "ESTAT_APP_ID not configured.",
                )
            except Exception as exc:
                write_quality_log(
                    conn,
                    run_id=run_id,
                    ingested_at=ingested_at,
                    dataset="japan_cpi_estat",
                    rows_received=0,
                    rows_inserted=0,
                    status="ERROR",
                    notes=str(exc),
                )
                print(f"[WARN] Japan CPI e-Stat stage failed: {exc}", file=sys.stderr)


        # ---------- Excel / Power Query dashboard exports ----------
        try:
            export_dashboard_data(conn)
        except Exception as exc:
            print(f"[WARN] Dashboard export failed: {exc}", file=sys.stderr)

        print(f"[DONE] Database: {DB_PATH}")

    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Global Market Monitor refresh job")
    parser.add_argument(
        "--mode",
        choices=["full", "market", "macro"],
        default="full",
        help="full=all data, market=market+quotes only, macro=FRED/e-Stat only",
    )
    args = parser.parse_args()
    run(mode=args.mode)
