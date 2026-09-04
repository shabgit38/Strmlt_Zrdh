import json
import math
import base64
import re
from datetime import date, datetime
from html import escape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import portfolio_streamlit
from kite_analytics import (
    build_historic_dashboard_frames,
    build_price_ladder_and_day_movers_frames,
    display_historic_price_ladder_frame,
    display_historic_returns_frame,
    format_price_ladder_summary_html,
    highlight_numeric_scale_cells,
    load_analytics_history,
)
from kite_auth import bootstrap_kite_app, clear_auth_state, get_secret_value, is_token_error
from momentum_early_entry import calculate_early_entry_frame
from momentum_score import calculate_momentum_scores_from_kite
from portfolio_terminal_component import render_calculators_terminal, render_portfolio_terminal
from setAlerts import render_alerts_tab
from stock_memory_cards import _merge_stock_notes, render_stock_memory_card
from top_gainers_losers import (
    build_day_movers_summary,
    build_portfolio_day_movers_summary,
    display_day_movers_summary,
    display_portfolio_day_movers_summary,
)
from getHldgBrk import (
    HOLDINGS_BREAKDOWN_DF_STATE_KEY,
    HOLDINGS_BREAKDOWN_VIEW_STATE_KEY,
    _active_breakdown_df,
    _holdings_breakdown_state_df,
    _load_holdings_breakdown_state,
    _ltp_match_symbol,
    _mapped_holdings_upload_columns,
    _normalized_symbol_value,
    _refresh_holdings_breakdown_state_for_symbols,
    _render_add_holdings_breakdown_entries_form,
    _set_holdings_breakdown_state,
    clean_holdings_breakdown_for_supabase,
    display_holdings_breakdown_df,
    display_exited_holdings_summary,
    enrich_holdings_breakdown_with_ltp,
    load_active_holdings_breakdown_from_supabase,
    load_exited_holdings_breakdown_from_supabase,
    upsert_holdings_breakdown_in_supabase,
    update_holdings_breakdown_from_orders,
)

st.set_page_config(layout="wide") 

SUPABASE_INDICES_TABLE_NAME = "Indices_constituents"
DEFAULT_MOMENTUM_BENCHMARK = "NIFTY 50"
BUTTON_COLOR = "#ffca83"
BUTTON_HOVER_COLOR = "#f2b766"
BUTTON_TEXT_COLOR = "#1f2937"
LTP_REFRESH_INTERVAL_MS = 60 * 60 * 1000
HOLDINGS_BREAKDOWN_ADD_MESSAGE_KEY = "holdings_breakdown_add_message"
TODAY_ORDERS_STATE_KEY = "kite_today_orders"
TODAY_ORDERS_DATE_STATE_KEY = "kite_today_orders_date"
TODAY_ORDERS_ERROR_STATE_KEY = "kite_today_orders_error"
TODAY_ORDERS_SYNC_ERROR_STATE_KEY = "kite_today_orders_sync_error"

PRICE_LADDER_EARLY_ENTRY_LABELS = (
    ("recent_pullback", "Recent pullback"),
    ("weekly_breakout", "Weekly breakout"),
    ("monthly_breakout", "Monthly breakout"),
    ("high20_breakout", "20D high breakout"),
    ("high60_breakout", "60D high breakout"),
    ("ema20_bounce", "EMA20 bounce"),
    ("ema50_reclaim", "EMA50 reclaim"),
)


def _live_ltp_refreshed_caption(state_key: str) -> None:
    refreshed_at = st.session_state.get(state_key)
    if refreshed_at:
        st.caption(f"Live LTP refreshed at {pd.Timestamp(refreshed_at).strftime('%Y-%m-%d %H:%M:%S')}")


def _apply_button_palette() -> None:
    st.markdown(
        f"""
        <style>
        section.main > div.block-container,
        div[data-testid="stAppViewContainer"] div[data-testid="stMain"] div[data-testid="stMainBlockContainer"] {{
            padding-left: 0 !important;
            padding-right: 0 !important;
            max-width: none !important;
        }}
        div.stButton > button,
        div.stButton > button[kind="primary"],
        button[data-testid="stBaseButton-primary"],
        button[data-testid="stBaseButton-secondary"] {{
            background-color: {BUTTON_COLOR} !important;
            border-color: {BUTTON_COLOR} !important;
            color: {BUTTON_TEXT_COLOR} !important;
            font-size: 0.8rem !important;
            line-height: 1.2 !important;
            min-height: 1.9rem !important;
            padding: 0.25rem 0.75rem !important;
            border-radius: 9999px !important;
        }}
        div.stButton > button p,
        div.stButton > button[kind="primary"] p,
        button[data-testid="stBaseButton-primary"] p,
        button[data-testid="stBaseButton-secondary"] p {{
            font-size: 0.8rem !important;
            line-height: 1.2 !important;
            margin: 0 !important;
        }}
        div.stButton > button:hover,
        div.stButton > button[kind="primary"]:hover,
        button[data-testid="stBaseButton-primary"]:hover,
        button[data-testid="stBaseButton-secondary"]:hover {{
            background-color: {BUTTON_HOVER_COLOR} !important;
            border-color: {BUTTON_HOVER_COLOR} !important;
            color: {BUTTON_TEXT_COLOR} !important;
        }}
        div.stButton > button:focus,
        div.stButton > button[kind="primary"]:focus,
        button[data-testid="stBaseButton-primary"]:focus,
        button[data-testid="stBaseButton-secondary"]:focus {{
            box-shadow: 0 0 0 0.15rem rgba(124, 58, 237, 0.28) !important;
            color: {BUTTON_TEXT_COLOR} !important;
        }}
        div[data-testid="stDataFrame"] div[role="gridcell"],
        div[data-testid="stDataFrame"] div[role="cell"],
        div[data-testid="stDataEditor"] div[role="gridcell"],
        div[data-testid="stDataEditor"] div[role="cell"] {{
            font-size: 0.8rem;
        }}
        section[data-testid="stSidebar"] {{
            width: 10rem !important;
            min-width: 10rem !important;
        }}
        section[data-testid="stSidebar"] > div {{
            width: 10rem !important;
        }}
        section[data-testid="stSidebar"] div.stButton {{
            width: fit-content !important;
        }}
        section[data-testid="stSidebar"] div.stButton > button {{
            width: fit-content !important;
            max-width: 9rem !important;
            white-space: normal !important;
            word-break: break-word !important;
            line-height: 1.15;
            min-height: 2rem;
            text-align: left;
            justify-content: flex-start;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


_apply_button_palette()


def _supabase_headers(supabase_key: str, *, write: bool = False) -> dict[str, str]:
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
    }
    if write:
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "return=minimal"
    return headers


@st.cache_data(ttl=24 * 60 * 60)
def load_instrument_token_from_supabase(tickers: list[str]) -> pd.DataFrame:
    """
    Load instrument rows from Supabase for the ticker symbols entered by the user.
    """
    normalized_tickers = sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})
    if not normalized_tickers:
        return pd.DataFrame()

    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_TABLE_NAME").strip()

    if not supabase_url or not supabase_key:
        raise ValueError(
            "Missing Supabase config. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "in .streamlit/secrets.toml or environment variables."
        )

    ticker_filter = ",".join(f"tradingsymbol.eq.{quote(ticker, safe='')}" for ticker in normalized_tickers)
    endpoint = (
        f"{supabase_url}/rest/v1/{quote(table_name, safe='')}"
        f"?select=*&or=({ticker_filter})"
    )
    request = Request(endpoint, headers=_supabase_headers(supabase_key), method="GET")
    try:
        with urlopen(request, timeout=60) as response:
            records = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Supabase instrument lookup failed with HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase instrument lookup failed: {exc.reason}") from exc

    instrument_token_df = pd.DataFrame(records)
    if instrument_token_df.empty:
        return pd.DataFrame(columns=["tradingsymbol", "instrument_token"])

    if "tradingsymbol" in instrument_token_df.columns:
        instrument_token_df["tradingsymbol"] = (
            instrument_token_df["tradingsymbol"].astype(str).str.strip().str.upper()
        )
    return instrument_token_df


@st.cache_data(ttl=24 * 60 * 60)
def load_indices_from_supabase() -> dict[str, str]:
    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_INDICES_TABLE_NAME").strip() or SUPABASE_INDICES_TABLE_NAME

    if not supabase_url or not supabase_key:
        raise ValueError(
            "Missing Supabase config. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "in .streamlit/secrets.toml or environment variables."
        )

    endpoint = (
        f"{supabase_url}/rest/v1/{quote(table_name, safe='')}"
        "?select=Index,Constituents&order=Index.asc"
    )
    request = Request(endpoint, headers=_supabase_headers(supabase_key), method="GET")
    try:
        with urlopen(request, timeout=60) as response:
            records = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Supabase indices lookup failed with HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase indices lookup failed: {exc.reason}") from exc

    indices: dict[str, str] = {}
    for record in records:
        index_name = str(record.get("Index") or "").strip()
        constituents = str(record.get("Constituents") or "").strip()
        if index_name and constituents:
            indices[index_name] = constituents
    return indices


def resolve_tokens_from_tickers(
    tickers: list[str],
    instruments_df: pd.DataFrame,
    exchange_by_symbol: dict[str, str] | None = None,
) -> tuple[dict[str, int], list[str]]:
    """
    Map comma-separated tickers to instrument tokens using the instrument dump.
    """
    resolved: dict[str, int] = {}
    missing: list[str] = []
    normalized = instruments_df.copy()
    normalized["tradingsymbol"] = normalized["tradingsymbol"].astype(str).str.strip().str.upper()

    for ticker in tickers:
        matches = normalized[normalized["tradingsymbol"] == ticker]
        if matches.empty:
            missing.append(ticker)
            continue
        # The instrument dump can contain the same symbol for multiple exchanges.
        if "exchange" in matches.columns:
            requested_exchange = (exchange_by_symbol or {}).get(_ltp_match_symbol(ticker), "NSE")
            exchange_matches = matches[
                matches["exchange"].astype(str).str.strip().str.upper() == "NSE"
            ]
            requested_matches = matches[
                matches["exchange"].astype(str).str.strip().str.upper() == requested_exchange
            ]
            if not requested_matches.empty:
                matches = requested_matches
            elif not exchange_matches.empty:
                matches = exchange_matches
        resolved[ticker] = int(matches.iloc[0]["instrument_token"])
    return resolved, missing


def _format_display_value(value: Any, decimals: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:,.{decimals}f}"
    return str(value)


def _format_percent_value(value: Any, decimals: int = 2) -> str:
    formatted_value = _format_display_value(value, decimals)
    if formatted_value == "-":
        return formatted_value
    return f"{formatted_value}%"


def _pnl_color(value: Any) -> str:
    converted = pd.to_numeric(value, errors="coerce")
    if pd.isna(converted):
        return "#475569"
    if converted > 0:
        return "#047857"
    if converted < 0:
        return "#b91c1c"
    return "#475569"


def _style_pnl_columns(df: pd.DataFrame):
    pnl_columns = [
        column
        for column in [
            "batch_pnl",
            "batch_pnl_pct",
            "pnl",
            "pnl_pct",
            "Batch P&L",
            "Batch P&L %",
            "P&L",
            "P&L %",
            "DayChg %",
            "Daychg%",
        ]
        if column in df.columns
    ]
    formatters = {
        column: _format_display_value
        for column in df.columns
        if column not in {"batch_pnl_pct", "pnl_pct", "Batch P&L %", "P&L %", "DayChg %", "Daychg%"}
    }
    for column in ["batch_pnl_pct", "pnl_pct", "Batch P&L %", "P&L %", "DayChg %", "Daychg%"]:
        if column in df.columns:
            formatters[column] = _format_percent_value

    styler = df.style.format(formatters, na_rep="-")
    for column in pnl_columns:
        styler = styler.map(lambda value: f"color: {_pnl_color(value)}; font-weight: 600", subset=[column])
    return styler


def _rng_symbol_color(range_pct: float | None) -> str:
    if range_pct is None:
        return ""
    if range_pct < 25:
        return "color: #dc2626; font-weight: 700"
    if range_pct < 50:
        return "color: #f97316; font-weight: 700"
    if range_pct < 75:
        return "color: #84cc16; font-weight: 700"
    return "color: #16a34a; font-weight: 700"


def _rng_color_by_symbol(dashboard_df: pd.DataFrame) -> dict[str, str]:
    colors: dict[str, str] = {}
    if dashboard_df.empty:
        return colors

    for symbol in dashboard_df.columns:
        values = dashboard_df[symbol]
        range_pct = None
        for value in values:
            if not isinstance(value, str) or not value.startswith("Rng:"):
                continue
            try:
                range_pct = float(value.removeprefix("Rng:").split("%", 1)[0])
            except ValueError:
                range_pct = None
            break

        color = _rng_symbol_color(range_pct)
        if color:
            colors[str(symbol).strip().upper()] = color
    return colors


def _dataframe_height(row_count: int, *, min_rows: int = 1, max_rows: int | None = None) -> int:
    visible_rows = max(row_count, min_rows)
    if max_rows is not None:
        visible_rows = min(visible_rows, max_rows)
    header_height = 38
    row_height = 35
    border_padding = 4
    return header_height + (visible_rows * row_height) 


def _sort_historic_dashboard_by_rng(dashboard_df: pd.DataFrame) -> pd.DataFrame:
    if dashboard_df.empty:
        return dashboard_df

    def rng_value(column: str) -> float:
        value = dashboard_df[column].iloc[0]
        if isinstance(value, str) and value.startswith("Rng:"):
            try:
                return float(value.removeprefix("Rng:").split("%", 1)[0])
            except ValueError:
                return float("-inf")
        return float("-inf")

    sorted_columns = sorted(dashboard_df.columns, key=rng_value, reverse=True)
    return dashboard_df.loc[:, sorted_columns]


def _parse_prefixed_float(value: Any, prefix: str) -> float | None:
    if not isinstance(value, str) or not value.startswith(prefix):
        return None
    try:
        return float(value.split(":", 1)[1].strip().split()[0])
    except (IndexError, ValueError):
        return None


def _extract_historic_ladder_summary(dashboard_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if dashboard_df.empty:
        return pd.DataFrame()

    for symbol in dashboard_df.columns:
        row: dict[str, Any] = {"Ticker": str(symbol).strip().upper()}
        for value in dashboard_df[symbol]:
            if not isinstance(value, str):
                continue

            if value.startswith("Rng:"):
                try:
                    row["Range %"] = float(value.removeprefix("Rng:").split("%", 1)[0])
                except ValueError:
                    pass
            elif "\n" in value and value.startswith("EMA") and value.endswith("%"):
                lines = value.splitlines()
                try:
                    row[f"{lines[0]} Dist %"] = float(lines[-1].strip().removesuffix("%"))
                except (IndexError, ValueError):
                    pass
            elif "\n" in value and value.startswith(("52W High", "52W Low")) and value.endswith("%"):
                lines = value.splitlines()
                try:
                    row[f"{lines[0]} Dist %"] = float(lines[-1].strip().removesuffix("%"))
                except (IndexError, ValueError):
                    pass
            elif value.startswith("52W High:"):
                row["52W High"] = _parse_prefixed_float(value, "52W High:")
            elif value.startswith("52W Low:"):
                row["52W Low"] = _parse_prefixed_float(value, "52W Low:")

        rows.append(row)

    return pd.DataFrame(rows)


def _filter_historic_price_ladder(
    dashboard_df: pd.DataFrame,
    ema_filter: str,
    proximity_filter: str,
    proximity_pct: float,
) -> pd.DataFrame:
    if dashboard_df.empty or (ema_filter == "All" and proximity_filter == "All"):
        return dashboard_df

    summary_df = _extract_historic_ladder_summary(dashboard_df).set_index("Ticker")
    selected_symbols = pd.Series(True, index=summary_df.index, dtype=bool)

    def numeric_column(column: str) -> pd.Series:
        if column not in summary_df.columns:
            return pd.Series(float("nan"), index=summary_df.index)
        return pd.to_numeric(summary_df[column], errors="coerce")

    if ema_filter != "All":
        direction, ema_label = ema_filter.split(" ", 1)
        distances = numeric_column(f"{ema_label} Dist %")
        selected_symbols &= distances.ge(0) if direction == "Above" else distances.lt(0)

    if proximity_filter != "All":
        distance_column = f"{proximity_filter.removeprefix('Near ')} Dist %"
        distances = numeric_column(distance_column)
        selected_symbols &= distances.abs().le(float(proximity_pct))

    selected = set(summary_df.index[selected_symbols.fillna(False)])
    return dashboard_df.loc[:, [column for column in dashboard_df.columns if str(column).strip().upper() in selected]]


def build_consolidated_momentum_dashboard(
    momentum_df: pd.DataFrame,
    returns_df: pd.DataFrame,
    dashboard_df: pd.DataFrame,
) -> pd.DataFrame:
    if momentum_df.empty:
        return pd.DataFrame()

    consolidated = momentum_df.copy()
    if "ticker" in consolidated.columns:
        consolidated = consolidated.rename(columns={"ticker": "Ticker"})
    consolidated["Ticker"] = consolidated["Ticker"].astype(str).str.strip().str.upper()

    rename_map = {
        "ltp": "LTP",
        "latest_close": "Latest Close",
        "pullback_score": "Pullback Score",
        "entry_signal": "Entry Signal",
        "ret_6m": "6M Momentum",
        "ret_12_1": "12-1 Momentum",
        "rs_vs_nifty": "RS vs Nifty",
        "dist_52w_high": "52W High Proximity",
        "above_ema200": "Above EMA200",
        "ema50_gt_ema200": "EMA50 > EMA200",
        "vol_adj_mtm": "Vol Adj Mtm",
        "data_status": "Status",
    }
    consolidated = consolidated.rename(columns=rename_map)

    if not returns_df.empty and "Ticker" in returns_df.columns:
        return_columns = [
            column
            for column in ["Ticker", "Today Return %", "1W Return %", "1M Return %", "3M Return %", "6M Return %", "1Y Return %", "YTD Return %"]
            if column in returns_df.columns
        ]
        returns_summary = returns_df[return_columns].copy()
        returns_summary["Ticker"] = returns_summary["Ticker"].astype(str).str.strip().str.upper()
        consolidated = consolidated.merge(returns_summary, on="Ticker", how="left")

    ladder_summary = _extract_historic_ladder_summary(dashboard_df)
    if not ladder_summary.empty:
        consolidated = consolidated.merge(ladder_summary, on="Ticker", how="left")

    preferred_columns = [
        "Ticker",
        "Pullback Score",
        "Entry Signal",
        "Status",
        "LTP",
        "Latest Close",
        "Range %",
        "6M Momentum",
        "12-1 Momentum",
        "RS vs Nifty",
        "52W High Proximity",
        "Today Return %",
        "1W Return %",
        "1M Return %",
        "3M Return %",
        "6M Return %",
        "1Y Return %",
        "YTD Return %",
        "Above EMA200",
        "EMA50 > EMA200",
        "EMA10 Dist %",
        "EMA20 Dist %",
        "EMA50 Dist %",
        "EMA100 Dist %",
        "EMA200 Dist %",
        "Vol Adj Mtm",
        "52W High",
        "52W Low",
    ]
    ordered_columns = [column for column in preferred_columns if column in consolidated.columns]
    extra_columns = [column for column in consolidated.columns if column not in ordered_columns]
    return consolidated[ordered_columns + extra_columns]


def display_consolidated_momentum_dashboard(consolidated_df: pd.DataFrame) -> None:
    if consolidated_df.empty:
        return

    percent_fraction_columns = [
        column
        for column in ["6M Momentum", "12-1 Momentum", "RS vs Nifty", "52W High Proximity"]
        if column in consolidated_df.columns
    ]
    percent_point_columns = [
        column
        for column in ["Today Return %", "1W Return %", "1M Return %", "3M Return %", "6M Return %", "1Y Return %", "YTD Return %", "Range %", "EMA10 Dist %", "EMA20 Dist %", "EMA50 Dist %", "EMA100 Dist %", "EMA200 Dist %"]
        if column in consolidated_df.columns
    ]
    formatters = {
        "Pullback Score": "{:.1f}",
        "LTP": "{:.2f}",
        "Latest Close": "{:.2f}",
        "Vol Adj Mtm": "{:.2f}",
        "52W High": "{:.2f}",
        "52W Low": "{:.2f}",
        **{column: "{:.2%}" for column in percent_fraction_columns},
        **{column: "{:.2f}" for column in percent_point_columns},
    }

    st.dataframe(
        consolidated_df.style.format(formatters, na_rep="-"),
        width="stretch",
        height=_dataframe_height(len(consolidated_df), max_rows=18),
        hide_index=True,
    )


def build_correlation_matrix(close_prices_df: pd.DataFrame) -> pd.DataFrame:
    if close_prices_df.empty:
        return pd.DataFrame()

    numeric_prices = close_prices_df.apply(pd.to_numeric, errors="coerce")
    daily_returns = numeric_prices.pct_change(fill_method=None).dropna(how="all")
    if daily_returns.empty:
        return pd.DataFrame()

    return daily_returns.corr()


def _correlation_cell_style(value: Any) -> str:
    value = pd.to_numeric(value, errors="coerce")
    if pd.isna(value):
        return ""

    value = float(value)
    if value >= 0.8:
        return "background-color: #dc2626; color: #ffffff; font-weight: 700"
    if value >= 0.6:
        return "background-color: #f97316; color: #ffffff; font-weight: 700"
    if value >= 0.3:
        return "background-color: #facc15; color: #422006; font-weight: 700"
    if value >= 0:
        return "background-color: #bbf7d0; color: #14532d; font-weight: 700"
    return "background-color: #bfdbfe; color: #1e3a8a; font-weight: 700"


def display_correlation_matrix(close_prices_df: pd.DataFrame) -> None:
    correlation_df = build_correlation_matrix(close_prices_df)
    if correlation_df.empty:
        st.info("No correlation data available.")
        return

    st.markdown(
        """
        **Correlation Legend**

        - `+1.00`: stocks move almost together
        - `0.70+`: highly correlated
        - `0.30 to 0.70`: moderate correlation
        - `0.00 to 0.30`: low correlation
        - `< 0`: often move opposite
        """
    )
    st.dataframe(
        correlation_df.style.format("{:.2f}", na_rep="-").map(_correlation_cell_style),
        width="stretch",
        height=_dataframe_height(len(correlation_df), max_rows=18),
    )


def _pullback_signal_style(score: Any, signal: Any) -> str:
    signal_text = str(signal or "").strip()
    score_value = pd.to_numeric(score, errors="coerce")
    if signal_text == "Watchlist - Below EMA20":
        return "background-color: #7DCE9B; color: #111827; font-weight: 700"
    if pd.isna(score_value):
        return ""
    if score_value < 45:
        return "background-color: #64748B; color: #FFFFFF; font-weight: 700"
    if score_value < 65:
        return "background-color: #5EA6D1; color: #111827; font-weight: 700"
    if score_value < 80:
        return "background-color: #FFB15C; color: #111827; font-weight: 700"
    return "background-color: #0F766E; color: #FFFFFF; font-weight: 700"


def highlight_momentum_rank_cells(data: pd.DataFrame) -> pd.DataFrame:
    styles = pd.DataFrame("", index=data.index, columns=data.columns)
    highlight_columns = [
        column
        for column in ["ticker", "Entry"]
        if column in data.columns
    ]
    if not highlight_columns or "pullback_score" not in data.columns:
        return styles

    scores = pd.to_numeric(data["pullback_score"], errors="coerce")
    for index, score in scores.items():
        signal = data.at[index, "entry_signal"] if "entry_signal" in data.columns else None
        style = _pullback_signal_style(score, signal)
        if not style:
            continue

        for column in highlight_columns:
            styles.at[index, column] = style

    return styles


def _group_momentum_symbols_by_label(momentum_df: pd.DataFrame) -> dict[str, list[str]]:
    label_groups = {
        "Strong Entry": [],
        "Watchlist - Below EMA20": [],
        "Near Entry": [],
        "Wait": [],
        "Avoid": [],
    }
    if momentum_df.empty or not {"ticker", "entry_signal"}.issubset(momentum_df.columns):
        return label_groups

    for _, row in momentum_df.iterrows():
        label = str(row.get("entry_signal") or "").strip()
        ticker = str(row.get("ticker") or "").strip().upper()
        if label in label_groups and ticker:
            label_groups[label].append(ticker)
    return label_groups


def _momentum_label_by_symbol(momentum_df: pd.DataFrame) -> dict[str, str]:
    """Return the same symbol labels used by the Momentum Summary card."""
    return {
        symbol: label
        for label, symbols in _group_momentum_symbols_by_label(momentum_df).items()
        for symbol in symbols
    }


SUMMARY_HIGHLIGHT_ACCENTS = {
    "Top Gainer": "#7DCE9B",
    "Top Gainers": "#7DCE9B",
    "Top Loser": "#DC2626",
    "Top Losers": "#DC2626",
    "Top Contributor": "#7DCE9B",
    "Top Drag": "#DC2626",
}


def _format_summary_symbols(
    symbols: list[str],
    highlight_symbols: dict[str, str] | None = None,
    mtf_symbols: set[str] | None = None,
    exited_symbols: set[str] | None = None,
) -> str:
    highlight_accents = {
        str(symbol).strip().upper(): str(accent).strip()
        for symbol, accent in (highlight_symbols or {}).items()
        if str(symbol).strip() and str(accent).strip()
    }
    if not symbols:
        return "-"

    normalized_mtf_symbols = {str(symbol).strip().upper() for symbol in (mtf_symbols or set())}
    normalized_exited_symbols = {str(symbol).strip().upper() for symbol in (exited_symbols or set())}
    formatted_symbols: list[str] = []
    for symbol in symbols:
        symbol_text = str(symbol).strip()
        marker = ""
        if symbol_text.upper() in normalized_mtf_symbols:
            marker = " <span style='font-size:0.65rem;font-weight:800;color:#F59E0B;vertical-align:super;'>M</span>"
        elif symbol_text.upper() in normalized_exited_symbols:
            marker = " <span style='font-size:0.65rem;font-weight:800;color:#DC2626;vertical-align:super;'>E</span>"
        accent = highlight_accents.get(symbol_text.upper())
        if accent:
            formatted_symbols.append(
                "<span style='display:inline-block;margin:0.05rem 0.08rem 0.05rem 0;"
                f"padding:0.03rem 0.2rem;border:1px solid {accent};"
                f"border-left:3px solid {accent};border-radius:0.2rem;'>"
                f"{escape(symbol_text)}{marker}</span>"
            )
        else:
            formatted_symbols.append(f"{escape(symbol_text)}{marker}")
    return ", ".join(formatted_symbols)


def _format_momentum_label_summary(
    label_groups: dict[str, list[str]],
    *,
    highlight_symbols: dict[str, str] | None = None,
    mtf_symbols: set[str] | None = None,
    exited_symbols: set[str] | None = None,
) -> str:
    summary_items = [
        ("Strong Entry", "#0F766E", "#FFFFFF", "rgba(15, 118, 110, 0.18)", label_groups["Strong Entry"]),
        ("Watchlist - Below EMA20", "#7DCE9B", "#111827", "rgba(125, 206, 155, 0.16)", label_groups["Watchlist - Below EMA20"]),
        ("Near Entry", "#FFB15C", "#111827", "rgba(255, 177, 92, 0.16)", label_groups["Near Entry"]),
        ("Wait", "#5EA6D1", "#111827", "rgba(94, 166, 209, 0.16)", label_groups["Wait"]),
        ("Avoid", "#64748B", "#FFFFFF", "rgba(100, 116, 139, 0.18)", label_groups["Avoid"]),
    ]
    rows = []
    for label, background, foreground, tint, symbols in summary_items:
        symbol_text = _format_summary_symbols(symbols, highlight_symbols, mtf_symbols, exited_symbols)
        rows.append(
            "<div style='display:flex;align-items:center;gap:0.5rem;font-size:0.8rem;'>"
            f"<span style='min-width:5rem;font-weight:700;color:{background};'>{label}</span>"
            f"<span style='background:{tint};color:#FFFFFF;font-weight:400;"
            f"padding:0.2rem 0.45rem;border-radius:0.25rem;'>"
            f"{symbol_text}</span></div>"
        )
    return (
        "<div style='display:grid;gap:0.35rem;margin:0 0 0.75rem 0;'>"
        + "".join(rows)
        + "</div>"
    )


def display_momentum_label_summary(
    momentum_df: pd.DataFrame,
    *,
    highlight_symbols: dict[str, str] | None = None,
    exited_symbols: set[str] | None = None,
) -> None:
    label_groups = _group_momentum_symbols_by_label(momentum_df)
    if any(label_groups.values()):
        st.markdown(
            _format_momentum_label_summary(
                label_groups,
                highlight_symbols=highlight_symbols,
                exited_symbols=exited_symbols,
            ),
            unsafe_allow_html=True,
        )


def _summary_panel_html(title: str, body_html: str, accent: str) -> str:
    return (
        f"<div style='border:1px solid {accent};"
        "border-radius:8px;padding:0.65rem 0.75rem;margin:0.15rem 0 0.75rem 0;'>"
        f"<div style='font-size:1rem;font-weight:400;text-transform:uppercase;"
        f"margin-bottom:0.45rem;color:{accent};'>"
        f"{escape(title)}</div>"
        f"{body_html}"
        "</div>"
    )


def _prepared_momentum_display_df(momentum_df: pd.DataFrame) -> pd.DataFrame:
    momentum_display_df = momentum_df.copy()
    if {"ema20", "atr14"}.issubset(momentum_display_df.columns):
        momentum_display_df["Entry"] = momentum_display_df.apply(_format_entry_range, axis=1)
    sort_columns = [
        column
        for column in ["pullback_score"]
        if column in momentum_display_df.columns
    ]
    if sort_columns:
        momentum_display_df = momentum_display_df.sort_values(
            by=sort_columns,
            ascending=[False] * len(sort_columns),
            na_position="last",
        )
    momentum_display_df = _merge_stock_notes(momentum_display_df)
    note_columns = [column for column in ["why", "moat", "risk"] if column in momentum_display_df.columns]
    if note_columns:
        note_values = momentum_display_df[note_columns].fillna("").astype(str)
        momentum_display_df["notes_status"] = note_values.apply(
            lambda row: "Yes" if any(value.strip() for value in row) else "No",
            axis=1,
        )
    momentum_display_columns = [
        "ticker",
        "ltp",
        "latest_close",
        "pullback_score",
        "entry_signal",
        "Entry",
        "atr14",
        "notes_status",
        "research_age_days",
        "last_reviewed_date",
        "ret_12_1",
        "ret_6m",
        "rs_vs_nifty",
        "ema10_extension_pct",
        "ema20_extension_pct",
        "rsi14",
        "volume_ratio",
        "zscore_50",
        "dist_52w_high",
        "above_ema200",
        "ema50_gt_ema200",
        "vol_adj_mtm",
        "data_status",
    ]
    return momentum_display_df[
        [column for column in momentum_display_columns if column in momentum_display_df.columns]
    ]


def render_momentum_ranking_table(
    momentum_df: pd.DataFrame,
    day_movers_df: pd.DataFrame,
    *,
    key: str,
    show_summary: bool = True,
    mtf_symbols: set[str] | None = None,
    exited_symbols: set[str] | None = None,
) -> None:
    if momentum_df.empty:
        st.info("No momentum score data available.")
        return

    momentum_summary_highlight_symbols = _summary_ticker_accents(
        build_day_movers_summary(day_movers_df)
    )
    momentum_display_df = _prepared_momentum_display_df(momentum_df)
    if show_summary:
        display_momentum_label_summary(
            momentum_display_df,
            highlight_symbols=momentum_summary_highlight_symbols,
        )
    table_display_df = momentum_display_df.copy()
    normalized_mtf_symbols = {str(symbol).strip().upper() for symbol in (mtf_symbols or set())}
    normalized_exited_symbols = {str(symbol).strip().upper() for symbol in (exited_symbols or set())}
    if "ticker" in table_display_df.columns and (normalized_mtf_symbols or normalized_exited_symbols):
        table_display_df["ticker"] = table_display_df["ticker"].apply(
            lambda symbol: (
                f"{symbol} ᴹ" if str(symbol).strip().upper() in normalized_mtf_symbols
                else f"{symbol} E" if str(symbol).strip().upper() in normalized_exited_symbols
                else symbol
            )
        )
    table_col, notes_col = st.columns([3, 1], vertical_alignment="top")
    with table_col:
        momentum_selection = st.dataframe(
            table_display_df.style.format(
                {
                    "ltp": "{:.2f}",
                    "latest_close": "{:.2f}",
                    "pullback_score": "{:.1f}",
                    "ret_6m": "{:.2%}",
                    "ret_12_1": "{:.2%}",
                    "rs_vs_nifty": "{:.2%}",
                    "ema10_extension_pct": "{:.2f}%",
                    "ema20_extension_pct": "{:.2f}%",
                    "atr14": "{:.2f}",
                    "rsi14": "{:.1f}",
                    "volume_ratio": "{:.2f}",
                    "zscore_50": "{:.2f}",
                    "dist_52w_high": "{:.2%}",
                    "dist_52w_score": "{:.1f}",
                    "above_ema200_score": "{:.1f}",
                    "ema_trend_score": "{:.1f}",
                    "vol_adj_mtm": "{:.2f}",
                },
                na_rep="-",
            ).apply(highlight_momentum_rank_cells, axis=None),
            width="stretch",
            height=_dataframe_height(len(momentum_display_df), max_rows=18),
            hide_index=True,
            column_config={
                "ticker": st.column_config.TextColumn(
                    "ticker",
                    width="medium",
                ),
                "ltp": st.column_config.NumberColumn(
                    "LTP",
                    format="%.2f",
                ),
                "latest_close": st.column_config.NumberColumn(
                    "Latest Close",
                    format="%.2f",
                ),
                "notes_status": st.column_config.TextColumn(
                    "Notes",
                    width="small",
                ),
                "research_age_days": st.column_config.NumberColumn(
                    "Review Age",
                    format="%d",
                    width="small",
                ),
                "last_reviewed_date": st.column_config.TextColumn(
                    "Last Reviewed",
                    width="small",
                ),
                "ema10_extension_pct": st.column_config.NumberColumn(
                    "ema10_ext",
                    format="%.2f%%",
                ),
                "ema20_extension_pct": st.column_config.NumberColumn(
                    "ema20_ext",
                    format="%.2f%%",
                ),
            },
            key=key,
            on_select="rerun",
            selection_mode="single-row",
        )
    with notes_col:
        selected_rows = momentum_selection.selection.rows if momentum_selection.selection else []
        selected_row = selected_rows[0] if selected_rows else None
        if selected_row is not None and 0 <= selected_row < len(momentum_display_df):
            render_stock_memory_card(momentum_display_df.iloc[selected_row])
        else:
            st.info("Select a stock row to view or add notes.")


def _summary_ticker_accents(summary_df: pd.DataFrame) -> dict[str, str]:
    if summary_df.empty or not {"Metric", "Ticker"}.issubset(summary_df.columns):
        return {}

    accents: dict[str, str] = {}
    for _, row in summary_df.iterrows():
        symbol = str(row.get("Ticker") or "").strip().upper()
        metric = str(row.get("Metric") or "").strip()
        accent = SUMMARY_HIGHLIGHT_ACCENTS.get(metric)
        if symbol and accent:
            accents[symbol] = accent
    return accents


def _format_entry_range(row: pd.Series) -> str:
    ema20 = pd.to_numeric(row.get("ema20"), errors="coerce")
    atr14 = pd.to_numeric(row.get("atr14"), errors="coerce")
    if pd.isna(ema20) or pd.isna(atr14):
        return "-"

    entry_lower = ema20
    #fair_entry = ema20 + (0.15 * atr14)
    entry_upper = ema20 + (0.5 * atr14)
    return f"L-{entry_lower:.2f}|U-{entry_upper:.2f}"




#st.subheader("Portfolio Holdings")


def _read_uploaded_file(uploaded_file) -> pd.DataFrame:
    filename = uploaded_file.name.lower()
    st.session_state["kite_holdings_download_filename"] = filename
    filedate= filename.split("_")[1] if "_" in filename else "Unknown"
    
    #print(f"Uploaded holdings file date: {filedate}")
      
    if filename.endswith(".csv"):
        return pd.read_csv(uploaded_file)
    if filename.endswith(".xlsx"):
        return pd.read_excel(uploaded_file)    
    raise ValueError("Upload a CSV or XLSX file.")


def _trigger_csv_download(df: pd.DataFrame, filename: str) -> None:
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    csv_b64 = base64.b64encode(csv_bytes).decode("ascii")
    safe_filename = escape(filename or "kite_holdings.csv", quote=True)
    html = f"""
        <!doctype html>
        <a id="kite-holdings-download" href="data:text/csv;base64,{csv_b64}" download="{safe_filename}"></a>
        <script>
            document.getElementById("kite-holdings-download").click();
        </script>
    """
    st.iframe(
        "data:text/html;base64," + base64.b64encode(html.encode("utf-8")).decode("ascii"),
        height=1,
    )


def _cache_ltp_by_symbol(df: pd.DataFrame) -> None:
    if {"tradingsymbol", "last_price"}.issubset(df.columns):
        ltp_by_symbol: dict[str, Any] = {}
        for symbol, ltp in zip(df["tradingsymbol"], df["last_price"]):
            if pd.isna(ltp):
                continue
            symbol_key = _normalized_symbol_value(symbol)
            fallback_key = _ltp_match_symbol(symbol)
            if symbol_key:
                ltp_by_symbol[symbol_key] = ltp
            if fallback_key and fallback_key not in ltp_by_symbol:
                ltp_by_symbol[fallback_key] = ltp
        st.session_state["ltp_by_symbol"] = ltp_by_symbol
    else:
        st.session_state["ltp_by_symbol"] = {}


def _historic_alert_exchange_by_symbol() -> dict[str, str]:
    alerts_data = st.session_state.get("kite_alerts_data", {})
    alerts = alerts_data.get("alerts", []) if isinstance(alerts_data, dict) else []
    exchange_by_symbol: dict[str, str] = {}
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        symbol = _ltp_match_symbol(alert.get("lhs_tradingsymbol"))
        exchange = str(alert.get("lhs_exchange") or "").strip().upper()
        if symbol and exchange:
            exchange_by_symbol[symbol] = exchange
    return exchange_by_symbol


def _historic_quote_instrument(symbol: str, exchange: str) -> str:
    if exchange == "INDICES":
        index_symbols = {
            "NIFTY 50": "NSE:NIFTY 50",
            "NIFTY BANK": "NSE:NIFTY BANK",
            "SENSEX": "BSE:SENSEX",
        }
        return index_symbols.get(symbol, f"NSE:{symbol}")
    return f"{exchange}:{symbol}"


def fetch_live_ltp(
    kite,
    symbols: list[str],
    exchange_by_symbol: dict[str, str] | None = None,
) -> pd.DataFrame:
    normalized_symbols = sorted(
        {_ltp_match_symbol(symbol) for symbol in symbols if _ltp_match_symbol(symbol)}
    )
    if not normalized_symbols:
        return pd.DataFrame(columns=["Symbol", "LTP"])

    normalized_exchanges = {
        _ltp_match_symbol(symbol): str(exchange).strip().upper()
        for symbol, exchange in (exchange_by_symbol or {}).items()
        if _ltp_match_symbol(symbol) and str(exchange).strip()
    }
    instruments = [
        _historic_quote_instrument(symbol, normalized_exchanges.get(symbol, "NSE"))
        for symbol in normalized_symbols
    ]
    data = kite.ltp(*instruments)
    rows: list[dict[str, Any]] = []
    for instrument, quote_data in data.items():
        if not isinstance(quote_data, dict) or quote_data.get("last_price") is None:
            continue
        symbol = _ltp_match_symbol(str(instrument).split(":", 1)[-1])
        rows.append(
            {
                "Symbol": symbol,
                "LTP": float(quote_data["last_price"]),
            }
        )
    return pd.DataFrame(rows, columns=["Symbol", "LTP"])


def fetch_live_ltp_by_symbol(
    kite,
    symbols: list[str],
    exchange_by_symbol: dict[str, str] | None = None,
) -> dict[str, float]:
    ltp_df = fetch_live_ltp(kite, symbols, exchange_by_symbol=exchange_by_symbol)
    if ltp_df.empty:
        return {}
    normalized_ltp_by_symbol = {
        str(row["Symbol"]).strip().upper(): float(row["LTP"])
        for _, row in ltp_df.iterrows()
        if pd.notna(row.get("LTP"))
    }
    ltp_by_symbol = dict(normalized_ltp_by_symbol)
    for symbol in symbols:
        original_symbol = str(symbol).strip().upper()
        normalized_symbol = _ltp_match_symbol(original_symbol)
        if original_symbol and normalized_symbol in normalized_ltp_by_symbol:
            ltp_by_symbol[original_symbol] = normalized_ltp_by_symbol[normalized_symbol]
    return ltp_by_symbol


def _historic_request_signature(tickers: list[str], benchmark: str) -> tuple[tuple[str, ...], str]:
    return tuple(tickers), benchmark.strip().upper()


def _patch_historic_dashboard_ltp(
    dashboard_df: pd.DataFrame,
    live_ltp_by_symbol: dict[str, float],
) -> pd.DataFrame:
    updated_df = dashboard_df.copy()
    for column in updated_df.columns:
        symbol = str(column).strip().upper()
        ltp = live_ltp_by_symbol.get(symbol)
        if ltp is None:
            continue

        values = updated_df[column].tolist()
        range_low = None
        range_high = None
        for value in values:
            if not isinstance(value, str):
                continue
            range_match = re.fullmatch(r"\[([\d,.]+)\s*-\s*([\d,.]+)\]", value.strip())
            if range_match:
                range_low = float(range_match.group(1).replace(",", ""))
                range_high = float(range_match.group(2).replace(",", ""))
                break

        for index, value in enumerate(values):
            if not isinstance(value, str):
                continue
            if value.startswith("Rng:") and range_low is not None and range_high is not None:
                range_pct = (
                    min(max(((ltp - range_low) / (range_high - range_low)) * 100, 0), 100)
                    if range_high != range_low
                    else 0
                )
                values[index] = f"Rng:{range_pct:.1f}% [{ltp:.2f}]"
            elif re.fullmatch(r"LTP:\s*[\d,.]+", value.strip()):
                values[index] = f"LTP: {ltp:.2f}"
            elif value.startswith("Position:"):
                values[index] = re.sub(
                    r"(\bLTP\s+)[\d,]+(?:\.\d+)?",
                    lambda match: f"{match.group(1)}{ltp:,.2f}".rstrip("0").rstrip("."),
                    value,
                    count=1,
                )
        updated_df[column] = values
    return updated_df


def _patch_historic_day_movers_ltp(
    day_movers_df: pd.DataFrame,
    live_ltp_by_symbol: dict[str, float],
) -> pd.DataFrame:
    if day_movers_df.empty or "Ticker" not in day_movers_df.columns:
        return day_movers_df

    updated_df = day_movers_df.copy()
    for index, row in updated_df.iterrows():
        symbol = str(row.get("Ticker") or "").strip().upper()
        ltp = live_ltp_by_symbol.get(symbol)
        previous_close = pd.to_numeric(row.get("Previous Close"), errors="coerce")
        if ltp is None:
            continue
        updated_df.at[index, "LTP"] = ltp
        if pd.notna(previous_close) and float(previous_close) != 0:
            updated_df.at[index, "DayChg"] = round(ltp - float(previous_close), 2)
            updated_df.at[index, "DayChg %"] = round(
                ((ltp - float(previous_close)) / float(previous_close)) * 100,
                2,
            )
    return updated_df


def _apply_live_ltp_to_holdings(holdings_df: pd.DataFrame, ltp_df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    if holdings_df.empty or "tradingsymbol" not in holdings_df.columns or ltp_df.empty:
        return holdings_df, []

    updated_df = holdings_df.copy()
    symbol_key = updated_df["tradingsymbol"].astype(str).str.strip().str.upper()
    ltp_by_symbol = ltp_df.set_index("Symbol")["LTP"]
    live_ltp = pd.to_numeric(symbol_key.map(ltp_by_symbol), errors="coerce")
    matched = live_ltp.notna()
    if matched.any():
        updated_df.loc[matched, "last_price"] = live_ltp[matched]
        if {"average_price", "quantity"}.issubset(updated_df.columns):
            average_price = pd.to_numeric(updated_df["average_price"], errors="coerce")
            quantity = pd.to_numeric(updated_df["quantity"], errors="coerce")
            last_price = pd.to_numeric(updated_df["last_price"], errors="coerce")
            invested = average_price * quantity
            updated_df["pnl"] = (last_price - average_price) * quantity
            updated_df["pnl_pct"] = updated_df["pnl"].where(invested.ne(0)) / invested * 100

    missing_symbols = sorted(set(symbol_key[~matched].dropna()) - {""})
    return updated_df, missing_symbols


def refresh_live_ltp_for_holdings(holdings_df: pd.DataFrame) -> pd.DataFrame:
    if holdings_df.empty or "tradingsymbol" not in holdings_df.columns:
        st.session_state["ltp_by_symbol"] = {}
        return holdings_df

    symbols = holdings_df["tradingsymbol"].dropna().astype(str).tolist()
    try:
        kite, _, _ = bootstrap_kite_app("Zerodha Holdings")
        ltp_df = fetch_live_ltp(kite, symbols)
    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
            st.error("Your session expired. Please login again to refresh live LTP.")
            st.rerun()
        st.session_state["kite_holdings_ltp_refresh_error"] = str(exc)
        return holdings_df

    updated_df, missing_symbols = _apply_live_ltp_to_holdings(holdings_df, ltp_df)
    _cache_ltp_by_symbol(updated_df)
    st.session_state["kite_holdings_df"] = updated_df
    st.session_state["kite_holdings_ltp_refreshed_at"] = pd.Timestamp.now().isoformat()
    if missing_symbols:
        st.session_state["kite_holdings_ltp_missing_symbols"] = missing_symbols
    else:
        st.session_state.pop("kite_holdings_ltp_missing_symbols", None)
    st.session_state.pop("kite_holdings_ltp_refresh_error", None)
    return updated_df


def _build_exited_analytics_rows(
    exited_breakdown_df: pd.DataFrame,
    current_symbols: set[str],
    token_map: dict[str, int],
    live_ltp_by_symbol: dict[str, float],
) -> tuple[list[dict[str, Any]], set[str]]:
    if exited_breakdown_df.empty or "symbol" not in exited_breakdown_df.columns:
        return [], set()

    row_type = exited_breakdown_df.get(
        "row_type", pd.Series(index=exited_breakdown_df.index, dtype=object)
    ).astype(str).str.upper().str.strip()
    status = exited_breakdown_df.get(
        "holding_status", pd.Series(index=exited_breakdown_df.index, dtype=object)
    ).astype(str).str.upper().str.strip()
    sector = exited_breakdown_df.get(
        "sector", pd.Series(index=exited_breakdown_df.index, dtype=object)
    ).astype(str).str.upper().str.strip()
    exited_df = exited_breakdown_df[status.eq("EXITED") & ~sector.eq("OPTIONS")].copy()
    if exited_df.empty:
        return [], set()

    exited_rows: list[dict[str, Any]] = []
    exited_symbols: set[str] = set()
    for symbol, symbol_df in exited_df.groupby(
        exited_df["symbol"].map(_normalized_symbol_value), sort=False
    ):
        if not symbol or symbol in current_symbols or symbol not in token_map:
            continue
        summary_rows = symbol_df[row_type.loc[symbol_df.index].eq("SUMMARY")]
        row = summary_rows.iloc[0] if not summary_rows.empty else symbol_df.iloc[0]
        ltp = pd.to_numeric(live_ltp_by_symbol.get(symbol, row.get("ltp")), errors="coerce")
        average_price = pd.to_numeric(row.get("buy_avg"), errors="coerce")
        quantity = pd.to_numeric(row.get("total_qty"), errors="coerce")
        if pd.isna(quantity) or quantity <= 0:
            exited_quantity = symbol_df.get("exit_qty")
            if exited_quantity is None:
                exited_quantity = symbol_df.get("batch_qty")
            quantity = (
                pd.to_numeric(exited_quantity, errors="coerce").fillna(0).sum()
                if exited_quantity is not None
                else 0
            )
        if pd.isna(average_price):
            average_price = pd.to_numeric(symbol_df.get("batch_price"), errors="coerce").mean()
        exited_rows.append(
            {
                "tradingsymbol": symbol,
                "instrument_token": token_map[symbol],
                "last_price": float(ltp) if pd.notna(ltp) else None,
                "average_price": float(average_price) if pd.notna(average_price) else None,
                "quantity": float(quantity) if pd.notna(quantity) else None,
            }
        )
        exited_symbols.add(symbol)
    return exited_rows, exited_symbols


def fetch_and_display_holdings():
    try:
        kite, _, _ = bootstrap_kite_app("Zerodha Holdings")
        holdings = kite.holdings()
        df = pd.DataFrame(holdings)
        try:
            exited_breakdown_df = load_exited_holdings_breakdown_from_supabase()
        except Exception as breakdown_exc:
            exited_breakdown_df = pd.DataFrame()
            st.session_state["kite_holdings_exited_error"] = str(breakdown_exc)
        else:
            st.session_state.pop("kite_holdings_exited_error", None)

        current_symbols = {
            _normalized_symbol_value(symbol)
            for symbol in df.get("tradingsymbol", pd.Series(dtype=object))
            if _normalized_symbol_value(symbol)
        }
        exited_status = exited_breakdown_df.get(
            "holding_status", pd.Series(index=exited_breakdown_df.index, dtype=object)
        ).astype(str).str.upper().str.strip()
        exited_sector = exited_breakdown_df.get(
            "sector", pd.Series(index=exited_breakdown_df.index, dtype=object)
        ).astype(str).str.upper().str.strip()
        exited_symbols_requested = {
            _normalized_symbol_value(symbol)
            for symbol in exited_breakdown_df.loc[
                exited_status.eq("EXITED") & ~exited_sector.eq("OPTIONS"),
                "symbol",
            ]
            if _normalized_symbol_value(symbol)
        } if not exited_breakdown_df.empty and "symbol" in exited_breakdown_df.columns else set()
        exited_symbols_requested -= current_symbols

        instruments_df = load_instrument_token_from_supabase(sorted(current_symbols | exited_symbols_requested))
        token_map, _ = resolve_tokens_from_tickers(
            sorted(current_symbols | exited_symbols_requested), instruments_df
        )
        exited_ltp_by_symbol = fetch_live_ltp_by_symbol(kite, sorted(exited_symbols_requested))
        analytics_rows = df.to_dict(orient="records")
        exited_rows, exited_symbols = _build_exited_analytics_rows(
            exited_breakdown_df,
            current_symbols,
            token_map,
            exited_ltp_by_symbol,
        )
        analytics_rows.extend(exited_rows)
        analytics_ltp_by_symbol = _holdings_live_ltp_by_symbol(df)
        analytics_ltp_by_symbol.update(exited_ltp_by_symbol)

        if analytics_rows:
            _cache_ltp_by_symbol(df)
            st.session_state.setdefault("ltp_by_symbol", {}).update(exited_ltp_by_symbol)
            as_of_date = datetime.now().date().isoformat()
            dashboard_df, day_movers_df, failed_symbols = build_price_ladder_and_day_movers_frames(
                kite,
                analytics_rows,
                as_of_date,
                symbol_key="tradingsymbol",
                token_key="instrument_token",
                live_ltp_by_symbol=analytics_ltp_by_symbol,
                buy_avg_key="average_price",
                quantity_key="quantity",
            )
            momentum_df, momentum_failed_symbols, momentum_error = _calculate_holdings_momentum_data(
                kite,
                df,
                analytics_rows,
                as_of_date,
                live_ltp_by_symbol=analytics_ltp_by_symbol,
            )
            early_entry_labels = _price_ladder_early_entry_labels_by_symbol(
                kite,
                analytics_rows,
                as_of_date,
                momentum_df,
                symbol_key="tradingsymbol",
                live_ltp_by_symbol=analytics_ltp_by_symbol,
            )
            st.session_state["kite_holdings_df"] = df
            st.session_state["kite_holdings_dashboard_df"] = dashboard_df
            st.session_state["kite_holdings_day_movers_df"] = day_movers_df
            st.session_state["kite_holdings_dashboard_failed_symbols"] = failed_symbols
            st.session_state["kite_holdings_token_rows"] = df.to_dict(orient="records")
            st.session_state["kite_holdings_analytics_token_rows"] = analytics_rows
            st.session_state["kite_holdings_as_of_date"] = as_of_date
            st.session_state.pop("kite_holdings_returns_df", None)
            st.session_state["kite_holdings_momentum_df"] = momentum_df
            st.session_state["kite_holdings_early_entry_labels"] = early_entry_labels
            st.session_state["kite_holdings_momentum_failed_symbols"] = momentum_failed_symbols
            st.session_state["kite_holdings_exited_symbols"] = exited_symbols
            st.session_state["kite_holdings_momentum_benchmark_used"] = DEFAULT_MOMENTUM_BENCHMARK
            if momentum_error:
                st.session_state["kite_holdings_momentum_error"] = momentum_error
            else:
                st.session_state.pop("kite_holdings_momentum_error", None)
            st.session_state["kite_holdings_fetched_at"] = pd.Timestamp.now().isoformat()
            st.session_state["kite_holdings_ltp_refreshed_at"] = st.session_state["kite_holdings_fetched_at"]
            st.session_state.pop("kite_holdings_ltp_refresh_error", None)
            st.session_state.pop("kite_holdings_ltp_missing_symbols", None)
            # st.session_state["kite_holdings_download_filename"] = (
            #     f"holdings_{pd.Timestamp.now().strftime('%Y-%m-%d_%H.%M.%S')}.csv"
            # )
            # _trigger_csv_download(df, st.session_state["kite_holdings_download_filename"])
            try:
                _set_holdings_breakdown_state(load_active_holdings_breakdown_from_supabase())
                st.session_state.pop("kite_holdings_breakdown_error", None)
            except Exception as breakdown_exc:
                st.session_state.pop(HOLDINGS_BREAKDOWN_DF_STATE_KEY, None)
                st.session_state["kite_holdings_breakdown_error"] = str(breakdown_exc)
            #print("session state kite_holdings_download_filename:\n", st.session_state["kite_holdings_download_filename"])
        else:
            st.session_state.pop("kite_holdings_df", None)
            st.session_state.pop("kite_holdings_returns_df", None)
            st.session_state.pop("kite_holdings_momentum_df", None)
            st.session_state.pop("kite_holdings_early_entry_labels", None)
            st.session_state.pop("kite_holdings_momentum_failed_symbols", None)
            st.session_state.pop("kite_holdings_momentum_error", None)
            st.session_state.pop("kite_holdings_momentum_benchmark_used", None)
            st.session_state.pop("kite_holdings_dashboard_df", None)
            st.session_state.pop("kite_holdings_close_prices_df", None)
            st.session_state.pop("kite_holdings_day_movers_df", None)
            st.session_state.pop("kite_holdings_dashboard_failed_symbols", None)
            st.session_state.pop("kite_holdings_token_rows", None)
            st.session_state.pop("kite_holdings_analytics_token_rows", None)
            st.session_state.pop("kite_holdings_as_of_date", None)
            st.session_state.pop("kite_holdings_fetched_at", None)
            st.session_state.pop("kite_holdings_ltp_refreshed_at", None)
            st.session_state.pop("kite_holdings_ltp_refresh_error", None)
            st.session_state.pop("kite_holdings_ltp_missing_symbols", None)
            st.session_state.pop("kite_holdings_ltp_refresh_count", None)
            st.session_state.pop("kite_holdings_exited_symbols", None)
            st.session_state.pop("kite_holdings_exited_error", None)
            st.session_state.pop("kite_holdings_download_filename", None)
            st.session_state.pop(HOLDINGS_BREAKDOWN_DF_STATE_KEY, None)
            st.session_state.pop("kite_holdings_breakdown_error", None)
            st.session_state["ltp_by_symbol"] = {}
            st.warning("No holdings found in this account.")
    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
            st.error("Your session expired. Please login again to view holdings.")
            st.rerun()
        st.error(f"Error fetching holdings. Please try again. Details: {exc}")


def _holdings_live_ltp_by_symbol(holdings_df: pd.DataFrame) -> dict[str, float]:
    if holdings_df.empty or not {"tradingsymbol", "last_price"}.issubset(holdings_df.columns):
        return {}

    ltp_by_symbol: dict[str, float] = {}
    for symbol, ltp in zip(holdings_df["tradingsymbol"], holdings_df["last_price"]):
        symbol_key = str(symbol or "").strip().upper()
        ltp_value = pd.to_numeric(ltp, errors="coerce")
        if symbol_key and pd.notna(ltp_value):
            ltp_by_symbol[symbol_key] = float(ltp_value)
    return ltp_by_symbol


def _calculate_holdings_momentum_data(
    kite,
    holdings_df: pd.DataFrame,
    token_rows: list[dict[str, Any]],
    as_of_date: str,
    live_ltp_by_symbol: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, list[str], str | None]:
    benchmark_symbol = DEFAULT_MOMENTUM_BENCHMARK
    try:
        benchmark_df = load_instrument_token_from_supabase([benchmark_symbol])
        benchmark_token_map, missing_benchmark = resolve_tokens_from_tickers([benchmark_symbol], benchmark_df)
        if missing_benchmark or benchmark_symbol not in benchmark_token_map:
            return pd.DataFrame(), [], f"Momentum benchmark token not found: {benchmark_symbol}"

        momentum_df, momentum_failed_symbols = calculate_momentum_scores_from_kite(
            kite,
            token_rows,
            benchmark_token_map[benchmark_symbol],
            as_of_date,
            symbol_key="tradingsymbol",
            token_key="instrument_token",
            live_ltp_by_symbol=live_ltp_by_symbol or _holdings_live_ltp_by_symbol(holdings_df),
        )
        return momentum_df, momentum_failed_symbols, None
    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
            st.error("Your session expired. Please login again to calculate momentum.")
            st.rerun()
        return pd.DataFrame(), [], str(exc)


def _price_ladder_early_entry_labels_by_symbol(
    kite: Any,
    token_rows: list[dict[str, Any]],
    as_of_date: str,
    momentum_df: pd.DataFrame,
    *,
    symbol_key: str = "Ticker",
    token_key: str = "instrument_token",
    live_ltp_by_symbol: dict[str, float] | None = None,
) -> dict[str, list[str]]:
    """Format existing early-entry features as non-scoring price-ladder labels."""
    stock_data: dict[str, pd.DataFrame] = {}
    for row in token_rows:
        symbol = str(row.get(symbol_key) or "").strip().upper()
        token = row.get(token_key)
        if not symbol or pd.isna(token):
            continue
        try:
            stock_data[symbol] = load_analytics_history(kite, token, as_of_date)
        except Exception as exc:
            # A stale instrument token should not prevent labels for the rest of
            # the selected basket. Authentication failures still surface above.
            if "invalid token" not in str(exc).strip().lower():
                raise

    early_entry_df = calculate_early_entry_frame(
        stock_data,
        momentum_df,
        live_ltp_by_symbol=live_ltp_by_symbol,
    )
    labels_by_symbol: dict[str, list[str]] = {}
    for _, row in early_entry_df.iterrows():
        labels = [
            label
            for field, label in PRICE_LADDER_EARLY_ENTRY_LABELS
            if bool(row.get(field, False))
        ]
        if bool(row.get("recent_pullback", False)):
            days = pd.to_numeric(row.get("days_since_pullback"), errors="coerce")
            if pd.notna(days):
                labels[0] = f"Recent pullback ({int(days)}d)"
        if labels:
            labels_by_symbol[str(row.get("ticker") or "").strip().upper()] = labels
    return labels_by_symbol


def _render_price_ladder_summary_card(
    dashboard_df: pd.DataFrame,
    *,
    highlight_symbols: dict[str, str] | None = None,
    momentum_labels: dict[str, str] | None = None,
    early_entry_labels: dict[str, list[str]] | None = None,
    notes_by_symbol: dict[str, list[str]] | None = None,
    show_positions: bool = False,
    mtf_symbols: set[str] | None = None,
    exited_symbols: set[str] | None = None,
) -> None:
    summary_html = format_price_ladder_summary_html(
        dashboard_df,
        highlight_symbols=highlight_symbols,
        momentum_labels=momentum_labels,
        early_entry_labels=early_entry_labels,
        notes_by_symbol=notes_by_symbol,
        show_positions=show_positions,
        mtf_symbols=mtf_symbols,
        exited_symbols=exited_symbols,
    )
    if not summary_html:
        st.info("No price ladder summary available.")
        return
    st.markdown(
        _summary_panel_html("Price Ladder Summary", summary_html, BUTTON_COLOR),
        unsafe_allow_html=True,
    )


def _stock_notes_by_symbol(momentum_df: pd.DataFrame) -> dict[str, list[str]]:
    if momentum_df.empty or "ticker" not in momentum_df.columns:
        return {}

    notes_df = _merge_stock_notes(momentum_df)
    notes_by_symbol: dict[str, list[str]] = {}
    for _, row in notes_df.iterrows():
        symbol = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
        notes = [
            " ".join(str(row.get(column)).split())
            for column in ["why", "risk", "moat"]
            if pd.notna(row.get(column)) and str(row.get(column)).strip()
        ]
        review_date = row.get("last_reviewed_date")
        if notes and pd.notna(review_date) and str(review_date).strip():
            notes.append(f"Reviewed: {' '.join(str(review_date).split())}")
        if symbol and notes:
            notes_by_symbol[symbol] = notes
    return notes_by_symbol


def _render_holdings_momentum_summary(
    momentum_df: pd.DataFrame,
    day_movers_df: pd.DataFrame,
    mtf_symbols: set[str] | None = None,
    exited_symbols: set[str] | None = None,
) -> None:
    if momentum_df.empty:
        st.info("No momentum summary available.")
        return

    momentum_summary_highlight_symbols = _summary_ticker_accents(build_day_movers_summary(day_movers_df))
    momentum_display_df = _prepared_momentum_display_df(momentum_df)
    label_groups = _group_momentum_symbols_by_label(momentum_display_df)
    st.markdown(
        _summary_panel_html(
            "Momentum Summary",
            _format_momentum_label_summary(
                label_groups,
                highlight_symbols=momentum_summary_highlight_symbols,
                mtf_symbols=mtf_symbols,
                exited_symbols=exited_symbols,
            ),
            BUTTON_COLOR,
        ),
        unsafe_allow_html=True,
    )


def _render_holdings_analytics_tab(kite_holdings_df: pd.DataFrame | None) -> None:
    if kite_holdings_df is None:
        st.info("Fetch holdings to display analytics.")
        return

    sorted_dashboard_df = _sort_historic_dashboard_by_rng(
        st.session_state.get("kite_holdings_dashboard_df", pd.DataFrame())
    )
    day_movers_df = st.session_state.get("kite_holdings_day_movers_df", pd.DataFrame())
    momentum_df = st.session_state.get("kite_holdings_momentum_df", pd.DataFrame())
    early_entry_labels = st.session_state.get("kite_holdings_early_entry_labels", {})
    exited_symbols = st.session_state.get("kite_holdings_exited_symbols", set())
    price_ladder_highlight_symbols = _summary_ticker_accents(
        build_portfolio_day_movers_summary(kite_holdings_df)
    )
    mtf_symbol_set = portfolio_streamlit.mtf_symbols(kite_holdings_df)

    display_portfolio_day_movers_summary(kite_holdings_df)

    exited_error = st.session_state.get("kite_holdings_exited_error")
    if exited_error:
        st.warning(f"Could not load exited holdings for analytics: {exited_error}")

    momentum_error = st.session_state.get("kite_holdings_momentum_error")
    if momentum_error:
        st.warning(f"Momentum dashboard could not be calculated: {momentum_error}")
    momentum_failed_symbols = st.session_state.get("kite_holdings_momentum_failed_symbols", [])
    if momentum_failed_symbols:
        st.warning(
            "No momentum data returned for: "
            + ", ".join(momentum_failed_symbols[:10])
            + ("..." if len(momentum_failed_symbols) > 10 else "")
        )

    _render_holdings_momentum_summary(momentum_df, day_movers_df, mtf_symbol_set, exited_symbols)
    _render_price_ladder_summary_card(
        sorted_dashboard_df,
        highlight_symbols=price_ladder_highlight_symbols,
        momentum_labels=_momentum_label_by_symbol(momentum_df),
        early_entry_labels=early_entry_labels,
        notes_by_symbol=_stock_notes_by_symbol(momentum_df),
        show_positions=True,
        mtf_symbols=mtf_symbol_set,
        exited_symbols=exited_symbols,
    )

    with st.expander("Momentum Ranking", expanded=False):
        render_momentum_ranking_table(
            momentum_df,
            day_movers_df,
            key="kite_holdings_momentum_ranking_table",
            show_summary=False,
            mtf_symbols=mtf_symbol_set,
            exited_symbols=exited_symbols,
        )

    with st.expander("Price Ladder", expanded=False):
        display_historic_price_ladder_frame(
            sorted_dashboard_df,
            max_rows=12,
            highlight_symbols=price_ladder_highlight_symbols,
            show_summary=False,
            mtf_symbols=mtf_symbol_set,
            exited_symbols=exited_symbols,
        )


def _holding_symbol_options(
    holdings_df: pd.DataFrame,
    holdings_breakdown_df: pd.DataFrame | None = None,
) -> list[str]:
    kite_symbols = (
        {
            str(symbol).upper().strip()
            for symbol in holdings_df["tradingsymbol"].dropna()
            if str(symbol).strip()
        }
        if not holdings_df.empty and "tradingsymbol" in holdings_df.columns
        else set()
    )
    kite_isins = (
        {
            str(isin).upper().strip()
            for isin in holdings_df["isin"].dropna()
            if str(isin).strip()
        }
        if not holdings_df.empty and "isin" in holdings_df.columns
        else set()
    )

    if holdings_breakdown_df is not None and not holdings_breakdown_df.empty:
        row_type = holdings_breakdown_df.get("row_type", pd.Series(index=holdings_breakdown_df.index, dtype=object)).astype(str).str.upper().str.strip()
        status = holdings_breakdown_df.get("holding_status", pd.Series(index=holdings_breakdown_df.index, dtype=object)).astype(str).str.upper().str.strip()
        summary_rows = holdings_breakdown_df[row_type.eq("SUMMARY")]
        exited_summaries = holdings_breakdown_df[row_type.eq("SUMMARY") & status.eq("EXITED")]
        active_summaries = summary_rows[~summary_rows.index.isin(exited_summaries.index)]

        def _identity_values(frame: pd.DataFrame, column: str) -> set[str]:
            if column not in frame.columns:
                return set()
            return {str(value).upper().strip() for value in frame[column].dropna() if str(value).strip()}

        exited_symbols = _identity_values(exited_summaries, "symbol")
        exited_isins = _identity_values(exited_summaries, "isin")
        active_symbols = _identity_values(active_summaries, "symbol")
        active_isins = _identity_values(active_summaries, "isin")
        suppressed_symbols = {
            str(row.get("tradingsymbol") or "").upper().strip()
            for _, row in holdings_df.iterrows()
            if (
                str(row.get("tradingsymbol") or "").upper().strip() in exited_symbols
                or str(row.get("isin") or "").upper().strip() in exited_isins
            )
            and str(row.get("tradingsymbol") or "").upper().strip() not in active_symbols
            and str(row.get("isin") or "").upper().strip() not in active_isins
        }
        kite_symbols -= suppressed_symbols

    breakdown_symbols: set[str] = set()
    if holdings_breakdown_df is not None and not holdings_breakdown_df.empty:
        active_breakdown_df = _active_breakdown_df(holdings_breakdown_df)
        if "symbol" in active_breakdown_df.columns:
            active_breakdown_df = active_breakdown_df.assign(
                _symbol_key=active_breakdown_df["symbol"].astype(str).str.upper().str.strip()
            )
            for symbol, symbol_rows in active_breakdown_df.groupby("_symbol_key"):
                symbol_isins = (
                    {
                        str(isin).upper().strip()
                        for isin in symbol_rows["isin"].dropna()
                        if str(isin).strip()
                    }
                    if "isin" in symbol_rows.columns
                    else set()
                )
                if symbol and symbol not in kite_symbols and not (symbol_isins & kite_isins):
                    breakdown_symbols.add(symbol)

    return sorted(kite_symbols | breakdown_symbols)


def _filter_breakdown_for_holding(
    holdings_breakdown_df: pd.DataFrame,
    holdings_df: pd.DataFrame,
    symbol: str,
) -> pd.DataFrame:
    symbol_key = _normalized_symbol_value(symbol)
    if not symbol_key or holdings_breakdown_df.empty or "symbol" not in holdings_breakdown_df.columns:
        return pd.DataFrame()

    selected_rows = holdings_breakdown_df["symbol"].astype(str).str.upper().str.strip().eq(symbol_key)

    if "isin" in holdings_breakdown_df.columns and "isin" in holdings_df.columns:
        holding_rows = holdings_df[
            holdings_df["tradingsymbol"].astype(str).str.upper().str.strip().eq(symbol_key)
        ]
        selected_isins = {
            str(isin).upper().strip()
            for isin in holding_rows["isin"].dropna()
            if str(isin).strip()
        }
        if selected_isins:
            selected_rows = selected_rows | holdings_breakdown_df["isin"].astype(str).str.upper().str.strip().isin(selected_isins)

    return holdings_breakdown_df[selected_rows].copy()


def _today_orders_display_df(
    orders: list[dict[str, Any]],
    holdings_breakdown_df: pd.DataFrame,
) -> pd.DataFrame:
    database_symbols = (
        {
            str(symbol).upper().strip()
            for symbol in holdings_breakdown_df.get("symbol", pd.Series(dtype=object)).dropna()
            if str(symbol).strip()
        }
        if not holdings_breakdown_df.empty
        else set()
    )
    display_rows = []
    for order in orders:
        symbol = str(order.get("tradingsymbol") or "").upper().strip()
        display_rows.append(
            {
                "Order ID": order.get("order_id"),
                "Timestamp": order.get("order_timestamp"),
                "Symbol": symbol,
                "Exchange": order.get("exchange"),
                "Side": order.get("transaction_type"),
                "Status": order.get("status"),
                "Order Type": order.get("order_type"),
                "Product": order.get("product"),
                "Quantity": order.get("quantity"),
                "Filled Qty": order.get("filled_quantity"),
                "Price": order.get("price"),
                "Avg Price": order.get("average_price"),
                "Trigger Price": order.get("trigger_price"),
                "Validity": order.get("validity"),
                "Database Match": symbol in database_symbols if symbol else False,
            }
        )
    return pd.DataFrame(display_rows)


def _render_today_orders_for_breakdown() -> None:
    today = datetime.now().date().isoformat()
    if st.session_state.get(TODAY_ORDERS_DATE_STATE_KEY) != today:
        try:
            orders_kite, _, _ = bootstrap_kite_app("Zerodha Holdings")
            complete_orders = [
                order
                for order in orders_kite.orders()
                if str(order.get("status") or "").upper().strip() == "COMPLETE"
            ]
            st.session_state[TODAY_ORDERS_STATE_KEY] = complete_orders
            st.session_state[TODAY_ORDERS_DATE_STATE_KEY] = today
            st.session_state.pop(TODAY_ORDERS_ERROR_STATE_KEY, None)
        except Exception as exc:
            st.session_state[TODAY_ORDERS_STATE_KEY] = []
            st.session_state[TODAY_ORDERS_ERROR_STATE_KEY] = str(exc)
        else:
            try:
                affected_symbols = update_holdings_breakdown_from_orders(complete_orders)
                if affected_symbols:
                    _load_holdings_breakdown_state()
                st.session_state.pop(TODAY_ORDERS_SYNC_ERROR_STATE_KEY, None)
            except Exception as exc:
                st.session_state[TODAY_ORDERS_SYNC_ERROR_STATE_KEY] = str(exc)

    orders_error = st.session_state.get(TODAY_ORDERS_ERROR_STATE_KEY)
    if orders_error:
        st.warning(f"Could not load today's Kite orders: {orders_error}")
        return

    sync_error = st.session_state.get(TODAY_ORDERS_SYNC_ERROR_STATE_KEY)
    if sync_error:
        st.warning(f"Could not update holdings breakdown from today's orders: {sync_error}")

    breakdown_df = _holdings_breakdown_state_df()
    orders_df = _today_orders_display_df(
        st.session_state.get(TODAY_ORDERS_STATE_KEY, []),
        breakdown_df,
    )
    st.subheader("Today's Kite Orders")
    if orders_df.empty:
        st.info("No orders placed today.")
    else:
        st.dataframe(orders_df, width="stretch", hide_index=True)


if "access_token" not in st.session_state:
    bootstrap_kite_app("Zerodha Holdings")


MAIN_NAV_OPTIONS = {
    "Historic Data": "Historic Data",
    "Holdings": "Holdings",
    "Calculators": "Calculators",
    "Alerts": "Alerts",
    "Upload": "Upload",
}
if st.session_state.get("main_navigation") in {
    "Upload Holdings",
    "Upload Holdings Breakdown",
}:
    st.session_state["main_navigation"] = "Upload"
if st.session_state.get("main_navigation") not in MAIN_NAV_OPTIONS:
    st.session_state["main_navigation"] = "Historic Data"

st.sidebar.markdown("**Navigation**")
for main_nav_label in MAIN_NAV_OPTIONS:
    active_prefix = "> " if st.session_state["main_navigation"] == main_nav_label else ""
    if st.sidebar.button(
        f"{active_prefix}{main_nav_label}",
        key=f"main_nav_{main_nav_label}",
        width="stretch",
    ):
        st.session_state["main_navigation"] = main_nav_label
        st.rerun()

selected_main_label = st.session_state["main_navigation"]
selected_main_tab = MAIN_NAV_OPTIONS[selected_main_label]

if selected_main_tab == "Upload":
    tab_upload_holdings, tab_upload_breakdown = st.tabs(
        ["Holdings", "Breakdown / Add Entries"]
    )

    with tab_upload_holdings:
        uploaded_kite_holdings_file = st.file_uploader(
            "Upload holdings CSV or XLSX",
            type=["csv", "xlsx"],
            key="kite_holdings_upload",
        )

        if uploaded_kite_holdings_file is not None:
            try:
                with st.spinner("Processing uploaded holdings..."):
                    kite_holdings_df = _read_uploaded_file(uploaded_kite_holdings_file)
                    _cache_ltp_by_symbol(kite_holdings_df)
                    as_of = pd.Timestamp.now().isoformat()
                    snapshot = portfolio_streamlit.build_portfolio_terminal_snapshot(
                        kite_holdings_df,
                        _holdings_breakdown_state_df(),
                        as_of=as_of,
                    )
                render_portfolio_terminal(snapshot, key="uploaded_portfolio_terminal_component")
                if st.checkbox("Show holdings breakdown", key="show_upload_kite_holdings_breakdown"):
                    if _holdings_breakdown_state_df().empty:
                        with st.spinner("Loading holdings breakdown..."):
                            _load_holdings_breakdown_state()
                    display_holdings_breakdown_df(_holdings_breakdown_state_df())
            except ImportError as exc:
                st.error(f"Failed to read XLSX file. Install the missing dependency: {exc}")
            except Exception as exc:
                st.error(f"Failed to upload Kite holdings: {exc}")

    with tab_upload_breakdown:
        affected_symbols_to_refresh: list[str] = []

        uploaded_brkholdings_file = st.file_uploader(
            "Upload holdings breakdown CSV or XLSX",
            type=["csv", "xlsx"],
            key="holdings_breakdown_upload",
        )

        if uploaded_brkholdings_file is not None:
            try:
                with st.spinner("Processing uploaded breakdown..."):
                    brkdown_df = _read_uploaded_file(uploaded_brkholdings_file)
                    upload_columns = _mapped_holdings_upload_columns(brkdown_df)
                    #print("holdings breakdown upload columns:\n", upload_columns)
                    #print("holdings breakdown before cleaning:\n", brkdown_df.head())
                    holdings_breakdown_df = clean_holdings_breakdown_for_supabase(brkdown_df)

                    #print("holdings breakdown after cleaning:\n", holdings_breakdown_df.head())

                    upsert_holdings_breakdown_in_supabase(holdings_breakdown_df, upload_columns)
                if "symbol" in holdings_breakdown_df.columns:
                    affected_symbols_to_refresh.extend(
                        holdings_breakdown_df["symbol"].dropna().astype(str).str.upper().str.strip().unique().tolist()
                    )

            except ImportError as exc:
                st.error(f"Failed to read XLSX file. Install the missing dependency: {exc}")
            except Exception as exc:
                st.error(f"Failed to upload holdings breakdown: {exc}")

        if affected_symbols_to_refresh:
            try:
                with st.spinner("Refreshing holdings breakdown..."):
                    _refresh_holdings_breakdown_state_for_symbols(affected_symbols_to_refresh)
                st.session_state[HOLDINGS_BREAKDOWN_VIEW_STATE_KEY] = True
            except Exception as exc:
                st.warning(f"Could not refresh holdings breakdown from Supabase: {exc}")

if selected_main_tab == "Holdings":
    fetch_holdings_col, holdings_ltp_col = st.columns([1, 3], vertical_alignment="center")
    with fetch_holdings_col:
        if st.button("Fetch Holdings", type="primary"):
            with st.spinner("Fetching holdings and analytics..."):
                fetch_and_display_holdings()#get holdings from kite,
    with holdings_ltp_col:
        _live_ltp_refreshed_caption("kite_holdings_ltp_refreshed_at")
    #session state - kite_holdings_df, kite_holdings_download_filename, ltp_by_symbol

    kite_holdings_df = st.session_state.get("kite_holdings_df")
    tab_analytics, tab_portfolio_react, tab_returns, tab_holdings_breakdown = st.tabs(
        ["Analytics", "Portfolio", "Returns", "Holdings Breakdown"]
    )

    with tab_portfolio_react:
        if kite_holdings_df is None:
            st.info("Fetch holdings to display the React portfolio UI.")
        else:
            as_of = st.session_state.get("kite_holdings_fetched_at") or pd.Timestamp.now().isoformat()
            snapshot = portfolio_streamlit.build_portfolio_terminal_snapshot(
                kite_holdings_df,
                _holdings_breakdown_state_df(),
                as_of=as_of,
                dashboard_df=st.session_state.get("kite_holdings_dashboard_df", pd.DataFrame()),
            )
            render_portfolio_terminal(snapshot, key="portfolio_terminal_component")
            ltp_refresh_error = st.session_state.get("kite_holdings_ltp_refresh_error")
            if ltp_refresh_error:
                st.warning(f"Could not refresh live LTP: {ltp_refresh_error}")
            ltp_missing_symbols = st.session_state.get("kite_holdings_ltp_missing_symbols", [])
            if ltp_missing_symbols:
                st.warning(
                    "No live LTP returned for: "
                    + ", ".join(ltp_missing_symbols[:10])
                    + ("..." if len(ltp_missing_symbols) > 10 else "")
                )
            failed_symbols = st.session_state.get("kite_holdings_dashboard_failed_symbols", [])
            if failed_symbols:
                st.warning(
                    "Could not load dashboard data for: "
                    + ", ".join(failed_symbols[:10])
                    + ("..." if len(failed_symbols) > 10 else "")
                )

    with tab_analytics:
        _render_holdings_analytics_tab(kite_holdings_df)

    with tab_returns:
        if st.button("Display Historical Returns", key="display_kite_holdings_returns"):
            token_rows = st.session_state.get("kite_holdings_token_rows", [])
            if not token_rows:
                st.warning("Fetch holdings before loading returns.")
            else:
                try:
                    with st.spinner("Loading holdings returns..."):
                        returns_kite, _, _ = bootstrap_kite_app("Zerodha Holdings")
                        returns_df, _, returns_failed_symbols, _ = build_historic_dashboard_frames(
                            returns_kite,
                            token_rows,
                            st.session_state.get("kite_holdings_as_of_date") or datetime.now().date().isoformat(),
                            symbol_key="tradingsymbol",
                            token_key="instrument_token",
                            ltp_key="last_price",
                            include_close_prices=False,
                            include_ladders=False,
                        )
                    st.session_state["kite_holdings_returns_df"] = returns_df
                    if returns_failed_symbols:
                        st.warning(
                            "No returns data returned for: "
                            + ", ".join(returns_failed_symbols[:10])
                            + ("..." if len(returns_failed_symbols) > 10 else "")
                        )
                except Exception as exc:
                    if is_token_error(exc):
                        clear_auth_state()
                        st.error("Your session expired. Please login again to load returns.")
                        st.rerun()
                    st.error(f"Error loading returns: {exc}")
        if "kite_holdings_returns_df" in st.session_state:
            display_historic_returns_frame(
                st.session_state.get("kite_holdings_returns_df", pd.DataFrame()),
                max_rows=18,
            )

    with tab_holdings_breakdown:
        if HOLDINGS_BREAKDOWN_DF_STATE_KEY not in st.session_state:
            try:
                _load_holdings_breakdown_state()
                st.session_state.pop("kite_holdings_breakdown_error", None)
            except Exception as breakdown_exc:
                st.session_state["kite_holdings_breakdown_error"] = str(breakdown_exc)

        _render_today_orders_for_breakdown()
        breakdown_error = st.session_state.get("kite_holdings_breakdown_error")
        if breakdown_error:
            st.warning(f"Could not load holdings breakdown from Supabase: {breakdown_error}")
        if kite_holdings_df is None:
            st.info("Fetch holdings to display holdings breakdown.")
        else:
            holdings_breakdown_state_df = _holdings_breakdown_state_df()
            holding_symbols = _holding_symbol_options(kite_holdings_df, holdings_breakdown_state_df)
            kite_holding_symbols = set(_holding_symbol_options(kite_holdings_df))
            if not holding_symbols:
                st.info("No holding symbols found.")
            else:
                selected_breakdown_symbol = st.selectbox(
                    "Select a holding to edit/exit breakdown",
                    holding_symbols,
                    index=None,
                    placeholder="Select a holding",
                    key="holdings_breakdown_selected_symbol",
                    format_func=lambda symbol: (
                        symbol if symbol in kite_holding_symbols else f"{symbol} — not in Kite"
                    ),
                )
                if not selected_breakdown_symbol:
                    pass
                elif holdings_breakdown_state_df.empty:
                    st.info("No holdings breakdown found in Supabase.")
                else:
                    selected_breakdown_df = _filter_breakdown_for_holding(
                        holdings_breakdown_state_df,
                        kite_holdings_df,
                        selected_breakdown_symbol,
                    )
                    if selected_breakdown_df.empty:
                        st.info(f"No breakdown found for {selected_breakdown_symbol}.")
                    else:
                        display_holdings_breakdown_df(selected_breakdown_df, show_exited_summary=False)

                st.markdown(
                    '<div style="border-top: 2px solid #f59e0b; margin: 1rem 0;"></div>',
                    unsafe_allow_html=True,
                )
                add_message = st.session_state.pop(HOLDINGS_BREAKDOWN_ADD_MESSAGE_KEY, None)
                if add_message:
                    st.success(add_message)
                with st.expander("Add Breakdown Entries", expanded=False):
                    try:
                        added_symbols = _render_add_holdings_breakdown_entries_form(
                            st.session_state.get("ltp_by_symbol", {})
                        )
                    except Exception as exc:
                        added_symbols = []
                        st.error(f"Failed to add holdings breakdown entries: {exc}")

                    if added_symbols:
                        try:
                            with st.spinner("Refreshing holdings breakdown..."):
                                _refresh_holdings_breakdown_state_for_symbols(added_symbols)
                            st.session_state[HOLDINGS_BREAKDOWN_VIEW_STATE_KEY] = True
                            st.session_state[HOLDINGS_BREAKDOWN_ADD_MESSAGE_KEY] = (
                                "Added/updated breakdown entries for: " + ", ".join(added_symbols)
                            )
                            st.rerun()
                        except Exception as exc:
                            st.warning(f"Could not refresh holdings breakdown from Supabase: {exc}")

                st.markdown(
                    '<div style="border-top: 2px solid #f59e0b; margin: 1rem 0;"></div>',
                    unsafe_allow_html=True,
                )
                exited_action_col, exited_header_col = st.columns([0.25, 8], vertical_alignment="center")
                with exited_action_col:
                    show_exited_summary = st.button(
                        "",
                        key="show_exited_holdings_summary",
                        icon=":material/visibility:",
                        help="Show exited holdings summary",
                        width="content",
                    )
                with exited_header_col:
                    st.markdown(
                        '<div style="font-size: 1rem; font-weight: 600; line-height: 1.5;">Exited Holdings Summary</div>',
                        unsafe_allow_html=True,
                    )
                if show_exited_summary:
                    try:
                        exited_breakdown_df = load_exited_holdings_breakdown_from_supabase()
                        if exited_breakdown_df.empty:
                            st.info("No exited holdings found.")
                        else:
                            exited_breakdown_df, _ = enrich_holdings_breakdown_with_ltp(
                                exited_breakdown_df,
                                st.session_state.get("ltp_by_symbol", {}),
                            )
                            display_exited_holdings_summary(exited_breakdown_df, show_header=False)
                    except Exception as exc:
                        st.warning(f"Could not load exited holdings summary: {exc}")
    #display_supabase_holdings_breakdown()  

    if kite_holdings_df is not None:
        ltp_refresh_count = st_autorefresh(
            interval=LTP_REFRESH_INTERVAL_MS,
            key="ltp_refresh",
        )
        previous_ltp_refresh_count = st.session_state.get("kite_holdings_ltp_refresh_count")
        if previous_ltp_refresh_count is None:
            st.session_state["kite_holdings_ltp_refresh_count"] = ltp_refresh_count
        elif ltp_refresh_count != previous_ltp_refresh_count:
            kite_holdings_df = refresh_live_ltp_for_holdings(kite_holdings_df)
            st.session_state["kite_holdings_ltp_refresh_count"] = ltp_refresh_count


if selected_main_tab == "Calculators":
    render_calculators_terminal(
        key="calculators_terminal_component",
        mtf_holdings=portfolio_streamlit.build_mtf_holdings_snapshot(
            st.session_state.get("kite_holdings_df"),
            _holdings_breakdown_state_df(),
        ),
    )


if selected_main_tab == "Alerts":
    render_alerts_tab()


if selected_main_tab == "Historic Data":
    if "historic_saved_tickers_input" not in st.session_state:
        st.session_state["historic_saved_tickers_input"] = st.session_state.get("historic_tickers_input", "")
    if "historic_tickers_input" not in st.session_state:
        st.session_state["historic_tickers_input"] = st.session_state["historic_saved_tickers_input"]

    try:
        indices = load_indices_from_supabase()
    except Exception as exc:
        indices = {}
        st.warning(f"Could not load index constituents: {exc}")

    if indices:
        index_names = ["Custom"] + list(indices.keys())
        default_index = next((name for name in index_names if name.lower() == "main indices"), "Custom")
        if "historic_saved_selected_index" not in st.session_state:
            selected_index_value = st.session_state.get("historic_selected_index")
            st.session_state["historic_saved_selected_index"] = (
                selected_index_value if selected_index_value in index_names else default_index
            )
        if st.session_state.get("historic_saved_selected_index") not in index_names:
            st.session_state["historic_saved_selected_index"] = default_index
        if st.session_state.get("historic_selected_index") not in index_names:
            st.session_state["historic_selected_index"] = st.session_state["historic_saved_selected_index"]
        index_column, benchmark_column = st.columns([2, 1])
        with index_column:
            selected_index = st.selectbox("Select index", index_names, key="historic_selected_index")
            st.session_state["historic_saved_selected_index"] = selected_index
        with benchmark_column:
            benchmark_symbol = st.text_input(
                "Momentum benchmark",
                value=DEFAULT_MOMENTUM_BENCHMARK,
                key="historic_momentum_benchmark",
                help="Used for relative strength in the Quant Momentum score.",
            )
        previous_selected_index = st.session_state.get("historic_previous_selected_index")
        if selected_index == "Custom":
            if previous_selected_index != "Custom":
                st.session_state["historic_tickers_input"] = ""
                st.session_state["historic_saved_tickers_input"] = ""
                st.session_state["historic_previous_selected_index"] = selected_index
                st.rerun()
        else:
            constituent_tickers = [
                ticker.strip()
                for ticker in indices[selected_index].split(",")
                if ticker.strip()
            ]
            prepend_index_names = {
                ticker.strip().upper()
                for group_name, group_constituents in indices.items()
                if group_name.strip().lower() in {"main indices", "indices2"}
                for ticker in group_constituents.split(",")
                if ticker.strip()
            }
            selected_tickers = constituent_tickers
            if selected_index.strip().upper() in prepend_index_names:
                selected_tickers = [selected_index, *selected_tickers]
            selected_constituents = ", ".join(
                dict.fromkeys(selected_tickers)
            )
            if st.session_state["historic_tickers_input"] != selected_constituents:
                st.session_state["historic_tickers_input"] = selected_constituents
                st.session_state["historic_saved_tickers_input"] = selected_constituents
                st.session_state["historic_previous_selected_index"] = selected_index
                st.rerun()
        st.session_state["historic_previous_selected_index"] = selected_index
    else:
        benchmark_symbol = st.text_input(
            "Momentum benchmark",
            value=DEFAULT_MOMENTUM_BENCHMARK,
            key="historic_momentum_benchmark",
            help="Used for relative strength in the Quant Momentum score.",
        )

    tickers_input = st.text_area(
        label="e.g. RELIANCE, INFY, TCS",
        key="historic_tickers_input",
        help="Enter one or more stock ticker symbols separated by commas.",
    )
    st.session_state["historic_saved_tickers_input"] = tickers_input

    fetch_dashboard_col, historic_ltp_col = st.columns([1, 3], vertical_alignment="center")
    with fetch_dashboard_col:
        fetch_dashboard_clicked = st.button(
            "Fetch dashboard",
            type="primary",
            key="historic_fetch_dashboard",
            help="Fetch cached 2Y daily Kite data and show a sorted price ladder per ticker.",
        )
    with historic_ltp_col:
        _live_ltp_refreshed_caption("historic_ltp_refreshed_at")

    if fetch_dashboard_clicked:
        raw_tickers = [item.strip().upper() for item in tickers_input.split(",") if item.strip()]
        benchmark_symbol = benchmark_symbol.strip().upper()

        if not raw_tickers:
            st.warning("Enter at least one ticker symbol.")
        elif not benchmark_symbol:
            st.warning("Enter a benchmark symbol.")
        else:
            request_signature = _historic_request_signature(raw_tickers, benchmark_symbol)
            has_matching_dashboard = (
                not st.session_state.get("historic_dashboard_df", pd.DataFrame()).empty
                and st.session_state.get("historic_loaded_request_signature") == request_signature
            )
            if has_matching_dashboard:
                st.session_state["historic_pending_ltp_tickers"] = raw_tickers
            else:
                st.session_state["historic_pending_tickers"] = raw_tickers
                st.session_state["historic_pending_benchmark"] = benchmark_symbol

    pending_historic_ltp_tickers = st.session_state.get("historic_pending_ltp_tickers")
    if pending_historic_ltp_tickers:
        try:
            historic_kite, _, _ = bootstrap_kite_app("Zerodha Historical Data")
            alert_exchange_by_symbol = _historic_alert_exchange_by_symbol()
            with st.spinner("Refreshing live LTP..."):
                live_ltp_by_symbol = fetch_live_ltp_by_symbol(
                    historic_kite,
                    pending_historic_ltp_tickers,
                    exchange_by_symbol=alert_exchange_by_symbol,
                )
                st.session_state["historic_dashboard_df"] = _patch_historic_dashboard_ltp(
                    st.session_state.get("historic_dashboard_df", pd.DataFrame()),
                    live_ltp_by_symbol,
                )
                st.session_state["historic_day_movers_df"] = _patch_historic_day_movers_ltp(
                    st.session_state.get("historic_day_movers_df", pd.DataFrame()),
                    live_ltp_by_symbol,
                )
                st.session_state["historic_live_ltp_by_symbol"] = live_ltp_by_symbol
                st.session_state["historic_ltp_refreshed_at"] = pd.Timestamp.now().isoformat()
                missing_ltp_symbols = sorted(
                    set(pending_historic_ltp_tickers) - set(live_ltp_by_symbol)
                )
                if missing_ltp_symbols:
                    st.session_state["historic_ltp_missing_symbols"] = missing_ltp_symbols
                else:
                    st.session_state.pop("historic_ltp_missing_symbols", None)
        except Exception as exc:
            if is_token_error(exc):
                clear_auth_state()
                st.error("Your session expired. Please login again to refresh live LTP.")
                st.rerun()
            st.error(f"Error refreshing live LTP: {exc}")
        finally:
            st.session_state.pop("historic_pending_ltp_tickers", None)

    pending_historic_tickers = st.session_state.get("historic_pending_tickers")
    if pending_historic_tickers:
        try:
            as_of_date = datetime.now().date().isoformat()
            historic_kite, _, _ = bootstrap_kite_app("Zerodha Historical Data")
            pending_benchmark = st.session_state.get("historic_pending_benchmark", DEFAULT_MOMENTUM_BENCHMARK)
            alert_exchange_by_symbol = _historic_alert_exchange_by_symbol()

            with st.spinner("Fetching historic dashboard..."):
                instruments_df = load_instrument_token_from_supabase(
                    pending_historic_tickers + [pending_benchmark]
                )
                token_map, missing_tickers = resolve_tokens_from_tickers(
                    pending_historic_tickers,
                    instruments_df,
                    exchange_by_symbol=alert_exchange_by_symbol,
                )
                benchmark_token_map, missing_benchmark = resolve_tokens_from_tickers([pending_benchmark], instruments_df)

                if missing_tickers:
                    st.session_state["historic_missing_tickers"] = missing_tickers
                else:
                    st.session_state.pop("historic_missing_tickers", None)

                if missing_benchmark:
                    st.session_state["historic_missing_benchmark"] = pending_benchmark
                else:
                    st.session_state.pop("historic_missing_benchmark", None)

                if not token_map:
                    st.error("No instrument tokens found for the selected tickers.")
                    st.session_state.pop("historic_pending_tickers", None)
                else:
                    token_rows = [
                        {"Ticker": ticker, "instrument_token": token}
                        for ticker, token in token_map.items()
                    ]
                    live_ltp_by_symbol = fetch_live_ltp_by_symbol(
                        historic_kite,
                        pending_historic_tickers,
                        exchange_by_symbol=alert_exchange_by_symbol,
                    )
                    dashboard_df, day_movers_df, skipped_symbols = build_price_ladder_and_day_movers_frames(
                        historic_kite,
                        token_rows,
                        as_of_date,
                        live_ltp_by_symbol=live_ltp_by_symbol,
                    )
                    st.session_state["historic_dashboard_df"] = dashboard_df
                    st.session_state["historic_day_movers_df"] = day_movers_df
                    st.session_state["historic_live_ltp_by_symbol"] = live_ltp_by_symbol
                    st.session_state["historic_skipped_symbols"] = skipped_symbols
                    st.session_state["historic_momentum_benchmark_used"] = pending_benchmark
                    st.session_state["historic_token_rows"] = token_rows
                    st.session_state["historic_as_of_date"] = as_of_date
                    st.session_state["historic_ltp_refreshed_at"] = pd.Timestamp.now().isoformat()
                    st.session_state["historic_loaded_request_signature"] = _historic_request_signature(
                        pending_historic_tickers,
                        pending_benchmark,
                    )
                    st.session_state.pop("historic_ltp_missing_symbols", None)
                    st.session_state.pop("historic_returns_df", None)
                    st.session_state.pop("historic_close_prices_df", None)

                    if benchmark_token_map:
                        try:
                            momentum_df, momentum_failed_symbols = calculate_momentum_scores_from_kite(
                                historic_kite,
                                token_rows,
                                benchmark_token_map[pending_benchmark],
                                as_of_date,
                                live_ltp_by_symbol=live_ltp_by_symbol,
                            )
                            st.session_state["historic_momentum_df"] = momentum_df
                            st.session_state["historic_momentum_failed_symbols"] = momentum_failed_symbols
                            st.session_state.pop("historic_momentum_error", None)
                        except Exception as momentum_exc:
                            st.session_state.pop("historic_momentum_df", None)
                            st.session_state.pop("historic_momentum_failed_symbols", None)
                            st.session_state["historic_momentum_error"] = str(momentum_exc)
                    else:
                        st.session_state.pop("historic_momentum_df", None)
                        st.session_state.pop("historic_momentum_failed_symbols", None)
                        st.session_state.pop("historic_momentum_error", None)

                    st.session_state["historic_early_entry_labels"] = _price_ladder_early_entry_labels_by_symbol(
                        historic_kite,
                        token_rows,
                        as_of_date,
                        st.session_state.get("historic_momentum_df", pd.DataFrame()),
                        live_ltp_by_symbol=live_ltp_by_symbol,
                    )

        except Exception as exc:
            if is_token_error(exc):
                clear_auth_state()
                st.error("Your session expired. Please login again to load dashboard data.")
                st.rerun()
            st.error(f"Error fetching dashboard data: {exc}")
        finally:
            st.session_state.pop("historic_pending_tickers", None)
            st.session_state.pop("historic_pending_benchmark", None)

    missing_tickers = st.session_state.get("historic_missing_tickers", [])
    if missing_tickers:
        st.warning(f"Skipped tickers with no instrument token: {', '.join(missing_tickers)}")

    missing_benchmark = st.session_state.get("historic_missing_benchmark")
    if missing_benchmark:
        st.warning(f"Momentum benchmark token not found: {missing_benchmark}")

    historic_ltp_missing_symbols = st.session_state.get("historic_ltp_missing_symbols", [])
    if historic_ltp_missing_symbols:
        st.warning(
            "No live LTP returned for: "
            + ", ".join(historic_ltp_missing_symbols[:10])
            + ("..." if len(historic_ltp_missing_symbols) > 10 else "")
        )

    skipped_symbols = st.session_state.get("historic_skipped_symbols", [])
    if skipped_symbols:
        st.warning(
            "No dashboard data returned for: "
            + ", ".join(skipped_symbols[:10])
            + ("..." if len(skipped_symbols) > 10 else "")
        )

    momentum_failed_symbols = st.session_state.get("historic_momentum_failed_symbols", [])
    if momentum_failed_symbols:
        st.warning(
            "No momentum data returned for: "
            + ", ".join(momentum_failed_symbols[:10])
            + ("..." if len(momentum_failed_symbols) > 10 else "")
        )

    momentum_error = st.session_state.get("historic_momentum_error")
    if momentum_error:
        st.warning(f"Momentum dashboard could not be calculated: {momentum_error}")

    if "historic_returns_df" in st.session_state or "historic_dashboard_df" in st.session_state:
        sorted_dashboard_df = _sort_historic_dashboard_by_rng(
            st.session_state.get("historic_dashboard_df", pd.DataFrame())
        )
        returns_df = st.session_state.get("historic_returns_df", pd.DataFrame())
        day_movers_df = st.session_state.get("historic_day_movers_df", pd.DataFrame())
        momentum_df = st.session_state.get("historic_momentum_df", pd.DataFrame())
        early_entry_labels = st.session_state.get("historic_early_entry_labels", {})
        benchmark_used = st.session_state.get("historic_momentum_benchmark_used")
        #if benchmark_used and not momentum_df.empty:
        #    st.caption(f"Relative strength benchmark: {benchmark_used}")

        tab_analytics, tab_returns, tab_correlation = st.tabs(
            ["Analytics", "Returns", "Correlation"]
        )
        with tab_analytics:
            historic_ladder_highlight_symbols = _summary_ticker_accents(
                build_day_movers_summary(day_movers_df)
            )
            momentum_summary_col, day_movers_col = st.columns([3, 2], gap="medium")
            with momentum_summary_col:
                _render_holdings_momentum_summary(momentum_df, day_movers_df)
            with day_movers_col:
                display_day_movers_summary(day_movers_df)

            ema_filter_col, proximity_filter_col, proximity_pct_col = st.columns([2, 2, 1], gap="small")
            with ema_filter_col:
                historic_ema_filter = st.selectbox(
                    "EMA filter",
                    [
                        "All",
                        "Above EMA10",
                        "Below EMA10",
                        "Above EMA20",
                        "Below EMA20",
                        "Above EMA50",
                        "Below EMA50",
                        "Above EMA100",
                        "Below EMA100",
                        "Above EMA200",
                        "Below EMA200",
                    ],
                    key="historic_price_summary_ema_filter",
                )
            with proximity_filter_col:
                historic_proximity_filter = st.selectbox(
                    "52-week proximity",
                    ["All", "Near 52W High", "Near 52W Low"],
                    key="historic_price_summary_52w_filter",
                )
            with proximity_pct_col:
                historic_proximity_pct = st.number_input(
                    "Near within %",
                    min_value=0.1,
                    max_value=100.0,
                    value=5.0,
                    step=0.5,
                    key="historic_price_summary_near_pct",
                )

            filtered_dashboard_df = _filter_historic_price_ladder(
                sorted_dashboard_df,
                historic_ema_filter,
                historic_proximity_filter,
                historic_proximity_pct,
            )
            st.caption(f"{len(filtered_dashboard_df.columns)} of {len(sorted_dashboard_df.columns)} stocks")

            _render_price_ladder_summary_card(
                filtered_dashboard_df,
                highlight_symbols=historic_ladder_highlight_symbols,
                momentum_labels=_momentum_label_by_symbol(momentum_df),
                early_entry_labels=early_entry_labels,
                notes_by_symbol=_stock_notes_by_symbol(momentum_df),
                show_positions=True,
            )

            with st.expander("Momentum Ranking", expanded=False):
                render_momentum_ranking_table(
                    momentum_df,
                    day_movers_df,
                    key="historic_momentum_ranking_table",
                    show_summary=False,
                )

            with st.expander("Price Ladder", expanded=False):
                display_historic_price_ladder_frame(
                    filtered_dashboard_df,
                    max_rows=12,
                    highlight_symbols=historic_ladder_highlight_symbols,
                    show_summary=False,
                )
        with tab_returns:
            if st.button("Display Historical Returns", key="display_historic_returns"):
                token_rows = st.session_state.get("historic_token_rows", [])
                if not token_rows:
                    st.warning("Fetch a historic dashboard before loading returns.")
                else:
                    try:
                        with st.spinner("Loading historical returns..."):
                            returns_kite, _, _ = bootstrap_kite_app("Zerodha Historical Data")
                            returns_df, _, returns_failed_symbols, _ = build_historic_dashboard_frames(
                                returns_kite,
                                token_rows,
                                st.session_state.get("historic_as_of_date") or datetime.now().date().isoformat(),
                                live_ltp_by_symbol=st.session_state.get("historic_live_ltp_by_symbol", {}),
                                include_close_prices=False,
                                include_ladders=False,
                            )
                        st.session_state["historic_returns_df"] = returns_df
                        if returns_failed_symbols:
                            st.warning(
                                "No returns data returned for: "
                                + ", ".join(returns_failed_symbols[:10])
                                + ("..." if len(returns_failed_symbols) > 10 else "")
                            )
                    except Exception as exc:
                        if is_token_error(exc):
                            clear_auth_state()
                            st.error("Your session expired. Please login again to load returns.")
                            st.rerun()
                        st.error(f"Error loading returns: {exc}")
            if "historic_returns_df" in st.session_state:
                display_historic_returns_frame(
                    st.session_state.get("historic_returns_df", pd.DataFrame()),
                    max_rows=18,
                )
        with tab_correlation:
            if st.button("Show Correlation", key="show_historic_correlation"):
                token_rows = st.session_state.get("historic_token_rows", [])
                if not token_rows:
                    st.warning("Fetch a historic dashboard before loading correlation.")
                else:
                    try:
                        with st.spinner("Loading correlation data..."):
                            correlation_kite, _, _ = bootstrap_kite_app("Zerodha Historical Data")
                            _, _, correlation_failed_symbols, close_prices_df = build_historic_dashboard_frames(
                                correlation_kite,
                                token_rows,
                                st.session_state.get("historic_as_of_date") or datetime.now().date().isoformat(),
                                include_returns=False,
                                include_ladders=False,
                            )
                        st.session_state["historic_close_prices_df"] = close_prices_df
                        if correlation_failed_symbols:
                            st.warning(
                                "No correlation data returned for: "
                                + ", ".join(correlation_failed_symbols[:10])
                                + ("..." if len(correlation_failed_symbols) > 10 else "")
                            )
                    except Exception as exc:
                        if is_token_error(exc):
                            clear_auth_state()
                            st.error("Your session expired. Please login again to load correlation.")
                            st.rerun()
                        st.error(f"Error loading correlation: {exc}")
            if "historic_close_prices_df" in st.session_state:
                display_correlation_matrix(
                    st.session_state.get("historic_close_prices_df", pd.DataFrame())
                )



#if "access_token" in st.session_state:
#    if st.sidebar.button("Logout"):
#        clear_auth_state()
#        st.rerun()
