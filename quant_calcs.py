import numpy as np
import pandas as pd


def calculate_ema(series: pd.Series, span: int) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    ema = pd.Series(np.nan, index=values.index, dtype="float64")
    if span <= 0 or len(values) < span:
        return ema

    first_valid_window = values.dropna().iloc[:span]
    if len(first_valid_window) < span:
        return ema

    seed_index = first_valid_window.index[-1]
    multiplier = 2 / (span + 1)
    previous_ema = float(first_valid_window.mean())
    ema.loc[seed_index] = previous_ema

    start_position = values.index.get_loc(seed_index) + 1
    for index, value in values.iloc[start_position:].items():
        if pd.isna(value):
            ema.loc[index] = previous_ema
            continue
        previous_ema = (float(value) - previous_ema) * multiplier + previous_ema
        ema.loc[index] = previous_ema

    return ema


def calculate_atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    required_columns = {"High", "Low", "Close"}
    atr = pd.Series(np.nan, index=df.index, dtype="float64")
    if window <= 0 or df.empty or not required_columns.issubset(df.columns):
        return atr

    high = pd.to_numeric(df["High"], errors="coerce")
    low = pd.to_numeric(df["Low"], errors="coerce")
    close = pd.to_numeric(df["Close"], errors="coerce")
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    valid_true_range = true_range.dropna()
    if len(valid_true_range) < window:
        return atr

    seed_index = valid_true_range.index[window - 1]
    previous_atr = float(valid_true_range.iloc[:window].mean())
    atr.loc[seed_index] = previous_atr

    start_position = true_range.index.get_loc(seed_index) + 1
    for index, value in true_range.iloc[start_position:].items():
        if pd.isna(value):
            atr.loc[index] = previous_atr
            continue
        previous_atr = ((previous_atr * (window - 1)) + float(value)) / window
        atr.loc[index] = previous_atr

    return atr


def calculate_volume_ma(volume: pd.Series, window: int = 20) -> pd.Series:
    values = pd.to_numeric(volume, errors="coerce")
    if window <= 0:
        return pd.Series(np.nan, index=values.index, dtype="float64")
    return values.rolling(window=window, min_periods=window).mean()


def calculate_donchian(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    result = pd.DataFrame(
        {
            "donchian_20_high": np.nan,
            "donchian_20_low": np.nan,
            "donchian_20_mid": np.nan,
        },
        index=df.index,
    )
    required_columns = {"High", "Low"}
    if window <= 0 or df.empty or not required_columns.issubset(df.columns):
        return result

    high = pd.to_numeric(df["High"], errors="coerce")
    low = pd.to_numeric(df["Low"], errors="coerce")
    result["donchian_20_high"] = high.rolling(window=window, min_periods=window).max()
    result["donchian_20_low"] = low.rolling(window=window, min_periods=window).min()
    result["donchian_20_mid"] = (
        result["donchian_20_high"] + result["donchian_20_low"]
    ) / 2
    return result


def calculate_zscore(series: pd.Series, window: int = 50) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    zscore = pd.Series(np.nan, index=values.index, dtype="float64")
    if window <= 0:
        return zscore

    rolling_mean = values.rolling(window=window, min_periods=window).mean()
    rolling_std = values.rolling(window=window, min_periods=window).std(ddof=0)
    valid_rows = rolling_std.ne(0) & rolling_std.notna()
    zscore.loc[valid_rows] = (
        values.loc[valid_rows] - rolling_mean.loc[valid_rows]
    ) / rolling_std.loc[valid_rows]
    return zscore


def calculate_rsi(series: pd.Series, window: int = 14) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    rsi = pd.Series(np.nan, index=values.index, dtype="float64")
    if window <= 0 or len(values) <= window:
        return rsi

    changes = values.diff()
    gains = changes.clip(lower=0)
    losses = changes.clip(upper=0).abs()
    valid_changes = changes.dropna()
    if len(valid_changes) < window:
        return rsi

    seed_index = valid_changes.index[window - 1]
    seed_gains = gains.loc[valid_changes.index[:window]]
    seed_losses = losses.loc[valid_changes.index[:window]]
    average_gain = float(seed_gains.mean())
    average_loss = float(seed_losses.mean())

    def rsi_value(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 0.0
        relative_strength = avg_gain / avg_loss
        return 100 - (100 / (1 + relative_strength))

    rsi.loc[seed_index] = rsi_value(average_gain, average_loss)

    start_position = values.index.get_loc(seed_index) + 1
    for index in values.index[start_position:]:
        gain = gains.loc[index]
        loss = losses.loc[index]
        if pd.isna(gain) or pd.isna(loss):
            rsi.loc[index] = rsi_value(average_gain, average_loss)
            continue
        average_gain = ((average_gain * (window - 1)) + float(gain)) / window
        average_loss = ((average_loss * (window - 1)) + float(loss)) / window
        rsi.loc[index] = rsi_value(average_gain, average_loss)

    return rsi


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
