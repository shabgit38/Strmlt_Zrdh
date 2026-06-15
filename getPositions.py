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
TRADE_CALCULATOR_COLUMNS = [
    "SYMBOL",
    "BUY",
    "QTY",
    "TOTAL INVESTED",
    "SELL",
    "PROFIT",
    "PROFIT %",
    "ENTRY",
    "EXIT",
    "DAYS",
    "SL",
    "TGT",
]
TRADE_CALCULATOR_INPUT_COLUMNS = ["SYMBOL", "BUY", "QTY", "SELL", "ENTRY", "EXIT", "SL", "TGT"]
TRADE_CALCULATOR_CALCULATED_COLUMNS = ["TOTAL INVESTED", "PROFIT", "PROFIT %", "DAYS"]
OPTION_CALCULATOR_COLUMNS = [
    "Symbol",
    "Open Qty",
    "Avg Price",
    "LTP",
    "Spot",
    "Exit Price",
    "Days_Expiry",
    "Breakeven",
    "Dist_Spot",
    "Invested",
    "Current",
    "P&L",
    "P&L %",
]
OPTION_CALCULATOR_EDITABLE_COLUMNS = ["Symbol", "Open Qty", "Avg Price", "Exit Price"]
OPTION_CALCULATOR_API_COLUMNS = ["LTP", "Spot"]
OPTION_CALCULATOR_CALCULATED_COLUMNS = ["Days_Expiry", "Breakeven", "Dist_Spot", "Invested", "Current", "P&L", "P&L %"]
OPTION_CALCULATOR_DISABLED_COLUMNS = OPTION_CALCULATOR_API_COLUMNS + OPTION_CALCULATOR_CALCULATED_COLUMNS
OPTION_SYMBOL_HELP = (
    "Use Kite monthly option symbols like NIFTY26JUN25000CE or BANKNIFTY26JUN56000PE. "
    "Format: UNDERLYING + YY + MMM + STRIKE + CE/PE."
)


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
    pnl_columns = [column for column in ["P&L", "P&L %", "PROFIT", "PROFIT %"] if column in df.columns]
    formatters = {
        column: _format_display_value
        for column in df.columns
        if column not in {"P&L %", "PROFIT %", "Dist_Spot"}
    }
    if "P&L %" in df.columns:
        formatters["P&L %"] = _format_percent_value
    if "PROFIT %" in df.columns:
        formatters["PROFIT %"] = _format_percent_value

    styler = df.style.format(formatters, na_rep="-")
    for column in pnl_columns:
        styler = styler.map(lambda value: f"color: {_pnl_color(value)}; font-weight: 600", subset=[column])
    return styler


def _empty_trade_calculator_df() -> pd.DataFrame:
    return pd.DataFrame([{column: None for column in TRADE_CALCULATOR_COLUMNS}])


def _calculate_trade_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_trade_calculator_df()

    calculated_df = df.copy()
    for column in TRADE_CALCULATOR_COLUMNS:
        if column not in calculated_df.columns:
            calculated_df[column] = None

    calculated_df = calculated_df[TRADE_CALCULATOR_COLUMNS]
    calculated_df["SYMBOL"] = calculated_df["SYMBOL"].astype("string").str.upper().str.strip()
    calculated_df["SYMBOL"] = calculated_df["SYMBOL"].replace({"": pd.NA, "<NA>": pd.NA})

    for column in ["BUY", "QTY", "SELL", "SL", "TGT"]:
        calculated_df[column] = pd.to_numeric(calculated_df[column], errors="coerce")

    for column in ["ENTRY", "EXIT"]:
        calculated_df[column] = pd.to_datetime(calculated_df[column], errors="coerce").dt.date

    total_invested = calculated_df["BUY"] * calculated_df["QTY"]
    calculated_df["TOTAL INVESTED"] = total_invested

    has_sell = calculated_df["SELL"].notna()
    calculated_df["PROFIT"] = (calculated_df["SELL"] - calculated_df["BUY"]) * calculated_df["QTY"]
    calculated_df.loc[~has_sell, "PROFIT"] = None
    calculated_df["PROFIT %"] = calculated_df["PROFIT"].where(total_invested.ne(0)) / total_invested * 100

    today = date.today()
    entry_dates = pd.to_datetime(calculated_df["ENTRY"], errors="coerce")
    exit_dates = pd.to_datetime(calculated_df["EXIT"], errors="coerce")
    effective_exit_dates = exit_dates.fillna(pd.Timestamp(today))
    calculated_df["DAYS"] = (effective_exit_dates - entry_dates).dt.days
    calculated_df.loc[entry_dates.isna(), "DAYS"] = None

    blank_rows = calculated_df[TRADE_CALCULATOR_INPUT_COLUMNS].isna().all(axis=1)
    calculated_df.loc[blank_rows, TRADE_CALCULATOR_CALCULATED_COLUMNS] = None
    return calculated_df


def _trade_calculator_summary_df(df: pd.DataFrame) -> pd.DataFrame:
    trade_df = _calculate_trade_rows(df)
    trade_df = trade_df[trade_df["SYMBOL"].notna()].copy()
    if trade_df.empty:
        return pd.DataFrame()

    qty = pd.to_numeric(trade_df["QTY"], errors="coerce").fillna(0)
    invested = pd.to_numeric(trade_df["TOTAL INVESTED"], errors="coerce").fillna(0)
    profit = pd.to_numeric(trade_df["PROFIT"], errors="coerce")
    summary_df = (
        trade_df.assign(_QTY=qty, _INVESTED=invested, _PROFIT=profit)
        .groupby("SYMBOL", as_index=False)
        .agg(
            QTY=("_QTY", "sum"),
            **{
                "TOTAL INVESTED": ("_INVESTED", "sum"),
                "PROFIT": ("_PROFIT", "sum"),
            },
        )
    )
    summary_df["AVG BUY"] = summary_df["TOTAL INVESTED"].where(summary_df["QTY"].ne(0)) / summary_df["QTY"]
    summary_df["PROFIT %"] = summary_df["PROFIT"].where(summary_df["TOTAL INVESTED"].ne(0)) / summary_df["TOTAL INVESTED"] * 100
    return summary_df[["SYMBOL", "QTY", "AVG BUY", "TOTAL INVESTED", "PROFIT", "PROFIT %"]]


def _trade_calculator_editor_df(df: pd.DataFrame) -> pd.DataFrame:
    editor_df = df.copy()
    for column in TRADE_CALCULATOR_COLUMNS:
        if column not in editor_df.columns:
            editor_df[column] = None
    return _calculate_trade_rows(editor_df[TRADE_CALCULATOR_COLUMNS])


def _trade_calculator_inputs_changed(previous_df: pd.DataFrame, current_df: pd.DataFrame) -> bool:
    previous_inputs = _calculate_trade_rows(previous_df)[TRADE_CALCULATOR_INPUT_COLUMNS].reset_index(drop=True)
    current_inputs = _calculate_trade_rows(current_df)[TRADE_CALCULATOR_INPUT_COLUMNS].reset_index(drop=True)
    return not previous_inputs.astype("string").fillna("").equals(current_inputs.astype("string").fillna(""))


def render_trade_calculator() -> None:
    st.subheader("TRADE CALCULATOR")
    if "trade_calculator_df" not in st.session_state:
        st.session_state["trade_calculator_df"] = _empty_trade_calculator_df()

    editor_df = _trade_calculator_editor_df(st.session_state["trade_calculator_df"])
    edited_df = st.data_editor(
        editor_df,
        key="trade_calculator_editor",
        width="stretch",
        hide_index=True,
        num_rows="dynamic",
        disabled=TRADE_CALCULATOR_CALCULATED_COLUMNS,
        column_order=TRADE_CALCULATOR_COLUMNS,
        column_config={
            "SYMBOL": st.column_config.TextColumn("SYMBOL", width="medium"),
            "BUY": st.column_config.NumberColumn("BUY", width="small", format="%.2f"),
            "QTY": st.column_config.NumberColumn("QTY", width="small", format="%d"),
            "TOTAL INVESTED": st.column_config.NumberColumn("TOTAL INVESTED", width="small", format="%.2f"),
            "SELL": st.column_config.NumberColumn("SELL", width="small", format="%.2f"),
            "PROFIT": st.column_config.NumberColumn("PROFIT", width="small", format="%.2f"),
            "PROFIT %": st.column_config.NumberColumn("PROFIT %", width="small", format="%.2f%%"),
            "ENTRY": st.column_config.DateColumn("ENTRY", width="small"),
            "EXIT": st.column_config.DateColumn("EXIT", width="small"),
            "DAYS": st.column_config.NumberColumn("DAYS", width="small", format="%d"),
            "SL": st.column_config.NumberColumn("SL", width="small", format="%.2f"),
            "TGT": st.column_config.NumberColumn("TGT", width="small", format="%.2f"),
        },
    )
    if _trade_calculator_inputs_changed(st.session_state["trade_calculator_df"], edited_df):
        st.session_state["trade_calculator_df"] = _calculate_trade_rows(edited_df)
        st.rerun()

    summary_df = _trade_calculator_summary_df(st.session_state["trade_calculator_df"])
    if not summary_df.empty:
        st.dataframe(
            _style_pnl_columns(summary_df),
            width="stretch",
            hide_index=True,
            column_config={
                "SYMBOL": st.column_config.TextColumn("SYMBOL", width="medium"),
                "QTY": st.column_config.NumberColumn("QTY", width="small", format="%d"),
                "AVG BUY": st.column_config.NumberColumn("AVG BUY", width="small", format="%.2f"),
                "TOTAL INVESTED": st.column_config.NumberColumn("TOTAL INVESTED", width="small", format="%.2f"),
                "PROFIT": st.column_config.NumberColumn("PROFIT", width="small", format="%.2f"),
                "PROFIT %": st.column_config.NumberColumn("PROFIT %", width="small", format="%.2f%%"),
            },
        )


def _empty_option_calculator_df() -> pd.DataFrame:
    return pd.DataFrame([{column: None for column in OPTION_CALCULATOR_COLUMNS}])


def _format_manual_spot_distance(breakeven: Any, spot: Any) -> str | None:
    spot_value = pd.to_numeric(spot, errors="coerce")
    breakeven_value = pd.to_numeric(breakeven, errors="coerce")
    if pd.isna(spot_value) or pd.isna(breakeven_value) or spot_value == 0:
        return None

    distance = abs(float(breakeven_value) - float(spot_value))
    distance_text = f"{distance:.0f}" if distance.is_integer() else f"{distance:.2f}"
    return f"{distance_text} [{distance / float(spot_value) * 100:.1f}%]"


def _option_calculator_symbols(df: pd.DataFrame) -> list[str]:
    if df.empty or "Symbol" not in df.columns:
        return []
    return sorted(
        {
            str(symbol).upper().strip()
            for symbol in df["Symbol"].dropna().tolist()
            if str(symbol).strip()
        }
    )


def _fetch_option_calculator_ltp(kite, symbols: list[str]) -> dict[str, dict[str, float]]:
    normalized_symbols = sorted({symbol.upper().strip() for symbol in symbols if symbol.strip()})
    if not normalized_symbols:
        return {}

    option_instruments = [f"NFO:{symbol}" for symbol in normalized_symbols]
    underlying_by_symbol = {
        symbol: underlying
        for symbol in normalized_symbols
        if (underlying := _option_underlying_instrument(symbol))
    }
    instruments = option_instruments + sorted(set(underlying_by_symbol.values()))
    quotes = kite.ltp(*instruments)

    market_data: dict[str, dict[str, float]] = {symbol: {} for symbol in normalized_symbols}
    for symbol in normalized_symbols:
        option_quote = quotes.get(f"NFO:{symbol}")
        if isinstance(option_quote, dict) and option_quote.get("last_price") is not None:
            market_data[symbol]["LTP"] = float(option_quote["last_price"])

        underlying = underlying_by_symbol.get(symbol)
        spot_quote = quotes.get(underlying) if underlying else None
        if isinstance(spot_quote, dict) and spot_quote.get("last_price") is not None:
            market_data[symbol]["Spot"] = float(spot_quote["last_price"])

    return market_data


def _hydrate_option_calculator_ltp(df: pd.DataFrame) -> pd.DataFrame:
    calculated_df = _calculate_option_rows(df)
    symbols = _option_calculator_symbols(calculated_df)
    if not symbols:
        return calculated_df

    try:
        kite, _, _ = bootstrap_kite_app("Zerodha Open Positions")
        market_data = _fetch_option_calculator_ltp(kite, symbols)
    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
            st.error("Your session expired. Please login again to fetch option LTP.")
            st.rerun()
        st.warning(f"Could not fetch option LTP: {exc}")
        return calculated_df

    hydrated_df = calculated_df.copy()
    symbol_key = hydrated_df["Symbol"].astype("string").str.upper().str.strip()
    for symbol, values in market_data.items():
        row_mask = symbol_key.eq(symbol).fillna(False)
        if not row_mask.any():
            continue
        if "LTP" in values:
            hydrated_df.loc[row_mask, "LTP"] = values["LTP"]
            avg_price = pd.to_numeric(hydrated_df.loc[row_mask, "Avg Price"], errors="coerce")
            avg_price_missing = hydrated_df.index.isin(avg_price[avg_price.isna()].index)
            hydrated_df.loc[row_mask & avg_price_missing, "Avg Price"] = values["LTP"]
        if "Spot" in values:
            hydrated_df.loc[row_mask, "Spot"] = values["Spot"]

    return _calculate_option_rows(hydrated_df)


def _calculate_option_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_option_calculator_df()

    calculated_df = df.copy()
    for column in OPTION_CALCULATOR_COLUMNS:
        if column not in calculated_df.columns:
            calculated_df[column] = None

    calculated_df = calculated_df[OPTION_CALCULATOR_COLUMNS]
    calculated_df["Symbol"] = calculated_df["Symbol"].astype("string").str.upper().str.strip()
    calculated_df["Symbol"] = calculated_df["Symbol"].replace({"": pd.NA, "<NA>": pd.NA})

    for column in ["Open Qty", "Avg Price", "LTP", "Spot", "Exit Price"]:
        calculated_df[column] = pd.to_numeric(calculated_df[column], errors="coerce")

    option_details = calculated_df["Symbol"].apply(_parse_option_position)
    calculated_df["Days_Expiry"] = option_details.apply(lambda details: details.get("days_to_expiry"))
    calculated_df["Breakeven"] = calculated_df.apply(
        lambda row: _option_breakeven(row.get("Symbol"), row.get("Avg Price")),
        axis=1,
    )
    calculated_df["Dist_Spot"] = calculated_df.apply(
        lambda row: _format_manual_spot_distance(row.get("Breakeven"), row.get("Spot")),
        axis=1,
    )
    exit_price = calculated_df["Exit Price"]
    valuation_price = exit_price.where(exit_price.notna(), calculated_df["LTP"])
    calculated_df["Invested"] = calculated_df["Open Qty"].abs() * calculated_df["Avg Price"]
    calculated_df["Current"] = calculated_df["Open Qty"] * valuation_price
    calculated_df["P&L"] = calculated_df["Open Qty"] * (valuation_price - calculated_df["Avg Price"])
    calculated_df["P&L %"] = calculated_df["P&L"].where(calculated_df["Invested"].ne(0)) / calculated_df["Invested"] * 100

    blank_rows = calculated_df[OPTION_CALCULATOR_EDITABLE_COLUMNS + OPTION_CALCULATOR_API_COLUMNS].isna().all(axis=1)
    calculated_df.loc[blank_rows, OPTION_CALCULATOR_CALCULATED_COLUMNS] = None
    return calculated_df


def _option_calculator_editable_values_changed(previous_df: pd.DataFrame, current_df: pd.DataFrame) -> bool:
    previous_inputs = _calculate_option_rows(previous_df)[OPTION_CALCULATOR_EDITABLE_COLUMNS].reset_index(drop=True)
    current_inputs = _calculate_option_rows(current_df)[OPTION_CALCULATOR_EDITABLE_COLUMNS].reset_index(drop=True)
    return not previous_inputs.astype("string").fillna("").equals(current_inputs.astype("string").fillna(""))


def render_option_calculator() -> None:
    st.subheader("OPTION CALCULATOR", help=OPTION_SYMBOL_HELP)
    if "option_calculator_df" not in st.session_state:
        st.session_state["option_calculator_df"] = _empty_option_calculator_df()

    editor_df = _calculate_option_rows(st.session_state["option_calculator_df"])
    edited_df = st.data_editor(
        editor_df,
        key="option_calculator_editor",
        width="stretch",
        hide_index=True,
        num_rows="dynamic",
        disabled=OPTION_CALCULATOR_DISABLED_COLUMNS,
        column_order=OPTION_CALCULATOR_COLUMNS,
        column_config={
            "Symbol": st.column_config.TextColumn("Symbol", width="small"),
            "Open Qty": st.column_config.NumberColumn("Open Qty", width="small", format="%d"),
            "Avg Price": st.column_config.NumberColumn("Avg Price", width="small", format="%.2f"),
            "LTP": st.column_config.NumberColumn("LTP", width="small", format="%.2f"),
            "Spot": st.column_config.NumberColumn("Spot", width="small", format="%.2f"),
            "Exit Price": st.column_config.NumberColumn("Exit Price", width="small", format="%.2f"),
            "Days_Expiry": st.column_config.NumberColumn("Days_Expiry", width="small", format="%d"),
            "Breakeven": st.column_config.NumberColumn("Breakeven", width="small", format="%.2f"),
            "Dist_Spot": st.column_config.TextColumn("Dist_Spot", width="small"),
            "Invested": st.column_config.NumberColumn("Invested", width="small", format="%.2f"),
            "Current": st.column_config.NumberColumn("Current", width="small", format="%.2f"),
            "P&L": st.column_config.NumberColumn("P&L", width="small", format="%.2f"),
            "P&L %": st.column_config.NumberColumn("P&L %", width="small", format="%.2f%%"),
        },
    )
    if _option_calculator_editable_values_changed(st.session_state["option_calculator_df"], edited_df):
        st.session_state["option_calculator_df"] = _hydrate_option_calculator_ltp(edited_df)
        st.rerun()


def _last_tuesday(year: int, month: int) -> date:
    expiry = date(year, month, calendar.monthrange(year, month)[1])
    while expiry.weekday() != 1:
        expiry = date.fromordinal(expiry.toordinal() - 1)
    return expiry


def _parse_option_position(symbol: Any) -> dict[str, Any]:
    if symbol is None or pd.isna(symbol):
        return {}
    match = OPTION_SYMBOL_PATTERN.match(str(symbol).upper().strip())
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
    if not instrument:
        return None
    current = pd.to_numeric(underlying_ltp.get(instrument), errors="coerce")
    breakeven_value = pd.to_numeric(breakeven, errors="coerce")
    if pd.isna(current) or pd.isna(breakeven_value) or current == 0:
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


def _live_ltp_refreshed_caption(state_key: str) -> None:
    refreshed_at = st.session_state.get(state_key)
    if refreshed_at:
        st.caption(f"Live LTP refreshed at {pd.Timestamp(refreshed_at).strftime('%Y-%m-%d %H:%M:%S')}")


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
    positions_df["dist_spot"] = positions_df.apply(
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
        "dist_spot",
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
            "days_to_expiry": "Days_Expiry",
            "breakeven": "Breakeven",
            "dist_spot": "Dist_Spot",
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

    metric_col1, metric_col2 = st.columns(2)
    with metric_col1:
        total_pnl = pd.to_numeric(display_df.get("P&L", pd.Series(dtype=float)), errors="coerce").sum()
        st.metric("Total P&L", f"{total_pnl:,.2f}", delta=f"{total_pnl:,.2f}")
    with metric_col2:
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
            "Days_Expiry": st.column_config.NumberColumn("Days_Expiry", width="small", format="%d"),
            "Breakeven": st.column_config.NumberColumn("Breakeven", width="small", format="%.2f"),
            "Dist_Spot": st.column_config.TextColumn("Dist_Spot", width="small"),
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
        st.session_state["kite_open_positions_ltp_refreshed_at"] = pd.Timestamp.now().isoformat()
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
    fetch_col, as_of_col = st.columns([1, 3], vertical_alignment="center")
    with fetch_col:
        if st.button("Fetch Open Positions", type="primary"):
            fetch_open_positions()
    with as_of_col:
        _live_ltp_refreshed_caption("kite_open_positions_ltp_refreshed_at")

    open_positions = st.session_state.get("kite_open_positions")
    ltp_error = st.session_state.get("kite_open_positions_ltp_error")
    if open_positions is None:
        st.info("Fetch open positions from Kite.")
        render_option_calculator()
        st.divider()
        render_trade_calculator()
        return

    if ltp_error:
        st.warning(f"Could not load underlying LTP for distance calculation: {ltp_error}")
    display_open_positions(
        open_positions,
        st.session_state.get("kite_open_positions_underlying_ltp", {}),
    )
    st.divider()
    render_option_calculator()
    st.divider()
    render_trade_calculator()
