import numpy as np
import pandas as pd


def calculate_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def calculate_return(close: pd.Series, periods: int) -> float:
    if len(close) <= periods:
        return np.nan

    start_close = pd.to_numeric(close.iloc[-periods], errors="coerce")
    latest_close = pd.to_numeric(close.iloc[-1], errors="coerce")
    if pd.isna(start_close) or pd.isna(latest_close) or float(start_close) == 0:
        return np.nan

    return float(latest_close) / float(start_close) - 1


def calculate_12_1_momentum(close: pd.Series) -> float:
    if len(close) < 252:
        return np.nan

    start_close = pd.to_numeric(close.iloc[-252], errors="coerce")
    skip_month_close = pd.to_numeric(close.iloc[-21], errors="coerce")
    if pd.isna(start_close) or pd.isna(skip_month_close) or float(start_close) == 0:
        return np.nan

    return float(skip_month_close) / float(start_close) - 1


def calculate_rolling_high(close: pd.Series, window: int = 252) -> float:
    if len(close) < window:
        return np.nan

    return float(pd.to_numeric(close.tail(window), errors="coerce").max())


def calculate_annualized_volatility(close: pd.Series, window: int = 126) -> float:
    if len(close) < window:
        return np.nan

    daily_returns = pd.to_numeric(close, errors="coerce").pct_change()
    volatility = daily_returns.tail(window).std() * np.sqrt(252)
    return float(volatility) if pd.notna(volatility) else np.nan


def get_momentum_label(score: float | int | None) -> str:
    score_value = pd.to_numeric(score, errors="coerce")
    if pd.isna(score_value):
        return "Insufficient Data"

    if score_value >= 85:
        return "Elite"
    if score_value >= 70:
        return "Strong"
    if score_value >= 55:
        return "Watch"
    if score_value >= 40:
        return "Weak"
    return "Avoid"
