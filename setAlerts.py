from __future__ import annotations

import json
import math
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st

from kite_analytics import build_metric_values, calculate_distance_pct, load_analytics_history
from kite_auth import bootstrap_kite_app, clear_auth_state, is_token_error
from kite_auth import get_secret_value
from portfolio_terminal_component import render_alerts_terminal


ALERTS_STATE_KEY = "kite_alerts_data"
ALERTS_LAST_REQUEST_ID_KEY = "kite_alerts_last_request_id"
ALERTS_LOG_STATE_KEY = "kite_alerts_fetch_log"
ALERTS_PRICE_CONTEXT_VERSION = 4
ALERTS_DEFAULT_STATUS = "active"
KITE_ALERTS_ENDPOINT = "https://api.kite.trade/alerts"
ALERTS_HTTP_TIMEOUT_SECONDS = 10
ALERTS_DEBUG_LOG_ENABLED = False
ALERT_EMA_LABELS = ("EMA10", "EMA20", "EMA50", "EMA100", "EMA200")
SECTOR_INDEX_LABELS = (
    ("PRIVATE BANK", "Banking"),
    ("PSU BANK", "Banking"),
    ("BANK", "Banking"),
    ("FINANCIAL SERVICES", "Financial Services"),
    ("AUTO", "Automobiles"),
    ("AUTOMOBILE", "Automobiles"),
    ("FMCG", "FMCG"),
    ("IT", "Information Technology"),
    ("INFORMATION TECHNOLOGY", "Information Technology"),
    ("MEDIA", "Media"),
    ("METAL", "Metals"),
    ("PHARMA", "Pharmaceuticals"),
    ("HEALTHCARE", "Healthcare"),
    ("REALTY", "Real Estate"),
    ("CONSUMER DURABLES", "Consumer Durables"),
    ("OIL GAS", "Oil & Gas"),
    ("ENERGY", "Energy"),
    ("INFRA", "Infrastructure"),
)
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
        "disabledSymbolsText": str(previous_data.get("disabledSymbolsText") or ""),
    }

    try:
        _log_alerts_step("Bootstrapping Kite client for alerts.")
        kite, api_key, _ = bootstrap_kite_app("Zerodha Alerts")
        _log_alerts_step("Kite client ready.")
        payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}

        if action == "create":
            _log_alerts_step("Creating alert through Kite Alerts API.")
            created_alert, payload, trigger_adjustment = _create_alert_with_condition_adjustment(
                kite,
                api_key,
                st.session_state.access_token,
                payload,
            )
            created_message = "Alert created."
            if trigger_adjustment is not None:
                created_message = (
                    f"Alert created; trigger adjusted from {trigger_adjustment['previous']:.2f} "
                    f"to {trigger_adjustment['target']:.2f} (1% {trigger_adjustment['direction']} "
                    f"current LTP {trigger_adjustment['ltp']:.2f})."
                )
            created_uuid = _created_alert_uuid(created_alert)
            if created_uuid:
                next_data["alerts"] = _append_created_alert_row(next_data["alerts"], created_uuid, payload, created_alert)
                next_data["disabledSymbolsText"] = _disabled_alert_symbols_text(next_data["alerts"])
                _enrich_alerts_with_sectors(next_data)
                next_data["message"] = created_message
                next_data["loaded"] = True
                next_data.pop("error", None)
                if not ALERTS_DEBUG_LOG_ENABLED:
                    next_data.pop("fetchMeta", None)
                    next_data.pop("debug", None)
                return next_data
            next_data["message"] = created_message
        elif action == "modify":
            uuid = str(payload.get("uuid") or "")
            if not uuid:
                raise ValueError("Missing alert uuid for modify.")
            _log_alerts_step(f"Modifying alert uuid={uuid}.")
            modify_alert(api_key, st.session_state.access_token, uuid, payload)
            next_data["alerts"] = _patch_modified_alert_row(next_data["alerts"], uuid, payload)
            next_data["disabledSymbolsText"] = _disabled_alert_symbols_text(next_data["alerts"])
            _enrich_alerts_with_sectors(next_data)
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
            next_data["disabledSymbolsText"] = _disabled_alert_symbols_text(next_data["alerts"])
            next_data["message"] = "Alert deleted."
            next_data["loaded"] = True
            next_data.pop("error", None)
            if not ALERTS_DEBUG_LOG_ENABLED:
                next_data.pop("fetchMeta", None)
                next_data.pop("debug", None)
            return next_data
        elif action == "reset":
            return _reset_disabled_alerts(
                kite,
                api_key,
                st.session_state.access_token,
                previous_data,
                next_data,
                payload,
            )

        _log_alerts_step(f"Fetching alerts list with status_filter={fetch_status_filter}.")
        alerts, fetch_meta = get_alerts(api_key, st.session_state.access_token, fetch_status_filter)
        _log_alerts_step(f"Parsed {len(alerts)} alert row(s) from Kite response.")
        ltp_enriched_alerts = enrich_alerts_with_ltp(kite, alerts)
        next_data["alerts"] = _dedupe_alerts(
            enrich_alerts_with_price_context(
                kite,
                ltp_enriched_alerts,
                previous_alerts=list(previous_data.get("alerts") or []),
            )
        )
        _enrich_alerts_with_sectors(next_data)
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


def _reset_disabled_alerts(
    kite: Any,
    api_key: str,
    access_token: str,
    previous_data: dict[str, Any],
    next_data: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    _log_alerts_step("Fetching disabled alerts for reset.")
    disabled_alerts, _ = _get_alerts_by_status(api_key, access_token, "disabled")
    disabled_alerts = enrich_alerts_with_ltp(kite, disabled_alerts) if disabled_alerts else []

    previous_alerts = list(previous_data.get("alerts") or [])
    previous_by_uuid = {
        str(alert.get("uuid") or "").strip(): alert
        for alert in previous_alerts
        if str(alert.get("uuid") or "").strip()
    }
    for alert in disabled_alerts:
        previous_alert = previous_by_uuid.get(str(alert.get("uuid") or "").strip(), {})
        if previous_alert.get("price_context"):
            alert["price_context"] = previous_alert["price_context"]
            if previous_alert.get("price_context_version") is not None:
                alert["price_context_version"] = previous_alert["price_context_version"]

    resettable_alerts = [alert for alert in disabled_alerts if _alert_reset_skip_reason(alert) is None]
    symbols = sorted(
        {
            str(alert.get("lhs_tradingsymbol") or "").strip().upper()
            for alert in resettable_alerts
            if str(alert.get("lhs_tradingsymbol") or "").strip()
        }
    )
    instrument_df = _load_alert_instrument_tokens(symbols) if symbols else pd.DataFrame()
    as_of_date = pd.Timestamp.now().date().isoformat()
    analytics_by_instrument: dict[tuple[str, str], tuple[dict[str, float], str | None]] = {}
    skipped_details: list[str] = []
    failed_details: list[str] = []
    reset_count = 0
    enabled_reset_count = 0
    no_ema_fallback_count = 0
    condition_adjustment_count = 0

    for alert in disabled_alerts:
        symbol = str(alert.get("lhs_tradingsymbol") or "").strip().upper() or "Unknown symbol"
        uuid = str(alert.get("uuid") or "").strip()
        skip_reason = _alert_reset_skip_reason(alert)
        if skip_reason:
            skipped_details.append(f"{symbol}: {skip_reason}")
            continue
        if not uuid:
            skipped_details.append(f"{symbol}: missing alert UUID")
            continue

        current_price = pd.to_numeric(alert.get("ltp"), errors="coerce")
        if pd.isna(current_price) or float(current_price) <= 0:
            skipped_details.append(f"{symbol}: current LTP unavailable")
            continue

        instrument_token = _resolve_alert_instrument_token(alert, instrument_df)
        if instrument_token is None:
            skipped_details.append(f"{symbol}: instrument token unavailable")
            continue

        instrument_key = (str(alert.get("lhs_exchange") or "").strip().upper(), symbol)
        if instrument_key not in analytics_by_instrument:
            try:
                analytics_df = load_analytics_history(kite, instrument_token, as_of_date)
                metrics = build_metric_values(analytics_df, live_ltp=float(current_price))
                alert["price_context"] = _format_alert_price_context(analytics_df, current_price)
                alert["price_context_version"] = ALERTS_PRICE_CONTEXT_VERSION
                analytics_by_instrument[instrument_key] = (metrics, None)
            except Exception as exc:
                _log_alerts_step(f"EMA history failed for {symbol}: {exc}")
                analytics_by_instrument[instrument_key] = ({}, str(exc))

        metrics, history_error = analytics_by_instrument[instrument_key]
        if history_error:
            skipped_details.append(f"{symbol}: EMA history unavailable ({history_error})")
            continue

        try:
            target_price, used_fallback, adjusted_for_condition = _reset_alert_trigger_target(
                float(current_price),
                metrics,
                str(alert.get("operator") or ""),
            )
        except ValueError as exc:
            skipped_details.append(f"{symbol}: {exc}")
            continue

        update_fields = {field_name: alert.get(field_name) for field_name in ALERT_FIELD_NAMES}
        update_fields["rhs_constant"] = f"{target_price:.2f}"
        try:
            normalized_update_fields = _alert_payload(update_fields)
            modify_alert(api_key, access_token, uuid, normalized_update_fields)
        except Exception as exc:
            _log_alerts_step(f"Reset failed for alert uuid={uuid}, symbol={symbol}: {exc}")
            failed_details.append(f"{symbol}: {exc}")
            continue

        alert.update(normalized_update_fields)
        alert["status"] = "enabled"
        alert["price_context"] = None
        alert.pop("price_context_version", None)
        reset_count += 1
        no_ema_fallback_count += int(used_fallback)
        condition_adjustment_count += int(adjusted_for_condition)

    requested_enabled_uuids = {
        str(uuid).strip()
        for uuid in payload.get("enabledAlertUuids", [])
        if str(uuid).strip()
    } if isinstance(payload.get("enabledAlertUuids"), list) else set()
    updated_enabled_alerts: dict[str, dict[str, Any]] = {}
    found_enabled_uuids: set[str] = set()
    for alert in previous_alerts:
        uuid = str(alert.get("uuid") or "").strip()
        if (
            uuid not in requested_enabled_uuids
            or str(alert.get("status") or "").strip().lower() != "enabled"
        ):
            continue
        found_enabled_uuids.add(uuid)
        symbol = str(alert.get("lhs_tradingsymbol") or "").strip().upper() or "Unknown symbol"
        skip_reason = _alert_reset_skip_reason(alert)
        if skip_reason:
            skipped_details.append(f"{symbol}: {skip_reason}")
            continue
        if (_alert_distance_percentage(alert) or 0) <= 2:
            skipped_details.append(f"{symbol}: distance is no longer greater than 2%")
            continue

        current_price = pd.to_numeric(alert.get("ltp"), errors="coerce")
        try:
            target_price = _enabled_alert_reset_target(
                float(current_price),
                str(alert.get("operator") or ""),
            )
        except (ValueError, TypeError) as exc:
            skipped_details.append(f"{symbol}: {exc}")
            continue

        update_fields = {field_name: alert.get(field_name) for field_name in ALERT_FIELD_NAMES}
        update_fields["rhs_constant"] = f"{target_price:.2f}"
        try:
            normalized_update_fields = _alert_payload(update_fields)
            modify_alert(api_key, access_token, uuid, normalized_update_fields)
        except Exception as exc:
            _log_alerts_step(f"Enabled alert reset failed for uuid={uuid}, symbol={symbol}: {exc}")
            failed_details.append(f"{symbol}: {exc}")
            continue

        updated_alert = dict(alert)
        updated_alert.update(normalized_update_fields)
        updated_alert["status"] = "enabled"
        updated_enabled_alerts[uuid] = updated_alert
        enabled_reset_count += 1

    skipped_details.extend(
        f"{uuid}: enabled alert is no longer available"
        for uuid in sorted(requested_enabled_uuids - found_enabled_uuids)
    )

    reset_uuids = {
        str(alert.get("uuid") or "").strip()
        for alert in disabled_alerts
        if str(alert.get("uuid") or "").strip()
    }
    previously_enabled_alerts = [
        updated_enabled_alerts.get(str(alert.get("uuid") or "").strip(), dict(alert))
        for alert in previous_alerts
        if str(alert.get("status") or "").strip().lower() == "enabled"
        and str(alert.get("uuid") or "").strip() not in reset_uuids
    ]
    next_data["alerts"] = _dedupe_alerts(previously_enabled_alerts + disabled_alerts)
    next_data["disabledSymbolsText"] = _disabled_alert_symbols_text(next_data["alerts"])
    next_data["loaded"] = True
    _enrich_alerts_with_sectors(next_data)
    next_data.pop("error", None)

    if not disabled_alerts and not requested_enabled_uuids:
        next_data["message"] = "No disabled alerts to reset."
    else:
        next_data["message"] = (
            f"Reset and re-enabled {reset_count} disabled alert(s); "
            f"adjusted {enabled_reset_count} enabled alert(s)."
        )
        if no_ema_fallback_count:
            next_data["message"] += f" Used the 1% LTP fallback for {no_ema_fallback_count}."
        if condition_adjustment_count:
            next_data["message"] += f" Moved {condition_adjustment_count} trigger(s) to avoid an already-met condition."
        if skipped_details:
            next_data["message"] += f" Skipped {len(skipped_details)}."
        if failed_details:
            next_data["message"] += f" Failed {len(failed_details)}."
        details = skipped_details + [f"Failed {detail}" for detail in failed_details]
        if details:
            next_data["error"] = "; ".join(details)

    return next_data


def _alert_reset_skip_reason(alert: dict[str, Any]) -> str | None:
    if str(alert.get("type") or "").strip().lower() != "simple":
        return "unsupported alert type"
    if str(alert.get("rhs_type") or "").strip().lower() != "constant":
        return "only constant-price alerts are supported"
    lhs_attribute = str(alert.get("lhs_attribute") or "LastTradedPrice").strip().lower()
    if lhs_attribute != "lasttradedprice":
        return "only LastTradedPrice alerts are supported"
    return None


def _alert_distance_percentage(alert: dict[str, Any]) -> float | None:
    if str(alert.get("rhs_type") or "").strip().lower() != "constant":
        return None
    ltp = pd.to_numeric(alert.get("ltp"), errors="coerce")
    trigger = pd.to_numeric(alert.get("rhs_constant"), errors="coerce")
    if (
        pd.isna(ltp)
        or pd.isna(trigger)
        or not math.isfinite(float(ltp))
        or not math.isfinite(float(trigger))
        or float(ltp) <= 0
    ):
        return None
    return abs(float(trigger) - float(ltp)) / float(ltp) * 100


def _enabled_alert_reset_target(current_price: float, operator: str) -> float:
    normalized_operator = operator.strip()
    target_price = _non_triggering_alert_target(current_price, normalized_operator)
    if _alert_condition_is_met(normalized_operator, current_price, target_price):
        raise ValueError("could not move trigger outside the already-met condition")
    return target_price


def _reset_alert_trigger_target(
    current_price: float,
    metrics: dict[str, Any],
    operator: str,
) -> tuple[float, bool, bool]:
    if not math.isfinite(current_price) or current_price <= 0:
        raise ValueError("current LTP is invalid")

    normalized_operator = operator.strip()
    if normalized_operator not in {"<=", ">=", "<", ">", "=="}:
        raise ValueError(f"unsupported alert operator {normalized_operator or '(missing)'}")

    nearest_lower_ema = _nearest_lower_ema(current_price, metrics)
    used_fallback = nearest_lower_ema is None
    target_price = current_price * 0.99 if used_fallback else nearest_lower_ema[1]
    target_price = float(f"{target_price:.2f}")

    if _alert_condition_is_met(normalized_operator, current_price, target_price):
        condition_adjustment = True
        target_price = _non_triggering_alert_target(current_price, normalized_operator)
    else:
        condition_adjustment = False

    if target_price <= 0:
        raise ValueError("no positive trigger price can avoid an already-met condition")
    if _alert_condition_is_met(normalized_operator, current_price, target_price):
        raise ValueError("could not move trigger outside the already-met condition")
    return target_price, used_fallback, condition_adjustment


def _non_triggering_alert_target(current_price: float, operator: str) -> float:
    if not math.isfinite(current_price) or current_price <= 0:
        raise ValueError("current LTP is invalid")
    operator = operator.strip()
    if operator not in {"<=", ">=", "<", ">", "=="}:
        raise ValueError(f"unsupported alert operator {operator or '(missing)'}")
    if operator in {">=", ">", "=="}:
        target_price = max(current_price * 1.01, current_price + 0.01)
    else:
        target_price = min(current_price * 0.99, current_price - 0.01)
    target_price = float(f"{target_price:.2f}")
    if target_price <= 0:
        raise ValueError("no positive trigger price can avoid an already-met condition")
    return target_price


def _alert_condition_is_met(operator: str, current_price: float, target_price: float) -> bool:
    if operator == "<=":
        return current_price <= target_price
    if operator == ">=":
        return current_price >= target_price
    if operator == "<":
        return current_price < target_price
    if operator == ">":
        return current_price > target_price
    return current_price == target_price


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


def _create_alert_with_condition_adjustment(
    kite: Any,
    api_key: str,
    access_token: str,
    fields: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str], dict[str, float | str] | None]:
    payload = _alert_payload(fields)
    try:
        return create_alert(api_key, access_token, payload), payload, None
    except RuntimeError as exc:
        if "the rule condition is already met" not in str(exc).lower():
            raise

    instrument = _quote_instrument_for_alert(payload)
    if not instrument:
        raise RuntimeError("Cannot adjust alert trigger because the instrument is unavailable.")
    quotes = kite.ltp(instrument)
    quote = quotes.get(instrument, {}) if isinstance(quotes, dict) else {}
    current_price = pd.to_numeric(quote.get("last_price"), errors="coerce") if isinstance(quote, dict) else float("nan")
    if pd.isna(current_price) or not math.isfinite(float(current_price)) or float(current_price) <= 0:
        raise RuntimeError(f"Cannot adjust alert trigger because current LTP is unavailable for {instrument}.")

    ltp = float(current_price)
    target_price = _non_triggering_alert_target(ltp, payload["operator"])
    previous_trigger = pd.to_numeric(payload.get("rhs_constant"), errors="coerce")
    adjusted_payload = dict(payload)
    adjusted_payload["rhs_constant"] = f"{target_price:.2f}"
    created_alert = create_alert(api_key, access_token, adjusted_payload)
    direction = "below" if payload["operator"].strip() in {"<", "<="} else "above"
    adjustment: dict[str, float | str] = {
        "previous": float(previous_trigger) if not pd.isna(previous_trigger) else 0.0,
        "target": target_price,
        "ltp": ltp,
        "direction": direction,
    }
    return created_alert, adjusted_payload, adjustment


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


def enrich_alerts_with_price_context(
    kite: Any,
    alerts: list[dict[str, Any]],
    *,
    previous_alerts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    previous_context_by_uuid = {
        str(alert.get("uuid") or "").strip(): alert.get("price_context")
        for alert in previous_alerts or []
        if str(alert.get("uuid") or "").strip()
        and alert.get("price_context_version") == ALERTS_PRICE_CONTEXT_VERSION
    }
    enriched_alerts: list[dict[str, Any]] = []
    new_alert_indexes: list[int] = []
    for alert in alerts:
        next_alert = dict(alert)
        is_disabled = str(next_alert.get("status") or "").strip().lower() == "disabled"
        uuid = str(next_alert.get("uuid") or "").strip()
        if not is_disabled:
            next_alert["price_context"] = None
            next_alert.pop("price_context_version", None)
        elif uuid and uuid in previous_context_by_uuid:
            next_alert["price_context"] = previous_context_by_uuid[uuid]
        else:
            next_alert["price_context"] = None
            new_alert_indexes.append(len(enriched_alerts))
        enriched_alerts.append(next_alert)

    if not new_alert_indexes:
        _log_alerts_step("Reused cached price context for all existing alert UUIDs.")
        return enriched_alerts

    symbols = sorted(
        {
            str(enriched_alerts[index].get("lhs_tradingsymbol") or "").strip().upper()
            for index in new_alert_indexes
            if str(enriched_alerts[index].get("lhs_tradingsymbol") or "").strip()
        }
    )
    if not symbols:
        return enriched_alerts

    try:
        _log_alerts_step(f"Loading instrument tokens for {len(symbols)} new alert symbol(s).")
        instrument_df = _load_alert_instrument_tokens(symbols)
    except Exception as exc:
        st.session_state["kite_alerts_price_context_error"] = str(exc)
        _log_alerts_step(f"Price context token lookup failed; continuing without context: {exc}")
        return enriched_alerts

    as_of_date = pd.Timestamp.now().date().isoformat()
    for index in new_alert_indexes:
        next_alert = enriched_alerts[index]
        token = _resolve_alert_instrument_token(next_alert, instrument_df)
        if token is None:
            continue

        try:
            analytics_df = load_analytics_history(kite, token, as_of_date)
            next_alert["price_context"] = _format_alert_price_context(analytics_df, next_alert.get("ltp"))
            next_alert["price_context_version"] = ALERTS_PRICE_CONTEXT_VERSION
        except Exception as exc:
            next_alert["price_context"] = None
            _log_alerts_step(
                f"Price context failed for {next_alert.get('lhs_tradingsymbol') or '-'}; continuing: {exc}"
            )
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


def _sector_label_for_index(index_name: Any) -> str | None:
    normalized_words = "".join(
        character if character.isalnum() else " "
        for character in str(index_name or "").upper()
    ).split()
    for index_fragment, sector_label in SECTOR_INDEX_LABELS:
        fragment_words = index_fragment.split()
        if any(
            normalized_words[start : start + len(fragment_words)] == fragment_words
            for start in range(len(normalized_words) - len(fragment_words) + 1)
        ):
            return sector_label
    return None


def _sector_map_from_index_records(records: list[dict[str, Any]]) -> dict[str, str]:
    sectors_by_symbol: dict[str, set[str]] = {}
    for record in records:
        sector = _sector_label_for_index(record.get("Index"))
        if sector is None:
            continue
        symbols = str(record.get("Constituents") or "").split(",")
        for symbol in symbols:
            normalized_symbol = symbol.strip().upper()
            if normalized_symbol:
                sectors_by_symbol.setdefault(normalized_symbol, set()).add(sector)
    return {
        symbol: ", ".join(sorted(sectors))
        for symbol, sectors in sectors_by_symbol.items()
    }


@st.cache_data(ttl=24 * 60 * 60)
def _load_alert_sector_by_symbol() -> dict[str, str]:
    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_INDICES_TABLE_NAME").strip() or "Indices_constituents"
    if not supabase_url or not supabase_key:
        raise ValueError("Missing Supabase config for alert sector lookup.")

    endpoint = (
        f"{supabase_url}/rest/v1/{quote(table_name, safe='')}"
        "?select=Index,Constituents&order=Index.asc"
    )
    request = Request(endpoint, headers=_supabase_headers(supabase_key), method="GET")
    try:
        with urlopen(request, timeout=60) as response:
            records = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Supabase alert sector lookup failed with HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase alert sector lookup failed: {exc.reason}") from exc

    return _sector_map_from_index_records(records)


def _enrich_alerts_with_sectors(alerts_data: dict[str, Any]) -> None:
    try:
        sector_by_symbol = _load_alert_sector_by_symbol()
    except Exception as exc:
        alerts_data["sectorError"] = str(exc)
        _log_alerts_step(f"Sector lookup failed; alerts loaded without sector labels: {exc}")
        return

    for alert in alerts_data.get("alerts", []):
        symbol = str(alert.get("lhs_tradingsymbol") or "").strip().upper()
        alert["sector"] = sector_by_symbol.get(symbol)
    alerts_data.pop("sectorError", None)


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

    parts = _alert_position_distance_labels(current_price, metrics)
    return " | ".join(part for part in parts if part) or None


def _nearest_lower_ema(current_price: Any, metrics: dict[str, Any]) -> tuple[str, float] | None:
    current = pd.to_numeric(current_price, errors="coerce")
    if pd.isna(current):
        return None

    lower_emas: list[tuple[str, float]] = []
    for label in ALERT_EMA_LABELS:
        level = pd.to_numeric(metrics.get(label), errors="coerce")
        if pd.notna(level) and float(level) <= float(current):
            lower_emas.append((label, float(level)))
    return max(lower_emas, key=lambda item: item[1], default=None)


def _nearest_distance_label(_group: str, current_price: Any, levels: dict[str, Any]) -> str | None:
    distances: list[tuple[float, str, float]] = []
    for label, level in levels.items():
        distance = calculate_distance_pct(current_price, level)
        if distance is not None:
            distances.append((abs(distance), label, distance))
    if not distances:
        return None

    _, label, signed_distance = min(distances, key=lambda item: item[0])
    return f"{label} {signed_distance:+.1f}% {_format_indicator_value(levels[label])}"


def _alert_position_distance_labels(current_price: Any, metrics: dict[str, Any]) -> list[str]:
    current = pd.to_numeric(current_price, errors="coerce")
    if pd.isna(current):
        return []

    current_value = float(current)
    ema_levels: list[tuple[str, float]] = []
    for label in ["EMA10", "EMA20", "EMA50", "EMA100", "EMA200"]:
        level = pd.to_numeric(metrics.get(label), errors="coerce")
        if pd.notna(level):
            ema_levels.append((label, float(level)))

    nearest_higher_ema = min(
        ((label, level) for label, level in ema_levels if level > current_value),
        key=lambda item: item[1] - current_value,
        default=None,
    )
    nearest_lower_ema = _nearest_lower_ema(current_value, dict(ema_levels))

    monthly_highs: list[tuple[str, float]] = []
    monthly_lows: list[tuple[str, float]] = []
    for label in ["1M High", "3M High", "6M High", "1M Low", "3M Low", "6M Low"]:
        level = pd.to_numeric(metrics.get(label), errors="coerce")
        if pd.notna(level):
            (monthly_highs if label.endswith("High") else monthly_lows).append((label, float(level)))

    nearest_monthly_high = min(
        ((label, level) for label, level in monthly_highs if level > current_value),
        key=lambda item: item[1] - current_value,
        default=None,
    )
    nearest_monthly_low = min(
        ((label, level) for label, level in monthly_lows if level <= current_value),
        key=lambda item: current_value - item[1],
        default=None,
    )

    surrounding_labels = [
        _distance_label_with_value(label, current_value, level)
        for item in [nearest_higher_ema, nearest_monthly_high]
        if item is not None
        for label, level in [item]
    ]
    surrounding_labels.append(f"LTP {_format_indicator_value(current_value)}")
    surrounding_labels.extend(
        _distance_label_with_value(label, current_value, level)
        for item in [nearest_lower_ema, nearest_monthly_low]
        if item is not None
        for label, level in [item]
    )
    return surrounding_labels


def _distance_label_with_value(label: str, current_price: float, level: float) -> str:
    distance = calculate_distance_pct(current_price, level)
    return f"{label} {distance:+.1f}% {_format_indicator_value(level)}"


def _format_indicator_value(value: Any) -> str:
    numeric_value = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric_value):
        return "-"
    return f"{float(numeric_value):.2f}".rstrip("0").rstrip(".")


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
