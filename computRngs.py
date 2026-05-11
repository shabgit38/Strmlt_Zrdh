import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# CORE DATA FETCH
# Fetches 2 years of daily OHLCV data so that EMA200 (needs ~200 warm-up bars)
# is reliable by the time we reach the most recent date.
# Only .NS (NSE) suffix is tried for now.
# ─────────────────────────────────────────────────────────────────────────────

def get_stock_data(ticker: str) -> pd.DataFrame:
    """
    Download 2Y of daily adjusted OHLCV for `ticker`.
    yfinance ≥0.2 returns MultiIndex columns even for a single ticker;
    we flatten them to plain names (Close, High, Low, Open, Volume).
    """
    #import yfinance as yf

    #df = yf.download(ticker, period="2y", interval="1d", auto_adjust=True, progress=False)
    #df.dropna(inplace=True)

    # Flatten MultiIndex columns produced by newer yfinance versions
    #if isinstance(df.columns, pd.MultiIndex):
    #    df.columns = df.columns.get_level_values(0)

    #return df


# ─────────────────────────────────────────────────────────────────────────────
# WEEKLY / MONTHLY CLOSE  (used in the Period H/L table)
# "Completed" means we exclude the still-open current period.
# ─────────────────────────────────────────────────────────────────────────────

def get_weekly_close(df: pd.DataFrame):
    """Return the closing price of the most recently *completed* trading week."""
    weekly = df["Close"].resample("W").last()
    if not weekly.empty:
        weekly = weekly.iloc[:-1]          # drop current (possibly incomplete) week
    return float(weekly.iloc[-1]) if not weekly.empty else None


def get_monthly_close(df: pd.DataFrame):
    """Return the closing price of the most recently *completed* calendar month."""
    monthly = df["Close"].resample("ME").last()
    if not monthly.empty:
        monthly = monthly.iloc[:-1]        # drop current (possibly incomplete) month
    return float(monthly.iloc[-1]) if not monthly.empty else None


# ─────────────────────────────────────────────────────────────────────────────
# PERIOD HIGH / LOW  (rolling windows — 1W, 1M, 3M, 6M, 1Y)
# We drop the current incomplete week before resampling so that a partial
# Monday–Wednesday candle does not inflate the "1W High".
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# WEEKLY OHLC  (used for weekly pivot points)
# Returns the H, L, C of the last *completed* Mon–Fri trading week so that
# the weekly classical pivot formula has a stable, closed reference bar.
# ─────────────────────────────────────────────────────────────────────────────

def get_weekly_ohlc(df: pd.DataFrame) -> dict | None:
    """
    Resample daily bars into weekly (Mon–Fri) OHLC bars, drop the current
    incomplete week, and return the most recent completed week as a dict
    with keys High, Low, Close.

    Returns None if there is insufficient data.
    """
    df = df.copy()
    df.index = pd.to_datetime(df.index)

    # Resample to weekly bars anchored on Friday (W-FRI closes each Mon-Fri week)
    weekly = df.resample("W-FRI").agg({
        "Open":  "first",
        "High":  "max",
        "Low":   "min",
        "Close": "last",
    }).dropna()

    # Drop the last row — it represents the current, still-open week
    completed = weekly.iloc[:-1]

    if completed.empty:
        return None

    last_week = completed.iloc[-1]
    return {
        "High":  float(last_week["High"]),
        "Low":   float(last_week["Low"]),
        "Close": float(last_week["Close"]),
    }
