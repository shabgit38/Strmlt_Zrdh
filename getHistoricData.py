from datetime import datetime, time
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st
from kiteconnect import KiteConnect

from kite_auth import bootstrap_kite_app, clear_auth_state, get_secret_value, is_token_error

# ------------------------------------------------------------------------------
# KITE HISTORICAL DATA
# Wrapper around Kite's historical candle endpoint:
# GET /instruments/historical/:instrument_token/:interval
# ------------------------------------------------------------------------------

def get_kite_historical_data(
    kite: KiteConnect,
    instrument_token: int | str,
    interval: str,
    from_date: str | datetime,
    to_date: str | datetime,
    continuous: int | bool = 0,
    oi: int | bool = 0,
) -> pd.DataFrame:
    """
    Fetch historical candles from Kite and return them as a DataFrame.
    """
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

    required_columns = {"tradingsymbol", "instrument_token"}
    if not required_columns.issubset(instrument_token_df.columns):
        raise ValueError(
            f"Supabase instrument lookup missing columns: {required_columns - set(instrument_token_df.columns)}"
        )

    if "tradingsymbol" in instrument_token_df.columns:
        instrument_token_df["tradingsymbol"] = (
            instrument_token_df["tradingsymbol"].astype(str).str.strip().str.upper()
        )

    return instrument_token_df

def resolve_tokens_from_tickers(tickers: list[str], instruments_df: pd.DataFrame) -> dict[str, int]:
    """
    Map comma-separated tickers to instrument tokens using the instrument dump.
    """
    resolved: dict[str, int] = {}
    normalized = instruments_df.copy()
    normalized["tradingsymbol"] = normalized["tradingsymbol"].astype(str).str.strip().str.upper()

    for ticker in tickers:
        matches = normalized[normalized["tradingsymbol"] == ticker]
        if matches.empty:
            raise ValueError(f"No instrument token found for ticker: {ticker}")

        # Prefer the first exact match. If the CSV contains duplicates, the user
        # can refine the lookup later by exchange/segment if needed.
        resolved[ticker] = int(matches.iloc[0]["instrument_token"])

    return resolved


kite, _, _ = bootstrap_kite_app("Zerodha Historical Data")

st.caption("Fetch historical candles from Kite using ticker symbols and a date range.")

tickers_input = st.text_input(
    "Tickers (comma-separated)",
    placeholder="e.g. RELIANCE, INFY, TCS",
    help="Enter one or more stock ticker symbols separated by commas.",
)

col1, col2 = st.columns(2)
with col1:
    from_date = st.date_input("From date")
with col2:
    to_date = st.date_input("To date")

interval = st.selectbox(
    "Interval",
    ["minute", "day", "3minute", "5minute", "10minute", "15minute", "30minute", "60minute"],
    index=1,
)

continuous =  0  #st.checkbox("Continuous data", value=False)
oi = 0 #st.checkbox("Include OI", value=False)

if st.button("Fetch historical data", type="primary"):
    raw_tickers = [item.strip().upper() for item in tickers_input.split(",") if item.strip()]

    if not raw_tickers:
        st.warning("Enter at least one ticker symbol.")
        st.stop()

    if from_date > to_date:
        st.warning("From date must be before or equal to To date.")
        st.stop()

    start_dt = datetime.combine(from_date, time.min)
    end_dt = datetime.combine(to_date, time(23, 59, 59))

    try:
        instruments_df = load_instrument_token_from_supabase(raw_tickers)
        token_map = resolve_tokens_from_tickers(raw_tickers, instruments_df)

        all_frames: list[pd.DataFrame] = []
        for ticker, token in token_map.items():
            historical_df = get_kite_historical_data(
                kite=kite,
                instrument_token=token,
                interval=interval,
                from_date=start_dt,
                to_date=end_dt,
                continuous=continuous,
                oi=oi,
            )

            if historical_df.empty:
                continue

            historical_df = historical_df.copy()
            historical_df.insert(0, "Ticker", ticker)
            historical_df.insert(1, "InstrumentToken", token)
            all_frames.append(historical_df)

        st.subheader("Historical candles")
        if not all_frames:
            st.info("No candle data returned for the selected inputs.")
        else:
            result_df = pd.concat(all_frames).sort_index()
            print(result_df.head(10))
            #st.dataframe(result_df, width="stretch")
            #st.download_button(
            #    "Download CSV",
            #    data=result_df.to_csv(),
            #    file_name=f"kite_historical_{'_'.join(raw_tickers)}_{interval}.csv",
            #    mime="text/csv",
            #)

    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
            st.error("Your session expired. Please login again to load historical data.")
            st.rerun()
        st.error(f"Error fetching historical data: {exc}")


if "access_token" in st.session_state:
    if st.sidebar.button("Logout"):
        clear_auth_state()
        st.rerun()
