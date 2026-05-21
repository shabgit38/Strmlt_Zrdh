from datetime import datetime, time

import pandas as pd
import streamlit as st
from kiteconnect import KiteConnect


def _normalize_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    return df


def add_ema(df: pd.DataFrame) -> pd.DataFrame:
    for span in [10, 20, 50, 100, 200]:
        df[f"EMA{span}"] = df["Close"].ewm(span=span, adjust=False).mean()
    return df


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
        df = _normalize_datetime_index(df)

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
    # Define the end point as today
    end = datetime.combine(pd.to_datetime(to_date).date(), time(23, 59, 59))
    # Calculate the start point two years prior at midnight
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
    df = _normalize_datetime_index(df)

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
        #"Day Low": float(latest["Low"]),
        #"Day High": float(latest["High"]),
        "LTP": float(latest["Close"]),
    }

    for period in ["1W", "1M", "3M", "6M", "1Y"]:
        high, low = high_low[period]
        metrics[f"{period} Low"] = float(low)
        metrics[f"{period} High"] = float(high)

    latest_date = pd.to_datetime(analytics_df.index[-1])
    df_52w = analytics_df[analytics_df.index >= latest_date - pd.DateOffset(weeks=52)]
    if not df_52w.empty:
        metrics["52W Low"] = float(df_52w["Low"].min())
        metrics["52W High"] = float(df_52w["High"].max())

    metrics["2Y Low"] = float(analytics_df["Low"].min())
    metrics["2Y High"] = float(analytics_df["High"].max())

    for span in [10, 20, 50,100, 200]:
        metrics[f"EMA{span}"] = float(latest[f"EMA{span}"])

    return metrics


def calculate_range_position(metrics: dict[str, float]) -> tuple[float, float, float] | None:
    lows = [value for label, value in metrics.items() if "Low" in label]
    highs = [value for label, value in metrics.items() if "High" in label]
    ltp = metrics.get("LTP")

    if ltp is None or not lows or not highs:
        return None

    range_low = min(lows)
    range_high = max(highs)
    if range_high == range_low:
        return None

    position = ((ltp - range_low) / (range_high - range_low)) * 100
    return round(min(max(position, 0), 100), 1), range_low, range_high


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

    df = _normalize_datetime_index(analytics_df)
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
        #--add code start---
        latest_price_f = float(latest_price)
        start_close_f = float(start_close)

        return_pct = round(
            (latest_price_f - start_close_f)/ start_close_f * 100,2
        )
        returns[label] = {
            "return_pct": return_pct,
            "latest_price": round(latest_price_f, 2),
            "start_close": round(start_close_f, 2)
        }
        #--add code end---
        #returns[label] = round((float(latest_price) - float(start_close)) / float(start_close) * 100, 2)

    return returns


def build_metric_ladder(analytics_df: pd.DataFrame) -> list[tuple[str, float | tuple[float, float, float] | None]]:
    """
    Build an ascending price ladder from the cached 2Y daily dataframe.
    """
    metrics = build_metric_values(analytics_df)
    range_position = calculate_range_position(metrics)
    ladder: list[tuple[str, float | tuple[float, float, float] | None]] = [
        ("Range Position", range_position),
        ("Range Used", range_position),
    ]
    ladder.extend(sorted(metrics.items(), key=lambda item: item[1]))
    return ladder


def build_vertical_dashboard(ladders: dict[str, list[tuple[str, float | tuple[float, float, float] | None]]]) -> pd.DataFrame:
    """
    Build a vertical table with one sorted metric ladder column per ticker.
    """
    max_rows = max((len(ladder) for ladder in ladders.values()), default=0)
    table: dict[str, list[str]] = {}
    for ticker, ladder in ladders.items():
        cells = [
            f"Rng:{value[0]:.1f}%" if label == "Range Position" and value is not None
            else f"[{value[1]:.2f} - {value[2]:.2f}]" if label == "Range Used" and value is not None
            else f"{label}: {value:.2f}" if value is not None
            else f"{label}: -"
            for label, value in ladder
        ]
        cells.extend([""] * (max_rows - len(cells)))
        table[ticker] = cells
    return pd.DataFrame(table, index=range(1, max_rows + 1))


RETURN_PERCENT_COLUMNS = [
    "1W Return %",
    "1M Return %",
    "3M Return %",
    "6M Return %",
    "1Y Return %",
    "2Y Return %",
    "YTD Return %",
]


def build_historic_dashboard_frames(
    _kite: KiteConnect,
    token_rows: list[dict],
    as_of_date: str,
    *,
    symbol_key: str = "Ticker",
    token_key: str = "instrument_token",
    ltp_key: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """
    Build the returns and sorted price ladder frames shared by historic screens.
    """
    ladders: dict[str, list[tuple[str, float]]] = {}
    return_rows: list[dict] = []
    skipped_symbols: list[str] = []

    for row in token_rows:
        symbol = str(row.get(symbol_key) or "").strip().upper()
        token = row.get(token_key)
        if not symbol or pd.isna(token):
            continue

        analytics_df = load_analytics_history(_kite, token, as_of_date)
        if analytics_df.empty:
            skipped_symbols.append(symbol)
            continue

        ltp = row.get(ltp_key) if ltp_key is not None else None
        returns = compute_period_returns(analytics_df, ltp)
        return_rows.append(
            {
                "Ticker": symbol,
                **{
                    period: (
                        returns[period]["return_pct"]
                        if isinstance(returns.get(period), dict)
                        else returns.get(period)
                    )
                    for period in RETURN_PERCENT_COLUMNS
                },
            }
        )
        ladders[symbol] = build_metric_ladder(analytics_df)

    return pd.DataFrame(return_rows), build_vertical_dashboard(ladders), skipped_symbols


def _historic_dashboard_height(row_count: int, *, min_rows: int = 1, max_rows: int = 12) -> int:
    visible_rows = min(max(row_count, min_rows), max_rows)
    header_height = 38
    row_height = 35
    border_padding = 4
    return (visible_rows * row_height) + header_height + border_padding


def display_historic_dashboard_frames(    
    dashboard_df: pd.DataFrame,
    returns_df: pd.DataFrame,
    *,
    max_rows: int = 12,
) -> None:
    """
    Display the shared returns and sorted price ladder dashboard.
    """

    if dashboard_df.empty:
        st.info("No dashboard data returned for the selected inputs.")
        return

    st.dataframe(
        dashboard_df.style.map(highlight_ltp_cells),
        width="stretch",
        height=_historic_dashboard_height(len(dashboard_df), max_rows=max_rows),
        hide_index=True,
    )

    if not returns_df.empty:
        st.dataframe(
            returns_df,
            width="stretch",
            height=_historic_dashboard_height(len(returns_df), max_rows=max_rows),
            hide_index=True,
        )


def highlight_ltp_cells(value: str) -> str:
    if isinstance(value, str) and value.startswith("Rng:"):
        try:
            range_pct = float(value.removeprefix("Rng:").removesuffix("%"))
        except ValueError:
            return "font-weight: 700"

        if range_pct < 25:
            return "background-color: #dc2626; color: #ffffff; font-weight: 700"
        if range_pct < 50:
            return "background-color: #f97316; color: #ffffff; font-weight: 700"
        if range_pct < 75:
            return "background-color: #84cc16; color: #1a2e05; font-weight: 700"
        return "background-color: #16a34a; color: #ffffff; font-weight: 700"
    if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
        return "background-color: #bae6fd; color: #082f49; font-weight: 600"
    if isinstance(value, str) and value.startswith("LTP:"):
        return "background-color: #facc15; color: #422006; font-weight: 700"
    return ""
