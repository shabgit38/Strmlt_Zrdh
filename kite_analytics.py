from datetime import datetime, time
from html import escape
from typing import Any

import pandas as pd
import streamlit as st
from kiteconnect import KiteConnect

from quant_calcs import calculate_ema


def _normalize_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    return df


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
    if latest_date.date() == datetime.now().date() and len(df) >= 2:
        previous_close = pd.to_numeric(df.iloc[-2]["Close"], errors="coerce")
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

    latest_date = pd.to_datetime(normalized_df.index[-1]).date()
    if latest_date == datetime.now().date() and len(normalized_df) >= 2:
        reference_row = normalized_df.iloc[-2]
    else:
        reference_row = normalized_df.iloc[-1]

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
) -> list[tuple[str, float | tuple[float, ...] | None]]:
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
    ladder: list[tuple[str, float | tuple[float, ...] | None]] = [
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
    for span in [20, 50, 100, 200]:
        label = f"EMA{span}"
        ladder.append((f"__EMA_DISTANCE__{label}", calculate_distance_pct(current_price, metrics.get(label))))
    ladder.extend(sorted({**metrics, **pivot_points(analytics_df)}.items(), key=lambda item: item[1]))
    return ladder


def build_vertical_dashboard(ladders: dict[str, list[tuple[str, float | tuple[float, ...] | None]]]) -> pd.DataFrame:
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
            else f"{label.removeprefix('__EMA_DISTANCE__')}\n{value:+.2f}%" if label.startswith("__EMA_DISTANCE__") and value is not None
            else f"{label.removeprefix('__EMA_DISTANCE__')}: NA" if label.startswith("__EMA_DISTANCE__")
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
    ladders: dict[str, list[tuple[str, float]]] = {}
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
    latest_price = pd.to_numeric(ltp, errors="coerce")
    if pd.isna(latest_price):
        latest_price = pd.to_numeric(df.iloc[-1]["Close"], errors="coerce")
    previous_close = pd.to_numeric(df.iloc[-2]["Close"], errors="coerce")
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
    ladders: dict[str, list[tuple[str, float]]] = {}
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
) -> None:
    """
    Display the shared returns and sorted price ladder dashboard.
    """

    display_historic_price_ladder_frame(dashboard_df, max_rows=max_rows)
    display_historic_returns_frame(returns_df, max_rows=max_rows)


def display_historic_price_ladder_frame(
    dashboard_df: pd.DataFrame,
    *,
    max_rows: int = 12,
) -> None:
    """
    Display the sorted price ladder dashboard.
    """

    if dashboard_df.empty:
        st.info("No dashboard data returned for the selected inputs.")
        return

    symbol_color_groups = _group_dashboard_symbols_by_range_color(dashboard_df)
    if any(symbol_color_groups.values()):
        st.markdown(
            _format_symbol_color_summary(symbol_color_groups),
            unsafe_allow_html=True,
        )

    st.dataframe(
        dashboard_df.style.map(highlight_ltp_cells),
        width="stretch",
        height=_historic_dashboard_height(len(dashboard_df), max_rows=max_rows),
        hide_index=True,
    )


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


def _format_symbol_color_summary(color_groups: dict[str, list[str]]) -> str:
    summary_items = [
        (">= 75", *MOMENTUM_PALETTE["entry"], color_groups["green"]),
        (">= 50 and < 75", *MOMENTUM_PALETTE["near"], color_groups["light_green"]),
        (">= 25 and < 50", *MOMENTUM_PALETTE["wait"], color_groups["orange"]),
        ("< 25", *MOMENTUM_PALETTE["avoid"], color_groups["red"]),
    ]
    rows = []
    for label, background, foreground, symbols in summary_items:
        symbol_text = escape(", ".join(symbols)) if symbols else "-"
        rows.append(
            "<div style='display:flex;align-items:center;gap:0.5rem;font-size:0.8rem;'>"
            f"<span style='min-width:6.5rem;font-weight:700;color:{background};'>{label}</span>"
            f"<span style='background:{background};color:{foreground};font-weight:700;"
            "padding:0.2rem 0.45rem;border-radius:0.25rem;'>"
            f"{symbol_text}</span></div>"
        )
    return (
        "<div style='display:grid;gap:0.35rem;margin:0 0 0.75rem 0;'>"
        + "".join(rows)
        + "</div>"
    )


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
    if isinstance(value, str) and value.startswith("EMA") and "\n" in value and "%" in value:
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
