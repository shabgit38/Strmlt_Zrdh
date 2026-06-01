from datetime import datetime, time

import numpy as np
import pandas as pd
from kiteconnect import KiteConnect
import streamlit as st

from quant_calcs import (
    calculate_12_1_momentum,
    calculate_annualized_volatility,
    calculate_ema,
    calculate_return,
    calculate_rolling_high,
    get_momentum_label,
)

st.set_page_config(layout="wide") 

MOMENTUM_SCORE_WEIGHTS = {
    "ret_12_1_rank": 0.25,
    "ret_6m_rank": 0.20,
    "rs_rank": 0.15,
    "dist_52w_score": 0.15,
    "above_ema200_score": 0.10,
    "ema_trend_score": 0.10,
    "vol_adj_rank": 0.05,
}

REQUIRED_FEATURE_COLUMNS = [
    "ret_6m",
    "ret_12_1",
    "rs_vs_nifty",
    "dist_52w_score",
    "vol_adj_momentum",
]


def _normalize_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
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


def load_momentum_history(
    kite: KiteConnect,
    instrument_token: int | str,
    to_date: str | datetime,
    *,
    years: int = 2,
) -> pd.DataFrame:
    if isinstance(to_date, datetime):
        end_date = to_date.date()
    else:
        end_date = pd.to_datetime(to_date).date()

    end = datetime.combine(end_date, time(23, 59, 59))
    start = datetime.combine((pd.Timestamp(end) - pd.DateOffset(years=years)).date(), time.min)
    return get_kite_historical_data(
        kite=kite,
        instrument_token=instrument_token,
        interval="day",
        from_date=start,
        to_date=end,
    )


def _get_close_series(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype="float64")

    close_column = "Close" if "Close" in df.columns else "close" if "close" in df.columns else None
    if close_column is None:
        return pd.Series(dtype="float64")

    close = pd.to_numeric(df[close_column], errors="coerce").dropna()
    return close.sort_index()


def _get_ticker(stock_df: pd.DataFrame, fallback: str | None = None) -> str:
    if "ticker" in stock_df.columns and not stock_df["ticker"].dropna().empty:
        return str(stock_df["ticker"].dropna().iloc[-1]).strip().upper()
    if "Ticker" in stock_df.columns and not stock_df["Ticker"].dropna().empty:
        return str(stock_df["Ticker"].dropna().iloc[-1]).strip().upper()
    return str(fallback or "").strip().upper()


def calculate_momentum_features(
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    *,
    ticker: str | None = None,
) -> dict[str, float | bool | str]:
    stock_close = _get_close_series(stock_df)
    benchmark_close = _get_close_series(benchmark_df)
    resolved_ticker = _get_ticker(stock_df, ticker)

    if len(stock_close) < 252 or len(benchmark_close) < 126:
        return {
            "ticker": resolved_ticker,
            "ltp": np.nan,
            "ret_6m": np.nan,
            "ret_12_1": np.nan,
            "rs_vs_nifty": np.nan,
            "dist_52w_high": np.nan,
            "dist_52w_score": np.nan,
            "above_ema200": False,
            "above_ema200_score": 0,
            "ema50_gt_ema200": False,
            "ema_trend_score": 0,
            "vol_adj_momentum": np.nan,
            "data_status": "Insufficient Data",
        }

    ltp = float(stock_close.iloc[-1])
    ret_6m = calculate_return(stock_close, 126)
    ret_12_1 = calculate_12_1_momentum(stock_close)
    benchmark_6m_return = calculate_return(benchmark_close, 126)
    rs_vs_nifty = ret_6m - benchmark_6m_return if pd.notna(ret_6m) and pd.notna(benchmark_6m_return) else np.nan

    high_52w = calculate_rolling_high(stock_close, 252)
    dist_52w_high = ltp / high_52w if pd.notna(high_52w) and high_52w != 0 else np.nan
    dist_52w_score = min(dist_52w_high, 1.0) * 100 if pd.notna(dist_52w_high) else np.nan

    ema_50 = calculate_ema(stock_close, 50)
    ema_200 = calculate_ema(stock_close, 200)
    above_ema200 = bool(ltp > ema_200.iloc[-1]) if pd.notna(ema_200.iloc[-1]) else False
    ema50_gt_ema200 = bool(ema_50.iloc[-1] > ema_200.iloc[-1]) if pd.notna(ema_50.iloc[-1]) and pd.notna(ema_200.iloc[-1]) else False

    vol_126d = calculate_annualized_volatility(stock_close, 126)
    vol_adj_momentum = ret_6m / vol_126d if pd.notna(ret_6m) and pd.notna(vol_126d) and vol_126d != 0 else np.nan
    data_status = "OK"
    if pd.isna(vol_126d) or vol_126d == 0:
        data_status = "Zero Volatility"
    elif any(
        pd.isna(value)
        for value in [ret_6m, ret_12_1, rs_vs_nifty, dist_52w_score, vol_adj_momentum]
    ):
        data_status = "Insufficient Data"

    return {
        "ticker": resolved_ticker,
        "ltp": ltp,
        "ret_6m": ret_6m,
        "ret_12_1": ret_12_1,
        "rs_vs_nifty": rs_vs_nifty,
        "dist_52w_high": dist_52w_high,
        "dist_52w_score": dist_52w_score,
        "above_ema200": above_ema200,
        "above_ema200_score": 100 if above_ema200 else 0,
        "ema50_gt_ema200": ema50_gt_ema200,
        "ema_trend_score": 100 if ema50_gt_ema200 else 0,
        "vol_adj_momentum": vol_adj_momentum,
        "data_status": data_status,
    }


def add_percentile_ranks(features_df: pd.DataFrame) -> pd.DataFrame:
    ranked_df = features_df.copy()
    rank_columns = {
        "ret_12_1": "ret_12_1_rank",
        "ret_6m": "ret_6m_rank",
        "rs_vs_nifty": "rs_rank",
        "vol_adj_momentum": "vol_adj_rank",
    }

    for source_column, rank_column in rank_columns.items():
        if source_column not in ranked_df.columns:
            ranked_df[rank_column] = np.nan
            continue

        ranked_df[rank_column] = (
            pd.to_numeric(ranked_df[source_column], errors="coerce")
            .rank(pct=True)
            .mul(100)
        )

    return ranked_df


def calculate_final_momentum_score(features_df: pd.DataFrame) -> pd.DataFrame:
    scored_df = add_percentile_ranks(features_df)

    weighted_parts = []
    for column, weight in MOMENTUM_SCORE_WEIGHTS.items():
        if column not in scored_df.columns:
            scored_df[column] = np.nan
        weighted_parts.append(pd.to_numeric(scored_df[column], errors="coerce") * weight)

    scored_df["momentum_score"] = sum(weighted_parts)
    if "data_status" not in scored_df.columns:
        scored_df["data_status"] = "OK"
    missing_required = scored_df[REQUIRED_FEATURE_COLUMNS].isna().any(axis=1)
    scored_df.loc[missing_required & scored_df["data_status"].eq("OK"), "data_status"] = "Insufficient Data"
    scored_df.loc[scored_df["data_status"].ne("OK"), "momentum_score"] = np.nan
    scored_df["momentum_label"] = scored_df["momentum_score"].apply(get_momentum_label)

    return scored_df.sort_values(
        by="momentum_score",
        ascending=False,
        na_position="last",
    ).reset_index(drop=True)


def calculate_all_momentum_scores(
    stock_data_dict: dict[str, pd.DataFrame],
    benchmark_df: pd.DataFrame,
) -> pd.DataFrame:
    feature_rows = [
        calculate_momentum_features(stock_df, benchmark_df, ticker=ticker)
        for ticker, stock_df in stock_data_dict.items()
    ]
    if not feature_rows:
        return pd.DataFrame()

    return calculate_final_momentum_score(pd.DataFrame(feature_rows))


def calculate_momentum_scores_from_kite(
    kite: KiteConnect,
    stock_token_rows: list[dict],
    benchmark_token: int | str,
    as_of_date: str | datetime,
    *,
    symbol_key: str = "Ticker",
    token_key: str = "instrument_token",
) -> tuple[pd.DataFrame, list[str]]:
    failed_symbols: list[str] = []
    stock_data: dict[str, pd.DataFrame] = {}

    benchmark_df = load_momentum_history(kite, benchmark_token, as_of_date)
    if benchmark_df.empty:
        return pd.DataFrame(), ["BENCHMARK"]

    for row in stock_token_rows:
        symbol = str(row.get(symbol_key) or "").strip().upper()
        token = row.get(token_key)
        if not symbol or pd.isna(token):
            continue

        try:
            stock_df = load_momentum_history(kite, token, as_of_date)
        except Exception:
            failed_symbols.append(symbol)
            continue

        if stock_df.empty:
            failed_symbols.append(symbol)
            continue

        stock_data[symbol] = stock_df

    if not stock_data:
        return pd.DataFrame(), failed_symbols

    return calculate_all_momentum_scores(stock_data, benchmark_df), failed_symbols
