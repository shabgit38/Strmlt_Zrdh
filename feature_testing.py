import json
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st

from getHldgBrk import (
    _ltp_match_symbol,
    _normalized_symbol_value,
    load_holdings_breakdown_from_supabase,
    update_holdings_breakdown_row,
)
from kite_auth import bootstrap_kite_app, clear_auth_state, get_secret_value, is_token_error
from momentum_score import calculate_momentum_scores_from_kite
from stock_memory_cards import render_stock_memory_card

DEFAULT_BENCHMARK_SYMBOL = "NIFTY 50"
SUPABASE_INDICES_TABLE_NAME = "Indices_constituents"
DISPLAY_COLUMNS = [
    "ticker",
    "ltp",
    "latest_close",
    "pullback_score",
    "entry_signal",
    "ret_6m",
    "ret_12_1",
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


@st.cache_data(ttl=24 * 60 * 60)
def load_index_constituents() -> pd.DataFrame:
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
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
    }

    request = Request(endpoint, headers=headers, method="GET")
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

    return pd.DataFrame(records)


def find_symbol_indices(symbol: str, indices_df: pd.DataFrame) -> pd.DataFrame:
    symbol = symbol.strip().upper()
    if not symbol or indices_df.empty:
        return pd.DataFrame(columns=["Index", "Matched Symbol"])

    matches: list[dict[str, str]] = []
    for _, row in indices_df.iterrows():
        index_name = str(row.get("Index") or "").strip()
        constituents = str(row.get("Constituents") or "")
        symbols = [item.strip().upper() for item in constituents.split(",") if item.strip()]
        if symbol in symbols:
            matches.append({"Index": index_name, "Matched Symbol": symbol})

    return pd.DataFrame(matches)


@st.cache_data(ttl=24 * 60 * 60)
def load_instrument_rows(symbols: list[str]) -> pd.DataFrame:
    normalized_symbols = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    if not normalized_symbols:
        return pd.DataFrame()

    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_TABLE_NAME").strip()

    if not supabase_url or not supabase_key:
        raise ValueError(
            "Missing Supabase config. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "in .streamlit/secrets.toml or environment variables."
        )

    symbol_filter = ",".join(
        f"tradingsymbol.eq.{quote(symbol, safe='')}" for symbol in normalized_symbols
    )
    endpoint = (
        f"{supabase_url}/rest/v1/{quote(table_name, safe='')}"
        f"?select=tradingsymbol,instrument_token,exchange&or=({symbol_filter})"
    )
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
    }

    request = Request(endpoint, headers=headers, method="GET")
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

    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=["tradingsymbol", "instrument_token", "exchange"])

    df["tradingsymbol"] = df["tradingsymbol"].astype(str).str.strip().str.upper()
    if "exchange" in df.columns:
        df["exchange"] = df["exchange"].astype(str).str.strip().str.upper()
    return df


def resolve_token(symbol: str, instruments_df: pd.DataFrame) -> int | None:
    symbol = symbol.strip().upper()
    matches = instruments_df[instruments_df["tradingsymbol"] == symbol]
    if matches.empty:
        return None

    if "exchange" in matches.columns:
        nse_matches = matches[matches["exchange"] == "NSE"]
        if not nse_matches.empty:
            matches = nse_matches

    token = pd.to_numeric(matches.iloc[0]["instrument_token"], errors="coerce")
    if pd.isna(token):
        return None
    return int(token)


def build_token_rows(symbols: list[str], instruments_df: pd.DataFrame) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    missing: list[str] = []
    for symbol in symbols:
        token = resolve_token(symbol, instruments_df)
        if token is None:
            missing.append(symbol)
            continue
        rows.append({"Ticker": symbol, "instrument_token": token})
    return rows, missing


def build_kite_isin_lookup(holdings: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    exact_isin_by_symbol: dict[str, str] = {}
    fallback_isin_by_symbol: dict[str, str] = {}
    for holding in holdings:
        symbol = _normalized_symbol_value(holding.get("tradingsymbol"))
        isin = str(holding.get("isin") or "").strip().upper()
        if not symbol or not isin:
            continue
        exact_isin_by_symbol[symbol] = isin
        fallback_symbol = _ltp_match_symbol(symbol)
        if fallback_symbol and fallback_symbol not in fallback_isin_by_symbol:
            fallback_isin_by_symbol[fallback_symbol] = isin
    return exact_isin_by_symbol, fallback_isin_by_symbol


def lookup_isin(
    symbol: str,
    exact_isin_by_symbol: dict[str, str],
    fallback_isin_by_symbol: dict[str, str],
) -> str | None:
    symbol_key = _normalized_symbol_value(symbol)
    return exact_isin_by_symbol.get(symbol_key) or fallback_isin_by_symbol.get(_ltp_match_symbol(symbol_key))


def update_holdings_breakdown_isin_from_kite(kite) -> pd.DataFrame:
    holdings = kite.holdings()
    exact_isin_by_symbol, fallback_isin_by_symbol = build_kite_isin_lookup(holdings)
    if not exact_isin_by_symbol and not fallback_isin_by_symbol:
        return pd.DataFrame(columns=["Symbol", "ISIN", "Status"])

    breakdown_df = load_holdings_breakdown_from_supabase()
    if breakdown_df.empty or "symbol" not in breakdown_df.columns or "id" not in breakdown_df.columns:
        return pd.DataFrame(columns=["Symbol", "ISIN", "Status"])

    updated_rows: list[dict[str, str]] = []
    for _, row in breakdown_df.iterrows():
        row_id = row.get("id")
        symbol = _normalized_symbol_value(row.get("symbol"))
        if not symbol or row_id is None or pd.isna(row_id):
            continue

        isin = lookup_isin(symbol, exact_isin_by_symbol, fallback_isin_by_symbol)
        if not isin:
            updated_rows.append({"Symbol": symbol, "ISIN": "", "Status": "No Kite match"})
            continue

        update_holdings_breakdown_row(row_id, {"isin": isin})
        updated_rows.append({"Symbol": symbol, "ISIN": isin, "Status": "Updated"})

    return pd.DataFrame(updated_rows)


st.set_page_config(layout="wide")
#st.title("Quant Momentum Score Check")

if "request_token" in st.query_params and "access_token" not in st.session_state:
    bootstrap_kite_app("Quant Momentum Score Check")

kite, _, _ = bootstrap_kite_app("Quant Momentum Score Check")

st.subheader("One-time Holdings Breakdown ISIN Update")
st.caption("Fetches Kite holdings and writes only the ISIN field on matching holdings_breakdown rows.")
if st.button("Write ISIN to Holdings Breakdown", key="write_holdings_breakdown_isin"):
    try:
        isin_update_df = update_holdings_breakdown_isin_from_kite(kite)
        st.session_state["holdings_breakdown_isin_update_df"] = isin_update_df
        if isin_update_df.empty:
            st.info("No holdings breakdown rows were updated.")
        else:
            updated_count = int(isin_update_df["Status"].eq("Updated").sum())
            st.success(f"Updated ISIN for {updated_count} holdings breakdown row(s).")
    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
            st.error("Your session expired. Please login again.")
            st.rerun()
        st.error(f"ISIN update failed: {exc}")

isin_update_df = st.session_state.get("holdings_breakdown_isin_update_df")
if isinstance(isin_update_df, pd.DataFrame) and not isin_update_df.empty:
    st.dataframe(isin_update_df, width="stretch", hide_index=True)

st.divider()
st.subheader("Index Membership Check")

index_check_symbol = st.text_input(
    "Stock symbol to check in Supabase index constituents",
    value="",
    key="index_membership_symbol",
)
if st.button("Check Index Membership", key="check_index_membership"):
    symbol = index_check_symbol.strip().upper()
    if not symbol:
        st.warning("Enter a stock symbol.")
    else:
        try:
            index_constituents_df = load_index_constituents()
            matched_indices_df = find_symbol_indices(symbol, index_constituents_df)
            if matched_indices_df.empty:
                st.info(f"{symbol} was not found in any index constituents.")
            else:
                st.success(
                    f"{symbol} found in {len(matched_indices_df)} index"
                    f"{'' if len(matched_indices_df) == 1 else 'es'}."
                )
                st.dataframe(matched_indices_df, width="stretch", hide_index=True)
        except Exception as exc:
            st.error(f"Could not check index membership: {exc}")

st.divider()
st.subheader("Quant Momentum Score Check")
benchmark_symbol = st.text_input("Benchmark symbol", value=DEFAULT_BENCHMARK_SYMBOL)
tickers_input = st.text_area(
    "Stock tickers",
    value="",
    help="Enter ticker symbols separated by commas.",
)

if st.button("Calculate Momentum Scores", type="primary"):
    stock_symbols = [item.strip().upper() for item in tickers_input.split(",") if item.strip()]
    benchmark_symbol = benchmark_symbol.strip().upper()

    if not stock_symbols:
        st.warning("Enter at least one stock ticker.")
        st.stop()
    if not benchmark_symbol:
        st.warning("Enter a benchmark symbol.")
        st.stop()

    try:
        instruments_df = load_instrument_rows(stock_symbols + [benchmark_symbol])
        stock_token_rows, missing_stocks = build_token_rows(stock_symbols, instruments_df)
        benchmark_token = resolve_token(benchmark_symbol, instruments_df)

        if missing_stocks:
            st.warning(f"Missing stock tokens: {', '.join(missing_stocks)}")
        if benchmark_token is None:
            st.error(f"Missing benchmark token: {benchmark_symbol}")
            st.stop()
        if not stock_token_rows:
            st.error("No stock tokens resolved.")
            st.stop()

        score_df, failed_symbols = calculate_momentum_scores_from_kite(
            kite,
            stock_token_rows,
            benchmark_token,
            datetime.now().date().isoformat(),
        )

        if failed_symbols:
            st.warning(f"Failed symbols: {', '.join(failed_symbols)}")
        if score_df.empty:
            st.info("No momentum score data returned.")
            st.stop()

        st.session_state["momentum_score_check_df"] = score_df
    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
            st.error("Your session expired. Please login again.")
            st.rerun()
        st.error(f"Momentum score check failed: {exc}")

score_df = st.session_state.get("momentum_score_check_df")
if score_df is not None and not score_df.empty:
    visible_columns = [column for column in DISPLAY_COLUMNS if column in score_df.columns]
    left_col, right_col = st.columns([3, 1])
    with left_col:
        selection = st.dataframe(
            score_df[visible_columns].style.format(
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
                    "vol_adj_mtm": "{:.2f}",
                },
                na_rep="-",
            ),
            width="stretch",
            hide_index=True,
            column_config={
                "ltp": st.column_config.NumberColumn(
                    "LTP",
                    format="%.2f",
                ),
                "latest_close": st.column_config.NumberColumn(
                    "Latest Close",
                    format="%.2f",
                ),
                "pullback_score": st.column_config.NumberColumn(
                    "pullback_score",
                    help=(
                        "Pullback Score:\n"
                        "+20 bullish EMA stack: EMA10 > EMA20 > EMA50 > EMA100 > EMA200\n"
                        "Required filter: Close > EMA20 for non-Avoid signal\n"
                        "+15 EMA10 extension between -3% and +7%\n"
                        "+20 EMA20 <= Close <= EMA20 + 0.5 * ATR14\n"
                        "+10 RSI between 50 and 70\n"
                        "+15 volume ratio < 1\n"
                        "+5 Z-score between -2 and +2.5\n"
                        "+15 relative strength vs Nifty > 0"
                    ),
                    format="%.1f",
                ),
                "entry_signal": st.column_config.TextColumn(
                    "entry_signal",
                    help=(
                        "Signal from pullback_score:\n"
                        ">=80 and Close > EMA20: Strong Entry\n"
                        ">=80 and Close <= EMA20: Watchlist - Below EMA20\n"
                        "65-79 Near Entry\n"
                        "45-64 Wait\n"
                        "<45 Avoid"
                    ),
                ),
            },
            key="momentum_score_check_table",
            on_select="rerun",
            selection_mode="single-row",
        )
    selected_rows = selection.selection.rows if selection.selection else []
    with right_col:
        if selected_rows:
            render_stock_memory_card(score_df.iloc[selected_rows[0]])
        else:
            st.info("Select a stock row to view notes.")
