from datetime import datetime
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st

from kite_analytics import (
    build_metric_ladder,
    build_vertical_dashboard,
    compute_period_returns,
    highlight_ltp_cells,
    load_analytics_history,
)
from kite_auth import bootstrap_kite_app, clear_auth_state, get_secret_value, is_token_error


@st.cache_data(ttl=24 * 60 * 60)
def load_instrument_token_from_supabase(tickers: list[str]) -> pd.DataFrame:
    """
    Load instrument rows from Supabase for the ticker symbols entered by the user.
    """
    normalized_tickers = sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})
    print(f"Loading instrument tokens from Supabase for tickers: {normalized_tickers}")
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

    instrument_token_df = pd.DataFrame(records)
    if instrument_token_df.empty:
        return pd.DataFrame(columns=["tradingsymbol", "instrument_token"])

   
    if "tradingsymbol" in instrument_token_df.columns:
        instrument_token_df["tradingsymbol"] = (
            instrument_token_df["tradingsymbol"].astype(str).str.strip().str.upper()
        )
    print(instrument_token_df.head())
    print(f"Loaded {len(instrument_token_df)} instrument tokens from Supabase for tickers: {', '.join(instrument_token_df['tradingsymbol'])}")

    return instrument_token_df

def resolve_tokens_from_tickers(tickers: list[str], instruments_df: pd.DataFrame) -> dict[str, int]:
    """
    Map comma-separated tickers to instrument tokens using the instrument dump.
    """
    resolved: dict[str, int] = {}
    normalized = instruments_df.copy()
    normalized["tradingsymbol"] = normalized["tradingsymbol"].astype(str).str.strip().str.upper()
    print(f"Resolving tokens for tickers: {tickers}")
    for ticker in tickers:
        matches = normalized[normalized["tradingsymbol"] == ticker]
        if matches.empty:
            raise ValueError(f"No instrument token found for ticker: {ticker}")

        # Prefer the first exact match. If the CSV contains duplicates, the user
        # can refine the lookup later by exchange/segment if needed.
        resolved[ticker] = int(matches.iloc[0]["instrument_token"])
    print(f"Resolved tickers to tokens: {resolved}")
    return resolved

def _dataframe_height(row_count: int, *, min_rows: int = 1, max_rows: int | None = None) -> int:
    visible_rows = max(row_count, min_rows)
    if max_rows is not None:
        visible_rows = min(visible_rows, max_rows)
    header_height = 38
    row_height = 35
    border_padding = 4
    return header_height + (visible_rows * row_height) + border_padding

kite, _, _ = bootstrap_kite_app("Zerodha Historical Data")

st.caption("Fetch cached 2Y daily Kite data and show a sorted price ladder per ticker.")

tickers_input = st.text_input(
    "Tickers (comma-separated)",
    placeholder="e.g. RELIANCE, INFY, TCS",
    help="Enter one or more stock ticker symbols separated by commas.",
)

if st.button("Fetch dashboard", type="primary"):
    raw_tickers = [item.strip().upper() for item in tickers_input.split(",") if item.strip()]

    if not raw_tickers:
        st.warning("Enter at least one ticker symbol.")
        st.stop()

    as_of_date = datetime.now().date().isoformat()

    try:
        instruments_df = load_instrument_token_from_supabase(raw_tickers)
        token_map = resolve_tokens_from_tickers(raw_tickers, instruments_df)

        ladders: dict[str, list[tuple[str, float]]] = {}
        return_rows: list[dict] = []
        for ticker, token in token_map.items():
            analytics_df = load_analytics_history(kite, token, as_of_date)
            if analytics_df.empty:
                continue

            ladders[ticker] = build_metric_ladder(analytics_df)
            return_rows.append({"Ticker": ticker, **compute_period_returns(analytics_df)})

        if not ladders:
            st.info("No dashboard data returned for the selected inputs.")
        else:
            dashboard_df = build_vertical_dashboard(ladders)
            st.dataframe(
                dashboard_df.style.map(highlight_ltp_cells),
                width="stretch",
                height=_dataframe_height(len(dashboard_df)), 
                hide_index=True
            )
            st.caption("Returns")
            st.dataframe(pd.DataFrame(return_rows), width="stretch", height=_dataframe_height(len(return_rows)), hide_index=True)

    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
            st.error("Your session expired. Please login again to load dashboard data.")
            st.rerun()
        st.error(f"Error fetching dashboard data: {exc}")


if "access_token" in st.session_state:
    if st.sidebar.button("Logout"):
        clear_auth_state()
        st.rerun()
