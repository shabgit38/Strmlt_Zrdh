import pandas as pd
import streamlit as st
from kiteconnect import KiteConnect

from kite_auth import bootstrap_kite_app, clear_auth_state, is_token_error


@st.cache_data(ttl=24 * 60 * 60)
def fetch_instruments_dump(api_key: str, access_token: str) -> pd.DataFrame:
    """Fetch the daily instrument dump and cache it for one day."""
    client = KiteConnect(api_key=api_key)
    client.set_access_token(access_token)
    return pd.DataFrame(client.instruments())


_, API_KEY, _ = bootstrap_kite_app("Zerodha Instrument Dump")

st.caption("Daily instrument CSV dump from Kite. It is useful for lookup and database import.")
if st.button("Load instrument list"):
    try:
        instruments_df = fetch_instruments_dump(API_KEY, st.session_state.access_token)
        st.success(f"Loaded {len(instruments_df):,} instruments.")
        st.dataframe(instruments_df.head(100), width="stretch")
        st.download_button(
            "Download full CSV",
            data=instruments_df.to_csv(index=False),
            file_name=f"kite_instruments_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
        )
    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
            st.error("Your session expired. Please login again to load instruments.")
            st.rerun()
        st.error("Error loading instrument list. Please try again.")


if "access_token" in st.session_state:
    if st.sidebar.button("Logout"):
        clear_auth_state()
        st.rerun()
