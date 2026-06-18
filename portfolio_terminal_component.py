from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from kite_auth import bootstrap_kite_app, clear_auth_state, is_token_error


_COMPONENT_DIR = Path(__file__).parent / "portfolio_terminal" / "dist"
_CALCULATORS_LIVE_DATA_STATE_KEY = "calculators_terminal_live_data"
_CALCULATORS_LAST_REQUEST_ID_STATE_KEY = "calculators_terminal_last_request_id"
_CALCULATORS_INSTRUMENTS_STATE_KEY = "calculators_terminal_instruments"
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
            "targetOptions": {},
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
        "targetOptions": dict(previous_data.get("targetOptions") or {}),
        "positions": previous_data.get("positions") or [],
    }

    try:
        kite, _, _ = bootstrap_kite_app("Zerodha Calculators")
        option_contracts = _load_calculator_option_contracts(kite)
        symbols = sorted(
            {
                str(symbol).upper().strip()
                for symbol in request.get("symbols", [])
                if str(symbol).strip()
            }
        )
        positions = _open_option_positions(kite, option_contracts)
        instruments: list[str] = []
        if request.get("includeSpots"):
            instruments.extend(_INDEX_SPOT_INSTRUMENTS.values())

        underlying_by_symbol = {symbol: _underlying_instrument_for_option(symbol) for symbol in symbols}
        contract_by_symbol = {contract["symbol"]: contract for contract in option_contracts}
        position_underlying_by_symbol = {
            position["symbol"]: _underlying_instrument_for_option(position["symbol"])
            for position in positions
        }
        for symbol in symbols:
            instruments.append(_option_quote_instrument(symbol, contract_by_symbol.get(symbol)))
            underlying = underlying_by_symbol.get(symbol)
            if underlying:
                instruments.append(underlying)
        for underlying in position_underlying_by_symbol.values():
            if underlying:
                instruments.append(underlying)

        quotes = kite.ltp(*sorted(set(instruments))) if instruments else {}
        if request.get("includeSpots"):
            live_data["spots"] = _spots_from_quotes(quotes)
            live_data["targetOptions"] = _target_options_from_spots(option_contracts, live_data["spots"])
        live_data["positions"] = _positions_with_spot(positions, position_underlying_by_symbol, quotes)

        for symbol in symbols:
            contract = contract_by_symbol.get(symbol)
            option_quote = quotes.get(_option_quote_instrument(symbol, contract), {})
            underlying_quote = quotes.get(underlying_by_symbol.get(symbol, ""), {})
            quote_payload: dict[str, Any] = {"symbol": symbol}
            if isinstance(option_quote, dict) and option_quote.get("last_price") is not None:
                quote_payload["ltp"] = float(option_quote["last_price"])
            if isinstance(underlying_quote, dict) and underlying_quote.get("last_price") is not None:
                quote_payload["spot"] = float(underlying_quote["last_price"])
            if contract:
                quote_payload.update(
                    {
                        "expiry": contract.get("expiry"),
                        "strike": contract.get("strike"),
                        "optionType": contract.get("optionType"),
                        "lotSize": contract.get("lotSize"),
                    }
                )
            elif expiry := _expiry_from_monthly_option_symbol(symbol):
                quote_payload["expiry"] = expiry
            live_data["options"][symbol] = quote_payload

        live_data["fetchedAt"] = st.session_state.get("kite_holdings_fetched_at")
        live_data.pop("error", None)
    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
        live_data["error"] = str(exc)

    return live_data


def _open_option_positions(kite, option_contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contract_by_symbol = {contract["symbol"]: contract for contract in option_contracts}
    positions = kite.positions()
    net_positions = positions.get("net", []) if isinstance(positions, dict) else []
    enriched_positions: list[dict[str, Any]] = []
    for position in net_positions:
        symbol = str(position.get("tradingsymbol") or "").upper().strip()
        quantity = _float_value(position.get("quantity"))
        if not symbol or not quantity:
            continue
        contract = contract_by_symbol.get(symbol)
        if contract is None:
            continue
        enriched_positions.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "averagePrice": _float_value(position.get("average_price")) or 0,
                "lastPrice": _float_value(position.get("last_price")) or 0,
                "pnl": _float_value(position.get("pnl")) or 0,
                "expiry": contract.get("expiry"),
                "strike": contract.get("strike"),
                "optionType": contract.get("optionType"),
                "lotSize": contract.get("lotSize"),
            }
        )
    return enriched_positions


def _positions_with_spot(
    positions: list[dict[str, Any]],
    underlying_by_symbol: dict[str, str | None],
    quotes: dict[str, Any],
) -> list[dict[str, Any]]:
    next_positions: list[dict[str, Any]] = []
    for position in positions:
        next_position = dict(position)
        underlying = underlying_by_symbol.get(str(position.get("symbol") or ""))
        quote = quotes.get(underlying or "", {})
        if isinstance(quote, dict) and quote.get("last_price") is not None:
            next_position["spot"] = float(quote["last_price"])
        next_positions.append(next_position)
    return next_positions


def _float_value(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_calculator_option_contracts(kite) -> list[dict[str, Any]]:
    if _CALCULATORS_INSTRUMENTS_STATE_KEY in st.session_state:
        return st.session_state[_CALCULATORS_INSTRUMENTS_STATE_KEY]

    contracts: list[dict[str, Any]] = []
    for exchange in ["NFO", "BFO"]:
        for instrument in kite.instruments(exchange):
            contract = _calculator_option_contract(instrument)
            if contract is not None:
                contracts.append(contract)

    st.session_state[_CALCULATORS_INSTRUMENTS_STATE_KEY] = contracts
    return contracts


def _calculator_option_contract(instrument: dict[str, Any]) -> dict[str, Any] | None:
    from datetime import date, datetime

    instrument_type = str(instrument.get("instrument_type") or "").upper()
    if instrument_type not in {"CE", "PE"}:
        return None

    name = str(instrument.get("name") or "").upper().strip()
    if name not in _INDEX_SPOT_INSTRUMENTS:
        return None

    expiry_value = instrument.get("expiry")
    if isinstance(expiry_value, datetime):
        expiry_date = expiry_value.date()
    elif isinstance(expiry_value, date):
        expiry_date = expiry_value
    elif expiry_value:
        try:
            expiry_date = datetime.fromisoformat(str(expiry_value)).date()
        except ValueError:
            return None
    else:
        return None

    if expiry_date < date.today():
        return None

    tradingsymbol = str(instrument.get("tradingsymbol") or "").upper().strip()
    if not tradingsymbol:
        return None

    return {
        "index": name,
        "symbol": tradingsymbol,
        "expiry": expiry_date.isoformat(),
        "strike": float(instrument.get("strike") or 0),
        "optionType": instrument_type,
        "lotSize": int(float(instrument.get("lot_size") or 0)),
        "exchange": str(instrument.get("exchange") or "").upper(),
        "segment": str(instrument.get("segment") or "").upper(),
        "instrumentToken": int(float(instrument.get("instrument_token") or 0)),
    }


def _target_options_from_spots(option_contracts: list[dict[str, Any]], spots: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    target_options: dict[str, list[dict[str, Any]]] = {}
    by_index = {
        index: [contract for contract in option_contracts if contract.get("index") == index]
        for index in _INDEX_SPOT_INSTRUMENTS
    }
    for spot in spots:
        index = str(spot.get("symbol") or "").upper()
        spot_price = spot.get("spot")
        if index not in _INDEX_SPOT_INSTRUMENTS or spot_price is None:
            continue
        target_options[index] = [
            {
                "index": index,
                "distancePct": distance * 100,
                "ce": _nearest_contract(by_index.get(index, []), "CE", float(spot_price) * (1 + distance)),
                "pe": _nearest_contract(by_index.get(index, []), "PE", float(spot_price) * (1 - distance)),
                "ceContracts": _nearest_contracts_by_expiry(
                    by_index.get(index, []), "CE", float(spot_price) * (1 + distance)
                ),
                "peContracts": _nearest_contracts_by_expiry(
                    by_index.get(index, []), "PE", float(spot_price) * (1 - distance)
                ),
            }
            for distance in [0.02, 0.03, 0.05]
        ]
    return target_options


def _nearest_contracts_by_expiry(contracts: list[dict[str, Any]], option_type: str, target_strike: float) -> list[dict[str, Any]]:
    matching_contracts = [contract for contract in contracts if contract.get("optionType") == option_type]
    contracts_by_expiry: dict[str, list[dict[str, Any]]] = {}
    for contract in matching_contracts:
        contracts_by_expiry.setdefault(str(contract.get("expiry")), []).append(contract)

    return [
        min(expiry_contracts, key=lambda contract: abs(float(contract.get("strike") or 0) - target_strike))
        for _, expiry_contracts in sorted(contracts_by_expiry.items())
    ]


def _nearest_contract(contracts: list[dict[str, Any]], option_type: str, target_strike: float) -> dict[str, Any] | None:
    matching_contracts = [contract for contract in contracts if contract.get("optionType") == option_type]
    if not matching_contracts:
        return None

    earliest_expiry = min(contract["expiry"] for contract in matching_contracts)
    expiry_contracts = [contract for contract in matching_contracts if contract["expiry"] == earliest_expiry]
    return min(expiry_contracts, key=lambda contract: abs(float(contract.get("strike") or 0) - target_strike))


def _spots_from_quotes(quotes: dict[str, Any]) -> list[dict[str, Any]]:
    spots: list[dict[str, Any]] = []
    for symbol, instrument in _INDEX_SPOT_INSTRUMENTS.items():
        quote = quotes.get(instrument, {})
        if isinstance(quote, dict) and quote.get("last_price") is not None:
            spots.append({"symbol": symbol, "spot": float(quote["last_price"]), "status": "Live"})
        else:
            spots.append({"symbol": symbol, "spot": None, "status": "Missing"})
    return spots


def _option_quote_instrument(symbol: str, contract: dict[str, Any] | None = None) -> str:
    exchange = str((contract or {}).get("exchange") or ("BFO" if symbol.startswith("SENSEX") else "NFO")).upper()
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
