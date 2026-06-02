import json
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st

from kite_auth import bootstrap_kite_app, clear_auth_state, get_secret_value, is_token_error
from momentum_score import calculate_momentum_scores_from_kite
from stock_memory_cards import render_stock_memory_card

DEFAULT_BENCHMARK_SYMBOL = "NIFTY 50"
DISPLAY_COLUMNS = [
    "ticker",
    "ltp",
    "ret_6m",
    "ret_12_1",
    "rs_vs_nifty",
    "dist_52w_high",
    "above_ema200",
    "ema50_gt_ema200",
    "vol_adj_mtm",
    "mtm_score",
    "mtm_label",
    "data_status",
]


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


st.set_page_config(layout="wide")
st.title("Quant Momentum Score Check")

if "request_token" in st.query_params and "access_token" not in st.session_state:
    bootstrap_kite_app("Quant Momentum Score Check")

kite, _, _ = bootstrap_kite_app("Quant Momentum Score Check")

benchmark_symbol = st.text_input("Benchmark symbol", value=DEFAULT_BENCHMARK_SYMBOL)
tickers_input = st.text_area(
    "Stock tickers",
    value="RELIANCE, TCS, INFY",
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
                    "ret_6m": "{:.2%}",
                    "ret_12_1": "{:.2%}",
                    "rs_vs_nifty": "{:.2%}",
                    "dist_52w_high": "{:.2%}",
                    "vol_adj_mtm": "{:.2f}",
                    "mtm_score": "{:.1f}",
                },
                na_rep="-",
            ),
            width="stretch",
            hide_index=True,
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

if "access_token" in st.session_state:
    if st.sidebar.button("Logout"):
        clear_auth_state()
        st.rerun()
