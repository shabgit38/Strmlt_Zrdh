from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st

from kite_analytics import build_metric_values, calculate_distance_pct, load_analytics_history, pivot_points
from kite_auth import bootstrap_kite_app, clear_auth_state, is_token_error
from kite_auth import get_secret_value
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
    fetch_status_filter = ALERTS_DEFAULT_STATUS if action == "create" else requested_status_filter
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
            created_alert = create_alert(api_key, st.session_state.access_token, payload)
            created_uuid = _created_alert_uuid(created_alert)
            if created_uuid:
                next_data["alerts"] = _append_created_alert_row(next_data["alerts"], created_uuid, payload, created_alert)
                next_data["message"] = "Alert created."
                next_data["loaded"] = True
                next_data.pop("error", None)
                if not ALERTS_DEBUG_LOG_ENABLED:
                    next_data.pop("fetchMeta", None)
                    next_data.pop("debug", None)
                return next_data
            next_data["message"] = "Alert created."
        elif action == "modify":
            uuid = str(payload.get("uuid") or "")
            if not uuid:
                raise ValueError("Missing alert uuid for modify.")
            _log_alerts_step(f"Modifying alert uuid={uuid}.")
            modify_alert(api_key, st.session_state.access_token, uuid, payload)
            next_data["alerts"] = _patch_modified_alert_row(next_data["alerts"], uuid, payload)
            next_data["message"] = "Alert modified."
            next_data["loaded"] = True
            next_data.pop("error", None)
            if not ALERTS_DEBUG_LOG_ENABLED:
                next_data.pop("fetchMeta", None)
                next_data.pop("debug", None)
            return next_data
        elif action == "delete":
            uuid = str(payload.get("uuid") or "")
            if not uuid:
                raise ValueError("Missing alert uuid for delete.")
            _log_alerts_step(f"Deleting alert uuid={uuid}.")
            delete_alert(api_key, st.session_state.access_token, uuid)
            next_data["alerts"] = _remove_deleted_alert_row(next_data["alerts"], uuid)
            next_data["message"] = "Alert deleted."
            next_data["loaded"] = True
            next_data.pop("error", None)
            if not ALERTS_DEBUG_LOG_ENABLED:
                next_data.pop("fetchMeta", None)
                next_data.pop("debug", None)
            return next_data

        _log_alerts_step(f"Fetching alerts list with status_filter={fetch_status_filter}.")
        alerts, fetch_meta = get_alerts(api_key, st.session_state.access_token, fetch_status_filter)
        _log_alerts_step(f"Parsed {len(alerts)} alert row(s) from Kite response.")
        ltp_enriched_alerts = enrich_alerts_with_ltp(kite, alerts)
        next_data["alerts"] = _dedupe_alerts(enrich_alerts_with_price_context(kite, ltp_enriched_alerts))
        next_data["disabledSymbolsText"] = _disabled_alert_symbols_text(next_data["alerts"])
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


def _patch_modified_alert_row(
    alerts: list[dict[str, Any]],
    uuid: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    patched_alerts: list[dict[str, Any]] = []
    found = False
    payload_fields = _alert_payload(payload)
    for alert in alerts:
        next_alert = dict(alert)
        if str(next_alert.get("uuid") or "").strip() == uuid:
            found = True
            next_alert.update(payload_fields)
            next_alert["uuid"] = uuid
            next_alert["status"] = "enabled"
            next_alert.setdefault("alert_count", alert.get("alert_count", 0))
            if _alert_symbol_changed(alert, next_alert):
                next_alert["ltp"] = None
                next_alert["price_context"] = None
        patched_alerts.append(next_alert)

    if found:
        return _dedupe_alerts(patched_alerts)

    fallback_alert = dict(payload_fields)
    fallback_alert["uuid"] = uuid
    fallback_alert.setdefault("status", "enabled")
    fallback_alert.setdefault("alert_count", 0)
    fallback_alert["ltp"] = None
    fallback_alert["price_context"] = None
    return _dedupe_alerts(patched_alerts + [fallback_alert])


def _append_created_alert_row(
    alerts: list[dict[str, Any]],
    uuid: str,
    payload: dict[str, Any],
    created_alert: dict[str, Any],
) -> list[dict[str, Any]]:
    payload_fields = _alert_payload(payload)
    next_alert = {
        **payload_fields,
        **{key: value for key, value in created_alert.items() if value is not None},
        "uuid": uuid,
    }
    next_alert.setdefault("status", "enabled")
    next_alert.setdefault("alert_count", 0)
    next_alert.setdefault("ltp", None)
    next_alert.setdefault("price_context", None)
    return _dedupe_alerts(list(alerts) + [next_alert])


def _created_alert_uuid(created_alert: dict[str, Any]) -> str:
    if not isinstance(created_alert, dict):
        return ""
    for key in ["uuid", "id", "alert_id"]:
        value = str(created_alert.get(key) or "").strip()
        if value:
            return value
    return ""


def _remove_deleted_alert_row(alerts: list[dict[str, Any]], uuid: str) -> list[dict[str, Any]]:
    return [
        dict(alert)
        for alert in alerts
        if str(alert.get("uuid") or "").strip() != uuid
    ]


def _alert_symbol_changed(previous_alert: dict[str, Any], next_alert: dict[str, Any]) -> bool:
    return any(
        str(previous_alert.get(field_name) or "").strip().upper()
        != str(next_alert.get(field_name) or "").strip().upper()
        for field_name in ["lhs_exchange", "lhs_tradingsymbol"]
    )


def _disabled_alert_symbols_text(alerts: list[dict[str, Any]]) -> str:
    symbols = {
        str(alert.get("lhs_tradingsymbol") or "").strip()
        for alert in alerts
        if str(alert.get("status") or "").lower().strip() == "disabled"
        and str(alert.get("lhs_tradingsymbol") or "").strip()
    }
    return ", ".join(sorted(symbols))


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


def enrich_alerts_with_price_context(kite: Any, alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    symbols = sorted(
        {
            str(alert.get("lhs_tradingsymbol") or "").strip().upper()
            for alert in alerts
            if str(alert.get("lhs_tradingsymbol") or "").strip()
        }
    )
    if not symbols:
        return alerts

    try:
        _log_alerts_step(f"Loading instrument tokens for {len(symbols)} alert symbol(s).")
        instrument_df = _load_alert_instrument_tokens(symbols)
    except Exception as exc:
        st.session_state["kite_alerts_price_context_error"] = str(exc)
        _log_alerts_step(f"Price context token lookup failed; continuing without context: {exc}")
        return alerts

    as_of_date = pd.Timestamp.now().date().isoformat()
    enriched_alerts: list[dict[str, Any]] = []
    for alert in alerts:
        next_alert = dict(alert)
        token = _resolve_alert_instrument_token(alert, instrument_df)
        if token is None:
            next_alert["price_context"] = None
            enriched_alerts.append(next_alert)
            continue

        try:
            analytics_df = load_analytics_history(kite, token, as_of_date)
            next_alert["price_context"] = _format_alert_price_context(analytics_df, next_alert.get("ltp"))
        except Exception as exc:
            next_alert["price_context"] = None
            _log_alerts_step(
                f"Price context failed for {next_alert.get('lhs_tradingsymbol') or '-'}; continuing: {exc}"
            )
        enriched_alerts.append(next_alert)
    return enriched_alerts


@st.cache_data(ttl=24 * 60 * 60)
def _load_alert_instrument_tokens(tickers: list[str]) -> pd.DataFrame:
    normalized_tickers = sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})
    if not normalized_tickers:
        return pd.DataFrame(columns=["tradingsymbol", "instrument_token", "exchange"])

    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_TABLE_NAME").strip()
    if not supabase_url or not supabase_key:
        raise ValueError("Missing Supabase config for alert price context.")

    ticker_filter = ",".join(f"tradingsymbol.eq.{quote(ticker, safe='')}" for ticker in normalized_tickers)
    endpoint = (
        f"{supabase_url}/rest/v1/{quote(table_name, safe='')}"
        f"?select=tradingsymbol,instrument_token,exchange&or=({ticker_filter})"
    )
    request = Request(endpoint, headers=_supabase_headers(supabase_key), method="GET")
    try:
        with urlopen(request, timeout=60) as response:
            records = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Supabase alert instrument lookup failed with HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase alert instrument lookup failed: {exc.reason}") from exc

    instrument_df = pd.DataFrame(records)
    if instrument_df.empty:
        return pd.DataFrame(columns=["tradingsymbol", "instrument_token", "exchange"])
    for column in ["tradingsymbol", "exchange"]:
        if column in instrument_df.columns:
            instrument_df[column] = instrument_df[column].astype(str).str.strip().str.upper()
    return instrument_df


def _supabase_headers(supabase_key: str) -> dict[str, str]:
    return {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
    }


def _resolve_alert_instrument_token(alert: dict[str, Any], instrument_df: pd.DataFrame) -> int | None:
    if instrument_df.empty or "tradingsymbol" not in instrument_df.columns or "instrument_token" not in instrument_df.columns:
        return None

    symbol = str(alert.get("lhs_tradingsymbol") or "").strip().upper()
    exchange = str(alert.get("lhs_exchange") or "").strip().upper()
    matches = instrument_df[instrument_df["tradingsymbol"].astype(str).str.upper().str.strip().eq(symbol)]
    if matches.empty:
        return None

    if exchange and exchange != "INDICES" and "exchange" in matches.columns:
        exchange_matches = matches[matches["exchange"].astype(str).str.upper().str.strip().eq(exchange)]
        if not exchange_matches.empty:
            matches = exchange_matches

    token = pd.to_numeric(matches.iloc[0].get("instrument_token"), errors="coerce")
    return int(token) if pd.notna(token) else None


def _format_alert_price_context(analytics_df: pd.DataFrame, ltp: Any) -> str | None:
    if analytics_df.empty:
        return None

    live_ltp = pd.to_numeric(ltp, errors="coerce")
    metrics = build_metric_values(analytics_df, live_ltp=float(live_ltp) if pd.notna(live_ltp) else None)
    current_price = metrics.get("LTP", metrics.get("Latest Close"))
    if current_price is None:
        return None

    parts = [
        _nearest_distance_label(
            "EMA",
            current_price,
            {f"EMA{span}": metrics.get(f"EMA{span}") for span in [20, 50, 100, 200]},
        ),
        _nearest_distance_label(
            "52W",
            current_price,
            {"52W Low": metrics.get("52W Low"), "52W High": metrics.get("52W High")},
        ),
        _nearest_distance_label("Pivot", current_price, pivot_points(analytics_df)),
    ]
    return " | ".join(part for part in parts if part) or None


def _nearest_distance_label(_group: str, current_price: Any, levels: dict[str, Any]) -> str | None:
    distances: list[tuple[float, str, float]] = []
    for label, level in levels.items():
        distance = calculate_distance_pct(current_price, level)
        if distance is not None:
            distances.append((abs(distance), label, distance))
    if not distances:
        return None

    _, label, signed_distance = min(distances, key=lambda item: item[0])
    return f"{label} {signed_distance:+.1f}%"


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
