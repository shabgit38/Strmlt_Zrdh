from datetime import datetime
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st

st.set_page_config(layout="wide") 

#st.set_page_config(
#    page_title="Ex-stream-ly Cool App",
#    page_icon="🧊",
#    layout="wide",
#    initial_sidebar_state="expanded",
#    menu_items={
#        'Get Help': 'https://www.extremelycoolapp.com/help',
#        'Report a bug': "https://www.extremelycoolapp.com/bug",
#        'About': "# This is a header. This is an *extremely* cool app!"
#    }
#)



from kite_analytics import (
    build_metric_ladder,
    build_vertical_dashboard,
    compute_period_returns,
    highlight_ltp_cells,
    load_analytics_history,
)
from kite_auth import bootstrap_kite_app, clear_auth_state, get_secret_value, is_token_error

SUPABASE_INDICES_TABLE_NAME = "Indices_constituents"


@st.cache_data(ttl=24 * 60 * 60)
def load_instrument_token_from_supabase(tickers: list[str]) -> pd.DataFrame:
    """
    Load instrument rows from Supabase for the ticker symbols entered by the user.
    """
    normalized_tickers = sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})
    #print(f"Loading instrument tokens from Supabase for tickers: {normalized_tickers}")
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
    #print(instrument_token_df.head())
    #print(f"Loaded {len(instrument_token_df)} instrument tokens from Supabase for tickers: {', '.join(instrument_token_df['tradingsymbol'])}")

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

        # Prefer the first exact match. If the CSV contains duplicates, the user
        # can refine the lookup later by exchange/segment if needed.
        resolved[ticker] = int(matches.iloc[0]["instrument_token"])
    #print(f"Resolved tickers to tokens: {resolved}")
    return resolved, missing

def _dataframe_height(row_count: int, *, min_rows: int = 1, max_rows: int | None = None) -> int:
    visible_rows = max(row_count, min_rows)
    if max_rows is not None:
        visible_rows = min(visible_rows, max_rows)
    header_height = 38
    row_height = 35
    border_padding = 4
    return  (visible_rows * row_height) + header_height + border_padding

kite, _, _ = bootstrap_kite_app("Zerodha Historical Data")

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
    selected_index = st.selectbox("Select index", index_names, key="historic_selected_index")
    if selected_index != "Custom":
        selected_constituents = indices[selected_index]
        if st.session_state["historic_tickers_input"] != selected_constituents:
            st.session_state["historic_tickers_input"] = selected_constituents
            st.rerun()

tickers_input = st.text_area(    
    label="e.g. RELIANCE, INFY, TCS",
    key="historic_tickers_input",
    help="Enter one or more stock ticker symbols separated by commas.",
)

if st.button("Fetch dashboard", type="primary"):
    raw_tickers = [item.strip().upper() for item in tickers_input.split(",") if item.strip()]

    if not raw_tickers:
        st.warning("Enter at least one ticker symbol.")
        st.stop()

    as_of_date = datetime.now().date().isoformat() #

    try:
        instruments_df = load_instrument_token_from_supabase(raw_tickers)
        token_map, missing_tickers = resolve_tokens_from_tickers(raw_tickers, instruments_df)

        if missing_tickers:
            st.warning(f"Skipped tickers with no instrument token: {', '.join(missing_tickers)}")

        if not token_map:
            st.error("No instrument tokens found for the selected tickers.")
            st.stop()

        ladders: dict[str, list[tuple[str, float]]] = {}
        return_rows: list[dict] = []
        for ticker, token in token_map.items():
            #load the cached 2Y daily dataframe for each ticker
            # -the start point two years prior and the end point as today
            analytics_df = load_analytics_history(kite, token, as_of_date)
            if analytics_df.empty:
                continue

            ladders[ticker] = build_metric_ladder(analytics_df)
            #print(f"Computed metric ladder for {ticker}: {ladders[ticker]}")
            return_rows.append({"Ticker": ticker, **compute_period_returns(analytics_df)})
        
            #print(f"Computed period returns for tickers: {return_rows}")


        flattened_rows = []

        for row in return_rows:

            flat_row = {
                "Ticker": row["Ticker"]
            }

            for period in [
                "1W Return %",
                "1M Return %",
                "3M Return %",
                "6M Return %",
                "1Y Return %",
                "2Y Return %",
                "YTD Return %"
            ]:

                data = row.get(period)

                if data is None:
                    flat_row[period] = None
                    #flat_row[f"{period} Start"] = None
                    #flat_row[f"{period} LTP"] = None

                else:
                    flat_row[period] = data["return_pct"]
                    #flat_row[f"{period} Start"] = data["start_close"]
                    #flat_row[f"{period} LTP"] = data["latest_price"]

            flattened_rows.append(flat_row)

        df_display = pd.DataFrame(flattened_rows)

        st.caption("Returns")
        #st.dataframe(pd.DataFrame(return_rows), width="stretch", height=_dataframe_height(len(return_rows)), hide_index=True)
        
        st.dataframe(
            df_display,
            width="stretch",
            height=_dataframe_height(len(df_display)),
            hide_index=True
        )


        if not ladders:
            st.info("No dashboard data returned for the selected inputs.")
        else:           
            dashboard_df = build_vertical_dashboard(ladders)
            #print("Constructed vertical dashboard DataFrame:\n", dashboard_df.head())
            st.dataframe(
                dashboard_df.style.map(highlight_ltp_cells),
                width="stretch",
                height=_dataframe_height(len(dashboard_df)), 
                hide_index=True
            )
                
    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
            st.error("Your session expired. Please login again to load dashboard data.")
            st.rerun()
        st.error(f"Error fetching dashboard data: {exc}")


#if "access_token" in st.session_state:
#    if st.sidebar.button("Logout"):
#        clear_auth_state()
#        st.rerun()
