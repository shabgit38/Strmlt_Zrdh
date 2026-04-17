from datetime import datetime, time

import pandas as pd
import streamlit as st
from kiteconnect import KiteConnect

from kite_auth import bootstrap_kite_app, clear_auth_state, is_token_error


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


kite, _, _ = bootstrap_kite_app("Zerodha Historical Data")

st.caption("Fetch historical candles from Kite using an instrument token and date range.")

instrument_token = st.text_input(
    "Instrument token",
    placeholder="e.g. 256265",
    help="Enter the Kite instrument token for the symbol you want to inspect.",
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

continuous = st.checkbox("Continuous data", value=False)
oi = st.checkbox("Include OI", value=False)

if st.button("Fetch historical data", type="primary"):
    if not instrument_token.strip():
        st.warning("Enter an instrument token.")
        st.stop()

    if from_date > to_date:
        st.warning("From date must be before or equal to To date.")
        st.stop()

    start_dt = datetime.combine(from_date, time.min)
    end_dt = datetime.combine(to_date, time(23, 59, 59))

    try:
        historical_df = get_kite_historical_data(
            kite=kite,
            instrument_token=instrument_token.strip(),
            interval=interval,
            from_date=start_dt,
            to_date=end_dt,
            continuous=continuous,
            oi=oi,
        )

        st.subheader("Historical candles")
        if historical_df.empty:
            st.info("No candle data returned for the selected inputs.")
        else:
            st.dataframe(historical_df, width="stretch")
            st.download_button(
                "Download CSV",
                data=historical_df.to_csv(),
                file_name=f"kite_historical_{instrument_token.strip()}_{interval}.csv",
                mime="text/csv",
            )
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
