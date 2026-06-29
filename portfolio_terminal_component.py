from pathlib import Path
import json
import math
from datetime import date
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import streamlit as st
import streamlit.components.v1 as components

from kite_auth import bootstrap_kite_app, clear_auth_state, get_secret_value, is_token_error


_COMPONENT_DIR = Path(__file__).parent / "portfolio_terminal" / "dist"
_CALCULATORS_LIVE_DATA_STATE_KEY = "calculators_terminal_live_data"
_CALCULATORS_LAST_REQUEST_ID_STATE_KEY = "calculators_terminal_last_request_id"
_CALCULATORS_INSTRUMENTS_STATE_KEY = "calculators_terminal_instruments"
_SUPABASE_INSTRUMENT_COLUMNS = ",".join(
    [
        "instrument_token",
        "tradingsymbol",
        "name",
        "expiry",
        "strike",
        "tick_size",
        "lot_size",
        "instrument_type",
        "exchange",
        "segment",
    ]
)
_TARGET_STRIKE_RANGE_PCT = 0.05
_NEAR_TARGET_STRIKE_RANGE_PCT = 0.02
_TARGET_STRIKE_STEP = 100
_FAR_TARGET_STRIKE_STEP = 500
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
_TARGET_OPTION_INDEXES = [
    "NIFTY",
    # "BANKNIFTY",
    # "SENSEX",
]

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


def render_alerts_terminal(alerts_data: dict[str, Any], *, key: str | None = None) -> Any:
    return _portfolio_terminal(
        snapshot=None,
        screen="alerts",
        alertsData=alerts_data,
        key=key,
        default=None,
    )


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
        symbols = sorted(
            {
                str(symbol).upper().strip()
                for symbol in request.get("symbols", [])
                if str(symbol).strip()
            }
        )
        raw_positions = _open_positions(kite)
        position_symbols = [position["symbol"] for position in raw_positions]
        contract_by_symbol = _option_contracts_by_symbol(kite, set(symbols + position_symbols))
        positions = _enrich_open_option_positions(raw_positions, contract_by_symbol)
        instruments: list[str] = []
        if request.get("includeSpots"):
            instruments.extend(_INDEX_SPOT_INSTRUMENTS.values())

        underlying_by_symbol = {symbol: _underlying_instrument_for_option(symbol) for symbol in symbols}
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
            live_data["targetOptions"] = _target_options_from_spots(
                _target_option_contracts_for_spots(kite, live_data["spots"]),
                live_data["spots"],
            )
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


def _open_positions(kite) -> list[dict[str, Any]]:
    positions = kite.positions()
    net_positions = positions.get("net", []) if isinstance(positions, dict) else []
    open_positions: list[dict[str, Any]] = []
    for position in net_positions:
        symbol = str(position.get("tradingsymbol") or "").upper().strip()
        quantity = _float_value(position.get("quantity"))
        if not symbol or not quantity:
            continue
        open_positions.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "averagePrice": _float_value(position.get("average_price")) or 0,
                "lastPrice": _float_value(position.get("last_price")) or 0,
                "pnl": _float_value(position.get("pnl")) or 0,
            }
        )
    return open_positions


def _enrich_open_option_positions(
    positions: list[dict[str, Any]],
    contract_by_symbol: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched_positions: list[dict[str, Any]] = []
    for position in positions:
        symbol = str(position.get("symbol") or "").upper().strip()
        contract = contract_by_symbol.get(symbol)
        if contract is None:
            continue
        enriched_positions.append(
            {
                "symbol": symbol,
                "quantity": position.get("quantity") or 0,
                "averagePrice": position.get("averagePrice") or 0,
                "lastPrice": position.get("lastPrice") or 0,
                "pnl": position.get("pnl") or 0,
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


def _option_contracts_by_symbol(kite, symbols: set[str]) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}

    try:
        contracts = _option_contracts_by_symbol_from_supabase(symbols)
        st.session_state.pop("calculators_terminal_instruments_source_error", None)
    except Exception as exc:
        st.session_state["calculators_terminal_instruments_source_error"] = str(exc)
        fallback_contracts = _load_calculator_option_contracts_from_kite(kite)
        contracts = [contract for contract in fallback_contracts if contract["symbol"] in symbols]
    return {contract["symbol"]: contract for contract in contracts}


def _target_option_contracts_for_spots(kite, spots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        contracts = _target_option_contracts_from_supabase(spots)
        st.session_state.pop("calculators_terminal_instruments_source_error", None)
        return contracts
    except Exception as exc:
        st.session_state["calculators_terminal_instruments_source_error"] = str(exc)
        return _load_calculator_option_contracts_from_kite(kite)


def _option_contracts_by_symbol_from_supabase(symbols: set[str]) -> list[dict[str, Any]]:
    normalized_symbols = sorted({symbol.upper().strip() for symbol in symbols if symbol.strip()})
    if not normalized_symbols:
        return []

    symbol_filter = ",".join(quote(symbol, safe="") for symbol in normalized_symbols)
    return _calculator_option_contracts_from_supabase_filters(
        [
            f"tradingsymbol=in.({symbol_filter})",
            "instrument_type=in.(CE,PE)",
        ]
    )


def _target_option_contracts_from_supabase(spots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    target_indexes = set(_TARGET_OPTION_INDEXES)
    for spot in spots:
        index = str(spot.get("symbol") or "").upper().strip()
        spot_price = _float_value(spot.get("spot"))
        if index not in target_indexes or spot_price is None:
            continue

        min_target, max_target = _target_strike_bounds(spot_price)
        contracts.extend(
            _calculator_option_contracts_from_supabase_filters(
                [
                    f"name=eq.{quote(index, safe='')}",
                    "instrument_type=in.(CE,PE)",
                    f"expiry=gte.{date.today().isoformat()}",
                    f"strike=gte.{min_target:.2f}",
                    f"strike=lte.{max_target:.2f}",
                    "order=strike.asc,expiry.asc",
                ]
            )
        )
    return contracts


def _target_strike_bounds(spot_price: float) -> tuple[float, float]:
    lower = math.floor((spot_price * (1 - _TARGET_STRIKE_RANGE_PCT)) / _TARGET_STRIKE_STEP) * _TARGET_STRIKE_STEP
    upper = math.ceil((spot_price * (1 + _TARGET_STRIKE_RANGE_PCT)) / _TARGET_STRIKE_STEP) * _TARGET_STRIKE_STEP
    return float(lower), float(upper)


def _target_strikes_for_spot(spot_price: float) -> list[float]:
    far_lower = math.ceil((spot_price * (1 - _TARGET_STRIKE_RANGE_PCT)) / _FAR_TARGET_STRIKE_STEP) * _FAR_TARGET_STRIKE_STEP
    far_upper = math.floor((spot_price * (1 + _TARGET_STRIKE_RANGE_PCT)) / _FAR_TARGET_STRIKE_STEP) * _FAR_TARGET_STRIKE_STEP
    lower_near_edge = spot_price * (1 - _NEAR_TARGET_STRIKE_RANGE_PCT)
    upper_near_edge = spot_price * (1 + _NEAR_TARGET_STRIKE_RANGE_PCT)
    near_lower = math.ceil((spot_price * (1 - _NEAR_TARGET_STRIKE_RANGE_PCT)) / _TARGET_STRIKE_STEP) * _TARGET_STRIKE_STEP
    near_upper = math.floor((spot_price * (1 + _NEAR_TARGET_STRIKE_RANGE_PCT)) / _TARGET_STRIKE_STEP) * _TARGET_STRIKE_STEP
    far_below_upper = math.floor(lower_near_edge / _FAR_TARGET_STRIKE_STEP) * _FAR_TARGET_STRIKE_STEP
    far_above_lower = math.ceil(upper_near_edge / _FAR_TARGET_STRIKE_STEP) * _FAR_TARGET_STRIKE_STEP

    strikes = set(range(int(near_lower), int(near_upper) + _TARGET_STRIKE_STEP, _TARGET_STRIKE_STEP))
    strikes.update(
        strike
        for strike in range(int(far_lower), int(far_below_upper) + _FAR_TARGET_STRIKE_STEP, _FAR_TARGET_STRIKE_STEP)
        if strike < near_lower
    )
    strikes.update(
        strike
        for strike in range(int(far_above_lower), int(far_upper) + _FAR_TARGET_STRIKE_STEP, _FAR_TARGET_STRIKE_STEP)
        if strike > near_upper
    )
    return [float(strike) for strike in sorted(strikes)]


def _calculator_option_contracts_from_supabase_filters(filters: list[str]) -> list[dict[str, Any]]:
    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_TABLE_NAME").strip()

    if not supabase_url or not supabase_key or not table_name:
        raise ValueError("Missing Supabase instrument config")

    endpoint = (
        f"{supabase_url}/rest/v1/{quote(table_name, safe='')}"
        f"?select={_SUPABASE_INSTRUMENT_COLUMNS}"
        + "".join(f"&{filter_value}" for filter_value in filters)
    )
    records = _fetch_supabase_records(endpoint, supabase_key)

    contracts: list[dict[str, Any]] = []
    for instrument in records:
        contract = _calculator_option_contract(instrument)
        if contract is not None:
            contracts.append(contract)
    return contracts


def _fetch_supabase_records(endpoint: str, supabase_key: str) -> list[dict[str, Any]]:
    page_size = 1000
    offset = 0
    records: list[dict[str, Any]] = []
    while True:
        request = Request(
            endpoint,
            headers={
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
                "Range": f"{offset}-{offset + page_size - 1}",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=60) as response:
                page_records = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Supabase instruments lookup failed with HTTP {exc.code}: {body or exc.reason}") from exc
        except URLError as exc:
            raise RuntimeError(f"Supabase instruments lookup failed: {exc.reason}") from exc

        if not page_records:
            break
        records.extend(page_records)
        if len(page_records) < page_size:
            break
        offset += page_size
    return records


def _load_calculator_option_contracts_from_kite(kite) -> list[dict[str, Any]]:
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
        for index in _TARGET_OPTION_INDEXES
    }
    for spot in spots:
        index = str(spot.get("symbol") or "").upper()
        spot_price = spot.get("spot")
        if index not in _TARGET_OPTION_INDEXES or spot_price is None:
            continue
        index_contracts = by_index.get(index, [])
        rows: list[dict[str, Any]] = []
        for strike in _target_strikes_for_spot(float(spot_price)):
            ce_contracts = _contracts_for_strike(index_contracts, "CE", strike)
            pe_contracts = _contracts_for_strike(index_contracts, "PE", strike)
            if not ce_contracts and not pe_contracts:
                continue
            rows.append(
                {
                    "index": index,
                    "strike": strike,
                    "ce": ce_contracts[0] if ce_contracts else None,
                    "pe": pe_contracts[0] if pe_contracts else None,
                    "ceContracts": ce_contracts,
                    "peContracts": pe_contracts,
                }
            )
        target_options[index] = rows
    return target_options


def _contracts_for_strike(contracts: list[dict[str, Any]], option_type: str, strike: float) -> list[dict[str, Any]]:
    return sorted(
        [
            contract
            for contract in contracts
            if contract.get("optionType") == option_type
            and _float_value(contract.get("strike")) == strike
        ],
        key=lambda contract: str(contract.get("expiry") or ""),
    )


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
