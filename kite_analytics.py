from datetime import datetime, time

import pandas as pd
import streamlit as st
from kiteconnect import KiteConnect

from indicators import add_ema


def get_kite_historical_data(
    kite: KiteConnect,
    instrument_token: int | str,
    interval: str,
    from_date: str | datetime,
    to_date: str | datetime,
    continuous: int | bool = 0,
    oi: int | bool = 0,
) -> pd.DataFrame:
    """
    Fetch historical candles from Kite and return them as a DataFrame.
    """
    if not isinstance(kite, KiteConnect):
        raise TypeError("kite must be an authenticated KiteConnect instance")

    def _normalize_dt(value: str | datetime) -> datetime:
        if isinstance(value, datetime):
            return value
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError as exc:
            raise ValueError(
                "from_date and to_date must be in 'yyyy-mm-dd hh:mm:ss' format"
            ) from exc

    start = _normalize_dt(from_date)
    end = _normalize_dt(to_date)

    candles = kite.historical_data(
        instrument_token=int(instrument_token),
        from_date=start,
        to_date=end,
        interval=interval,
        continuous=int(bool(continuous)),
        oi=int(bool(oi)),
    )

    df = pd.DataFrame(candles)
    if df.empty:
        return df

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)

    rename_map = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
        "oi": "OI",
    }
    df.rename(columns=rename_map, inplace=True)

    preferred_columns = ["Open", "High", "Low", "Close", "Volume", "OI"]
    existing_columns = [col for col in preferred_columns if col in df.columns]
    df = df[existing_columns]
    df.sort_index(inplace=True)
    return df


@st.cache_data(ttl=60 * 60)
def load_analytics_history(
    _kite: KiteConnect,
    instrument_token: int | str,
    to_date: str,
) -> pd.DataFrame:
    """
    Load one cached 2Y daily dataframe for levels and EMAs.
    """
    end = datetime.combine(pd.to_datetime(to_date).date(), time(23, 59, 59))
    start = datetime.combine((pd.Timestamp(end) - pd.DateOffset(years=2)).date(), time.min)
    return get_kite_historical_data(
        kite=_kite,
        instrument_token=instrument_token,
        interval="day",
        from_date=start,
        to_date=end,
    )


def get_high_low_resampled(df: pd.DataFrame) -> dict:
    """
    Return {period: (high, low)} for 1W, 1M, 3M, 6M, 1Y.
    All windows are trailing from the last completed Friday to avoid
    counting an open, partial week.
    """
    df = df.copy()
    df.index = pd.to_datetime(df.index)

    completed_weeks = df.resample("W-FRI").last().dropna()
    if len(completed_weeks) < 2:
        raise ValueError("Insufficient data for completed weekly high/low metrics.")

    last_complete_date = completed_weeks.index[-2]
    df = df[df.index <= last_complete_date]

    weekly = df.resample("W-FRI").agg({"High": "max", "Low": "min"}).dropna()
    monthly = df.resample("ME").agg({"High": "max", "Low": "min"}).dropna()

    latest = df.index.max()
    df_3m = df[df.index >= latest - pd.DateOffset(months=3)]
    df_6m = df[df.index >= latest - pd.DateOffset(months=6)]
    df_1y = df[df.index >= latest - pd.DateOffset(years=1)]

    return {
        "1W": (float(weekly.iloc[-1]["High"]), float(weekly.iloc[-1]["Low"])),
        "1M": (float(monthly.iloc[-1]["High"]), float(monthly.iloc[-1]["Low"])),
        "3M": (float(df_3m["High"].max()), float(df_3m["Low"].min())),
        "6M": (float(df_6m["High"].max()), float(df_6m["Low"].min())),
        "1Y": (float(df_1y["High"].max()), float(df_1y["Low"].min())),
    }


def build_metric_values(analytics_df: pd.DataFrame) -> dict[str, float]:
    """
    Build the shared 2Y daily metric values used by dashboards and holdings.
    """
    if analytics_df.empty:
        return {}

    analytics_df = add_ema(analytics_df.copy())
    latest = analytics_df.iloc[-1]
    high_low = get_high_low_resampled(analytics_df)

    metrics = {
        "Day Low": float(latest["Low"]),
        "Day High": float(latest["High"]),
        "LTP": float(latest["Close"]),
    }

    for period in ["1W", "1M", "3M", "6M", "1Y"]:
        high, low = high_low[period]
        metrics[f"{period} Low"] = float(low)
        metrics[f"{period} High"] = float(high)

    metrics["2Y Low"] = float(analytics_df["Low"].min())
    metrics["2Y High"] = float(analytics_df["High"].max())

    for span in [5, 10, 20, 100, 200]:
        metrics[f"EMA{span}"] = float(latest[f"EMA{span}"])

    return metrics


def compute_period_returns(
    analytics_df: pd.DataFrame,
    ltp: float | None = None,
) -> dict[str, float | None]:
    """
    Compute period returns from historical daily data and today's LTP.

    Returns are calculated as:
        (ltp - start_close) / start_close * 100

    For each lookback, start_close is the first available close on or after
    the target start date. YTD uses the first available close in the current
    calendar year.
    """
    if analytics_df.empty or "Close" not in analytics_df.columns:
        return {}

    df = analytics_df.copy()
    df.index = pd.to_datetime(df.index)
    df.sort_index(inplace=True)

    latest_price = pd.to_numeric(ltp, errors="coerce")
    if pd.isna(latest_price):
        latest_price = pd.to_numeric(df.iloc[-1]["Close"], errors="coerce")
    if pd.isna(latest_price):
        return {}

    latest_date = pd.to_datetime(df.index[-1])
    periods = {
        "1W Return %": latest_date - pd.DateOffset(weeks=1),
        "1M Return %": latest_date - pd.DateOffset(months=1),
        "3M Return %": latest_date - pd.DateOffset(months=3),
        "6M Return %": latest_date - pd.DateOffset(months=6),
        "1Y Return %": latest_date - pd.DateOffset(years=1),
        "2Y Return %": latest_date - pd.DateOffset(years=2),
        "YTD Return %": pd.Timestamp(year=latest_date.year, month=1, day=1),
    }

    returns: dict[str, float | None] = {}
    for label, start_date in periods.items():
        start_rows = df[df.index >= start_date]
        if start_rows.empty:
            returns[label] = None
            continue

        start_close = pd.to_numeric(start_rows.iloc[0]["Close"], errors="coerce")
        if pd.isna(start_close) or float(start_close) == 0:
            returns[label] = None
            continue

        returns[label] = round((float(latest_price) - float(start_close)) / float(start_close) * 100, 2)

    return returns


def build_metric_ladder(analytics_df: pd.DataFrame) -> list[tuple[str, float]]:
    """
    Build an ascending price ladder from the cached 2Y daily dataframe.
    """
    return sorted(build_metric_values(analytics_df).items(), key=lambda item: item[1])


def build_vertical_dashboard(ladders: dict[str, list[tuple[str, float]]]) -> pd.DataFrame:
    """
    Build a vertical table with one sorted metric ladder column per ticker.
    """
    max_rows = max((len(ladder) for ladder in ladders.values()), default=0)
    table: dict[str, list[str]] = {}
    for ticker, ladder in ladders.items():
        cells = [f"{label}: {value:.2f}" for label, value in ladder]
        cells.extend([""] * (max_rows - len(cells)))
        table[ticker] = cells
    return pd.DataFrame(table, index=range(1, max_rows + 1))


def highlight_ltp_cells(value: str) -> str:
    if isinstance(value, str) and value.startswith("LTP:"):
        return "background-color: #fff3cd; color: #7a4d00; font-weight: 700"
    return ""
