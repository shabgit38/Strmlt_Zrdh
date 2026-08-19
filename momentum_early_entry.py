from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from quant_calcs import calculate_atr, calculate_ema, calculate_volume_ma


NO_SETUP = "No Setup"
NO_TRIGGER = "No Trigger"


def calculate_early_entry_features(
    stock_df: pd.DataFrame,
    *,
    pullback_score: float | int | None = None,
    rs_vs_nifty: float | int | None = None,
    live_ltp: float | int | None = None,
) -> dict[str, Any]:
    prepared = _prepare_ohlcv(stock_df)
    if prepared.empty:
        return _empty_features()

    high = pd.to_numeric(prepared["High"], errors="coerce")
    low = pd.to_numeric(prepared["Low"], errors="coerce")
    close = pd.to_numeric(prepared["Close"], errors="coerce")
    open_ = pd.to_numeric(prepared["Open"], errors="coerce")
    volume = pd.to_numeric(prepared["Volume"], errors="coerce") if "Volume" in prepared.columns else pd.Series(np.nan, index=prepared.index)

    ema10 = calculate_ema(close, 10)
    ema20 = calculate_ema(close, 20)
    ema50 = calculate_ema(close, 50)
    ema200 = calculate_ema(close, 200)
    atr14 = calculate_atr(prepared, 14)
    volume_ma20 = calculate_volume_ma(volume, 20)

    latest_close = _latest_float(close)
    live_value = _coerce_float(live_ltp)
    effective_close = live_value if pd.notna(live_value) else latest_close
    latest_ema10 = _latest_float(ema10)
    latest_ema20 = _latest_float(ema20)
    latest_ema50 = _latest_float(ema50)
    latest_ema200 = _latest_float(ema200)
    latest_atr14 = _latest_float(atr14)
    latest_volume = _latest_float(volume)
    latest_volume_ma20 = _latest_float(volume_ma20)
    volume_ratio = (
        latest_volume / latest_volume_ma20
        if pd.notna(latest_volume) and pd.notna(latest_volume_ma20) and latest_volume_ma20 != 0
        else np.nan
    )

    prev_week_high_series = _previous_completed_period_high(high, "W-FRI")
    prev_month_high_series = _previous_completed_period_high(high, "ME")
    high_20d_series = high.shift(1).rolling(20, min_periods=20).max()
    high_60d_series = high.shift(1).rolling(60, min_periods=60).max()
    high_ath_series = high.shift(1).cummax()

    previous_close = _prior_float(close)
    previous_day_high = _prior_float(high)
    prev_week_high = _latest_float(prev_week_high_series)
    prev_month_high = _latest_float(prev_month_high_series)
    high_20d = _latest_float(high_20d_series)
    high_60d = _latest_float(high_60d_series)
    high_ath = _latest_float(high_ath_series)

    recent_pullback, days_since_pullback = _recent_pullback(low, ema20, atr14)
    weekly_breakout = _fresh_cross(effective_close, previous_close, prev_week_high)
    monthly_breakout = _fresh_cross(effective_close, previous_close, prev_month_high)
    high20_breakout = _fresh_cross(effective_close, previous_close, high_20d)
    high60_breakout = _fresh_cross(effective_close, previous_close, high_60d)

    pullback_pickup = bool(
        recent_pullback
        and pd.notna(effective_close)
        and pd.notna(previous_day_high)
        and pd.notna(latest_ema10)
        and effective_close > previous_day_high
        and effective_close > latest_ema10
    )
    ema20_bounce = bool(
        pd.notna(_latest_float(low))
        and pd.notna(effective_close)
        and pd.notna(_latest_float(open_))
        and pd.notna(previous_close)
        and pd.notna(latest_ema20)
        and pd.notna(latest_ema50)
        and pd.notna(latest_ema200)
        and pd.notna(latest_atr14)
        and _latest_float(low) <= latest_ema20 + 0.25 * latest_atr14
        and effective_close > latest_ema20
        and effective_close > _latest_float(open_)
        and effective_close > previous_close
        and latest_ema20 > latest_ema50 > latest_ema200
    )
    ema50_reclaim = bool(
        pd.notna(previous_close)
        and pd.notna(effective_close)
        and pd.notna(latest_ema50)
        and pd.notna(latest_ema200)
        and pd.notna(_coerce_float(rs_vs_nifty))
        and previous_close < latest_ema50
        and effective_close > latest_ema50
        and latest_ema50 > latest_ema200
        and float(rs_vs_nifty) > 0
    )

    entry_setup = _entry_setup(
        recent_pullback=recent_pullback,
        effective_close=effective_close,
        ema20=latest_ema20,
        ema50=latest_ema50,
        atr14=latest_atr14,
        weekly_breakout=weekly_breakout,
        monthly_breakout=monthly_breakout,
        high20_breakout=high20_breakout,
        high60_breakout=high60_breakout,
    )
    entry_trigger = _entry_trigger(
        pullback_pickup=pullback_pickup,
        weekly_breakout=weekly_breakout,
        monthly_breakout=monthly_breakout,
        ema20_bounce=ema20_bounce,
        ema50_reclaim=ema50_reclaim,
        volume_ratio=volume_ratio,
    )
    trigger_age = 0 if entry_trigger != NO_TRIGGER else None
    extended = bool(
        pd.notna(effective_close)
        and pd.notna(latest_ema20)
        and pd.notna(latest_atr14)
        and effective_close > latest_ema20 + 1.5 * latest_atr14
    )

    return {
        "momentum_state": _momentum_state(pullback_score),
        "prev_week_high": prev_week_high,
        "prev_month_high": prev_month_high,
        "high_20d": high_20d,
        "high_60d": high_60d,
        "high_ath": high_ath,
        "atr_pct": _pct_ratio(latest_atr14, effective_close),
        "volume_ratio_entry": volume_ratio,
        "ema20_distance_pct": _distance_pct(effective_close, latest_ema20),
        "ema50_distance_pct": _distance_pct(effective_close, latest_ema50),
        "dist_60d_high_pct": _distance_pct(effective_close, high_60d),
        "dist_ath_pct": _distance_pct(effective_close, high_ath),
        "recent_pullback": bool(recent_pullback),
        "days_since_pullback": days_since_pullback,
        "weekly_breakout": bool(weekly_breakout),
        "monthly_breakout": bool(monthly_breakout),
        "high20_breakout": bool(high20_breakout),
        "high60_breakout": bool(high60_breakout),
        "ema20_bounce": bool(ema20_bounce),
        "ema50_reclaim": bool(ema50_reclaim),
        "pullback_pickup": bool(pullback_pickup),
        "entry_setup": entry_setup,
        "entry_trigger": entry_trigger,
        "trigger_age": trigger_age,
        "entry_status": "Extended" if extended and entry_trigger == NO_TRIGGER else entry_trigger,
        "extended": extended,
    }


def calculate_early_entry_frame(
    stock_data: dict[str, pd.DataFrame],
    momentum_df: pd.DataFrame | None = None,
    *,
    live_ltp_by_symbol: dict[str, float] | None = None,
) -> pd.DataFrame:
    momentum_by_symbol = {}
    if momentum_df is not None and not momentum_df.empty and "ticker" in momentum_df.columns:
        momentum_by_symbol = {
            str(row.get("ticker") or "").strip().upper(): row
            for _, row in momentum_df.iterrows()
        }

    rows = []
    for symbol, stock_df in stock_data.items():
        symbol_key = str(symbol or "").strip().upper()
        momentum_row = momentum_by_symbol.get(symbol_key, {})
        features = calculate_early_entry_features(
            stock_df,
            pullback_score=momentum_row.get("pullback_score") if hasattr(momentum_row, "get") else None,
            rs_vs_nifty=momentum_row.get("rs_vs_nifty") if hasattr(momentum_row, "get") else None,
            live_ltp=(live_ltp_by_symbol or {}).get(symbol_key),
        )
        rows.append({"ticker": symbol_key, **features})
    return pd.DataFrame(rows)


def _prepare_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    prepared = df.copy()
    prepared.index = pd.to_datetime(prepared.index)
    if getattr(prepared.index, "tz", None) is not None:
        prepared.index = prepared.index.tz_localize(None)
    prepared = prepared.sort_index()
    required = {"Open", "High", "Low", "Close"}
    if not required.issubset(prepared.columns):
        return pd.DataFrame()
    return prepared


def _previous_completed_period_high(high: pd.Series, frequency: str) -> pd.Series:
    period_high = high.resample(frequency).max()
    previous_period_high = period_high.shift(1)
    return previous_period_high.reindex(high.index, method="ffill")


def _recent_pullback(low: pd.Series, ema20: pd.Series, atr14: pd.Series) -> tuple[bool, int | None]:
    pullback_zone_upper = ema20 + 0.5 * atr14
    touched = low.shift(1).le(pullback_zone_upper.shift(1))
    recent = touched.tail(5)
    if not bool(recent.any()):
        return False, None
    true_positions = [idx for idx, value in enumerate(recent.tolist()) if bool(value)]
    return True, len(recent) - 1 - true_positions[-1]


def _fresh_cross(close: float, previous_close: float, level: float) -> bool:
    return bool(
        pd.notna(close)
        and pd.notna(previous_close)
        and pd.notna(level)
        and close > level
        and previous_close <= level
    )


def _entry_setup(
    *,
    recent_pullback: bool,
    effective_close: float,
    ema20: float,
    ema50: float,
    atr14: float,
    weekly_breakout: bool,
    monthly_breakout: bool,
    high20_breakout: bool,
    high60_breakout: bool,
) -> str:
    if recent_pullback:
        return "Pullback"
    if pd.notna(effective_close) and pd.notna(ema20) and pd.notna(atr14) and abs(effective_close - ema20) <= 0.5 * atr14:
        return "EMA20 Support"
    if pd.notna(effective_close) and pd.notna(ema50) and pd.notna(atr14) and abs(effective_close - ema50) <= 0.75 * atr14:
        return "EMA50 Support"
    if weekly_breakout or monthly_breakout or high20_breakout or high60_breakout:
        return "Breakout"
    return NO_SETUP


def _entry_trigger(
    *,
    pullback_pickup: bool,
    weekly_breakout: bool,
    monthly_breakout: bool,
    ema20_bounce: bool,
    ema50_reclaim: bool,
    volume_ratio: float,
) -> str:
    volume_confirmed = pd.notna(volume_ratio) and volume_ratio >= 1.2
    if pullback_pickup and weekly_breakout:
        return "Pullback Pickup + Weekly Breakout"
    if pullback_pickup:
        return "Pullback Pickup"
    if weekly_breakout and volume_confirmed:
        return "Weekly Breakout + Volume"
    if weekly_breakout:
        return "Weekly Breakout"
    if monthly_breakout:
        return "Monthly Breakout"
    if ema20_bounce:
        return "EMA20 Bounce"
    if ema50_reclaim:
        return "EMA50 Reclaim"
    return NO_TRIGGER


def _momentum_state(score: float | int | None) -> str:
    score_value = _coerce_float(score)
    if pd.isna(score_value):
        return "Unknown"
    if score_value >= 80:
        return "Strong"
    if score_value >= 45:
        return "Moderate"
    return "Weak"


def _latest_float(series: pd.Series) -> float:
    if series.empty:
        return np.nan
    return _coerce_float(series.iloc[-1])


def _prior_float(series: pd.Series) -> float:
    if len(series) < 2:
        return np.nan
    return _coerce_float(series.iloc[-2])


def _coerce_float(value: Any) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return float(parsed) if pd.notna(parsed) else np.nan


def _distance_pct(value: float, reference: float) -> float:
    if pd.isna(value) or pd.isna(reference) or reference == 0:
        return np.nan
    return ((float(value) - float(reference)) / float(reference)) * 100


def _pct_ratio(value: float, reference: float) -> float:
    if pd.isna(value) or pd.isna(reference) or reference == 0:
        return np.nan
    return (float(value) / float(reference)) * 100


def _empty_features() -> dict[str, Any]:
    return {
        "momentum_state": "Unknown",
        "prev_week_high": np.nan,
        "prev_month_high": np.nan,
        "high_20d": np.nan,
        "high_60d": np.nan,
        "high_ath": np.nan,
        "atr_pct": np.nan,
        "volume_ratio_entry": np.nan,
        "ema20_distance_pct": np.nan,
        "ema50_distance_pct": np.nan,
        "dist_60d_high_pct": np.nan,
        "dist_ath_pct": np.nan,
        "recent_pullback": False,
        "days_since_pullback": None,
        "weekly_breakout": False,
        "monthly_breakout": False,
        "high20_breakout": False,
        "high60_breakout": False,
        "ema20_bounce": False,
        "ema50_reclaim": False,
        "pullback_pickup": False,
        "entry_setup": NO_SETUP,
        "entry_trigger": NO_TRIGGER,
        "trigger_age": None,
        "entry_status": NO_TRIGGER,
        "extended": False,
    }
