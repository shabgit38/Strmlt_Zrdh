import streamlit as st
from kiteconnect import KiteConnect
import pandas as pd

# --- 1. Configuration (Move to .streamlit/secrets.toml in production) ---
API_KEY = "8zvvbzhkzewf6r87"
API_SECRET = "kftv0wc8qibyxe851zlynfh7ozgjxpkj"

st.title("Zerodha Holdings ")

# --- 2. Initialize KiteConnect Instance ---
kite = KiteConnect(api_key=API_KEY)

# --- 3. Handle Authentication Flow ---
if "access_token" not in st.session_state:
    # Get request_token from URL (Kite redirects here after login)
    query_params = st.query_params
    request_token = query_params.get("request_token")

    if not request_token:
        # Step A: Show Login Link if no token is present
        login_url = kite.login_url()
        st.info("Please login to Zerodha to continue.")
        st.link_button("Login to Kite", login_url)
    else:
        # Step B: Exchange request_token for access_token
        try:
            data = kite.generate_session(request_token, api_secret=API_SECRET)
            st.session_state.access_token = data["access_token"]
            # Clear query params to clean the URL
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Authentication Failed: {e}")
else:
    # Step C: Use persisted access_token
    kite.set_access_token(st.session_state.access_token)
    
    # --- 4. Fetch and Display Holdings ---
    try:
        holdings = kite.holdings()
        if holdings:
            df = pd.DataFrame(holdings)
            print(df.columns)
            # Data Cleaning for Display
            display_cols = ['tradingsymbol', 'exchange', 'quantity', 'average_price', 'last_price', 'pnl']
            st.subheader("Your Portfolio Holdings")
            st.dataframe(df[display_cols],width='stretch')
            _=""" `use_container_width` will be removed after 2025-12-31.
                For `use_container_width=True`, use `width='stretch'`. 
                For `use_container_width=False`, use `width='content'"""

            # Summary Metrics
            total_pnl = df['pnl'].sum()
            st.metric("Total P&L", f"₹{total_pnl:,.2f}", delta=f"{total_pnl:.2f}")
        else:
            st.warning("No holdings found in this account.")
            
    except Exception as e:
        st.error(f"Error fetching holdings: {e}")
        if "Token" in str(e): # Reset session if token expires
            del st.session_state.access_token
            st.rerun()

# --- 5. Logout Button ---
if "access_token" in st.session_state:
    if st.sidebar.button("Logout"):
        del st.session_state.access_token
        st.rerun()