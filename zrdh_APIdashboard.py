import pandas as pd
import streamlit as st

from kite_auth import bootstrap_kite_app, clear_auth_state, is_token_error


kite, _, _ = bootstrap_kite_app("Zerodha Holdings")

try:
    holdings = kite.holdings()
    if holdings:
        df = pd.DataFrame(holdings)
        display_cols = [
            "tradingsymbol",
            "exchange",
            "price",
            "quantity",
            "average_price",
            "last_price",
            "day_change_percentage",
            "pnl",
        ]
        st.subheader("Your Portfolio Holdings")
        st.dataframe(df[display_cols], width="stretch")

        total_pnl = df["pnl"].sum()
        st.metric("Total P&L", f"₹{total_pnl:,.2f}", delta=f"{total_pnl:.2f}")
    else:
        st.warning("No holdings found in this account.")
except Exception as exc:
    if is_token_error(exc):
        clear_auth_state()
        st.error("Your session expired. Please login again to view holdings.")
        st.rerun()
    st.error("Error fetching holdings. Please try again.")


if "access_token" in st.session_state:
    if st.sidebar.button("Logout"):
        clear_auth_state()
        st.rerun()
