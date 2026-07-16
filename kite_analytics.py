from datetime import datetime, time
from html import escape
import re
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from kiteconnect import KiteConnect

from quant_calcs import calculate_ema


IST = ZoneInfo("Asia/Kolkata")
DAILY_CANDLE_COMPLETE_TIME = time(15, 40)


def _normalize_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    return df


def _completed_daily_rows(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Return daily rows completed as of the 15:40 IST availability buffer."""
    normalized_df = _normalize_datetime_index(df).sort_index()
    if normalized_df.empty:
        return normalized_df

    now_ist = now or datetime.now(IST)
    if now_ist.tzinfo is None:
        now_ist = now_ist.replace(tzinfo=IST)
    else:
        now_ist = now_ist.astimezone(IST)

    today = now_ist.date()
    row_dates = pd.Index(normalized_df.index.date)
    completed = normalized_df.loc[row_dates <= today]
    if now_ist.time().replace(tzinfo=None) < DAILY_CANDLE_COMPLETE_TIME:
        completed = completed.loc[pd.Index(completed.index.date) < today]
    return completed


def add_ema(df: pd.DataFrame) -> pd.DataFrame:
    for span in [10, 20, 50, 100, 200]:
        df[f"EMA{span}"] = calculate_ema(df["Close"], span)
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
    #print(
    #    "Kite historical_data response:",
    #    {
    #        "instrument_token": int(instrument_token),
    #        "interval": interval,
    #        "from_date": start,
    #        "to_date": end,
    #        "row_count": len(candles),
    #        "sample": candles[-5:] if candles else [],
    #    },
    #)

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
    if len(completed_weeks) >= 2:
        last_complete_date = completed_weeks.index[-2]
        df = df[df.index <= last_complete_date]
    elif len(completed_weeks) == 1:
        last_complete_date = completed_weeks.index[-1]
        df = df[df.index <= last_complete_date]

    weekly = df.resample("W-FRI").agg({"High": "max", "Low": "min"}).dropna()
    monthly = df.resample("ME").agg({"High": "max", "Low": "min"}).dropna()
    if df.empty:
        return {}

    latest = df.index.max()
    df_3m = df[df.index >= latest - pd.DateOffset(months=3)]
    df_6m = df[df.index >= latest - pd.DateOffset(months=6)]
    df_1y = df[df.index >= latest - pd.DateOffset(years=1)]

    ranges = {}
    if not weekly.empty:
        ranges["1W"] = (float(weekly.iloc[-1]["High"]), float(weekly.iloc[-1]["Low"]))
    if not monthly.empty:
        ranges["1M"] = (float(monthly.iloc[-1]["High"]), float(monthly.iloc[-1]["Low"]))
    for period, period_df in [("3M", df_3m), ("6M", df_6m), ("1Y", df_1y)]:
        if not period_df.empty:
            ranges[period] = (float(period_df["High"].max()), float(period_df["Low"].min()))
    return ranges


def build_metric_values(analytics_df: pd.DataFrame, live_ltp: float | None = None) -> dict[str, float]:
    """
    Build the shared 2Y daily metric values used by dashboards and holdings.
    """
    if analytics_df.empty:
        return {}

    analytics_df = add_ema(analytics_df.copy())
    latest = analytics_df.iloc[-1]
    latest_date = pd.to_datetime(analytics_df.index[-1])
    high_low = get_high_low_resampled(analytics_df)

    latest_close = float(latest["Close"])
    live_ltp_value = pd.to_numeric(live_ltp, errors="coerce")

    metrics = {
        "Latest Close": latest_close,
    }
    if pd.notna(live_ltp_value):
        metrics["LTP"] = float(live_ltp_value)
    if latest_date.date() == datetime.now().date():
        metrics["Today Low"] = float(latest["Low"])
        metrics["Today High"] = float(latest["High"])

    for period in ["1W", "1M", "3M", "6M", "1Y"]:
        if period not in high_low:
            continue
        high, low = high_low[period]
        metrics[f"{period} Low"] = float(low)
        metrics[f"{period} High"] = float(high)

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
    ltp = metrics.get("LTP", metrics.get("Latest Close"))

    if ltp is None or not lows or not highs:
        return None

    range_low = min(lows)
    range_high = max(highs)
    if range_high == range_low:
        return None

    position = ((ltp - range_low) / (range_high - range_low)) * 100
    return round(min(max(position, 0), 100), 1), range_low, range_high


def calculate_distance_pct(ltp: float | None, reference_value: float | None) -> float | None:
    ltp_value = pd.to_numeric(ltp, errors="coerce")
    reference = pd.to_numeric(reference_value, errors="coerce")
    if pd.isna(ltp_value) or pd.isna(reference) or float(reference) == 0:
        return None
    return round(((float(ltp_value) - float(reference)) / float(reference)) * 100, 2)


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

    returns: dict[str, float | None] = {}
    latest_date = pd.to_datetime(df.index[-1])
    completed_df = _completed_daily_rows(df)
    if latest_date.date() == datetime.now(IST).date() and not completed_df.empty:
        if pd.to_datetime(completed_df.index[-1]).date() == latest_date.date():
            previous_rows = completed_df.iloc[:-1]
        else:
            previous_rows = completed_df
        previous_close = (
            pd.to_numeric(previous_rows.iloc[-1]["Close"], errors="coerce")
            if not previous_rows.empty
            else None
        )
        if pd.notna(previous_close) and float(previous_close) != 0:
            returns["Today Return %"] = {
                "return_pct": round(
                    ((float(latest_price) - float(previous_close)) / float(previous_close)) * 100,
                    2,
                ),
                "latest_price": round(float(latest_price), 2),
                "start_close": round(float(previous_close), 2),
            }
        else:
            returns["Today Return %"] = None
    else:
        returns["Today Return %"] = None

    periods = {
        "1W Return %": latest_date - pd.DateOffset(weeks=1),
        "1M Return %": latest_date - pd.DateOffset(months=1),
        "3M Return %": latest_date - pd.DateOffset(months=3),
        "6M Return %": latest_date - pd.DateOffset(months=6),
        "1Y Return %": latest_date - pd.DateOffset(years=1),
        "2Y Return %": latest_date - pd.DateOffset(years=2),
        "YTD Return %": pd.Timestamp(year=latest_date.year, month=1, day=1),
    }
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


def compute_volume_gains(analytics_df: pd.DataFrame) -> dict[str, float | None]:
    """
    Compute completed weekly and monthly volume gain percentages.
    """
    if analytics_df.empty or "Volume" not in analytics_df.columns:
        return {}

    df = _normalize_datetime_index(analytics_df)
    df.sort_index(inplace=True)
    volume = pd.to_numeric(df["Volume"], errors="coerce")

    weekly_volume = volume.resample("W-FRI").sum(min_count=1).dropna()
    monthly_volume = volume.resample("ME").sum(min_count=1).dropna()

    def gain_pct(period_volume: pd.Series) -> float | None:
        if len(period_volume) < 3:
            return None

        current_completed = pd.to_numeric(period_volume.iloc[-2], errors="coerce")
        previous_completed = pd.to_numeric(period_volume.iloc[-3], errors="coerce")
        if (
            pd.isna(current_completed)
            or pd.isna(previous_completed)
            or float(previous_completed) == 0
        ):
            return None

        return round(
            ((float(current_completed) - float(previous_completed)) / float(previous_completed)) * 100,
            2,
        )

    return {
        "1W Volume Gain %": gain_pct(weekly_volume),
        "1M Volume Gain %": gain_pct(monthly_volume),
    }


def pivot_points(df: pd.DataFrame) -> dict[str, float]:
    """
    Return daily classical pivot values from the last completed session.
    """
    required_columns = {"High", "Low", "Close"}
    if df.empty or not required_columns.issubset(df.columns):
        return {}

    normalized_df = _normalize_datetime_index(df)
    normalized_df = normalized_df.sort_index().dropna(subset=list(required_columns))
    if normalized_df.empty:
        return {}

    completed_df = _completed_daily_rows(normalized_df)
    if completed_df.empty:
        return {}
    reference_row = completed_df.iloc[-1]

    high = float(reference_row["High"])
    low = float(reference_row["Low"])
    close = float(reference_row["Close"])
    price_range = high - low

    pivot = (high + low + close) / 3
    return {
        "D Pivot": pivot,
        "D R1": 2 * pivot - low,
        "D R2": pivot + price_range,
        "D R3": high + 2 * (pivot - low),
        "D S1": 2 * pivot - high,
        "D S2": pivot - price_range,
        "D S3": low - 2 * (high - pivot),
    }


def build_metric_ladder(
    analytics_df: pd.DataFrame,
    *,
    live_ltp: float | None = None,
    buy_avg: float | None = None,
    quantity: float | None = None,
    invested: float | None = None,
) -> list[tuple[str, float | str | tuple[float, ...] | None]]:
    """
    Build an ascending price ladder from the cached 2Y daily dataframe.
    """
    metrics = build_metric_values(analytics_df, live_ltp=live_ltp)
    range_position = calculate_range_position(metrics)
    current_price = metrics.get("LTP", metrics.get("Latest Close"))
    range_with_ltp = (
        (range_position[0], range_position[1], range_position[2], current_price)
        if range_position is not None and current_price is not None
        else None
    )
    pivots = pivot_points(analytics_df)
    ladder: list[tuple[str, float | str | tuple[float, ...] | None]] = [
        ("Range Position", range_with_ltp),
    ]
    if range_position is not None and buy_avg is not None:
        range_low = range_position[1]
        range_high = range_position[2]
        if range_high != range_low:
            buy_avg_rng = ((buy_avg - range_low) / (range_high - range_low)) * 100
            buy_avg_rng = round(min(max(buy_avg_rng, 0), 100), 1)
            ladder.append(("Buy", (buy_avg_rng, range_low, range_high, buy_avg)))
    if invested is not None:
        ladder.append(("Invested", invested))
    if quantity is not None:
        ladder.append(("Qty", quantity))

    ladder.append(("Range Used", range_position))
    ladder.append(("Position", _format_price_position(metrics, pivots, range_position)))
    for span in [20, 50, 100, 200]:
        label = f"EMA{span}"
        ladder.append((f"__EMA_DISTANCE__{label}", calculate_distance_pct(current_price, metrics.get(label))))
    for label in ["52W High", "52W Low"]:
        ladder.append((f"__52W_DISTANCE__{label}", calculate_distance_pct(current_price, metrics.get(label))))
    ladder.extend(sorted({**metrics, **pivots}.items(), key=lambda item: item[1]))
    return ladder


def _format_price_position(
    metrics: dict[str, float],
    pivots: dict[str, float],
    range_position: tuple[float, float, float] | None = None,
) -> str | None:
    current_price = metrics.get("LTP", metrics.get("Latest Close"))
    if current_price is None:
        return None
    current_label = "LTP" if metrics.get("LTP") is not None else "Latest Close"

    ema_values = {span: metrics.get(f"EMA{span}") for span in [20, 50, 100, 200]}
    ema20, ema50, ema100, ema200 = (ema_values[span] for span in [20, 50, 100, 200])
    if ema20 is not None and current_price >= ema20:
        ema_spans = [20]
    elif ema50 is not None and current_price >= ema50:
        ema_spans = [20, 50]
    elif ema100 is not None and current_price >= ema100:
        ema_spans = [50, 100]
    elif ema200 is not None and current_price >= ema200:
        ema_spans = [100, 200]
    else:
        ema_spans = [200]

    technical_parts = [
        _format_position_level(f"EMA{span}", current_price, ema_values[span])
        for span in ema_spans
        if ema_values[span] is not None
    ]
    if ema200 is not None and current_price < ema200:
        nearest_52w = _format_position_level("52W Low", current_price, metrics.get("52W Low"))
    else:
        nearest_52w = _nearest_position_level(
            current_price,
            {label: metrics.get(label) for label in ["52W Low", "52W High"]},
        )
    surrounding_pivots = _surrounding_position_levels(current_price, pivots)
    technical_parts.extend(part for part in [nearest_52w, *surrounding_pivots] if part)
    parts: list[str] = []
    if range_position is not None:
        parts.append(f"Upper Rng {_format_position_number(range_position[2])}")
    parts.append(f"{current_label} {_format_position_number(current_price)}")
    parts.extend(technical_parts)
    if range_position is not None:
        parts.append(f"Lower Rng {_format_position_number(range_position[1])}")
    return " | ".join(parts) or None


def _nearest_position_level(current_price: float, levels: dict[str, Any]) -> str | None:
    candidates: list[tuple[float, str]] = []
    for label, level in levels.items():
        distance = calculate_distance_pct(current_price, level)
        if distance is not None:
            candidates.append((abs(distance), _format_position_level(label, current_price, level)))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _surrounding_position_levels(current_price: float, levels: dict[str, Any]) -> list[str]:
    numeric_levels: list[tuple[float, str]] = []
    for label, level in levels.items():
        numeric_level = pd.to_numeric(level, errors="coerce")
        if pd.notna(numeric_level):
            numeric_levels.append((float(numeric_level), label))
    numeric_levels.sort(key=lambda item: item[0])

    exact = [item for item in numeric_levels if item[0] == current_price]
    if exact:
        selected = exact[:1]
    else:
        below = [item for item in numeric_levels if item[0] < current_price]
        above = [item for item in numeric_levels if item[0] > current_price]
        selected = []
        if below:
            selected.append(below[-1])
        if above:
            selected.append(above[0])

        pivot_value = pd.to_numeric(levels.get("D Pivot"), errors="coerce")
        if pd.notna(pivot_value) and current_price < float(pivot_value):
            selected.reverse()

    return [_format_position_level(label, current_price, level) for level, label in selected]


def _format_position_level(label: str, current_price: float, level: Any) -> str:
    distance = calculate_distance_pct(current_price, level)
    numeric_level = pd.to_numeric(level, errors="coerce")
    if distance is None or pd.isna(numeric_level):
        return ""
    level_text = _format_position_number(float(numeric_level))
    return f"{label} {distance:+.1f}% {level_text}"


def _format_position_number(value: float) -> str:
    return f"{float(value):,.2f}".rstrip("0").rstrip(".")


def build_vertical_dashboard(
    ladders: dict[str, list[tuple[str, float | str | tuple[float, ...] | None]]]
) -> pd.DataFrame:
    """
    Build a vertical table with one sorted metric ladder column per ticker.
    """
    max_rows = max((len(ladder) for ladder in ladders.values()), default=0)
    table: dict[str, list[str]] = {}
    for ticker, ladder in ladders.items():
        cells = [
            f"Rng:{value[0]:.1f}% [{value[3]:.2f}]" if label == "Range Position" and value is not None and len(value) > 3
            else f"Rng:{value[0]:.1f}%" if label == "Range Position" and value is not None
            else f"Buy:{value[0]:.1f}% [{value[3]:.2f}]" if label == "Buy" and value is not None and len(value) > 3
            else f"[{value[1]:.2f} - {value[2]:.2f}]" if label == "Range Used" and value is not None
            else f"Position: {value}" if label == "Position" and value is not None
            else "Position: -" if label == "Position"
            else f"{label.removeprefix('__EMA_DISTANCE__')}\n{value:+.2f}%" if label.startswith("__EMA_DISTANCE__") and value is not None
            else f"{label.removeprefix('__EMA_DISTANCE__')}: NA" if label.startswith("__EMA_DISTANCE__")
            else f"{label.removeprefix('__52W_DISTANCE__')}\n{value:+.2f}%" if label.startswith("__52W_DISTANCE__") and value is not None
            else f"{label.removeprefix('__52W_DISTANCE__')}: NA" if label.startswith("__52W_DISTANCE__")
            else f"{label}: {value:.2f}" if value is not None
            else f"{label}: -"
            for label, value in ladder
        ]
        cells.extend([""] * (max_rows - len(cells)))
        table[ticker] = cells
    return pd.DataFrame(table, index=range(1, max_rows + 1))


RETURN_PERCENT_COLUMNS = [
    "Today Return %",
    "1W Return %",
    "1M Return %",
    "3M Return %",
    "6M Return %",
    "1Y Return %",
    "2Y Return %",
    "YTD Return %",
]
RETURN_DISPLAY_COLUMN_LABELS = {
    "Today Return %": "Today Ret%",
    "1W Return %": "1W Ret%",
    "1M Return %": "1M Ret%",
    "3M Return %": "3M Ret%",
    "6M Return %": "6M Ret%",
    "1Y Return %": "1Y Ret%",
    "2Y Return %": "2Y Ret%",
    "YTD Return %": "YTD Ret%",
}

VOLUME_GAIN_COLUMNS = [
    "1W Volume Gain %",
    "1M Volume Gain %",
]

MOMENTUM_PALETTE = {
    "entry": ("#7DCE9B", "#111827"),
    "watch": ("#46D9E6", "#111827"),
    "near": ("#FFB15C", "#111827"),
    "wait": ("#5EA6D1", "#111827"),
    "avoid": ("#64748B", "#FFFFFF"),
}

# Display-only fallback: Position values remain in the dashboard for the summary chart.
SHOW_PRICE_POSITION_TEXT = False


def build_historic_dashboard_frames(
    _kite: KiteConnect,
    token_rows: list[dict],
    as_of_date: str,
    *,
    symbol_key: str = "Ticker",
    token_key: str = "instrument_token",
    ltp_key: str | None = None,
    buy_avg_key: str | None = None,
    quantity_key: str | None = None,
    invested_key: str | None = None,
    live_ltp_by_symbol: dict[str, float] | None = None,
    include_returns: bool = True,
    include_close_prices: bool = True,
    include_ladders: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], pd.DataFrame]:
    """
    Build the returns and sorted price ladder frames shared by historic screens.
    """
    ladders: dict[str, list[tuple[str, float | str | tuple[float, ...] | None]]] = {}
    close_prices: dict[str, pd.Series] = {}
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
        if include_close_prices and "Close" in analytics_df.columns:
            close_prices[symbol] = pd.to_numeric(analytics_df["Close"], errors="coerce")

        live_ltp = row.get(ltp_key) if ltp_key is not None else (live_ltp_by_symbol or {}).get(symbol)
        ltp = live_ltp
        buy_avg = pd.to_numeric(row.get(buy_avg_key), errors="coerce") if buy_avg_key is not None else None
        quantity = pd.to_numeric(row.get(quantity_key), errors="coerce") if quantity_key is not None else None
        invested = pd.to_numeric(row.get(invested_key), errors="coerce") if invested_key is not None else None
        if pd.isna(invested) and buy_avg_key is not None and quantity_key is not None:
            if pd.notna(buy_avg) and pd.notna(quantity):
                invested = float(buy_avg) * float(quantity)

        if include_returns:
            returns = compute_period_returns(analytics_df, ltp)
            #volume_gains = compute_volume_gains(analytics_df)
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
                    #**{column: volume_gains.get(column) for column in VOLUME_GAIN_COLUMNS},
                }
            )
        if include_ladders:
            ladders[symbol] = build_metric_ladder(
                analytics_df,
                live_ltp=live_ltp,
                buy_avg=float(buy_avg) if pd.notna(buy_avg) else None,
                quantity=float(quantity) if pd.notna(quantity) else None,
                invested=float(invested) if pd.notna(invested) else None,
            )

    close_prices_df = pd.DataFrame(close_prices).sort_index()
    return pd.DataFrame(return_rows), build_vertical_dashboard(ladders), skipped_symbols, close_prices_df


def _historic_day_mover_row(symbol: str, analytics_df: pd.DataFrame, ltp: Any = None) -> dict[str, float | str] | None:
    if analytics_df.empty or "Close" not in analytics_df.columns or len(analytics_df) < 2:
        return None

    df = _normalize_datetime_index(analytics_df)
    df.sort_index(inplace=True)
    completed_df = _completed_daily_rows(df)
    if completed_df.empty:
        return None
    latest_price = pd.to_numeric(ltp, errors="coerce")
    if pd.isna(latest_price):
        latest_price = pd.to_numeric(df.iloc[-1]["Close"], errors="coerce")
    latest_date = pd.to_datetime(df.index[-1]).date()
    if latest_date == datetime.now(IST).date() and pd.to_datetime(completed_df.index[-1]).date() == latest_date:
        previous_rows = completed_df.iloc[:-1]
    else:
        previous_rows = completed_df
    if previous_rows.empty:
        return None
    previous_close = pd.to_numeric(previous_rows.iloc[-1]["Close"], errors="coerce")
    if pd.isna(latest_price) or pd.isna(previous_close) or float(previous_close) == 0:
        return None

    latest_price_f = float(latest_price)
    previous_close_f = float(previous_close)
    return {
        "Ticker": symbol,
        "LTP": latest_price_f,
        "Previous Close": previous_close_f,
        "DayChg %": round(((latest_price_f - previous_close_f) / previous_close_f) * 100, 2),
        "DayChg": round(latest_price_f - previous_close_f, 2),
    }


def build_price_ladder_and_day_movers_frames(
    _kite: KiteConnect,
    token_rows: list[dict],
    as_of_date: str,
    *,
    symbol_key: str = "Ticker",
    token_key: str = "instrument_token",
    ltp_key: str | None = None,
    buy_avg_key: str | None = None,
    quantity_key: str | None = None,
    invested_key: str | None = None,
    live_ltp_by_symbol: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    ladders: dict[str, list[tuple[str, float | str | tuple[float, ...] | None]]] = {}
    day_mover_rows: list[dict[str, float | str]] = []
    skipped_symbols: list[str] = []
    resolved_live_ltp_by_symbol: dict[str, float] = {
        str(symbol).strip().upper(): float(ltp)
        for symbol, ltp in (live_ltp_by_symbol or {}).items()
        if str(symbol).strip() and pd.notna(pd.to_numeric(ltp, errors="coerce"))
    }

    if ltp_key is None and not resolved_live_ltp_by_symbol:
        symbols = sorted({str(row.get(symbol_key) or "").strip().upper() for row in token_rows if row.get(symbol_key)})
        if symbols:
            try:
                quotes = _kite.ltp(*[f"NSE:{symbol}" for symbol in symbols])
                resolved_live_ltp_by_symbol = {
                    str(instrument).split(":", 1)[-1].strip().upper(): float(quote["last_price"])
                    for instrument, quote in quotes.items()
                    if isinstance(quote, dict) and quote.get("last_price") is not None
                }
            except Exception:
                resolved_live_ltp_by_symbol = {}

    for row in token_rows:
        symbol = str(row.get(symbol_key) or "").strip().upper()
        token = row.get(token_key)
        if not symbol or pd.isna(token):
            continue

        analytics_df = load_analytics_history(_kite, token, as_of_date)
        if analytics_df.empty:
            skipped_symbols.append(symbol)
            continue

        live_ltp = row.get(ltp_key) if ltp_key is not None else resolved_live_ltp_by_symbol.get(symbol)
        ltp = live_ltp
        buy_avg = pd.to_numeric(row.get(buy_avg_key), errors="coerce") if buy_avg_key is not None else None
        quantity = pd.to_numeric(row.get(quantity_key), errors="coerce") if quantity_key is not None else None
        invested = pd.to_numeric(row.get(invested_key), errors="coerce") if invested_key is not None else None
        if pd.isna(invested) and buy_avg_key is not None and quantity_key is not None:
            if pd.notna(buy_avg) and pd.notna(quantity):
                invested = float(buy_avg) * float(quantity)

        day_mover = _historic_day_mover_row(symbol, analytics_df, ltp)
        if day_mover is not None:
            day_mover_rows.append(day_mover)
        ladders[symbol] = build_metric_ladder(
            analytics_df,
            live_ltp=live_ltp,
            buy_avg=float(buy_avg) if pd.notna(buy_avg) else None,
            quantity=float(quantity) if pd.notna(quantity) else None,
            invested=float(invested) if pd.notna(invested) else None,
        )

    return build_vertical_dashboard(ladders), pd.DataFrame(day_mover_rows), skipped_symbols


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
    highlight_symbols: dict[str, str] | None = None,
) -> None:
    """
    Display the shared returns and sorted price ladder dashboard.
    """

    display_historic_price_ladder_frame(dashboard_df, max_rows=max_rows, highlight_symbols=highlight_symbols)
    display_historic_returns_frame(returns_df, max_rows=max_rows)


def display_historic_price_ladder_frame(
    dashboard_df: pd.DataFrame,
    *,
    max_rows: int = 12,
    highlight_symbols: dict[str, str] | None = None,
    show_summary: bool = True,
) -> None:
    """
    Display the sorted price ladder dashboard.
    """

    if dashboard_df.empty:
        st.info("No dashboard data returned for the selected inputs.")
        return

    if show_summary:
        display_price_ladder_summary(dashboard_df, highlight_symbols=highlight_symbols)

    display_df = dashboard_df
    if not SHOW_PRICE_POSITION_TEXT:
        position_rows = dashboard_df.apply(
            lambda row: any(
                isinstance(value, str) and value.startswith("Position: ")
                for value in row
            ),
            axis=1,
        )
        display_df = dashboard_df.loc[~position_rows].reset_index(drop=True)

    st.dataframe(
        display_df.style.map(highlight_ltp_cells),
        width="stretch",
        height=_historic_dashboard_height(len(display_df), max_rows=max_rows),
        hide_index=True,
    )


def display_price_ladder_summary(
    dashboard_df: pd.DataFrame,
    *,
    highlight_symbols: dict[str, str] | None = None,
) -> None:
    summary_html = format_price_ladder_summary_html(dashboard_df, highlight_symbols=highlight_symbols)
    if summary_html:
        st.markdown(summary_html, unsafe_allow_html=True)
    else:
        st.info("No price ladder summary available.")


def format_price_ladder_summary_html(
    dashboard_df: pd.DataFrame,
    *,
    highlight_symbols: dict[str, str] | None = None,
    momentum_labels: dict[str, str] | None = None,
    show_positions: bool = False,
) -> str:
    if dashboard_df.empty:
        return ""

    symbol_color_groups = _group_dashboard_symbols_by_range_color(dashboard_df)
    if any(symbol_color_groups.values()):
        return _format_symbol_color_summary(
            symbol_color_groups,
            dashboard_df=dashboard_df if show_positions else None,
            highlight_symbols=highlight_symbols,
            momentum_labels=momentum_labels,
        )
    return ""


def display_historic_returns_frame(
    returns_df: pd.DataFrame,
    *,
    max_rows: int = 12,
) -> None:
    """
    Display the returns dashboard with shared return highlighting.
    """

    if returns_df.empty:
        st.info("No returns data available.")
        return

    formatted_percent_columns = [
        column
        for column in RETURN_PERCENT_COLUMNS + VOLUME_GAIN_COLUMNS
        if column in returns_df.columns
    ]
    display_df = returns_df.rename(columns=RETURN_DISPLAY_COLUMN_LABELS)
    display_percent_columns = [
        RETURN_DISPLAY_COLUMN_LABELS.get(column, column)
        for column in formatted_percent_columns
    ]
    st.dataframe(
        display_df.style.format(
            {column: "{:.1f}" for column in display_percent_columns},
            na_rep="-",
        ).apply(highlight_return_cells, axis=None),
        width="stretch",
        height=_historic_dashboard_height(len(display_df), max_rows=max_rows),
        hide_index=True,
    )


def _group_dashboard_symbols_by_range_color(dashboard_df: pd.DataFrame) -> dict[str, list[str]]:
    color_groups = {
        "green": [],
        "light_green": [],
        "orange": [],
        "red": [],
    }
    for symbol in dashboard_df.columns:
        range_pct = _get_symbol_range_pct(dashboard_df[symbol])
        if range_pct is None:
            continue

        if range_pct < 25:
            color_groups["red"].append(str(symbol))
        elif range_pct < 50:
            color_groups["orange"].append(str(symbol))
        elif range_pct < 75:
            color_groups["light_green"].append(str(symbol))
        else:
            color_groups["green"].append(str(symbol))
    return color_groups


def _get_symbol_range_pct(symbol_values: pd.Series) -> float | None:
    for value in symbol_values:
        if not isinstance(value, str) or not value.startswith("Rng:"):
            continue
        try:
            return float(value.removeprefix("Rng:").split("%", 1)[0])
        except ValueError:
            return None
    return None


def _format_summary_symbols(symbols: list[str], highlight_symbols: dict[str, str] | None = None) -> str:
    highlight_accents = {
        str(symbol).strip().upper(): str(accent).strip()
        for symbol, accent in (highlight_symbols or {}).items()
        if str(symbol).strip() and str(accent).strip()
    }
    if not symbols:
        return "-"

    formatted_symbols: list[str] = []
    for symbol in symbols:
        symbol_text = str(symbol).strip()
        accent = highlight_accents.get(symbol_text.upper())
        if accent:
            formatted_symbols.append(
                "<span style='display:inline-block;margin:0.05rem 0.08rem 0.05rem 0;"
                f"padding:0.03rem 0.2rem;border:1px solid {accent};"
                f"border-left:3px solid {accent};border-radius:0.2rem;'>"
                f"{escape(symbol_text)}</span>"
            )
        else:
            formatted_symbols.append(escape(symbol_text))
    return ", ".join(formatted_symbols)


def _format_symbol_color_summary(
    color_groups: dict[str, list[str]],
    *,
    dashboard_df: pd.DataFrame | None = None,
    highlight_symbols: dict[str, str] | None = None,
    momentum_labels: dict[str, str] | None = None,
) -> str:
    summary_items = [
        (">= 75", *MOMENTUM_PALETTE["entry"], "rgba(15, 118, 110, 0.18)", color_groups["green"]),
        (">= 50 and < 75", *MOMENTUM_PALETTE["near"], "rgba(125, 206, 155, 0.16)", color_groups["light_green"]),
        (">= 25 and < 50", *MOMENTUM_PALETTE["wait"], "rgba(255, 177, 92, 0.16)", color_groups["orange"]),
        ("< 25", *MOMENTUM_PALETTE["avoid"], "rgba(100, 116, 139, 0.18)", color_groups["red"]),
    ]
    rows = []
    for label, background, foreground, _tint, symbols in summary_items:
        if dashboard_df is not None:
            symbol_text = _format_summary_symbols_with_positions(
                symbols,
                dashboard_df,
                highlight_symbols,
                momentum_labels,
            )
            rows.append(
                "<div style='display:grid;gap:0.2rem;font-size:0.8rem;'>"
                f"<div style='font-weight:700;color:{background};padding:0.1rem 0 0.05rem;'>"
                f"Range {label}</div>"
                f"<div style='color:inherit;font-weight:400;padding:0.1rem 0.45rem;'>"
                f"{symbol_text}</div></div>"
            )
        else:
            symbol_text = _format_summary_symbols(symbols, highlight_symbols)
            rows.append(
                "<div style='display:flex;align-items:flex-start;gap:0.5rem;font-size:0.8rem;'>"
                f"<span style='min-width:6.5rem;font-weight:700;color:{background};'>{label}</span>"
                f"<span style='color:inherit;font-weight:400;"
                f"padding:0.2rem 0.45rem;border-radius:0.25rem;'>"
                f"{symbol_text}</span></div>"
            )
    return (
        "<div style='display:grid;gap:0.35rem;margin:0 0 0.75rem 0;'>"
        + "".join(rows)
        + "</div>"
    )


def _format_summary_symbols_with_positions(
    symbols: list[str],
    dashboard_df: pd.DataFrame,
    highlight_symbols: dict[str, str] | None = None,
    momentum_labels: dict[str, str] | None = None,
) -> str:
    if not symbols:
        return "-"

    highlight_accents = {
        str(symbol).strip().upper(): str(accent).strip()
        for symbol, accent in (highlight_symbols or {}).items()
        if str(symbol).strip() and str(accent).strip()
    }
    normalized_momentum_labels = {
        str(symbol).strip().upper(): str(label).strip()
        for symbol, label in (momentum_labels or {}).items()
        if str(symbol).strip() and str(label).strip()
    }
    label_colors = {
        "Strong Entry": MOMENTUM_PALETTE["entry"][0],
        "Watchlist - Below EMA20": MOMENTUM_PALETTE["watch"][0],
        "Near Entry": MOMENTUM_PALETTE["near"][0],
        "Wait": MOMENTUM_PALETTE["wait"][0],
        "Avoid": MOMENTUM_PALETTE["avoid"][0],
    }
    rows: list[str] = []
    for symbol in symbols:
        symbol_text = str(symbol).strip()
        accent = highlight_accents.get(symbol_text.upper())
        momentum_label = normalized_momentum_labels.get(symbol_text.upper())
        momentum_color = label_colors.get(momentum_label or "", "#64748B")
        momentum_badge = (
            "<span style='display:inline-block;"
            f"color:{momentum_color};font-size:0.72rem;font-weight:700;white-space:nowrap;'>"
            f"{escape(momentum_label)}</span>"
            if momentum_label
            else "<span></span>"
        )
        symbol_style = "font-weight:400;"
        if accent:
            symbol_style += (
                f"display:inline-block;padding:0.03rem 0.2rem;border:1px solid {accent};"
                f"border-left:3px solid {accent};border-radius:0.2rem;"
            )
        position = _position_text_from_dashboard_column(
            dashboard_df.get(symbol_text, pd.Series(dtype=object))
        )
        position_html = _format_position_summary_html(position) if position else "-"
        position_chart_html = _format_position_line_chart_html(position) if position else ""
        position_text_html = (
            f"<span style='min-width:0;font-weight:600;color:inherit;'>{position_html}</span>"
            if SHOW_PRICE_POSITION_TEXT
            else ""
        )
        rows.append(
            "<div style='display:grid;grid-template-columns:9rem 7rem minmax(0,1fr);"
            "column-gap:0.5rem;align-items:center;margin:0.12rem 0 0.35rem;'>"
            f"{momentum_badge}"
            f"<span style='{symbol_style}align-self:center;white-space:nowrap;'>"
            f"{escape(symbol_text)}</span>"
            f"{position_text_html}"
            f"{position_chart_html}"
            "</div>"
        )
    return "".join(rows)


def _position_text_from_dashboard_column(symbol_values: pd.Series) -> str | None:
    position: str | None = None
    ltp_label: str | None = None
    ltp_value: float | None = None
    range_low: float | None = None
    range_high: float | None = None
    low_52w_distance: str | None = None
    low_52w_value: float | None = None
    for value in symbol_values:
        if not isinstance(value, str):
            continue
        if value.startswith("Position: "):
            position = value.removeprefix("Position: ").strip() or None
        elif value.startswith("EMA") and "\n" not in value and "%" in value and ":" not in value:
            position = value.strip() or None
        elif value.startswith("LTP:") or value.startswith("Latest Close:"):
            label, raw_number = value.split(":", 1)
            try:
                ltp_label, ltp_value = label, float(raw_number.strip())
            except ValueError:
                pass
        elif value.startswith("[") and value.endswith("]") and " - " in value:
            try:
                low_text, high_text = value[1:-1].split(" - ", 1)
                range_low, range_high = float(low_text), float(high_text)
            except ValueError:
                pass
        elif value.startswith("52W Low\n"):
            candidate_distance = value.splitlines()[-1].strip()
            if re.fullmatch(r"[+-]\d+(?:\.\d+)?%", candidate_distance):
                low_52w_distance = candidate_distance
        elif value.startswith("52W Low:"):
            try:
                low_52w_value = float(value.split(":", 1)[1].strip())
            except ValueError:
                pass

    if not position:
        return None

    low_52w_part = (
        f"52W Low {low_52w_distance} {_format_position_number(low_52w_value)}"
        if low_52w_distance is not None and low_52w_value is not None
        else None
    )
    if position.startswith("Upper Rng "):
        parts = position.split(" | ")
        if low_52w_part and not any(part.startswith("52W Low ") for part in parts):
            lower_index = next(
                (index for index, part in enumerate(parts) if part.startswith("Lower Rng ")),
                len(parts),
            )
            parts.insert(lower_index, low_52w_part)
        return " | ".join(parts)

    parts: list[str] = []
    if range_high is not None:
        parts.append(f"Upper Rng {_format_position_number(range_high)}")
    if ltp_label and ltp_value is not None:
        parts.append(f"{ltp_label} {_format_position_number(ltp_value)}")
    parts.append(position)
    if low_52w_part and "52W Low " not in position:
        parts.append(low_52w_part)
    if range_low is not None:
        parts.append(f"Lower Rng {_format_position_number(range_low)}")
    return " | ".join(parts)


def _format_position_summary_html(position: str) -> str:
    formatted = escape(position).replace(" | ", "&nbsp;&nbsp;|&nbsp;&nbsp;")
    formatted = re.sub(
        r"(?:Latest Close|LTP)\s+[\d,]+(?:\.\d+)?",
        lambda match: (
            f"<span style='color:{MOMENTUM_PALETTE['near'][0]};font-weight:800;'>"
            f"{match.group(0)}</span>"
        ),
        formatted,
    )

    def color_distance(match: re.Match[str]) -> str:
        value = match.group(0)
        numeric_value = float(value.removesuffix("%"))
        color = (
            "#64748B"
            if numeric_value == 0
            else MOMENTUM_PALETTE["entry"][0]
            if numeric_value > 0
            else "#BE123C"
        )
        return f"<span style='color:{color};font-weight:800;'>{value}</span>"

    return re.sub(r"[+-]\d+(?:\.\d+)?%", color_distance, formatted)


def _format_position_line_chart_html(position: str) -> str:
    """Render the existing Position values; do not derive any technical metric."""
    parsed_points = _position_line_chart_points(position)

    if not any(point["label"] == "Upper Rng" for point in parsed_points) or not any(
        point["label"] == "Lower Rng" for point in parsed_points
    ):
        return ""

    ordered_points = sorted(parsed_points, key=lambda point: point["value"], reverse=True)
    nodes: list[str] = []
    last_index = len(ordered_points) - 1
    for index, point in enumerate(ordered_points):
        label = str(point["label"])
        value = float(point["value"])
        distance = point["distance"]
        distance = str(distance) if distance is not None else None
        is_endpoint = label in {"Upper Rng", "Lower Rng"}
        is_current = label in {"LTP", "Latest Close"}
        distance_color = "#64748B"
        if distance:
            numeric_distance = float(distance.removesuffix("%"))
            distance_color = (
                "#64748B"
                if numeric_distance == 0
                else MOMENTUM_PALETTE["entry"][0]
                if numeric_distance > 0
                else "#BE123C"
            )
        marker_color = (
            MOMENTUM_PALETTE["near"][0]
            if is_current
            else MOMENTUM_PALETTE["wait"][0]
            if is_endpoint
            else distance_color
        )
        title = escape(f"{label} {_format_position_number(value)}" + (f" {distance}" if distance else ""))
        left_line = "transparent" if index == 0 else "#64748B"
        right_line = "transparent" if index == last_index else "#64748B"
        node_background = "rgba(255, 202, 131, 0.12)" if is_current else "transparent"
        percentage_html = (
            f"<span style='color:{distance_color};font-weight:800;'>{escape(distance)}</span>"
            if distance
            else "<span>&nbsp;</span>"
        )
        nodes.append(
            f"<span title='{title}' style='display:grid;grid-template-rows:1.1rem 0.65rem auto;"
            f"min-width:6.25rem;text-align:center;align-items:center;background:{node_background};"
            "border-radius:0.35rem;'>"
            "<span style='font-size:0.8rem;font-weight:400;white-space:nowrap;color:#FFFFFF;'>"
            f"{escape(label)}</span>"
            "<span style='display:flex;align-items:center;width:100%;'>"
            f"<span style='flex:1;height:2px;background:{left_line};opacity:0.75;'></span>"
            f"<span style='width:0.55rem;height:0.55rem;flex:0 0 0.55rem;border-radius:50%;"
            f"background:{marker_color};border:1px solid currentColor;'></span>"
            f"<span style='flex:1;height:2px;background:{right_line};opacity:0.75;'></span>"
            "</span>"
            "<span style='display:grid;gap:0.02rem;font-size:0.8rem;line-height:1.05;'>"
            f"<span style='font-size:0.9rem;font-weight:700;color:{marker_color};'>"
            f"{escape(_format_position_number(value))}</span>"
            f"{percentage_html}</span></span>"
        )

    return (
        "<span style='grid-column:3;display:grid;grid-template-columns:repeat("
        + str(len(ordered_points))
        + ",minmax(6.25rem,1fr));width:100%;overflow-x:auto;margin:0;"
        "padding:0.1rem 0 0.2rem;'>"
        + "".join(nodes)
        + "</span>"
    )


def _position_line_chart_points(position: str) -> list[dict[str, float | str | None]]:
    """Parse the shared price-position string into serializable chart points."""
    parsed_points: list[tuple[str, float, str | None]] = []
    for part in position.split(" | "):
        endpoint_match = re.fullmatch(
            r"(Upper Rng|Lower Rng|LTP|Latest Close)\s+([\d,]+(?:\.\d+)?)",
            part.strip(),
        )
        if endpoint_match:
            parsed_points.append(
                (
                    endpoint_match.group(1),
                    float(endpoint_match.group(2).replace(",", "")),
                    None,
                )
            )
            continue

        metric_match = re.fullmatch(
            r"(.+?)\s+([+-]\d+(?:\.\d+)?%)\s+([\d,]+(?:\.\d+)?)",
            part.strip(),
        )
        if metric_match:
            parsed_points.append(
                (
                    metric_match.group(1),
                    float(metric_match.group(3).replace(",", "")),
                    metric_match.group(2),
                )
            )

    return [
        {"label": label, "value": value, "distance": distance}
        for label, value, distance in parsed_points
    ]


def position_line_chart_points_from_dashboard_column(
    symbol_values: pd.Series,
) -> list[dict[str, float | str | None]]:
    """Return the Historic Data position-chart points for one dashboard symbol."""
    position = _position_text_from_dashboard_column(symbol_values)
    if not position:
        return []
    points = _position_line_chart_points(position)
    if not any(point["label"] == "Upper Rng" for point in points) or not any(
        point["label"] == "Lower Rng" for point in points
    ):
        return []
    return sorted(points, key=lambda point: float(point["value"]), reverse=True)


def highlight_return_cells(data: pd.DataFrame) -> pd.DataFrame:
    return highlight_numeric_scale_cells(
        data,
        list(RETURN_DISPLAY_COLUMN_LABELS.values()) + RETURN_PERCENT_COLUMNS + VOLUME_GAIN_COLUMNS,
    )


def highlight_numeric_scale_cells(data: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    styles = pd.DataFrame("", index=data.index, columns=data.columns)
    for column in columns:
        if column not in data.columns:
            continue

        values = pd.to_numeric(data[column], errors="coerce")
        min_value = values.min(skipna=True)
        max_value = values.max(skipna=True)
        if pd.isna(min_value) or pd.isna(max_value) or min_value == max_value:
            continue

        for index, value in values.items():
            if pd.isna(value):
                continue

            position = (value - min_value) / (max_value - min_value) * 100
            if position < 25:
                styles.at[index, column] = "color: #BE123C; font-weight: 700"
            elif position < 50:
                styles.at[index, column] = "color: #64748B; font-weight: 700"
            elif position < 75:
                styles.at[index, column] = "color: #D97706; font-weight: 700"
            else:
                styles.at[index, column] = "color: #0F766E; font-weight: 700"
    return styles


def highlight_ltp_cells(value: str) -> str:
    if (
        isinstance(value, str)
        and (value.startswith("EMA") or value.startswith("52W High") or value.startswith("52W Low"))
        and "\n" in value
        and "%" in value
    ):
        try:
            distance_pct = float(value.splitlines()[-1].strip().removesuffix("%"))
        except (IndexError, ValueError):
            return "font-weight: 700"

        if distance_pct > 0:
            return "color: #0F766E; font-weight: 700"
        if distance_pct < 0:
            return "color: #BE123C; font-weight: 700"
        return "color: #64748B; font-weight: 700"

    if isinstance(value, str) and (
        value.startswith("Rng:") # or value.startswith("Buy:")
    ):
        try:
            range_text = value.split(":", 1)[1] if ":" in value else value
            range_pct = float(range_text.split("%", 1)[0])
        except ValueError:
            return "font-weight: 700"

        if range_pct < 25:
            return "background-color: #64748B; color: #FFFFFF; font-weight: 700"
        if range_pct < 50:
            return "background-color: #5EA6D1; color: #111827; font-weight: 700"
        if range_pct < 75:
            return "background-color: #FFB15C; color: #111827; font-weight: 700"
        return "background-color: #7DCE9B; color: #111827; font-weight: 700"
    if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
        return "background-color: #334155; color: #FFFFFF; font-weight: 600"
    if isinstance(value, str) and value.startswith("LTP:"):
        return "background-color: #334155; color: #FFFFFF; font-weight: 700"
    return ""
