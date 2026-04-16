import logging
import os

import streamlit as st
from kiteconnect import KiteConnect

try:
    from kiteconnect.exceptions import TokenException
except ImportError:  # pragma: no cover - depends on kiteconnect version
    TokenException = None


logger = logging.getLogger(__name__)


def get_secret_value(secret_name: str) -> str:
    """Load secrets from Streamlit secrets first, then environment variables."""
    if secret_name in st.secrets:
        return st.secrets[secret_name]
    return os.getenv(secret_name, "")


def get_query_param_value(param_name: str) -> str:
    """Return the first non-empty query param value as a string."""
    value = st.query_params.get(param_name, "")
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


def is_token_error(exc: Exception) -> bool:
    """Detect Kite token/session expiry errors without depending on message text."""
    if TokenException is not None and isinstance(exc, TokenException):
        return True

    exc_name = exc.__class__.__name__.lower()
    return "token" in exc_name or "session" in exc_name


def clear_auth_state() -> None:
    """Remove any local auth state before forcing a fresh login."""
    st.session_state.pop("access_token", None)
    st.query_params.clear()


def bootstrap_kite_app(page_title: str) -> tuple[KiteConnect, str, str]:
    """Render the common Kite auth flow and return an authenticated client."""
    api_key = get_secret_value("ZERODHA_API_KEY")
    api_secret = get_secret_value("ZERODHA_API_SECRET")

    st.title(page_title)

    if not api_key or not api_secret:
        st.error(
            "Missing credentials. Set ZERODHA_API_KEY and ZERODHA_API_SECRET in "
            ".streamlit/secrets.toml or environment variables."
        )
        st.stop()

    kite = KiteConnect(api_key=api_key)

    if "access_token" not in st.session_state:
        request_token = get_query_param_value("request_token")

        if not request_token:
            login_url = kite.login_url()
            print(f"Login URL: {login_url}")
            st.info("Please login to Zerodha to continue.")
            st.link_button("Login to Kite", login_url)
            st.stop()

        try:
            data = kite.generate_session(request_token, api_secret)
            st.session_state.access_token = data["access_token"]
            st.query_params.clear()
            st.rerun()
        except Exception as exc:
            logger.exception("Authentication failed while exchanging request_token")
            if is_token_error(exc):
                clear_auth_state()
                st.error("Your Kite login session expired or is invalid. Please login again.")
            else:
                st.error("Authentication failed. Please try logging in again.")
            st.stop()

    kite.set_access_token(st.session_state.access_token)
    return kite, api_key, api_secret
