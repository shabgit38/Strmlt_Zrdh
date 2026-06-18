from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from kite_auth import bootstrap_kite_app, clear_auth_state, is_token_error


_COMPONENT_DIR = Path(__file__).parent / "portfolio_terminal" / "dist"
_CALCULATORS_LIVE_DATA_STATE_KEY = "calculators_terminal_live_data"
_CALCULATORS_LAST_REQUEST_ID_STATE_KEY = "calculators_terminal_last_request_id"
_INDEX_SPOT_INSTRUMENTS = {
    "NIFTY": "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
    "SENSEX": "BSE:SENSEX",
}
_INDEX_UNDERLYING_INSTRUMENTS = {
    "NIFTY": "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
    "SENSEX": "BSE:SENSEX",
}

_portfolio_terminal = components.declare_component(
    "portfolio_terminal",
    path=str(_COMPONENT_DIR),
)


def render_portfolio_terminal(snapshot: dict[str, Any], *, key: str | None = None) -> None:
    _portfolio_terminal(snapshot=snapshot, screen="portfolio", key=key, default=None)


def render_calculators_terminal(*, key: str | None = None) -> None:
    if _CALCULATORS_LIVE_DATA_STATE_KEY not in st.session_state:
        st.session_state[_CALCULATORS_LIVE_DATA_STATE_KEY] = {
            "spots": _missing_spots(),
            "options": {},
        }

    component_value = _portfolio_terminal(
        snapshot=None,
        screen="calculators",
        liveData=st.session_state[_CALCULATORS_LIVE_DATA_STATE_KEY],
        key=key,
        default=None,
    )
    if not _is_market_data_request(component_value):
        return

    request_id = str(component_value.get("requestId") or "")
    if request_id and st.session_state.get(_CALCULATORS_LAST_REQUEST_ID_STATE_KEY) == request_id:
        return

    st.session_state[_CALCULATORS_LAST_REQUEST_ID_STATE_KEY] = request_id
    st.session_state[_CALCULATORS_LIVE_DATA_STATE_KEY] = _fetch_calculators_live_data(component_value)
    st.rerun()


def _missing_spots() -> list[dict[str, Any]]:
    return [{"symbol": symbol, "spot": None, "status": "Missing"} for symbol in _INDEX_SPOT_INSTRUMENTS]


def _is_market_data_request(value: Any) -> bool:
    return isinstance(value, dict) and value.get("type") == "marketData"


def _fetch_calculators_live_data(request: dict[str, Any]) -> dict[str, Any]:
    previous_data = st.session_state.get(_CALCULATORS_LIVE_DATA_STATE_KEY, {})
    live_data: dict[str, Any] = {
        "requestId": request.get("requestId"),
        "spots": previous_data.get("spots") or _missing_spots(),
        "options": dict(previous_data.get("options") or {}),
    }

    try:
        kite, _, _ = bootstrap_kite_app("Zerodha Calculators")
        symbols = sorted(
            {
                str(symbol).upper().strip()
                for symbol in request.get("symbols", [])
                if str(symbol).strip()
            }
        )
        instruments: list[str] = []
        if request.get("includeSpots"):
            instruments.extend(_INDEX_SPOT_INSTRUMENTS.values())

        underlying_by_symbol = {symbol: _underlying_instrument_for_option(symbol) for symbol in symbols}
        for symbol in symbols:
            instruments.append(_option_quote_instrument(symbol))
            underlying = underlying_by_symbol.get(symbol)
            if underlying:
                instruments.append(underlying)

        quotes = kite.ltp(*sorted(set(instruments))) if instruments else {}
        if request.get("includeSpots"):
            live_data["spots"] = _spots_from_quotes(quotes)

        for symbol in symbols:
            option_quote = quotes.get(_option_quote_instrument(symbol), {})
            underlying_quote = quotes.get(underlying_by_symbol.get(symbol, ""), {})
            quote_payload: dict[str, Any] = {"symbol": symbol}
            if isinstance(option_quote, dict) and option_quote.get("last_price") is not None:
                quote_payload["ltp"] = float(option_quote["last_price"])
            if isinstance(underlying_quote, dict) and underlying_quote.get("last_price") is not None:
                quote_payload["spot"] = float(underlying_quote["last_price"])
            expiry = _expiry_from_monthly_option_symbol(symbol)
            if expiry:
                quote_payload["expiry"] = expiry
            live_data["options"][symbol] = quote_payload

        live_data["fetchedAt"] = st.session_state.get("kite_holdings_fetched_at")
        live_data.pop("error", None)
    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
        live_data["error"] = str(exc)

    return live_data


def _spots_from_quotes(quotes: dict[str, Any]) -> list[dict[str, Any]]:
    spots: list[dict[str, Any]] = []
    for symbol, instrument in _INDEX_SPOT_INSTRUMENTS.items():
        quote = quotes.get(instrument, {})
        if isinstance(quote, dict) and quote.get("last_price") is not None:
            spots.append({"symbol": symbol, "spot": float(quote["last_price"]), "status": "Live"})
        else:
            spots.append({"symbol": symbol, "spot": None, "status": "Missing"})
    return spots


def _option_quote_instrument(symbol: str) -> str:
    exchange = "BFO" if symbol.startswith("SENSEX") else "NFO"
    return f"{exchange}:{symbol}"


def _underlying_instrument_for_option(symbol: str) -> str | None:
    for underlying, instrument in sorted(_INDEX_UNDERLYING_INSTRUMENTS.items(), key=lambda item: len(item[0]), reverse=True):
        if symbol.startswith(underlying):
            return instrument
    return None


def _expiry_from_monthly_option_symbol(symbol: str) -> str | None:
    import calendar
    import re
    from datetime import date

    match = re.match(r"^.+?(\d{2})([A-Z]{3})\d+(?:\.\d+)?(?:CE|PE)$", symbol.upper())
    if not match:
        return None

    month_lookup = {month.upper(): index for index, month in enumerate(calendar.month_abbr) if month}
    month = month_lookup.get(match.group(2))
    if month is None:
        return None

    expiry = date(2000 + int(match.group(1)), month, calendar.monthrange(2000 + int(match.group(1)), month)[1])
    while expiry.weekday() != 1:
        expiry = date.fromordinal(expiry.toordinal() - 1)
    return expiry.isoformat()
