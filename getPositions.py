import calendar
import re
from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from kite_auth import bootstrap_kite_app, clear_auth_state, is_token_error


OPTION_SYMBOL_PATTERN = re.compile(
    r"^(?P<underlying>.+?)(?P<year>\d{2})(?P<month>[A-Z]{3})(?P<strike>\d+(?:\.\d+)?)(?P<option_type>CE|PE)$"
)
OPTION_MONTHS = {month.upper(): index for index, month in enumerate(calendar.month_abbr) if month}
OPTION_UNDERLYING_INSTRUMENTS = {
    "NIFTY": "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
    "FINNIFTY": "NSE:NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
}


def _dataframe_height(row_count: int, *, min_rows: int = 1, max_rows: int | None = None) -> int:
    visible_rows = max(row_count, min_rows)
    if max_rows is not None:
        visible_rows = min(visible_rows, max_rows)
    return 38 + (visible_rows + 1) * 35


def _format_display_value(value: Any, decimals: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:,.{decimals}f}"
    return str(value)


def _format_percent_value(value: Any, decimals: int = 2) -> str:
    formatted_value = _format_display_value(value, decimals)
    if formatted_value == "-":
        return formatted_value
    return f"{formatted_value}%"


def _pnl_color(value: Any) -> str:
    converted = pd.to_numeric(value, errors="coerce")
    if pd.isna(converted):
        return "#475569"
    if converted > 0:
        return "#047857"
    if converted < 0:
        return "#b91c1c"
    return "#475569"


def _style_pnl_columns(df: pd.DataFrame):
    pnl_columns = [column for column in ["P&L", "P&L %"] if column in df.columns]
    formatters = {
        column: _format_display_value
        for column in df.columns
        if column not in {"P&L %", "Dist Current"}
    }
    if "P&L %" in df.columns:
        formatters["P&L %"] = _format_percent_value

    styler = df.style.format(formatters, na_rep="-")
    for column in pnl_columns:
        styler = styler.map(lambda value: f"color: {_pnl_color(value)}; font-weight: 600", subset=[column])
    return styler


def _last_tuesday(year: int, month: int) -> date:
    expiry = date(year, month, calendar.monthrange(year, month)[1])
    while expiry.weekday() != 1:
        expiry = date.fromordinal(expiry.toordinal() - 1)
    return expiry


def _parse_option_position(symbol: Any) -> dict[str, Any]:
    match = OPTION_SYMBOL_PATTERN.match(str(symbol or "").upper().strip())
    if not match:
        return {}

    month = OPTION_MONTHS.get(match.group("month"))
    if month is None:
        return {}

    year = 2000 + int(match.group("year"))
    expiry = _last_tuesday(year, month)
    return {
        "underlying": match.group("underlying"),
        "expiry": expiry,
        "days_to_expiry": (expiry - date.today()).days,
        "strike": float(match.group("strike")),
        "option_type": match.group("option_type"),
    }


def _option_breakeven(symbol: Any, average_price: Any) -> float | None:
    parsed = _parse_option_position(symbol)
    avg_price = pd.to_numeric(average_price, errors="coerce")
    if not parsed or pd.isna(avg_price):
        return None

    if parsed["option_type"] == "PE":
        return parsed["strike"] - float(avg_price)
    if parsed["option_type"] == "CE":
        return parsed["strike"] + float(avg_price)
    return None


def _option_underlying_instrument(symbol: Any) -> str | None:
    parsed = _parse_option_position(symbol)
    if not parsed:
        return None
    return OPTION_UNDERLYING_INSTRUMENTS.get(parsed["underlying"])


def _format_breakeven_distance(symbol: Any, breakeven: Any, underlying_ltp: dict[str, float]) -> str | None:
    instrument = _option_underlying_instrument(symbol)
    current = pd.to_numeric(underlying_ltp.get(instrument or ""), errors="coerce")
    breakeven_value = pd.to_numeric(breakeven, errors="coerce")
    if not instrument or pd.isna(current) or pd.isna(breakeven_value) or current == 0:
        return None

    distance = abs(float(breakeven_value) - float(current))
    distance_text = f"{distance:.0f}" if distance.is_integer() else f"{distance:.2f}"
    return f"{distance_text} [{distance / float(current) * 100:.1f}%]"


def _open_position_underlying_instruments(positions: dict[str, Any]) -> list[str]:
    net_positions = positions.get("net", []) if isinstance(positions, dict) else []
    instruments = {
        instrument
        for position in net_positions
        if (instrument := _option_underlying_instrument(position.get("tradingsymbol")))
    }
    return sorted(instruments)


def _fetch_underlying_ltp(kite, positions: dict[str, Any]) -> dict[str, float]:
    instruments = _open_position_underlying_instruments(positions)
    if not instruments:
        return {}

    quotes = kite.ltp(*instruments)
    return {
        instrument: float(quote["last_price"])
        for instrument, quote in quotes.items()
        if isinstance(quote, dict) and quote.get("last_price") is not None
    }


def _open_positions_display_df(
    positions: dict[str, Any],
    underlying_ltp: dict[str, float] | None = None,
) -> pd.DataFrame:
    net_positions = positions.get("net", []) if isinstance(positions, dict) else []
    positions_df = pd.DataFrame(net_positions)
    if positions_df.empty or "quantity" not in positions_df.columns:
        return pd.DataFrame()

    positions_df["quantity"] = pd.to_numeric(positions_df["quantity"], errors="coerce").fillna(0)
    positions_df = positions_df[positions_df["quantity"].ne(0)].copy()
    if positions_df.empty:
        return pd.DataFrame()

    for column in ["average_price", "last_price", "pnl", "m2m"]:
        if column in positions_df.columns:
            positions_df[column] = pd.to_numeric(positions_df[column], errors="coerce")

    average_price = positions_df.get("average_price", pd.Series(index=positions_df.index, dtype=float))
    last_price = positions_df.get("last_price", pd.Series(index=positions_df.index, dtype=float))
    invested = positions_df["quantity"].abs() * average_price
    current_value = positions_df["quantity"] * last_price
    pnl = positions_df.get("pnl", pd.Series(index=positions_df.index, dtype=float))

    positions_df["invested"] = invested
    positions_df["current_value"] = current_value
    positions_df["pnl_pct"] = pnl.where(invested.ne(0)) / invested * 100
    option_details = positions_df.get("tradingsymbol", pd.Series(index=positions_df.index, dtype=object)).apply(
        _parse_option_position
    )
    positions_df["days_to_expiry"] = option_details.apply(lambda details: details.get("days_to_expiry"))
    positions_df["breakeven"] = positions_df.apply(
        lambda row: _option_breakeven(row.get("tradingsymbol"), row.get("average_price")),
        axis=1,
    )
    positions_df["dist_current"] = positions_df.apply(
        lambda row: _format_breakeven_distance(
            row.get("tradingsymbol"),
            row.get("breakeven"),
            underlying_ltp or {},
        ),
        axis=1,
    )

    display_columns = [
        "tradingsymbol",
        "quantity",
        "days_to_expiry",
        "breakeven",
        "dist_current",
        "average_price",
        "last_price",
        "invested",
        "current_value",
        "pnl",
        "pnl_pct",
        "m2m",
    ]
    display_df = positions_df[[column for column in display_columns if column in positions_df.columns]]
    return display_df.rename(
        columns={
            "tradingsymbol": "Symbol",
            "quantity": "Open Qty",
            "days_to_expiry": "Days to Expiry",
            "breakeven": "Breakeven",
            "dist_current": "Dist Current",
            "average_price": "Avg Price",
            "last_price": "LTP",
            "invested": "Invested",
            "current_value": "Current",
            "pnl": "P&L",
            "pnl_pct": "P&L %",
            "m2m": "M2M",
        }
    )


def display_open_positions(
    positions: dict[str, Any],
    underlying_ltp: dict[str, float] | None = None,
) -> None:
    display_df = _open_positions_display_df(positions, underlying_ltp)
    if display_df.empty:
        st.info("No open positions found.")
        return

    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    with metric_col1:
        st.metric("Open Positions", f"{len(display_df):,}")
    with metric_col2:
        open_qty = pd.to_numeric(display_df.get("Open Qty", pd.Series(dtype=float)), errors="coerce").abs().sum()
        st.metric("Open Qty", f"{open_qty:,.0f}")
    with metric_col3:
        total_pnl = pd.to_numeric(display_df.get("P&L", pd.Series(dtype=float)), errors="coerce").sum()
        st.metric("Total P&L", f"{total_pnl:,.2f}", delta=f"{total_pnl:,.2f}")
    with metric_col4:
        total_m2m = pd.to_numeric(display_df.get("M2M", pd.Series(dtype=float)), errors="coerce").sum()
        st.metric("Total M2M", f"{total_m2m:,.2f}", delta=f"{total_m2m:,.2f}")

    st.dataframe(
        _style_pnl_columns(display_df),
        width="stretch",
        height=_dataframe_height(len(display_df), max_rows=18),
        hide_index=True,
        column_config={
            "Symbol": st.column_config.TextColumn("Symbol", width="small"),
            "Open Qty": st.column_config.NumberColumn("Open Qty", width="small", format="%d"),
            "Days to Expiry": st.column_config.NumberColumn("Days to Expiry", width="small", format="%d"),
            "Breakeven": st.column_config.NumberColumn("Breakeven", width="small", format="%.2f"),
            "Dist Current": st.column_config.TextColumn("Dist Current", width="small"),
            "Avg Price": st.column_config.NumberColumn("Avg Price", width="small", format="%.2f"),
            "LTP": st.column_config.NumberColumn("LTP", width="small", format="%.2f"),
            "Invested": st.column_config.NumberColumn("Invested", width="small", format="%.2f"),
            "Current": st.column_config.NumberColumn("Current", width="small", format="%.2f"),
            "P&L": st.column_config.NumberColumn("P&L", width="small", format="%.2f"),
            "P&L %": st.column_config.NumberColumn("P&L %", width="small", format="%.2f%%"),
            "M2M": st.column_config.NumberColumn("M2M", width="small", format="%.2f"),
        },
    )


def fetch_open_positions() -> None:
    try:
        kite, _, _ = bootstrap_kite_app("Zerodha Holdings")
        positions = kite.positions()
        st.session_state["kite_open_positions"] = positions
        st.session_state["kite_open_positions_fetched_at"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            st.session_state["kite_open_positions_underlying_ltp"] = _fetch_underlying_ltp(kite, positions)
            st.session_state.pop("kite_open_positions_ltp_error", None)
        except Exception as ltp_exc:
            st.session_state["kite_open_positions_underlying_ltp"] = {}
            st.session_state["kite_open_positions_ltp_error"] = str(ltp_exc)
    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
            st.error("Your session expired. Please login again to view positions.")
            st.rerun()
        st.error(f"Error fetching open positions. Please try again. Details: {exc}")


def render_open_positions_tab() -> None:
    if st.button("Fetch Open Positions", type="primary"):
        fetch_open_positions()

    open_positions = st.session_state.get("kite_open_positions")
    fetched_at = st.session_state.get("kite_open_positions_fetched_at")
    ltp_error = st.session_state.get("kite_open_positions_ltp_error")
    if open_positions is None:
        st.info("Fetch open positions from Kite to display current open positions.")
        return

    if fetched_at:
        st.caption(f"As of {fetched_at}")
    if ltp_error:
        st.warning(f"Could not load underlying LTP for distance calculation: {ltp_error}")
    display_open_positions(
        open_positions,
        st.session_state.get("kite_open_positions_underlying_ltp", {}),
    )
