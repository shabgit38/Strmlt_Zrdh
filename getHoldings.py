import json
import math
import base64
from datetime import date, datetime
from html import escape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st

from kite_analytics import (
    build_historic_dashboard_frames,
    display_historic_price_ladder_frame,
    display_historic_returns_frame,
    highlight_numeric_scale_cells,
)
from kite_auth import bootstrap_kite_app, clear_auth_state, get_secret_value, is_token_error
from momentum_score import calculate_momentum_scores_from_kite
from getPositions import render_open_positions_tab
from getHldgBrk import (
    HOLDINGS_BREAKDOWN_DF_STATE_KEY,
    HOLDINGS_BREAKDOWN_VIEW_STATE_KEY,
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
    display_selected_holding_batches,
    load_holdings_breakdown_from_supabase,
    upsert_holdings_breakdown_in_supabase,
)

st.set_page_config(layout="wide") 

SUPABASE_INDICES_TABLE_NAME = "Indices_constituents"
DEFAULT_MOMENTUM_BENCHMARK = "NIFTY 50"


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


def resolve_tokens_from_tickers(tickers: list[str], instruments_df: pd.DataFrame) -> tuple[dict[str, int], list[str]]:
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
        for column in ["batch_pnl", "batch_pnl_pct", "pnl", "pnl_pct", "Batch P&L", "Batch P&L %", "P&L", "P&L %","DayChg %"]
        if column in df.columns
    ]
    formatters = {
        column: _format_display_value
        for column in df.columns
        if column not in {"batch_pnl_pct", "pnl_pct", "Batch P&L %", "P&L %"}
    }
    for column in ["batch_pnl_pct", "pnl_pct", "Batch P&L %", "P&L %"]:
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


def _style_kite_holdings(display_df: pd.DataFrame, rng_colors: dict[str, str]):
    styler = _style_pnl_columns(display_df)
    if "Symbol" not in display_df.columns or not rng_colors:
        return styler

    def symbol_style(value: Any) -> str:
        return rng_colors.get(str(value).strip().upper(), "")

    return styler.map(symbol_style, subset=["Symbol"])


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
            elif value.startswith("52W High:"):
                row["52W High"] = _parse_prefixed_float(value, "52W High:")
            elif value.startswith("52W Low:"):
                row["52W Low"] = _parse_prefixed_float(value, "52W Low:")

        rows.append(row)

    return pd.DataFrame(rows)


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
        "ret_6m": "6M Momentum",
        "ret_12_1": "12-1 Momentum",
        "rs_vs_nifty": "RS vs Nifty",
        "dist_52w_high": "52W High Proximity",
        "above_ema200": "Above EMA200",
        "ema50_gt_ema200": "EMA50 > EMA200",
        "vol_adj_mtm": "Vol Adj Mtm",
        "mtm_score": "Mtm Score",
        "mtm_label": "Label",
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
        "Momentum Score",
        "Label",
        "Status",
        "LTP",
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
        "EMA20 Dist %",
        "EMA50 Dist %",
        "EMA100 Dist %",
        "EMA200 Dist %",
        "Vol Adj Momentum",
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
        for column in ["Today Return %", "1W Return %", "1M Return %", "3M Return %", "6M Return %", "1Y Return %", "YTD Return %", "Range %", "EMA20 Dist %", "EMA50 Dist %", "EMA100 Dist %", "EMA200 Dist %"]
        if column in consolidated_df.columns
    ]
    formatters = {
        "Momentum Score": "{:.1f}",
        "LTP": "{:.2f}",
        "Vol Adj Momentum": "{:.2f}",
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
        return "background-color: #0ea5e9; color: #ffffff; font-weight: 700"
    if pd.isna(score_value):
        return ""
    if score_value < 45:
        return "background-color: #dc2626; color: #ffffff; font-weight: 700"
    if score_value < 65:
        return "background-color: #f97316; color: #ffffff; font-weight: 700"
    if score_value < 80:
        return "background-color: #facc15; color: #422006; font-weight: 700"
    return "background-color: #16a34a; color: #ffffff; font-weight: 700"


def highlight_momentum_rank_cells(data: pd.DataFrame) -> pd.DataFrame:
    styles = pd.DataFrame("", index=data.index, columns=data.columns)
    highlight_columns = [
        column
        for column in ["ticker", "pullback_score", "entry_signal"]
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


def _format_momentum_label_summary(label_groups: dict[str, list[str]]) -> str:
    summary_items = [
        ("Strong Entry", "#16a34a", "#ffffff", label_groups["Strong Entry"]),
        ("Watchlist - Below EMA20", "#0ea5e9", "#ffffff", label_groups["Watchlist - Below EMA20"]),
        ("Near Entry", "#facc15", "#422006", label_groups["Near Entry"]),
        ("Wait", "#f97316", "#ffffff", label_groups["Wait"]),
        ("Avoid", "#dc2626", "#ffffff", label_groups["Avoid"]),
    ]
    rows = []
    for label, background, foreground, symbols in summary_items:
        symbol_text = escape(", ".join(symbols)) if symbols else "-"
        rows.append(
            "<div style='display:flex;align-items:center;gap:0.5rem;'>"
            f"<span style='min-width:5rem;font-weight:700;color:{background};'>{label}</span>"
            f"<span style='background:{background};color:{foreground};font-weight:700;"
            "padding:0.2rem 0.45rem;border-radius:0.25rem;'>"
            f"{symbol_text}</span></div>"
        )
    return (
        "<div style='display:grid;gap:0.35rem;margin:0 0 0.75rem 0;'>"
        + "".join(rows)
        + "</div>"
    )


def display_momentum_label_summary(momentum_df: pd.DataFrame) -> None:
    label_groups = _group_momentum_symbols_by_label(momentum_df)
    if any(label_groups.values()):
        st.markdown(
            _format_momentum_label_summary(label_groups),
            unsafe_allow_html=True,
        )


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


def display_kite_holdings(
    df: pd.DataFrame,
    kite=None,
    *,
    selection_key: str | None = None,
    selected_batches_df: pd.DataFrame | None = None,
    selected_batches_error: str | None = None,
) -> str | None:
    if df.empty:
        st.session_state["ltp_by_symbol"] = {}
        st.warning("No holdings found.")
        return None

    df = df.copy()
    _cache_ltp_by_symbol(df)
    
    #print("portfolio holdings columns:\n")
    #print(df.columns)
    
    df["invested"] = pd.to_numeric(df["average_price"], errors="coerce") * pd.to_numeric(
            df["quantity"], errors="coerce"
    )
    df["CurrentValue"] = pd.to_numeric(df["last_price"], errors="coerce") * pd.to_numeric(
            df["quantity"], errors="coerce"
    )
    if "pnl_pct" not in df.columns and {"pnl", "average_price", "quantity"}.issubset(df.columns):
        invested = df["invested"]        
        df["pnl_pct"] = pd.to_numeric(df["pnl"], errors="coerce").where(invested.ne(0)) / invested * 100

    display_cols = [
        "tradingsymbol",
        "quantity",
        "average_price",
        "invested",
        "CurrentValue",
        "last_price",        
        "pnl",
        "pnl_pct",
        "day_change_percentage",
    ]
    display_cols = [column for column in display_cols if column in df.columns]
    display_df = df[display_cols] if display_cols else df
    display_df = display_df.rename(
        columns={
            "tradingsymbol": "Symbol",
            "quantity": "Quantity",
            "average_price": "Avg Price",
            "invested": "Invested",
            "CurrentValue": "Current",
            "last_price": "LTP",
            "pnl": "P&L",
            "pnl_pct": "P&L %",
            "day_change_percentage": "DayChg %",
        }
    )
    #st.table(display_df, width="stretch", height=_dataframe_height(len(display_df)))
    col1, col2 ,col3= st.columns(3)
    with col1:
        total_invested = pd.to_numeric(df["invested"], errors="coerce").sum() if "invested" in df.columns else 0
        #st.write("Total Invested\n", f"{total_invested:,.2f}")
        st.metric("Total Invested", f"{total_invested:,.2f}")

    with col2:
        total_pnl = pd.to_numeric(df["pnl"], errors="coerce").sum() if "pnl" in df.columns else 0
        total_pnl_percent = (total_pnl/total_invested) *100 if total_invested != 0 else 0        
        st.metric("Total P&L", f"{total_pnl:,.2f}", delta=f"{total_pnl_percent:.2f}", format="%.2f%%")
       
        
    with col3:
        kite_holdings_download_filename = st.session_state.get("kite_holdings_download_filename", "")
        holdings_as_of = (
            kite_holdings_download_filename.split("_")[1]
            if "_" in kite_holdings_download_filename
            else "Unknown"
        )
        st.metric("As of", holdings_as_of)
        
    rng_colors = _rng_color_by_symbol(
        _sort_historic_dashboard_by_rng(
            st.session_state.get("kite_holdings_dashboard_df", pd.DataFrame())
        )
    )
    dataframe_kwargs = {}
    if selection_key:
        dataframe_kwargs = {
            "key": selection_key,
            "on_select": "rerun",
            "selection_mode": "single-row",
        }

    def render_holdings_table():
        return st.dataframe(
            _style_kite_holdings(display_df, rng_colors),
            width="stretch",
            height=_dataframe_height(len(display_df), max_rows=15),
            hide_index=False,
            column_config={
                "Symbol": st.column_config.TextColumn("Symbol", width="small"),
                "Quantity": st.column_config.NumberColumn("Quantity", width="small",format="%d"),
                "Avg Price": st.column_config.NumberColumn("Avg Price", width="small", format="%.2f"),
                "Invested": st.column_config.NumberColumn("Invested", width="small", format="%.2f"),
                "Current": st.column_config.NumberColumn("Current", width="small", format="%.2f"),
                "LTP": st.column_config.NumberColumn("LTP", width="small", format="%.2f"),
                "P&L": st.column_config.NumberColumn("P&L", width="small", format="%.2f"),
                "P&L %": st.column_config.NumberColumn("P&L %", width="small", format="%.2f%%"),
                "DayChg%": st.column_config.NumberColumn("DayChg %", width="small", format="%.2f%%"),
            },
            **dataframe_kwargs,
        )

    if selected_batches_df is not None or selected_batches_error is not None:
        holdings_column, batches_column = st.columns([3, 1])
        with holdings_column:
            selection = render_holdings_table()
    else:
        selection = render_holdings_table()

    if not selection_key:
        return None

    batches_df = selected_batches_df if selected_batches_df is not None else pd.DataFrame()
    selected_rows = selection.selection.rows if selection.selection else []
    if not selected_rows:
        if selected_batches_df is not None or selected_batches_error is not None:
            with batches_column:
                if selected_batches_error:
                    st.warning(f"Could not load holdings breakdown from Supabase: {selected_batches_error}")
                display_selected_holding_batches(None, batches_df)
        return None
    selected_row_index = selected_rows[0]
    if selected_row_index >= len(display_df) or "Symbol" not in display_df.columns:
        return None
    selected_symbol = str(display_df.iloc[selected_row_index]["Symbol"]).upper().strip()
    if selected_batches_df is not None or selected_batches_error is not None:
        with batches_column:
            if selected_batches_error:
                st.warning(f"Could not load holdings breakdown from Supabase: {selected_batches_error}")
            display_selected_holding_batches(selected_symbol, batches_df)
    return selected_symbol



def fetch_and_display_holdings():
    try:
        kite, _, _ = bootstrap_kite_app("Zerodha Holdings")
        holdings = kite.holdings()
        if holdings:
            df = pd.DataFrame(holdings)
            _cache_ltp_by_symbol(df)
            #print("Fetched holdings:\n", df.head())
            as_of_date = datetime.now().date().isoformat()
            returns_df, dashboard_df, failed_symbols, close_prices_df = build_historic_dashboard_frames(
                kite,
                df.to_dict(orient="records"),
                as_of_date,
                symbol_key="tradingsymbol",
                token_key="instrument_token",
                ltp_key="last_price",
                buy_avg_key="average_price",
                quantity_key="quantity",
            )
            st.session_state["kite_holdings_df"] = df
            st.session_state["kite_holdings_returns_df"] = returns_df
            st.session_state["kite_holdings_dashboard_df"] = dashboard_df
            st.session_state["kite_holdings_close_prices_df"] = close_prices_df
            st.session_state["kite_holdings_dashboard_failed_symbols"] = failed_symbols
            st.session_state["kite_holdings_download_filename"] = (
                f"holdings_{pd.Timestamp.now().strftime('%Y-%m-%d_%H.%M.%S')}.csv"
            )
            _trigger_csv_download(df, st.session_state["kite_holdings_download_filename"])
            try:
                _set_holdings_breakdown_state(load_holdings_breakdown_from_supabase())
                st.session_state.pop("kite_holdings_breakdown_error", None)
            except Exception as breakdown_exc:
                st.session_state.pop(HOLDINGS_BREAKDOWN_DF_STATE_KEY, None)
                st.session_state["kite_holdings_breakdown_error"] = str(breakdown_exc)
            #print("session state kite_holdings_download_filename:\n", st.session_state["kite_holdings_download_filename"])
        else:
            st.session_state.pop("kite_holdings_df", None)
            st.session_state.pop("kite_holdings_returns_df", None)
            st.session_state.pop("kite_holdings_dashboard_df", None)
            st.session_state.pop("kite_holdings_close_prices_df", None)
            st.session_state.pop("kite_holdings_dashboard_failed_symbols", None)
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


if "request_token" in st.query_params and "access_token" not in st.session_state:
    bootstrap_kite_app("Zerodha Holdings")


tab_historic_data, tab_fetch_kite, tab_open_positions, tab_upload_kite,  tab_upload_holdings_breakdown = st.tabs(
    ["Historic Data", "Fetch Holdings", "Open Positions", "Upload Holdings", "Upload Holdings Breakdown"]
)

with tab_upload_kite:
    uploaded_kite_holdings_file = st.file_uploader(
        "Upload holdings CSV or XLSX",
        type=["csv", "xlsx"],
        key="kite_holdings_upload",
    )

    if uploaded_kite_holdings_file is not None:
        try:
            kite_holdings_df = _read_uploaded_file(uploaded_kite_holdings_file)
            display_kite_holdings(kite_holdings_df)            
            if st.checkbox("Show holdings breakdown", key="show_upload_kite_holdings_breakdown"):
                if _holdings_breakdown_state_df().empty:
                    _load_holdings_breakdown_state()
                display_holdings_breakdown_df(_holdings_breakdown_state_df())
        except ImportError as exc:
            st.error(f"Failed to read XLSX file. Install the missing dependency: {exc}")
        except Exception as exc:
            st.error(f"Failed to upload Kite holdings: {exc}")

with tab_fetch_kite:
    if st.button("Fetch Holdings from Kite", type="primary"):
        fetch_and_display_holdings()#get holdings from kite,
    #session state - kite_holdings_df, kite_holdings_download_filename, ltp_by_symbol

    kite_holdings_df = st.session_state.get("kite_holdings_df")
    tab_price_ladder, tab_portfolio_holdings, tab_returns, tab_holdings_breakdown = st.tabs(
        ["Price Ladder", "Portfolio Holdings", "Returns", "Holdings Breakdown"]
    )

    with tab_portfolio_holdings:
        if kite_holdings_df is None:
            st.info("Fetch holdings from Kite to display portfolio holdings.")
        else:
            kite_holdings_download_filename = st.session_state.get("kite_holdings_download_filename", "Unknown")
            Holdings_fetchDate = kite_holdings_download_filename.split("_")[1] 

            #print("Holdings fetch date:", Holdings_fetchDate , "kite_holdings_download_filename:", kite_holdings_download_filename )
            
            display_kite_holdings(
                kite_holdings_df,
                selection_key="fetch_kite_holdings_table",
                selected_batches_df=_holdings_breakdown_state_df(),
                selected_batches_error=st.session_state.get("kite_holdings_breakdown_error"),
            )
            failed_symbols = st.session_state.get("kite_holdings_dashboard_failed_symbols", [])
            if failed_symbols:
                st.warning(
                    "Could not load dashboard data for: "
                    + ", ".join(failed_symbols[:10])
                    + ("..." if len(failed_symbols) > 10 else "")
                )

    with tab_price_ladder:
        display_historic_price_ladder_frame(
            _sort_historic_dashboard_by_rng(
                st.session_state.get("kite_holdings_dashboard_df", pd.DataFrame())
            ),
            max_rows=12,
        )

    with tab_returns:
        display_historic_returns_frame(
            st.session_state.get("kite_holdings_returns_df", pd.DataFrame()),
            max_rows=18,
        )

    with tab_holdings_breakdown:
        breakdown_error = st.session_state.get("kite_holdings_breakdown_error")
        if breakdown_error:
            st.warning(f"Could not load holdings breakdown from Supabase: {breakdown_error}")
        if kite_holdings_df is None:
            st.info("Fetch holdings from Kite to display holdings breakdown.")
        elif not _holdings_breakdown_state_df().empty:
            display_holdings_breakdown_df(_holdings_breakdown_state_df())
        else:
            st.info("No holdings breakdown found in Supabase.")
    #display_supabase_holdings_breakdown()  


with tab_open_positions:
    render_open_positions_tab()


with tab_upload_holdings_breakdown:

    affected_symbols_to_refresh: list[str] = []

    uploaded_brkholdings_file = st.file_uploader(
        "Upload holdings breakdown CSV or XLSX",
        type=["csv", "xlsx"],
    )

    if uploaded_brkholdings_file is not None:
        try:
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

    try:
        added_symbols = _render_add_holdings_breakdown_entries_form(
            st.session_state.get("ltp_by_symbol", {})
        )
        affected_symbols_to_refresh.extend(added_symbols)
    except Exception as exc:
        st.error(f"Failed to add holdings breakdown entries: {exc}")

    if affected_symbols_to_refresh:
        try:
            _refresh_holdings_breakdown_state_for_symbols(affected_symbols_to_refresh)
            st.session_state[HOLDINGS_BREAKDOWN_VIEW_STATE_KEY] = True
        except Exception as exc:
            st.warning(f"Could not refresh holdings breakdown from Supabase: {exc}")


with tab_historic_data:
    st.caption("Fetch cached 2Y daily Kite data and show a sorted price ladder per ticker.")

    if "historic_tickers_input" not in st.session_state:
        st.session_state["historic_tickers_input"] = ""

    try:
        indices = load_indices_from_supabase()
    except Exception as exc:
        indices = {}
        st.warning(f"Could not load index constituents: {exc}")

    if indices:
        index_names = ["Custom"] + list(indices.keys())
        index_column, benchmark_column = st.columns([2, 1])
        with index_column:
            selected_index = st.selectbox("Select index", index_names, key="historic_selected_index")
        with benchmark_column:
            benchmark_symbol = st.text_input(
                "Momentum benchmark",
                value=DEFAULT_MOMENTUM_BENCHMARK,
                key="historic_momentum_benchmark",
                help="Used for relative strength in the Quant Momentum score.",
            )
        if selected_index != "Custom":
            selected_constituents = indices[selected_index]
            if st.session_state["historic_tickers_input"] != selected_constituents:
                st.session_state["historic_tickers_input"] = selected_constituents
                st.rerun()
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

    if st.button("Fetch dashboard", type="primary", key="historic_fetch_dashboard"):
        raw_tickers = [item.strip().upper() for item in tickers_input.split(",") if item.strip()]
        benchmark_symbol = benchmark_symbol.strip().upper()

        if not raw_tickers:
            st.warning("Enter at least one ticker symbol.")
        elif not benchmark_symbol:
            st.warning("Enter a benchmark symbol.")
        else:
            st.session_state["historic_pending_tickers"] = raw_tickers
            st.session_state["historic_pending_benchmark"] = benchmark_symbol

    pending_historic_tickers = st.session_state.get("historic_pending_tickers")
    if pending_historic_tickers:
        try:
            as_of_date = datetime.now().date().isoformat()
            historic_kite, _, _ = bootstrap_kite_app("Zerodha Historical Data")
            pending_benchmark = st.session_state.get("historic_pending_benchmark", DEFAULT_MOMENTUM_BENCHMARK)
            instruments_df = load_instrument_token_from_supabase(
                pending_historic_tickers + [pending_benchmark]
            )
            token_map, missing_tickers = resolve_tokens_from_tickers(pending_historic_tickers, instruments_df)
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
                returns_df, dashboard_df, skipped_symbols, close_prices_df = build_historic_dashboard_frames(
                    historic_kite,
                    token_rows,
                    as_of_date,
                )
                st.session_state["historic_returns_df"] = returns_df
                st.session_state["historic_dashboard_df"] = dashboard_df
                st.session_state["historic_close_prices_df"] = close_prices_df
                st.session_state["historic_skipped_symbols"] = skipped_symbols
                st.session_state["historic_momentum_benchmark_used"] = pending_benchmark

                if benchmark_token_map:
                    try:
                        momentum_df, momentum_failed_symbols = calculate_momentum_scores_from_kite(
                            historic_kite,
                            token_rows,
                            benchmark_token_map[pending_benchmark],
                            as_of_date,
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

                st.session_state.pop("historic_pending_tickers", None)
                st.session_state.pop("historic_pending_benchmark", None)

        except Exception as exc:
            if is_token_error(exc):
                clear_auth_state()
                st.error("Your session expired. Please login again to load dashboard data.")
                st.rerun()
            st.error(f"Error fetching dashboard data: {exc}")

    missing_tickers = st.session_state.get("historic_missing_tickers", [])
    if missing_tickers:
        st.warning(f"Skipped tickers with no instrument token: {', '.join(missing_tickers)}")

    missing_benchmark = st.session_state.get("historic_missing_benchmark")
    if missing_benchmark:
        st.warning(f"Momentum benchmark token not found: {missing_benchmark}")

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
        momentum_df = st.session_state.get("historic_momentum_df", pd.DataFrame())
        close_prices_df = st.session_state.get("historic_close_prices_df", pd.DataFrame())
        benchmark_used = st.session_state.get("historic_momentum_benchmark_used")
        #if benchmark_used and not momentum_df.empty:
        #    st.caption(f"Relative strength benchmark: {benchmark_used}")

        tab_momentum, tab_ladder, tab_returns, tab_correlation = st.tabs(
            ["Momentum Ranking", "Price Ladder","Returns", "Correlation"]
        )
        with tab_momentum:
            if momentum_df.empty:
                st.info("No momentum score data available.")
            else:
                momentum_display_df = momentum_df.copy()
                if {"ema20", "atr14"}.issubset(momentum_display_df.columns):
                    momentum_display_df["Entry"] = momentum_display_df.apply(_format_entry_range, axis=1)
                sort_columns = [
                    column
                    for column in ["pullback_score", "mtm_score"]
                    if column in momentum_display_df.columns
                ]
                if sort_columns:
                    momentum_display_df = momentum_display_df.sort_values(
                        by=sort_columns,
                        ascending=[False] * len(sort_columns),
                        na_position="last",
                    )
                momentum_display_columns = [
                    "ticker",
                    "ltp",
                    "pullback_score",
                    "entry_signal",
                    "Entry",
                    "mtm_label",
                    "mtm_score",
                    "ret_12_1",
                    "ret_6m",
                    "rs_vs_nifty",
                    "ema10_extension_pct",
                    "ema20_extension_pct",
                    "atr14",
                    "rsi14",
                    "volume_ratio",
                    "zscore_50",
                    "dist_52w_high",
                    "above_ema200",
                    "ema50_gt_ema200",
                    "vol_adj_mtm",
                    "data_status",
                ]
                momentum_display_df = momentum_display_df[
                    [column for column in momentum_display_columns if column in momentum_display_df.columns]
                ]
                display_momentum_label_summary(momentum_display_df)
                st.dataframe(
                    momentum_display_df.style.format(
                        {
                            "ltp": "{:.2f}",
                            "pullback_score": "{:.1f}",
                            "mtm_score": "{:.1f}",
                            "ret_6m": "{:.2%}",
                            "ret_6m_rank": "{:.1f}",
                            "ret_12_1": "{:.2%}",
                            "ret_12_1_rank": "{:.1f}",
                            "rs_vs_nifty": "{:.2%}",
                            "rs_rank": "{:.1f}",
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
                            "vol_adj_rank": "{:.1f}",
                        },
                        na_rep="-",
                    ).apply(highlight_momentum_rank_cells, axis=None),
                    width="stretch",
                    height=_dataframe_height(len(momentum_display_df), max_rows=18),
                    hide_index=True,
                    column_config={
                        "ema10_extension_pct": st.column_config.NumberColumn(
                            "ema10_ext",
                            format="%.2f%%",
                        ),
                        "ema20_extension_pct": st.column_config.NumberColumn(
                            "ema20_ext",
                            format="%.2f%%",
                        ),
                    },
                )
        with tab_returns:
            display_historic_returns_frame(returns_df, max_rows=18)
        with tab_ladder:
            display_historic_price_ladder_frame(
                sorted_dashboard_df,
                max_rows=12,
            )
        with tab_correlation:
            display_correlation_matrix(close_prices_df)



#if "access_token" in st.session_state:
#    if st.sidebar.button("Logout"):
#        clear_auth_state()
#        st.rerun()
