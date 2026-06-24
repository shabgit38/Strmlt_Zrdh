from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import streamlit as st

from kite_auth import bootstrap_kite_app, clear_auth_state, is_token_error
from portfolio_terminal_component import render_alerts_terminal


ALERTS_STATE_KEY = "kite_alerts_data"
ALERTS_LAST_REQUEST_ID_KEY = "kite_alerts_last_request_id"
ALERTS_LOG_STATE_KEY = "kite_alerts_fetch_log"
ALERTS_DEFAULT_STATUS = "active"
KITE_ALERTS_ENDPOINT = "https://api.kite.trade/alerts"
ALERTS_HTTP_TIMEOUT_SECONDS = 10
ALERTS_DEBUG_LOG_ENABLED = False
ALERT_FIELD_NAMES = [
    "name",
    "type",
    "lhs_exchange",
    "lhs_tradingsymbol",
    "lhs_attribute",
    "operator",
    "rhs_type",
    "rhs_constant",
    "rhs_exchange",
    "rhs_tradingsymbol",
    "rhs_attribute",
]


def render_alerts_tab(*, key: str = "alerts_terminal_component") -> None:
    if ALERTS_STATE_KEY not in st.session_state:
        st.session_state[ALERTS_STATE_KEY] = _initial_alerts_data()

    _render_alerts_log_if_enabled()
    component_value = render_alerts_terminal(st.session_state[ALERTS_STATE_KEY], key=key)
    if not _is_alerts_request(component_value):
        _log_alerts_step_once("Waiting for alerts request from React.")
        return

    request_id = str(component_value.get("requestId") or "")
    _log_alerts_step(
        f"Received React request: action={component_value.get('action') or 'fetch'}, "
        f"filter={component_value.get('statusFilter') or ALERTS_DEFAULT_STATUS}, request_id={request_id or '-'}"
    )
    if request_id and st.session_state.get(ALERTS_LAST_REQUEST_ID_KEY) == request_id:
        _log_alerts_step(f"Ignored duplicate request_id={request_id}.")
        return

    st.session_state[ALERTS_LAST_REQUEST_ID_KEY] = request_id
    with st.spinner("Fetching alerts..."):
        st.session_state[ALERTS_STATE_KEY] = _handle_alerts_request(component_value)
    _log_alerts_step("Stored alerts data in session state; rerunning Streamlit.")
    st.rerun()


def _initial_alerts_data() -> dict[str, Any]:
    return {
        "alerts": [],
        "statusFilter": ALERTS_DEFAULT_STATUS,
        "loaded": False,
    }


def _is_alerts_request(value: Any) -> bool:
    return isinstance(value, dict) and value.get("type") == "alerts"


def _handle_alerts_request(request: dict[str, Any]) -> dict[str, Any]:
    previous_data = st.session_state.get(ALERTS_STATE_KEY, _initial_alerts_data())
    action = str(request.get("action") or "fetch")
    requested_status_filter = str(request.get("statusFilter") or previous_data.get("statusFilter") or ALERTS_DEFAULT_STATUS)
    fetch_status_filter = ALERTS_DEFAULT_STATUS if action in {"create", "modify", "delete"} else requested_status_filter
    next_data = {
        "alerts": list(previous_data.get("alerts") or []),
        "statusFilter": fetch_status_filter,
        "loaded": bool(previous_data.get("loaded")),
        "lastAction": action,
        "lastRequestId": str(request.get("requestId") or ""),
    }

    try:
        _log_alerts_step("Bootstrapping Kite client for alerts.")
        kite, api_key, _ = bootstrap_kite_app("Zerodha Alerts")
        _log_alerts_step("Kite client ready.")
        payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}

        if action == "create":
            _log_alerts_step("Creating alert through Kite Alerts API.")
            create_alert(api_key, st.session_state.access_token, payload)
            next_data["message"] = "Alert created."
        elif action == "modify":
            uuid = str(payload.get("uuid") or "")
            if not uuid:
                raise ValueError("Missing alert uuid for modify.")
            _log_alerts_step(f"Modifying alert uuid={uuid}.")
            modify_alert(api_key, st.session_state.access_token, uuid, payload)
            next_data["message"] = "Alert modified."
        elif action == "delete":
            uuid = str(payload.get("uuid") or "")
            if not uuid:
                raise ValueError("Missing alert uuid for delete.")
            _log_alerts_step(f"Deleting alert uuid={uuid}.")
            delete_alert(api_key, st.session_state.access_token, uuid)
            next_data["message"] = "Alert deleted."

        _log_alerts_step(f"Fetching alerts list with status_filter={fetch_status_filter}.")
        alerts, fetch_meta = get_alerts(api_key, st.session_state.access_token, fetch_status_filter)
        _log_alerts_step(f"Parsed {len(alerts)} alert row(s) from Kite response.")
        next_data["alerts"] = _dedupe_alerts(enrich_alerts_with_ltp(kite, alerts))
        _log_alerts_step(f"Prepared {len(next_data['alerts'])} alert row(s) for React table.")
        if ALERTS_DEBUG_LOG_ENABLED:
            next_data["fetchMeta"] = fetch_meta
            next_data["debug"] = (
                f"{action} handled; Kite returned {len(alerts)} alert(s); "
                f"status filter: {fetch_status_filter}"
            )
        next_data["loaded"] = True
        next_data.pop("error", None)
        if not ALERTS_DEBUG_LOG_ENABLED:
            next_data.pop("fetchMeta", None)
            next_data.pop("debug", None)
    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
        next_data["error"] = str(exc)
        if ALERTS_DEBUG_LOG_ENABLED:
            next_data["debug"] = f"{action} failed before table refresh."
        _log_alerts_step(f"Alerts request failed: {exc}")
    return next_data


def get_alerts(api_key: str, access_token: str, status: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if status in (None, "", "active"):
        enabled_alerts, enabled_meta = _get_alerts_by_status(api_key, access_token, "enabled")
        disabled_alerts, disabled_meta = _get_alerts_by_status(api_key, access_token, "disabled")
        active_alerts = _dedupe_alerts(enabled_alerts + disabled_alerts)
        return active_alerts, {
            "status": "active",
            "statuses": {
                "enabled": enabled_meta,
                "disabled": disabled_meta,
            },
            "pages": list(enabled_meta.get("pages", [])) + list(disabled_meta.get("pages", [])),
            "responseShapes": list(enabled_meta.get("responseShapes", [])) + list(disabled_meta.get("responseShapes", [])),
            "stopReason": f"enabled: {enabled_meta.get('stopReason', '-')}; disabled: {disabled_meta.get('stopReason', '-')}",
        }

    alerts, meta = _get_alerts_by_status(api_key, access_token, status)
    return _dedupe_alerts(alerts), meta


def _get_alerts_by_status(api_key: str, access_token: str, status: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    query = {"status": status}
    endpoint = f"{KITE_ALERTS_ENDPOINT}?{urlencode(query)}"
    _log_alerts_step(f"Calling Kite Alerts API status={status}, timeout={ALERTS_HTTP_TIMEOUT_SECONDS}s.")
    response = _kite_alerts_request(api_key, access_token, endpoint)
    response_alerts = _extract_alert_rows(response)
    filtered_alerts = [
        alert
        for alert in response_alerts
        if str(alert.get("status") or "").lower().strip() == status
    ]
    meta: dict[str, Any] = {
        "status": status,
        "pages": [{"page": 1, "count": len(filtered_alerts), "rawCount": len(response_alerts)}],
        "responseShapes": [_response_shape(response)],
        "stopReason": "single status fetch",
    }
    _log_alerts_step(
        f"Kite status={status} response raw_count={len(response_alerts)}, filtered_count={len(filtered_alerts)}."
    )
    return filtered_alerts, meta


def _dedupe_alerts(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped_alerts: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for alert in alerts:
        dedupe_key = _alert_dedupe_key(alert)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        deduped_alerts.append(alert)
    return deduped_alerts


def _alert_dedupe_key(alert: dict[str, Any]) -> str:
    uuid = str(alert.get("uuid") or "").strip()
    if uuid:
        return f"uuid:{uuid}"
    return "fallback:" + "|".join(
        str(alert.get(field_name) or "").strip().lower()
        for field_name in [
            "name",
            "status",
            "lhs_exchange",
            "lhs_tradingsymbol",
            "lhs_attribute",
            "operator",
            "rhs_type",
            "rhs_constant",
            "rhs_exchange",
            "rhs_tradingsymbol",
            "rhs_attribute",
        ]
    )


def _extract_alert_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    data = response.get("data", [])
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("alerts", "items", "results"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _response_shape(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data")
    if isinstance(data, dict):
        data_shape: Any = {"type": "dict", "keys": sorted(data.keys())}
    elif isinstance(data, list):
        data_shape = {"type": "list", "length": len(data)}
    else:
        data_shape = {"type": type(data).__name__}
    return {
        "topLevelKeys": sorted(response.keys()),
        "status": response.get("status"),
        "data": data_shape,
    }


def create_alert(api_key: str, access_token: str, fields: dict[str, Any]) -> dict[str, Any]:
    payload = _alert_payload(fields)
    response = _kite_alerts_request(api_key, access_token, KITE_ALERTS_ENDPOINT, method="POST", payload=payload)
    return response.get("data", {}) if isinstance(response.get("data"), dict) else {}


def modify_alert(api_key: str, access_token: str, uuid: str, fields: dict[str, Any]) -> dict[str, Any]:
    payload = _alert_payload(fields)
    endpoint = f"{KITE_ALERTS_ENDPOINT}/{uuid}"
    response = _kite_alerts_request(api_key, access_token, endpoint, method="PUT", payload=payload)
    return response.get("data", {}) if isinstance(response.get("data"), dict) else {}


def delete_alert(api_key: str, access_token: str, uuid: str) -> None:
    endpoint = f"{KITE_ALERTS_ENDPOINT}?{urlencode({'uuid': uuid})}"
    _kite_alerts_request(api_key, access_token, endpoint, method="DELETE")


def enrich_alerts_with_ltp(kite: Any, alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    instruments = sorted(
        {
            _quote_instrument_for_alert(alert)
            for alert in alerts
            if _quote_instrument_for_alert(alert)
        }
    )
    try:
        _log_alerts_step(f"Fetching LTP for {len(instruments)} alert instrument(s).")
        quotes = kite.ltp(*instruments) if instruments else {}
        _log_alerts_step(f"Received LTP quotes for {len(quotes) if isinstance(quotes, dict) else 0} instrument(s).")
    except Exception as exc:
        st.session_state["kite_alerts_ltp_error"] = str(exc)
        _log_alerts_step(f"LTP enrichment failed; continuing without LTP: {exc}")
        quotes = {}

    enriched_alerts: list[dict[str, Any]] = []
    for alert in alerts:
        next_alert = dict(alert)
        instrument = _quote_instrument_for_alert(alert)
        quote = quotes.get(instrument, {}) if isinstance(quotes, dict) else {}
        next_alert["ltp"] = quote.get("last_price") if isinstance(quote, dict) else None
        enriched_alerts.append(next_alert)
    return enriched_alerts


def _quote_instrument_for_alert(alert: dict[str, Any]) -> str:
    exchange = str(alert.get("lhs_exchange") or "").upper().strip()
    tradingsymbol = str(alert.get("lhs_tradingsymbol") or "").strip()
    if not exchange or not tradingsymbol:
        return ""
    if exchange == "INDICES":
        symbol_lookup = {
            "NIFTY 50": "NSE:NIFTY 50",
            "NIFTY BANK": "NSE:NIFTY BANK",
            "SENSEX": "BSE:SENSEX",
        }
        return symbol_lookup.get(tradingsymbol.upper(), f"NSE:{tradingsymbol}")
    return f"{exchange}:{tradingsymbol}"


def _alert_payload(fields: dict[str, Any]) -> dict[str, str]:
    payload: dict[str, str] = {}
    for field_name in ALERT_FIELD_NAMES:
        value = fields.get(field_name)
        if value is None:
            continue
        text_value = str(value).strip()
        if field_name == "type" and not text_value:
            text_value = "simple"
        if field_name == "lhs_attribute" and not text_value:
            text_value = "LastTradedPrice"
        if field_name == "rhs_type" and not text_value:
            text_value = "constant"
        if text_value:
            payload[field_name] = text_value

    payload.setdefault("type", "simple")
    payload.setdefault("lhs_attribute", "LastTradedPrice")
    payload.setdefault("rhs_type", "constant")

    required_fields = ["name", "lhs_exchange", "lhs_tradingsymbol", "operator", "rhs_type", "type"]
    missing_fields = [field_name for field_name in required_fields if not payload.get(field_name)]
    if missing_fields:
        raise ValueError(f"Missing alert field(s): {', '.join(missing_fields)}")
    if payload["rhs_type"] == "constant" and not payload.get("rhs_constant"):
        raise ValueError("Missing rhs_constant for constant alert.")
    return payload


def _kite_alerts_request(
    api_key: str,
    access_token: str,
    endpoint: str,
    *,
    method: str = "GET",
    payload: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = urlencode(payload or {}).encode("utf-8") if payload is not None else None
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {api_key}:{access_token}",
    }
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = Request(endpoint, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=ALERTS_HTTP_TIMEOUT_SECONDS) as response:
            body_text = response.read().decode("utf-8")
            _log_alerts_step(f"Kite Alerts API HTTP {response.status}; response_bytes={len(body_text)}.")
            return json.loads(body_text)
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Kite alerts API failed with HTTP {exc.code}: {error_body or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Kite alerts API failed: {exc.reason}") from exc


def _log_alerts_step(message: str) -> None:
    if not ALERTS_DEBUG_LOG_ENABLED:
        return
    logs = list(st.session_state.get(ALERTS_LOG_STATE_KEY, []))
    logs.append(message)
    st.session_state[ALERTS_LOG_STATE_KEY] = logs[-80:]


def _log_alerts_step_once(message: str) -> None:
    if not ALERTS_DEBUG_LOG_ENABLED:
        return
    logs = list(st.session_state.get(ALERTS_LOG_STATE_KEY, []))
    if logs and logs[-1] == message:
        return
    _log_alerts_step(message)


def _render_alerts_log_if_enabled() -> None:
    if not ALERTS_DEBUG_LOG_ENABLED:
        return

    logs = st.session_state.get(ALERTS_LOG_STATE_KEY, [])
    with st.expander("Alerts fetch log", expanded=True):
        if st.button("Clear alerts log", key="clear_alerts_fetch_log"):
            st.session_state[ALERTS_LOG_STATE_KEY] = []
            st.rerun()
        if not logs:
            st.caption("No alerts fetch steps logged yet.")
        else:
            for index, message in enumerate(logs[-40:], start=max(1, len(logs) - 39)):
                st.caption(f"{index}. {message}")
