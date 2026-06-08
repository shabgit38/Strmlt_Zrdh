import json
import math
import calendar
from datetime import date, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st

from kite_auth import get_secret_value


HOLDINGS_TABLE_NAME = "holdings_breakdown"
HOLDINGS_BREAKDOWN_DF_STATE_KEY = "holdings_breakdown_df"
HOLDINGS_BREAKDOWN_VIEW_STATE_KEY = "holdings_breakdown_view_enabled"
HOLDINGS_COLUMN_MAP = {
    "Row Type": "row_type",
    "Symbol": "symbol",
    "Sector": "sector",    
    "ISIN": "isin",
    "Total Qty": "total_qty",
    "Buy Avg": "buy_avg",
    "Invested": "invested",
    "LTP": "ltp",
    "Present": "present_value",
    "P&L": "pnl",
    "P&L %": "pnl_pct",
    "P&L chg": "pnl_pct",
    "Date": "trade_date",
    "Batch Qty": "batch_qty",
    "Batch Price": "batch_price",
    "Age(Days)": "age_days",
    "Batch P&L": "batch_pnl",
    "Batch P&L %": "batch_pnl_pct",
    "Present Age": "present_age",
}
REQUIRED_HOLDINGS_COLUMNS = {"Row Type", "Symbol"}
NUMERIC_HOLDINGS_COLUMNS = [    
    "buy_avg",
    "invested",
    "ltp",
    "exit_price",
    "present_value",
    "pnl",    
    "pnl_pct",
    "batch_price",
    "batch_pnl",
    "batch_pnl_pct",
]
INTEGER_HOLDINGS_COLUMNS = ["total_qty","age_days","batch_qty", "exit_qty"]

def _json_safe_value(value: Any) -> Any:
    """Convert pandas/numpy values into JSON-safe primitives for Supabase."""
    if pd.isna(value):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _chunk_records(records: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    return [records[i : i + chunk_size] for i in range(0, len(records), chunk_size)]


def _get_supabase_holdings_config() -> tuple[str, str, str]:
    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_HOLDINGS_TABLE_NAME").strip() or HOLDINGS_TABLE_NAME

    if not supabase_url or not supabase_key:
        raise ValueError(
            "Missing Supabase config. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "in .streamlit/secrets.toml or environment variables."
        )

    return supabase_url, supabase_key, table_name


def _supabase_headers(supabase_key: str, *, write: bool = False) -> dict[str, str]:
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
    }
    if write:
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "return=minimal"
    return headers


def _record_numeric_value(value: Any) -> float | None:
    value = _json_safe_value(value)
    if value is None:
        return None
    converted = pd.to_numeric(value, errors="coerce")
    if pd.isna(converted):
        return None
    return float(converted)


def _record_integer_value(value: Any) -> int | None:
    value = _record_numeric_value(value)
    if value is None:
        return None
    return int(value)


def _json_safe_record(record: dict[str, Any]) -> dict[str, Any]:
    safe_record = {key: _json_safe_value(value) for key, value in record.items()}
    for column in NUMERIC_HOLDINGS_COLUMNS:
        if column in safe_record:
            safe_record[column] = _record_numeric_value(safe_record[column])
    for column in INTEGER_HOLDINGS_COLUMNS:
        if column in safe_record:
            safe_record[column] = _record_integer_value(safe_record[column])
    return safe_record


def _supabase_write_record(record: dict[str, Any]) -> dict[str, Any]:
    safe_record = _json_safe_record(record)
    return {
        key: value
        for key, value in safe_record.items()
        if key not in SUPABASE_EXCLUDED_WRITE_COLUMNS
    }


def _holdings_upload_match_key(record: dict[str, Any]) -> tuple[Any, ...] | None:
    row_type = str(record.get("row_type") or "").upper().strip()
    symbol = str(record.get("symbol") or "").upper().strip()
    if not row_type or not symbol:
        return None
    #summary row updates only if row_type = SUMMARY and symbol match.
    if row_type == "SUMMARY":
        return (row_type, symbol)
    #batch row updates only if all four match: row_type = BATCH, symbol, and trade_date, and batch_price. This allows multiple batch rows for same symbol to be updated correctly.
    if row_type == "BATCH":
        return (
            row_type,
            symbol,
            _normalize_trade_date(record.get("trade_date")),
            _record_numeric_value(record.get("batch_price")),
        )
    return (row_type, symbol, _normalize_trade_date(record.get("trade_date")))


def _holdings_upload_patch(
    existing_record: dict[str, Any],
    uploaded_record: dict[str, Any],
    update_columns: set[str] | None = None,
) -> dict[str, Any]:
    existing_safe = _supabase_write_record(existing_record)
    uploaded_safe = _supabase_write_record(uploaded_record)
    if update_columns is not None:
        uploaded_safe = _filter_record_columns(uploaded_safe, update_columns)
    patch: dict[str, Any] = {}

    for key, uploaded_value in uploaded_safe.items():
        if existing_safe.get(key) != uploaded_value:
            patch[key] = uploaded_value

    return patch


def _mapped_holdings_upload_columns(df: pd.DataFrame) -> set[str]:
    mapped_columns: set[str] = set()
    for column in df.columns.astype(str).str.strip():
        if column.startswith("Unnamed"):
            continue
        mapped_column = HOLDINGS_COLUMN_MAP.get(column)
        if mapped_column is not None:
            mapped_columns.add(mapped_column)
    return mapped_columns


def _filter_record_columns(record: dict[str, Any], columns: set[str]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key in columns}


def _read_holdings_breakdown_upload(uploaded_file) -> pd.DataFrame:
    filename = uploaded_file.name.lower()
    if filename.endswith(".csv"):
        return pd.read_csv(uploaded_file)
    if filename.endswith(".xlsx"):
        return pd.read_excel(uploaded_file)
    raise ValueError("Upload a CSV or XLSX file.")


def _normalize_trade_date(value: Any) -> Any:
    if pd.isna(value) or value == "":
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return _json_safe_value(value)
    if isinstance(value, (int, float)) and math.isfinite(value):
        return (pd.Timestamp("1899-12-30") + pd.to_timedelta(int(value), unit="D")).date().isoformat()

    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def _is_exited_status(value: Any) -> bool:
    return str(value or "").strip().upper() == "EXITED"


def _parse_trade_date(value: Any) -> date | None:
    if value is None:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _holding_age_days(trade_date: Any) -> int | None:
    parsed_trade_date = _parse_trade_date(trade_date)
    if parsed_trade_date is None:
        return None
    return max((date.today() - parsed_trade_date).days, 0)


def _holding_present_age(trade_date: Any) -> str | None:
    parsed_trade_date = _parse_trade_date(trade_date)
    if parsed_trade_date is None:
        return None

    today = date.today()
    if parsed_trade_date > today:
        return "0 Years, 0 Months, 0 Days"

    years = today.year - parsed_trade_date.year
    months = today.month - parsed_trade_date.month
    days = today.day - parsed_trade_date.day

    if days < 0:
        months -= 1
        previous_month = today.month - 1 or 12
        previous_month_year = today.year if today.month > 1 else today.year - 1
        days += calendar.monthrange(previous_month_year, previous_month)[1]

    if months < 0:
        years -= 1
        months += 12

    return f"{years} Years, {months} Months, {days} Days"


def clean_holdings_breakdown_for_supabase(df: pd.DataFrame) -> pd.DataFrame:
        
    #strips column names
    #removes blank rows and Unnamed columns
    df = df.copy()
    df.columns = df.columns.astype(str).str.strip()
    df = df.dropna(how="all")
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
    
    #validates required columns: Row Type, Symbol
    missing_columns = REQUIRED_HOLDINGS_COLUMNS - set(df.columns)
    if missing_columns:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing_columns))}")
    
    #renames columns using HOLDINGS_COLUMN_MAP and removes duplicates by preferring the first occurrence
    df = df.rename(columns=HOLDINGS_COLUMN_MAP)
    if df.columns.duplicated().any():
        deduplicated_df = pd.DataFrame(index=df.index)
        for column in dict.fromkeys(df.columns):
            same_name_columns = df.loc[:, df.columns == column]
            if same_name_columns.shape[1] == 1:
                deduplicated_df[column] = same_name_columns.iloc[:, 0]
            else:
                deduplicated_df[column] = same_name_columns.bfill(axis=1).iloc[:, 0]
        df = deduplicated_df

    expected_columns = list(dict.fromkeys(HOLDINGS_COLUMN_MAP.values()))
    df = df[[column for column in expected_columns if column in df.columns]]

    df = df[df["row_type"].notna() & df["symbol"].notna()]
    if df.empty:
        raise ValueError("No holdings rows found after removing blank rows.")

    if "sector" in df.columns:
        symbol_key = _normalized_symbol_series(df["symbol"])
        df["sector"] = df["sector"].apply(_json_safe_value)
        summary_rows = df["row_type"].astype(str).str.upper().str.strip().eq("SUMMARY")
        sector_by_symbol = (
            df.loc[summary_rows]
            .assign(symbol_key=symbol_key[summary_rows])
            .dropna(subset=["sector"])
            .drop_duplicates("symbol_key")
            .set_index("symbol_key")["sector"]
            .to_dict()
        )
        df["sector"] = df["sector"].where(df["sector"].notna(), symbol_key.map(sector_by_symbol))


    #converts numeric/integer columns
    for column in NUMERIC_HOLDINGS_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    for column in INTEGER_HOLDINGS_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").apply(
                lambda value: int(value) if pd.notna(value) else None
            )

    #normalizes trade_date #computes age_days and present_age
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].apply(_normalize_trade_date)
        df["age_days"] = df["trade_date"].apply(_holding_age_days)
        df["present_age"] = df["trade_date"].apply(_holding_present_age)
    else:
        df["age_days"] = None
        df["present_age"] = None

    #JSON-cleans values
    for column in df.columns:
        df[column] = df[column].apply(_json_safe_value)

    return df


def _normalized_symbol_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.upper().str.strip()


def _normalized_symbol_value(symbol: Any) -> str:
    return str(symbol or "").upper().strip()


def _ltp_match_symbol(symbol: Any) -> str:
    normalized_symbol = _normalized_symbol_value(symbol)
    for suffix in ("-RR", "-IV"):
        if normalized_symbol.endswith(suffix):
            return normalized_symbol.removesuffix(suffix)
    return normalized_symbol


def _ltp_lookup_maps(ltp_by_symbol: dict[str, float]) -> tuple[dict[str, float], dict[str, float]]:
    exact_ltp_by_symbol: dict[str, float] = {}
    fallback_ltp_by_symbol: dict[str, float] = {}
    for symbol, ltp in (ltp_by_symbol or {}).items():
        if symbol is None or pd.isna(ltp):
            continue
        exact_key = _normalized_symbol_value(symbol)
        fallback_key = _ltp_match_symbol(symbol)
        if exact_key:
            exact_ltp_by_symbol[exact_key] = ltp
        if fallback_key and fallback_key not in fallback_ltp_by_symbol:
            fallback_ltp_by_symbol[fallback_key] = ltp
    return exact_ltp_by_symbol, fallback_ltp_by_symbol


def _lookup_ltp(ltp_by_symbol: dict[str, float], symbol: Any, default: Any = None) -> Any:
    exact_ltp_by_symbol, fallback_ltp_by_symbol = _ltp_lookup_maps(ltp_by_symbol)
    symbol_key = _normalized_symbol_value(symbol)
    if symbol_key in exact_ltp_by_symbol:
        return exact_ltp_by_symbol[symbol_key]
    return fallback_ltp_by_symbol.get(_ltp_match_symbol(symbol), default)


def enrich_holdings_breakdown_with_ltp(
    df: pd.DataFrame, ltp_by_symbol: dict[str, float]
) -> tuple[pd.DataFrame, list[str]]:
    
    #maps live LTP by symbol
    df = df.copy()
    if "symbol" not in df.columns:
        return df, []

    normalized_ltp_by_symbol, fallback_ltp_by_symbol = _ltp_lookup_maps(ltp_by_symbol)
    symbol_key = _normalized_symbol_series(df["symbol"])
    live_ltp = pd.to_numeric(symbol_key.map(normalized_ltp_by_symbol), errors="coerce")
    fallback_symbol_key = symbol_key.apply(_ltp_match_symbol)
    fallback_live_ltp = pd.to_numeric(fallback_symbol_key.map(fallback_ltp_by_symbol), errors="coerce")
    live_ltp = live_ltp.where(live_ltp.notna(), fallback_live_ltp)
    row_type = df["row_type"].astype(str).str.upper().str.strip()
    exited_rows = df.get("holding_status", pd.Series(index=df.index, dtype=object)).apply(_is_exited_status)
    active_rows = ~exited_rows
    matched_rows = live_ltp.notna() & active_rows

    if "ltp" not in df.columns:
        df["ltp"] = None
    df.loc[matched_rows, "ltp"] = live_ltp[matched_rows]

    summary_rows = matched_rows & row_type.eq("SUMMARY")
    batch_rows = matched_rows & row_type.eq("BATCH")
    exited_summary_rows = exited_rows & row_type.eq("SUMMARY")
    exited_batch_rows = exited_rows & row_type.eq("BATCH")

    #recalculates present_value, pnl, pnl_pct
    #recalculates batch-level batch_pnl, batch_pnl_pct
   
    for column in ["total_qty", "invested", "batch_qty", "exit_qty", "batch_price", "ltp", "exit_price"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    if "present_value" not in df.columns:
        df["present_value"] = None
    if "pnl" not in df.columns:
        df["pnl"] = None
    if "pnl_pct" not in df.columns:
        df["pnl_pct"] = None
    if "batch_pnl" not in df.columns:
        df["batch_pnl"] = None
    if "batch_pnl_pct" not in df.columns:
        df["batch_pnl_pct"] = None

    if {"total_qty", "ltp"}.issubset(df.columns):
        df.loc[summary_rows, "present_value"] = (
            df.loc[summary_rows, "total_qty"] * df.loc[summary_rows, "ltp"]
        )

    if {"present_value", "invested"}.issubset(df.columns):
        df.loc[summary_rows, "pnl"] = (
            df.loc[summary_rows, "present_value"] - df.loc[summary_rows, "invested"]
        )

    if {"pnl", "invested"}.issubset(df.columns):
        invested = df.loc[summary_rows, "invested"]
        df.loc[summary_rows, "pnl_pct"] = (
            df.loc[summary_rows, "pnl"].where(invested.ne(0)) / invested * 100
        )

    if {"batch_qty", "ltp"}.issubset(df.columns):
        df.loc[batch_rows, "present_value"] = (
            df.loc[batch_rows, "batch_qty"] * df.loc[batch_rows, "ltp"]
        )

    if {"batch_qty", "ltp", "batch_price"}.issubset(df.columns):
        df.loc[batch_rows, "batch_pnl"] = df.loc[batch_rows, "batch_qty"] * (
            df.loc[batch_rows, "ltp"] - df.loc[batch_rows, "batch_price"]
        )

    if {"ltp", "batch_price"}.issubset(df.columns):
        batch_price = df.loc[batch_rows, "batch_price"]
        df.loc[batch_rows, "batch_pnl_pct"] = (
            (df.loc[batch_rows, "ltp"] - batch_price).where(batch_price.ne(0))
            / batch_price
            * 100
        )

    if {"total_qty", "exit_price"}.issubset(df.columns):
        df.loc[exited_summary_rows, "present_value"] = (
            df.loc[exited_summary_rows, "total_qty"] * df.loc[exited_summary_rows, "exit_price"]
        )

    if {"present_value", "invested"}.issubset(df.columns):
        df.loc[exited_summary_rows, "pnl"] = (
            df.loc[exited_summary_rows, "present_value"] - df.loc[exited_summary_rows, "invested"]
        )

    if {"pnl", "invested"}.issubset(df.columns):
        invested = df.loc[exited_summary_rows, "invested"]
        df.loc[exited_summary_rows, "pnl_pct"] = (
            df.loc[exited_summary_rows, "pnl"].where(invested.ne(0)) / invested * 100
        )

    #handles exited rows differently using exit_price instead of live LTP, 
    #and also adjusts batch-level quantities and P&L based on exit_qty if available
    exited_qty = (
        pd.to_numeric(df.loc[exited_batch_rows, "exit_qty"], errors="coerce")
        if "exit_qty" in df.columns
        else pd.Series(index=df.loc[exited_batch_rows].index, dtype=float)
    )
    exited_qty = exited_qty.fillna(pd.to_numeric(df.loc[exited_batch_rows, "batch_qty"], errors="coerce"))
    df["_exited_summary_qty"] = 0
    df.loc[exited_batch_rows, "_exited_summary_qty"] = exited_qty

    if {"batch_qty", "exit_price"}.issubset(df.columns):
        df.loc[exited_batch_rows, "present_value"] = (
            exited_qty * df.loc[exited_batch_rows, "exit_price"]
        )

    if {"batch_qty", "exit_price", "batch_price"}.issubset(df.columns):
        df.loc[exited_batch_rows, "batch_pnl"] = exited_qty * (
            df.loc[exited_batch_rows, "exit_price"] - df.loc[exited_batch_rows, "batch_price"]
        )

    if {"exit_price", "batch_price"}.issubset(df.columns):
        batch_price = df.loc[exited_batch_rows, "batch_price"]
        df.loc[exited_batch_rows, "batch_pnl_pct"] = (
            (df.loc[exited_batch_rows, "exit_price"] - batch_price).where(batch_price.ne(0))
            / batch_price
            * 100
        )

    if {"batch_qty", "exit_qty"}.issubset(df.columns):
        remaining_qty = (
            pd.to_numeric(df.loc[exited_batch_rows, "batch_qty"], errors="coerce").fillna(0)
            - exited_qty.fillna(0)
        ).clip(lower=0)
        partially_exited_rows = exited_batch_rows & remaining_qty.gt(0).reindex(df.index, fill_value=False)
        df.loc[partially_exited_rows, "batch_qty"] = remaining_qty.reindex(df.index)[partially_exited_rows]
        df.loc[partially_exited_rows, "holding_status"] = None
        partial_matched_rows = partially_exited_rows & live_ltp.notna()
        df.loc[partial_matched_rows, "ltp"] = live_ltp[partial_matched_rows]
        if {"batch_qty", "ltp"}.issubset(df.columns):
            df.loc[partial_matched_rows, "present_value"] = (
                df.loc[partial_matched_rows, "batch_qty"] * df.loc[partial_matched_rows, "ltp"]
            )
        if {"batch_qty", "ltp", "batch_price"}.issubset(df.columns):
            df.loc[partial_matched_rows, "batch_pnl"] = df.loc[partial_matched_rows, "batch_qty"] * (
                df.loc[partial_matched_rows, "ltp"] - df.loc[partial_matched_rows, "batch_price"]
            )
        if {"ltp", "batch_price"}.issubset(df.columns):
            batch_price = df.loc[partial_matched_rows, "batch_price"]
            df.loc[partial_matched_rows, "batch_pnl_pct"] = (
                (df.loc[partial_matched_rows, "ltp"] - batch_price).where(batch_price.ne(0))
                / batch_price
                * 100
            )
        active_rows = active_rows | partially_exited_rows
        matched_rows = matched_rows | partial_matched_rows

    if "total_qty" in df.columns:
        df.loc[exited_summary_rows, "total_qty"] = 0

    unmatched_symbols = (
        sorted(symbol_key[active_rows & ~matched_rows].dropna().unique().tolist())
        if normalized_ltp_by_symbol
        else []
    )
    return df, unmatched_symbols

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
    pnl_columns = [
        column
        for column in ["batch_pnl", "batch_pnl_pct", "pnl", "pnl_pct", "Batch P&L", "Batch P&L %", "P&L", "P&L %","DayChg %"]
        if column in df.columns
    ]
    formatters = {
        column: _format_display_value
        for column in df.columns
        if column not in {"batch_pnl_pct", "pnl_pct", "Batch P&L %", "P&L %"}
    }
    for column in ["batch_pnl_pct", "pnl_pct", "Batch P&L %", "P&L %"]:
        if column in df.columns:
            formatters[column] = _format_percent_value

    styler = df.style.format(formatters, na_rep="-")
    for column in pnl_columns:
        styler = styler.map(lambda value: f"color: {_pnl_color(value)}; font-weight: 600", subset=[column])
    return styler


def _dataframe_height(row_count: int, *, min_rows: int = 1, max_rows: int | None = None) -> int:
    visible_rows = max(row_count, min_rows)
    if max_rows is not None:
        visible_rows = min(visible_rows, max_rows)
    header_height = 38
    row_height = 35
    border_padding = 4
    return header_height + (visible_rows + 1) * row_height + border_padding

def _summary_display_df(summary: pd.Series) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Qty": summary.get("total_qty"),
                "Buy Avg": summary.get("buy_avg"),
                "Invested": summary.get("invested"),
                "Present": summary.get("present_value"),
                "LTP": summary.get("ltp"),
                "P&L": summary.get("pnl"),
                "P&L %": summary.get("pnl_pct"),
            }
        ]
    )


def _summary_column_config() -> dict[str, Any]:
    return {
        "Qty": st.column_config.NumberColumn("Qty", width="small", format="%d"),
        "Buy Avg": st.column_config.NumberColumn("Buy Avg", width="small", format="%.2f"),
        "Entry Avg": st.column_config.NumberColumn("Entry Avg", width="small", format="%.2f"),
        "Invested": st.column_config.NumberColumn("Invested", width="small", format="%.2f"),
        "Present": st.column_config.NumberColumn("Present", width="small", format="%.2f"),
        "LTP": st.column_config.NumberColumn("LTP", width="small", format="%.2f"),
        "Exit Date": st.column_config.DateColumn("Exit Date", width="small"),
        "Exit Price": st.column_config.NumberColumn("Exit Price", width="small", format="%.2f"),
        "P&L": st.column_config.NumberColumn("P&L", width="small", format="%.2f"),
        "P&L %": st.column_config.NumberColumn("P&L %", width="small", format="%.2f%%"),
    }

def _summary_expander_label(summary: pd.Series, batch_count: int) -> str:
    return (
        f"{summary.get('symbol', '-')}"
        
    )


def _row_id(row: pd.Series) -> Any:
    return row.get("id")


def _row_has_id(row: pd.Series) -> bool:
    row_id = _row_id(row)
    return row_id is not None and not pd.isna(row_id)


def _float_input_value(value: Any, default: float = 0.0) -> float:
    converted = pd.to_numeric(value, errors="coerce")
    if pd.isna(converted):
        return default
    return float(converted)


def _int_input_value(value: Any, default: int = 0) -> int:
    converted = pd.to_numeric(value, errors="coerce")
    if pd.isna(converted):
        return default
    return int(converted)


def _date_input_value(value: Any) -> date:
    parsed = _parse_trade_date(value)
    return parsed or date.today()


def _recompute_breakdown_record(record: dict[str, Any], ltp_by_symbol: dict[str, float]) -> dict[str, Any]:
    record = _json_safe_record(record)
    symbol_key = str(record.get("symbol") or "").upper().strip()
    ltp = _lookup_ltp(ltp_by_symbol, symbol_key, record.get("ltp"))
    ltp = _record_numeric_value(ltp)
    if ltp is not None:
        record["ltp"] = ltp

    row_type = str(record.get("row_type") or "").upper()
    if record.get("trade_date") is not None:
        record["trade_date"] = _normalize_trade_date(record.get("trade_date"))
        record["age_days"] = _holding_age_days(record.get("trade_date"))
        record["present_age"] = _holding_present_age(record.get("trade_date"))

    if row_type == "SUMMARY":
        total_qty = _record_integer_value(record.get("total_qty"))
        buy_avg = _record_numeric_value(record.get("buy_avg"))
        if total_qty is not None and buy_avg is not None:
            record["invested"] = total_qty * buy_avg
        if total_qty is not None and ltp is not None:
            record["present_value"] = total_qty * ltp
        if record.get("present_value") is not None and record.get("invested") is not None:
            record["pnl"] = float(record["present_value"]) - float(record["invested"])
        if record.get("pnl") is not None and record.get("invested") not in (None, 0):
            record["pnl_pct"] = float(record["pnl"]) / float(record["invested"]) * 100

    if row_type == "BATCH":
        batch_qty = _record_integer_value(record.get("batch_qty"))
        batch_price = _record_numeric_value(record.get("batch_price"))
        if batch_qty is not None and ltp is not None:
            record["present_value"] = batch_qty * ltp
        if batch_qty is not None and batch_price is not None and ltp is not None:
            record["batch_pnl"] = batch_qty * (ltp - batch_price)
        if batch_price not in (None, 0) and ltp is not None:
            record["batch_pnl_pct"] = (ltp - float(batch_price)) / float(batch_price) * 100

    return record


def _recalculate_summary_from_supabase_batches(summary: pd.Series, ltp_by_symbol: dict[str, float]) -> None:
    if not _row_has_id(summary):
        return

    symbol = summary.get("symbol")
    symbol_key = str(symbol or "").upper().strip()
    if not symbol_key:
        return

    holdings_breakdown_df = load_holdings_breakdown_from_supabase()
    if holdings_breakdown_df.empty or "symbol" not in holdings_breakdown_df.columns:
        return

    row_type = holdings_breakdown_df.get("row_type", pd.Series(dtype=str)).astype(str).str.upper()
    symbol_series = holdings_breakdown_df["symbol"].astype(str).str.upper().str.strip()
    batch_df = holdings_breakdown_df[row_type.eq("BATCH") & symbol_series.eq(symbol_key)].copy()

    if batch_df.empty:
        total_qty = 0
        invested = 0.0
    else:
        qty = pd.to_numeric(batch_df.get("batch_qty", pd.Series(dtype=float)), errors="coerce").fillna(0)
        if "holding_status" in batch_df.columns:
            exited_rows = batch_df["holding_status"].apply(_is_exited_status)
            exit_qty = pd.to_numeric(batch_df.get("exit_qty", pd.Series(dtype=float)), errors="coerce").fillna(0)
            qty = qty.where(~exited_rows, (qty - exit_qty).clip(lower=0))
        price = pd.to_numeric(batch_df.get("batch_price", pd.Series(dtype=float)), errors="coerce").fillna(0)
        total_qty = int(qty.sum())
        invested = float((qty * price).sum())

    buy_avg = invested / total_qty if total_qty else 0.0
    ltp = _record_numeric_value(_lookup_ltp(ltp_by_symbol, symbol_key, summary.get("ltp")))
    present_value = total_qty * ltp if ltp is not None else None
    pnl = present_value - invested if present_value is not None else None
    pnl_pct = pnl / invested * 100 if pnl is not None and invested else None

    record = {
        "row_type": "SUMMARY",
        "symbol": symbol,
        "isin": summary.get("isin"),
        "total_qty": total_qty,
        "buy_avg": buy_avg,
        "invested": invested,
        "ltp": ltp,
        "present_value": present_value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
    }
    update_holdings_breakdown_row(_row_id(summary), record)


def _render_summary_form(summary: pd.Series, *, key_prefix: str, ltp_by_symbol: dict[str, float]) -> None:
    row_id = _row_id(summary)
    with st.form(f"{key_prefix}_summary_form"):
        input_cols = st.columns([1.2, 1.2, 0.9, 0.9, 4])
        with input_cols[0]:
            total_qty = st.number_input("Total Qty", value=_int_input_value(summary.get("total_qty")), step=1)
        with input_cols[1]:
            buy_avg = st.number_input("Buy Avg", value=_float_input_value(summary.get("buy_avg")), format="%.2f")
        with input_cols[2]:
            submitted = st.form_submit_button("Save holding", type="primary")
        with input_cols[3]:
            cancelled = st.form_submit_button("Cancel")

    if cancelled:
        st.session_state.pop("holdings_breakdown_editor", None)
        st.rerun()

    if submitted:
        record = _recompute_breakdown_record(
            {
                "row_type": "SUMMARY",
                "symbol": summary.get("symbol"),
                "isin": summary.get("isin"),
                "total_qty": total_qty,
                "buy_avg": buy_avg,
                "ltp": summary.get("ltp"),
            },
            ltp_by_symbol,
        )
        update_holdings_breakdown_row(row_id, record)
        _refresh_holdings_breakdown_state_for_symbols([summary.get("symbol")])
        st.session_state[HOLDINGS_BREAKDOWN_VIEW_STATE_KEY] = True
        st.session_state.pop("holdings_breakdown_editor", None)
        st.rerun()


def _render_batch_form(
    batch: pd.Series | None,
    *,
    key_prefix: str,
    summary: pd.Series,
    ltp_by_symbol: dict[str, float],
) -> None:
    is_edit = batch is not None
    source = batch if batch is not None else summary
    with st.form(f"{key_prefix}_batch_form"):
        input_cols = st.columns([1.2, 1.1, 1.1, 0.8, 0.8, 3])
        with input_cols[0]:
            trade_date = st.date_input("Date", value=_date_input_value(source.get("trade_date")))
        with input_cols[1]:
            batch_qty = st.number_input("Batch Qty", value=_int_input_value(source.get("batch_qty")), step=1)
        with input_cols[2]:
            batch_price = st.number_input("Batch Price", value=_float_input_value(source.get("batch_price")), format="%.2f")
        with input_cols[3]:
            submitted = st.form_submit_button("Save batch", type="primary")
        with input_cols[4]:
            cancelled = st.form_submit_button("Cancel")

    if cancelled:
        st.session_state.pop("holdings_breakdown_editor", None)
        st.rerun()

    if submitted:
        record = _recompute_breakdown_record(
            {
                "row_type": "BATCH",
                "symbol": source.get("symbol"),
                "isin": source.get("isin"),
                "trade_date": trade_date,
                "batch_qty": batch_qty,
                "batch_price": batch_price,
                "ltp": source.get("ltp"),
            },
            ltp_by_symbol,
        )
        if is_edit:
            update_holdings_breakdown_row(_row_id(batch), record)
        else:
            insert_holdings_breakdown_row(record)
        _recalculate_summary_from_supabase_batches(summary, ltp_by_symbol)
        _refresh_holdings_breakdown_state_for_symbols([source.get("symbol")])
        st.session_state[HOLDINGS_BREAKDOWN_VIEW_STATE_KEY] = True
        st.session_state.pop("holdings_breakdown_editor", None)
        st.rerun()


def _exit_quantity_for_row(row: pd.Series) -> int:
    return _int_input_value(row.get("exit_qty"), _int_input_value(row.get("batch_qty"), _int_input_value(row.get("total_qty"))))


def _exit_batch_record(row: pd.Series, exit_date: date, exit_price: float, exit_qty: int) -> dict[str, Any]:
    batch_price = _record_numeric_value(row.get("batch_price"))
    invested = exit_qty * batch_price if batch_price is not None else None
    exit_value = exit_qty * exit_price
    batch_pnl = exit_value - invested if invested is not None else None
    batch_pnl_pct = batch_pnl / invested * 100 if batch_pnl is not None and invested else None

    return {
        "row_type": "BATCH",
        "symbol": row.get("symbol"),
        "sector": row.get("sector"),
        "isin": row.get("isin"),
        "trade_date": row.get("trade_date"),
        "batch_qty": exit_qty,
        "batch_price": batch_price,
        "present_value": exit_value,
        "batch_pnl": batch_pnl,
        "batch_pnl_pct": batch_pnl_pct,
        "holding_status": "Exited",
        "exit_date": _normalize_trade_date(exit_date),
        "exit_price": exit_price,
        "exit_qty": exit_qty,
    }


def _render_exit_form(
    rows: list[pd.Series],
    *,
    key_prefix: str,
    summary: pd.Series | None = None,
    ltp_by_symbol: dict[str, float] | None = None,
) -> None:
    first_row = rows[0] if rows else pd.Series(dtype=object)
    is_single_batch_exit = len(rows) == 1 and str(first_row.get("row_type") or "").upper() == "BATCH"
    with st.form(f"{key_prefix}_exit_form"):
        input_cols = st.columns([1.2, 1.1, 1.0, 0.8, 0.8, 3])
        with input_cols[0]:
            exit_date = st.date_input("Exit Date", value=date.today())
        with input_cols[1]:
            exit_price = st.number_input("Exit Price", value=0.0, format="%.2f")
        with input_cols[2]:
            exit_qty = st.number_input(
                "Exit Qty",
                value=_exit_quantity_for_row(first_row),
                step=1,
                disabled=not is_single_batch_exit,
            )
        with input_cols[3]:
            submitted = st.form_submit_button("Save exit", type="primary")
        with input_cols[4]:
            cancelled = st.form_submit_button("Cancel")

    if cancelled:
        st.session_state.pop("holdings_breakdown_editor", None)
        st.rerun()

    if submitted:
        for row in rows:
            row_id = _row_id(row)
            row_exit_qty = exit_qty if is_single_batch_exit else _exit_quantity_for_row(row)
            row_type = str(row.get("row_type") or "").upper()
            batch_qty = _int_input_value(row.get("batch_qty"))

            if row_type == "BATCH" and is_single_batch_exit and row_exit_qty < batch_qty:
                remaining_qty = batch_qty - row_exit_qty
                active_record = _recompute_breakdown_record(
                    {
                        "row_type": "BATCH",
                        "symbol": row.get("symbol"),
                        "sector": row.get("sector"),
                        "isin": row.get("isin"),
                        "trade_date": row.get("trade_date"),
                        "batch_qty": remaining_qty,
                        "batch_price": row.get("batch_price"),
                        "ltp": row.get("ltp"),
                        "holding_status": None,
                        "exit_date": None,
                        "exit_price": None,
                        "exit_qty": None,
                    },
                    ltp_by_symbol or {},
                )
                update_holdings_breakdown_row(row_id, active_record)
                insert_holdings_breakdown_row(_exit_batch_record(row, exit_date, exit_price, row_exit_qty))
            else:
                if row_type == "BATCH":
                    record = _exit_batch_record(row, exit_date, exit_price, row_exit_qty)
                else:
                    record = {
                        "holding_status": "Exited",
                        "exit_date": _normalize_trade_date(exit_date),
                        "exit_price": exit_price,
                        "exit_qty": row_exit_qty,
                    }
                update_holdings_breakdown_row(row_id, record)

        if summary is not None:
            _recalculate_summary_from_supabase_batches(summary, ltp_by_symbol or {})
        affected_symbols = {
            str(row.get("symbol") or "").upper().strip()
            for row in rows
            if str(row.get("symbol") or "").strip()
        }
        if summary is not None and str(summary.get("symbol") or "").strip():
            affected_symbols.add(str(summary.get("symbol")).upper().strip())
        _refresh_holdings_breakdown_state_for_symbols(sorted(affected_symbols))
        st.session_state[HOLDINGS_BREAKDOWN_VIEW_STATE_KEY] = True
        st.session_state.pop("holdings_breakdown_editor", None)
        st.rerun()


def _empty_add_breakdown_entries_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Row Type": "SUMMARY",
                "Symbol": "",
                "Sector": "",
                "Total Qty": None,
                "Buy Avg": None,
                "Invested": None,
                "Batch Qty": None,
                "Batch Price": None,
                "Exit?": False,
                "Exit Date": None,
                "Exit Qty": None,
                "Exit Price": None,
            }
        ]
    )


def _add_breakdown_entries_column_config() -> dict[str, Any]:
    return {
        "Row Type": st.column_config.SelectboxColumn("Row Type", options=["SUMMARY", "BATCH"], required=True),
        "Symbol": st.column_config.TextColumn("Symbol", required=True),
        "Sector": st.column_config.TextColumn("Sector"),
        "Total Qty": st.column_config.NumberColumn("Total Qty", min_value=0, step=1, format="%d"),
        "Buy Avg": st.column_config.NumberColumn("Buy Avg", min_value=0.0, format="%.2f"),
        "Invested": st.column_config.NumberColumn("Invested", format="%.2f"),
        "Batch Qty": st.column_config.NumberColumn("Batch Qty", min_value=0, step=1, format="%d"),
        "Batch Price": st.column_config.NumberColumn("Batch Price", min_value=0.0, format="%.2f"),
        "Exit?": st.column_config.CheckboxColumn("Exit?"),
        "Exit Date": st.column_config.DateColumn("Exit Date"),
        "Exit Qty": st.column_config.NumberColumn("Exit Qty", min_value=0, step=1, format="%d"),
        "Exit Price": st.column_config.NumberColumn("Exit Price", min_value=0.0, format="%.2f"),
    }


def _has_add_breakdown_entry_values(row: pd.Series) -> bool:
    value_columns = [
        "Symbol",
        "Sector",
        "Total Qty",
        "Buy Avg",
        "Batch Qty",
        "Batch Price",
        "Exit Qty",
        "Exit Price",
    ]
    return any(pd.notna(row.get(column)) and str(row.get(column)).strip() != "" for column in value_columns)


def _insert_added_breakdown_entries(entries_df: pd.DataFrame, ltp_by_symbol: dict[str, float]) -> list[str]:
    affected_symbols: set[str] = set()
    errors: list[str] = []
    pending_records: list[dict[str, Any]] = []
    submitted_batch_symbols = {
        str(row.get("Symbol") or "").upper().strip()
        for _, row in entries_df.iterrows()
        if str(row.get("Row Type") or "").upper().strip() == "BATCH"
        and str(row.get("Symbol") or "").strip()
    }

    for row_number, row in entries_df.iterrows():
        if not _has_add_breakdown_entry_values(row):
            continue

        row_type = str(row.get("Row Type") or "").upper().strip()
        symbol = str(row.get("Symbol") or "").upper().strip()
        sector = _json_safe_value(row.get("Sector"))
        is_exit = bool(row.get("Exit?"))

        if row_type not in {"SUMMARY", "BATCH"}:
            errors.append(f"Row {row_number + 1}: select SUMMARY or BATCH.")
            continue
        if not symbol:
            errors.append(f"Row {row_number + 1}: Symbol is required.")
            continue

        if row_type == "SUMMARY":
            total_qty = _record_integer_value(row.get("Total Qty"))
            buy_avg = _record_numeric_value(row.get("Buy Avg"))
            symbol_df = load_holdings_breakdown_for_symbols([symbol])
            has_existing_batch = (
                not symbol_df.empty
                and "row_type" in symbol_df.columns
                and symbol_df["row_type"].astype(str).str.upper().str.strip().eq("BATCH").any()
            )
            has_batch_context = has_existing_batch or symbol in submitted_batch_symbols
            if not has_batch_context and (total_qty is None or buy_avg is None):
                errors.append(
                    f"Row {row_number + 1}: Total Qty and Buy Avg are required for SUMMARY when no batch rows exist."
                )
                continue
            record = _recompute_breakdown_record(
                {
                    "row_type": "SUMMARY",
                    "symbol": symbol,
                    "sector": sector,
                    "total_qty": total_qty if total_qty is not None else 0,
                    "buy_avg": buy_avg if buy_avg is not None else 0,
                    "ltp": _lookup_ltp(ltp_by_symbol, symbol),
                },
                ltp_by_symbol,
            )
        else:
            batch_qty = _record_integer_value(row.get("Batch Qty"))
            batch_price = _record_numeric_value(row.get("Batch Price"))
            if batch_qty is None or batch_price is None:
                errors.append(f"Row {row_number + 1}: Batch Qty and Batch Price are required for BATCH.")
                continue
            exit_date = row.get("Exit Date")
            exit_price = _record_numeric_value(row.get("Exit Price"))
            exit_qty = _record_integer_value(row.get("Exit Qty"))
            if is_exit and (pd.isna(exit_date) or exit_price is None or exit_qty is None):
                errors.append(f"Row {row_number + 1}: Exit Date, Exit Qty, and Exit Price are required for BATCH exit rows.")
                continue

            base_record = {
                "row_type": "BATCH",
                "symbol": symbol,
                "sector": sector,
                "trade_date": date.today(),
                "batch_qty": batch_qty,
                "batch_price": batch_price,
                "ltp": _lookup_ltp(ltp_by_symbol, symbol),
            }
            record = (
                _exit_batch_record(
                    pd.Series(base_record),
                    _date_input_value(exit_date),
                    float(exit_price),
                    int(exit_qty),
                )
                if is_exit
                else _recompute_breakdown_record(base_record, ltp_by_symbol)
            )

        if is_exit and row_type == "SUMMARY":
            record["holding_status"] = "Exited"

        if is_exit and row_type == "BATCH":
            record["holding_status"] = "Exited"
            record["exit_date"] = _normalize_trade_date(_date_input_value(exit_date))
            record["exit_qty"] = exit_qty
            record["exit_price"] = exit_price

        pending_records.append(record)
        affected_symbols.add(symbol)

    if errors:
        raise ValueError(" ".join(errors))

    for record in pending_records:
        row_type = str(record.get("row_type") or "").upper().strip()
        symbol = str(record.get("symbol") or "").upper().strip()
        if row_type == "SUMMARY":
            symbol_df = load_holdings_breakdown_for_symbols([symbol])
            summary_rows = (
                symbol_df[symbol_df["row_type"].astype(str).str.upper().str.strip().eq("SUMMARY")]
                if not symbol_df.empty and "row_type" in symbol_df.columns
                else pd.DataFrame()
            )
            if not summary_rows.empty:
                update_holdings_breakdown_row(_row_id(summary_rows.iloc[0]), record)
                continue
        insert_holdings_breakdown_row(record)

    for symbol in sorted(affected_symbols):
        symbol_df = load_holdings_breakdown_for_symbols([symbol])
        if symbol_df.empty or "row_type" not in symbol_df.columns:
            continue
        summaries = symbol_df[symbol_df["row_type"].astype(str).str.upper().str.strip().eq("SUMMARY")]
        for _, summary in summaries.iterrows():
            _recalculate_summary_from_supabase_batches(summary, ltp_by_symbol)

    return sorted(affected_symbols)


def _render_add_holdings_breakdown_entries_form(ltp_by_symbol: dict[str, float]) -> list[str]:
    #st.subheader("Add Holdings Breakdown Entries")
    with st.form("add_holdings_breakdown_entries_form"):
        entries_df = st.data_editor(
            _empty_add_breakdown_entries_df(),
            width="stretch",
            hide_index=True,
            num_rows="dynamic",
            disabled=["Invested"],
            column_config=_add_breakdown_entries_column_config(),
        )
        submitted = st.form_submit_button("Add entries", type="primary")

    if not submitted:
        return []

    return _insert_added_breakdown_entries(entries_df, ltp_by_symbol)


def _batch_display_df(batch_df: pd.DataFrame) -> pd.DataFrame:
    batch_columns = ["trade_date", "batch_qty", "batch_price", "present_value"]
    batch_columns.extend(["batch_pnl", "batch_pnl_pct", "present_age"])
    display_batch_df = batch_df[[column for column in batch_columns if column in batch_df.columns]]
    return display_batch_df.rename(
        columns={
            "trade_date": "Date",
            "batch_qty": "Batch Qty",
            "batch_price": "Batch Price",
            "exit_qty": "Exit Qty",
            "exit_date": "Exit Date",
            "exit_price": "Exit Price",
            "present_value": "Present",
            "batch_pnl": "Batch P&L",
            "batch_pnl_pct": "Batch P&L %",
            "present_age": "Present Age",
        }
    )


def _batch_column_config() -> dict[str, Any]:
    return {
        "Date": st.column_config.DateColumn("Date", width="small"),
        "Batch Qty": st.column_config.NumberColumn("Batch Qty", width="small", format="%d"),
        "Batch Price": st.column_config.NumberColumn("Batch Price", width="small", format="%.2f"),
        "Exit Qty": st.column_config.NumberColumn("Exit Qty", width="small", format="%d"),
        "Exit Date": st.column_config.DateColumn("Exit Date", width="small"),
        "Exit Price": st.column_config.NumberColumn("Exit Price", width="small", format="%.2f"),
        "Present": st.column_config.NumberColumn("Present", width="small", format="%.2f"),
        "Batch P&L": st.column_config.NumberColumn("Batch P&L", width="small", format="%.2f"),
        "Batch P&L %": st.column_config.NumberColumn("Batch P&L %", width="small", format="%.2f%%"),
        "Present Age": st.column_config.TextColumn("Present Age", width="medium"),
    }


def _active_breakdown_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "row_type" not in df.columns:
        return df

    row_type = df["row_type"].astype(str).str.upper().str.strip()
    exited_rows = df.get("holding_status", pd.Series(index=df.index, dtype=object)).apply(_is_exited_status)
    active_summary = row_type.eq("SUMMARY") & ~exited_rows
    if "total_qty" in df.columns:
        active_summary = active_summary & pd.to_numeric(df["total_qty"], errors="coerce").fillna(0).gt(0)

    active_symbols = set(df.loc[active_summary, "symbol"].astype(str).str.upper().str.strip())
    active_batch = (
        row_type.eq("BATCH")
        & ~exited_rows
        & df["symbol"].astype(str).str.upper().str.strip().isin(active_symbols)
    )
    return df[active_summary | active_batch].copy()


def _exited_holdings_summary_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "row_type" not in df.columns:
        return pd.DataFrame()

    row_type = df["row_type"].astype(str).str.upper().str.strip()
    exit_qty = pd.to_numeric(df.get("_exited_summary_qty", pd.Series(index=df.index, dtype=float)), errors="coerce").fillna(0)
    exited_batches = df[row_type.eq("BATCH") & exit_qty.gt(0)].copy()
    if exited_batches.empty:
        return pd.DataFrame()

    summaries = df[row_type.eq("SUMMARY")].copy()
    sector_by_symbol = {}
    if "sector" in summaries.columns:
        sector_by_symbol = (
            summaries.assign(symbol_key=summaries["symbol"].astype(str).str.upper().str.strip())
            .dropna(subset=["symbol_key"])
            .drop_duplicates("symbol_key")
            .set_index("symbol_key")["sector"]
            .to_dict()
        )

    symbol_key = exited_batches["symbol"].astype(str).str.upper().str.strip()
    exit_qty = pd.to_numeric(exited_batches["_exited_summary_qty"], errors="coerce").fillna(0)
    entry_price = pd.to_numeric(exited_batches.get("batch_price", pd.Series(dtype=float)), errors="coerce")
    exit_price = pd.to_numeric(exited_batches.get("exit_price", pd.Series(dtype=float)), errors="coerce")
    invested = exit_qty * entry_price
    exit_value = exit_qty * exit_price
    pnl = exit_value - invested
    pnl_pct = pnl.where(invested.ne(0)) / invested * 100

    return pd.DataFrame(
        {
            "Symbol": exited_batches["symbol"],
            "Sector": exited_batches.get("sector", symbol_key.map(sector_by_symbol)).fillna(symbol_key.map(sector_by_symbol)),
            "Exit Date": exited_batches.get("exit_date"),
            "Exit Qty": exit_qty,
            "Entry Price": entry_price,
            "Exit Price": exit_price,
            "Invested": invested,
            "Exit Value": exit_value,
            "P&L": pnl,
            "P&L %": pnl_pct,
        }
    )


def display_exited_holdings_summary(df: pd.DataFrame) -> None:
    exited_df = _exited_holdings_summary_df(df)
    if exited_df.empty:
        return

    st.subheader("Exited Holdings Summary")
    st.dataframe(
        _style_pnl_columns(exited_df),
        width="stretch",
        height=_dataframe_height(len(exited_df), max_rows=12),
        hide_index=True,
        column_config={
            "Symbol": st.column_config.TextColumn("Symbol", width="small"),
            "Sector": st.column_config.TextColumn("Sector", width="medium"),
            "Exit Date": st.column_config.DateColumn("Exit Date", width="small"),
            "Exit Qty": st.column_config.NumberColumn("Exit Qty", width="small", format="%d"),
            "Entry Price": st.column_config.NumberColumn("Entry Price", width="small", format="%.2f"),
            "Exit Price": st.column_config.NumberColumn("Exit Price", width="small", format="%.2f"),
            "Invested": st.column_config.NumberColumn("Invested", width="small", format="%.2f"),
            "Exit Value": st.column_config.NumberColumn("Exit Value", width="small", format="%.2f"),
            "P&L": st.column_config.NumberColumn("P&L", width="small", format="%.2f"),
            "P&L %": st.column_config.NumberColumn("P&L %", width="small", format="%.2f%%"),
        },
    )


def _render_batch_actions(batches: list[pd.Series], *, key_prefix: str) -> None:
    #st.caption("Actions")
    for index, batch in enumerate(batches):
        row_id = _row_id(batch)
        if not _row_has_id(batch):
            st.write("-")
            continue
        row_key = f"{key_prefix}_batch_{row_id}_{index}"
        with st.container(horizontal=True, gap="small", vertical_alignment="center"):
            if st.button(
                "",
                key=f"{row_key}_edit",
                icon=":material/edit:",
                help="Edit batch",
                width="content",
            ):
                st.session_state["holdings_breakdown_editor"] = {"mode": "edit_batch", "id": row_id}
                st.rerun()
            if st.button(
                "",
                key=f"{row_key}_exit",
                icon=":material/logout:",
                help="Exit batch",
                width="content",
            ):
                st.session_state["holdings_breakdown_editor"] = {"mode": "exit_batch", "id": row_id}
                st.rerun()


def _render_selected_batch_editor(
    batches: list[pd.Series],
    summary: pd.Series,
    *,
    key_prefix: str,
    ltp_by_symbol: dict[str, float],
) -> None:
    editor = st.session_state.get("holdings_breakdown_editor") or {}
    if editor.get("mode") not in {"edit_batch", "exit_batch"}:
        return

    selected_batch = next((batch for batch in batches if _row_id(batch) == editor.get("id")), None)
    if selected_batch is None:
        return

    row_id = _row_id(selected_batch)
    batch_key_prefix = f"{key_prefix}_batch_{row_id}"
    if editor.get("mode") == "edit_batch":
        _render_batch_form(selected_batch, key_prefix=batch_key_prefix, summary=summary, ltp_by_symbol=ltp_by_symbol)
    if editor.get("mode") == "exit_batch":
        _render_exit_form(
            [selected_batch],
            key_prefix=batch_key_prefix,
            summary=summary,
            ltp_by_symbol=ltp_by_symbol,
        )


def display_holdings_breakdown_preview(
    df: pd.DataFrame,
    *,
    enable_crud: bool = False,
    ltp_by_symbol: dict[str, float] | None = None,
) -> None:
    summary_batches: list[tuple[pd.Series, list[pd.Series]]] = []
    ltp_by_symbol = ltp_by_symbol or {}

    if enable_crud and "symbol" in df.columns:
        summary_rows = df[df["row_type"].astype(str).str.upper().eq("SUMMARY")]
        batch_rows = df[df["row_type"].astype(str).str.upper().eq("BATCH")]
        for _, summary in summary_rows.iterrows():
            symbol_key = str(summary.get("symbol") or "").upper().strip()
            batches = [
                row
                for _, row in batch_rows[
                    batch_rows["symbol"].astype(str).str.upper().str.strip().eq(symbol_key)
                ].iterrows()
            ]
            batches = sorted(batches, key=lambda row: _is_exited_status(row.get("holding_status")))
            summary_batches.append((summary, batches))
    else:
        current_summary: pd.Series | None = None
        current_batches: list[pd.Series] = []

        for _, row in df.iterrows():
            row_type = str(row.get("row_type") or "").upper()
            if row_type == "SUMMARY":
                if current_summary is not None:
                    summary_batches.append((current_summary, current_batches))
                current_summary = row
                current_batches = []
            elif row_type == "BATCH" and current_summary is not None:
                current_batches.append(row)

        if current_summary is not None:
            summary_batches.append((current_summary, current_batches))

    summary_batches = sorted(
        summary_batches,
        key=lambda item: _is_exited_status(item[0].get("holding_status")),
    )

    if not summary_batches:
        st.dataframe(df, width="stretch")
        return

    for summary, batches in summary_batches:
        #_summary_expander_label(summary, len(batches))
        with st.expander(_format_display_value(summary.get("symbol")), expanded=True):
            summary_display_df = _summary_display_df(summary)
            if enable_crud and _row_has_id(summary):
                row_id = _row_id(summary)
                key_prefix = f"summary_{row_id}"
                summary_col, actions_col = st.columns([8, 1.2])
                with summary_col:
                    st.dataframe(
                        _style_pnl_columns(summary_display_df),
                        width="stretch",
                        height=_dataframe_height(len(summary_display_df)),
                        hide_index=True,
                        column_config=_summary_column_config(),
                    )
                with actions_col:
                    #st.caption("Actions")
                    action_cols = st.columns(3)
                    with action_cols[0]:
                        if st.button("", key=f"{key_prefix}_add_batch", icon=":material/add:", help="Add batch"):
                            st.session_state["holdings_breakdown_editor"] = {"mode": "add_batch", "id": row_id}
                            st.rerun()
                    with action_cols[1]:
                        if st.button("", key=f"{key_prefix}_edit", icon=":material/edit:", help="Edit holding", width="content"):
                            st.session_state["holdings_breakdown_editor"] = {"mode": "edit_summary", "id": row_id}
                            st.rerun()
                    with action_cols[2]:
                        if st.button("", key=f"{key_prefix}_exit", icon=":material/logout:", help="Exit holding", width="content"):
                            st.session_state["holdings_breakdown_editor"] = {"mode": "exit_summary", "id": row_id}
                            st.rerun()

                editor = st.session_state.get("holdings_breakdown_editor") or {}
                if editor.get("id") == row_id and editor.get("mode") == "edit_summary":
                    _render_summary_form(summary, key_prefix=key_prefix, ltp_by_symbol=ltp_by_symbol)
                if editor.get("id") == row_id and editor.get("mode") == "add_batch":
                    _render_batch_form(None, key_prefix=key_prefix, summary=summary, ltp_by_symbol=ltp_by_symbol)
                if editor.get("id") == row_id and editor.get("mode") == "exit_summary":
                    rows = [summary] + [batch for batch in batches if _row_has_id(batch)]
                    _render_exit_form(rows, key_prefix=key_prefix, summary=summary, ltp_by_symbol=ltp_by_symbol)
            else:
                st.dataframe(
                    _style_pnl_columns(summary_display_df),
                    width="stretch",
                    height=_dataframe_height(len(summary_display_df)),#(numRows + 1) * 35 + 3
                    hide_index=True,
                    column_config=_summary_column_config(),
                )

            batch_df = pd.DataFrame(batches)
            if batch_df.empty:
                st.info("No batch rows found for this summary.")
                continue

            if enable_crud:
                table_col, actions_col = st.columns([8, 1.5])
                display_batch_df = _batch_display_df(batch_df)
                with table_col:
                    st.dataframe(
                        _style_pnl_columns(display_batch_df),
                        width="stretch",
                        height=_dataframe_height(len(display_batch_df)),
                        hide_index=True,
                        column_config=_batch_column_config(),
                    )
                with actions_col:
                    _render_batch_actions(batches, key_prefix=key_prefix)
                _render_selected_batch_editor(
                    batches,
                    summary,
                    key_prefix=key_prefix,
                    ltp_by_symbol=ltp_by_symbol,
                )
                continue

            display_batch_df = _batch_display_df(batch_df)
            st.dataframe(
                _style_pnl_columns(display_batch_df),
                width="stretch",
                height=_dataframe_height(len(display_batch_df)),#(numRows + 1) * 35 + 3
                hide_index=True,
                column_config=_batch_column_config(),
            )


def upsert_holdings_breakdown_in_supabase(df: pd.DataFrame, update_columns: set[str] | None = None) -> None:

    #It loads all existing Supabase rows:
    existing_df = load_holdings_breakdown_from_supabase()
    #print(f"Existing holdings breakdown Supabase: {existing_df.head()}")

    #It builds a dictionary of existing rows by match key.
    existing_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    if not existing_df.empty:
        for record in existing_df.to_dict(orient="records"):
            match_key = _holdings_upload_match_key(record)
            row_id = record.get("id")
            if match_key is not None and row_id is not None and not pd.isna(row_id):
                existing_by_key[match_key] = record

    #For each uploaded Excel row, it calculates the same match key.
    #If the uploaded key exists in Supabase, it calls:update_holdings_breakdown_row
    #If the key does not exist, it calls:insert_holdings_breakdown_row
    for record in df.to_dict(orient="records"):
        match_key = _holdings_upload_match_key(record)
        if match_key is not None and match_key in existing_by_key:
            existing_record = existing_by_key[match_key]
            patch = _holdings_upload_patch(existing_record, record, update_columns)
            if patch:
                update_holdings_breakdown_row(existing_record["id"], patch)
        else:
            insert_record = _filter_record_columns(record, update_columns) if update_columns is not None else record
            insert_holdings_breakdown_row(insert_record)


def insert_holdings_breakdown_row(record: dict[str, Any]) -> None:
    supabase_url, supabase_key, table_name = _get_supabase_holdings_config()
    encoded_table_name = quote(table_name, safe="")
    endpoint = f"{supabase_url}/rest/v1/{encoded_table_name}"
    safe_record = _supabase_write_record(record)
    payload = json.dumps([safe_record], allow_nan=False).encode("utf-8")
    request = Request(endpoint, data=payload, headers=_supabase_headers(supabase_key, write=True), method="POST")
    try:
        with urlopen(request, timeout=60) as response:
            response.read()
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Supabase holdings insert failed - HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase holdings insert failed: {exc.reason}") from exc


def update_holdings_breakdown_row(row_id: Any, record: dict[str, Any]) -> None:
    supabase_url, supabase_key, table_name = _get_supabase_holdings_config()
    encoded_table_name = quote(table_name, safe="")
    endpoint = f"{supabase_url}/rest/v1/{encoded_table_name}?id=eq.{quote(str(row_id), safe='')}"
    safe_record = _supabase_write_record(record)
    payload = json.dumps(safe_record, allow_nan=False).encode("utf-8")
    request = Request(endpoint, data=payload, headers=_supabase_headers(supabase_key, write=True), method="PATCH")
    try:
        with urlopen(request, timeout=60) as response:
            response.read()
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Supabase holdings update failed - HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase holdings update failed: {exc.reason}") from exc


def _prepare_holdings_breakdown_df(records: list[dict[str, Any]]) -> pd.DataFrame:
    holdings_breakdown_df = pd.DataFrame(records)
    if holdings_breakdown_df.empty:
        return pd.DataFrame()

    for column in NUMERIC_HOLDINGS_COLUMNS:
        if column in holdings_breakdown_df.columns:
            holdings_breakdown_df[column] = pd.to_numeric(holdings_breakdown_df[column], errors="coerce")

    for column in INTEGER_HOLDINGS_COLUMNS:
        if column in holdings_breakdown_df.columns:
            holdings_breakdown_df[column] = pd.to_numeric(holdings_breakdown_df[column], errors="coerce").apply(
                lambda value: int(value) if pd.notna(value) else None
            )

    if "trade_date" in holdings_breakdown_df.columns:
        holdings_breakdown_df["trade_date"] = holdings_breakdown_df["trade_date"].apply(_normalize_trade_date)
        holdings_breakdown_df["age_days"] = holdings_breakdown_df["trade_date"].apply(_holding_age_days)
        holdings_breakdown_df["present_age"] = holdings_breakdown_df["trade_date"].apply(_holding_present_age)
    if "exit_date" in holdings_breakdown_df.columns:
        holdings_breakdown_df["exit_date"] = holdings_breakdown_df["exit_date"].apply(_normalize_trade_date)

    return holdings_breakdown_df


def load_holdings_breakdown_from_supabase() -> pd.DataFrame:
    supabase_url, supabase_key, table_name = _get_supabase_holdings_config()
    encoded_table_name = quote(table_name, safe="")
    endpoint = f"{supabase_url}/rest/v1/{encoded_table_name}?select=*&order=id.asc"
    headers = _supabase_headers(supabase_key)

    request = Request(endpoint, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=60) as response:
            records = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Supabase holdings breakdown lookup failed with HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase holdings breakdown lookup failed: {exc.reason}") from exc

    return _prepare_holdings_breakdown_df(records)


def load_holdings_breakdown_for_symbols(symbols: list[str]) -> pd.DataFrame:
    normalized_symbols = sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})
    if not normalized_symbols:
        return pd.DataFrame()

    supabase_url, supabase_key, table_name = _get_supabase_holdings_config()
    encoded_table_name = quote(table_name, safe="")
    symbol_filter = ",".join(quote(symbol, safe="") for symbol in normalized_symbols)
    endpoint = (
        f"{supabase_url}/rest/v1/{encoded_table_name}"
        f"?select=*&symbol=in.({symbol_filter})&order=id.asc"
    )
    headers = _supabase_headers(supabase_key)

    request = Request(endpoint, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=60) as response:
            records = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Supabase holdings breakdown lookup failed with HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase holdings breakdown lookup failed: {exc.reason}") from exc

    return _prepare_holdings_breakdown_df(records)


def _normalized_symbol_values(symbols: list[str]) -> list[str]:
    return sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})


def _holdings_breakdown_state_df() -> pd.DataFrame:
    state_df = st.session_state.get(HOLDINGS_BREAKDOWN_DF_STATE_KEY)
    if isinstance(state_df, pd.DataFrame):
        return state_df.copy()
    return pd.DataFrame()


def _set_holdings_breakdown_state(df: pd.DataFrame) -> None:
    st.session_state[HOLDINGS_BREAKDOWN_DF_STATE_KEY] = df.copy()


def _load_holdings_breakdown_state() -> pd.DataFrame:
    df = load_holdings_breakdown_from_supabase()
    _set_holdings_breakdown_state(df)
    return df


def _merge_holdings_breakdown_symbols(
    current_df: pd.DataFrame,
    fresh_df: pd.DataFrame,
    symbols: list[str],
) -> pd.DataFrame:
    normalized_symbols = set(_normalized_symbol_values(symbols))
    if not normalized_symbols:
        return current_df

    if current_df.empty or "symbol" not in current_df.columns:
        merged_df = fresh_df.copy()
    else:
        symbol_key = current_df["symbol"].astype(str).str.upper().str.strip()
        merged_df = pd.concat(
            [current_df[~symbol_key.isin(normalized_symbols)], fresh_df],
            ignore_index=True,
        )

    if "id" in merged_df.columns:
        merged_df = merged_df.sort_values("id", kind="stable")
    return merged_df.reset_index(drop=True)


def _refresh_holdings_breakdown_state_for_symbols(symbols: list[str]) -> pd.DataFrame:
    normalized_symbols = _normalized_symbol_values(symbols)
    if not normalized_symbols:
        return _holdings_breakdown_state_df()

    current_df = _holdings_breakdown_state_df()
    if current_df.empty:
        return _load_holdings_breakdown_state()

    fresh_df = load_holdings_breakdown_for_symbols(normalized_symbols)
    merged_df = _merge_holdings_breakdown_symbols(current_df, fresh_df, normalized_symbols)
    _set_holdings_breakdown_state(merged_df)
    return merged_df

def display_holdings_breakdown_df(holdings_breakdown_df: pd.DataFrame) -> None:
    if holdings_breakdown_df.empty:
        st.info("No holdings breakdown found in Supabase.")
        return

    holdings_breakdown_df, unmatched_symbols = enrich_holdings_breakdown_with_ltp(
        holdings_breakdown_df,
        st.session_state.get("ltp_by_symbol", {}),
    )

    #st.subheader("Holdings Breakdown")
    if unmatched_symbols:
        st.warning(
            "No live LTP found for: "
            + ", ".join(unmatched_symbols[:10])
            + ("..." if len(unmatched_symbols) > 10 else "")
        )

    active_breakdown_df = _active_breakdown_df(holdings_breakdown_df)
    if active_breakdown_df.empty:
        if _exited_holdings_summary_df(holdings_breakdown_df).empty:
            st.info("No active holdings found.")
    else:
        display_holdings_breakdown_preview(
            active_breakdown_df,
            enable_crud=True,
            ltp_by_symbol=st.session_state.get("ltp_by_symbol", {}),
        )

    display_exited_holdings_summary(holdings_breakdown_df)


def display_supabase_holdings_breakdown(symbols: list[str] | None = None) -> None:
    try:
        holdings_breakdown_df = (
            load_holdings_breakdown_for_symbols(symbols)
            if symbols
            else load_holdings_breakdown_from_supabase()
        )
    except Exception as exc:
        st.warning(f"Could not load holdings breakdown from Supabase: {exc}")
        return

    display_holdings_breakdown_df(holdings_breakdown_df)


def _selected_holding_batches_display_df(symbol: str, holdings_breakdown_df: pd.DataFrame) -> pd.DataFrame:
    symbol_key = _normalized_symbol_value(symbol)
    if not symbol_key or holdings_breakdown_df.empty or "symbol" not in holdings_breakdown_df.columns:
        return pd.DataFrame()

    enriched_df, _ = enrich_holdings_breakdown_with_ltp(
        holdings_breakdown_df,
        st.session_state.get("ltp_by_symbol", {}),
    )
    active_df = _active_breakdown_df(enriched_df)
    if active_df.empty or "row_type" not in active_df.columns:
        return pd.DataFrame()

    row_type = active_df["row_type"].astype(str).str.upper().str.strip()
    symbol_series = active_df["symbol"].astype(str).str.upper().str.strip()
    batch_df = active_df[row_type.eq("BATCH") & symbol_series.eq(symbol_key)].copy()
    if batch_df.empty:
        return pd.DataFrame()

    if "id" in batch_df.columns:
        batch_df = batch_df.sort_values("id", kind="stable")

    age = (
        batch_df["present_age"]
        if "present_age" in batch_df.columns
        else batch_df.get("age_days", pd.Series(index=batch_df.index, dtype=object))
    )
    return pd.DataFrame(
        {
            "Price": pd.to_numeric(
                batch_df.get("batch_price", pd.Series(index=batch_df.index, dtype=float)),
                errors="coerce",
            ),
            "Qty": pd.to_numeric(
                batch_df.get("batch_qty", pd.Series(index=batch_df.index, dtype=float)),
                errors="coerce",
            ),
            "Age": age,
            "Profit %": pd.to_numeric(
                batch_df.get("batch_pnl_pct", pd.Series(index=batch_df.index, dtype=float)),
                errors="coerce",
            ),
        }
    )


def display_selected_holding_batches(symbol: str | None, holdings_breakdown_df: pd.DataFrame) -> None:
    if not symbol:
        st.info("Select a holding row to view batch details.")
        return

    st.subheader(str(symbol).upper())
    if holdings_breakdown_df.empty:
        st.info("No holdings breakdown data loaded.")
        return

    display_df = _selected_holding_batches_display_df(symbol, holdings_breakdown_df)
    if display_df.empty:
        st.info("No active batch rows found for this symbol.")
        return

    st.dataframe(
        display_df,
        width="stretch",
        hide_index=True,
        height=_dataframe_height(len(display_df), max_rows=12),
        column_config={
            "Price": st.column_config.NumberColumn("Price", width="small", format="%.2f"),
            "Qty": st.column_config.NumberColumn("Qty", width="small", format="%d"),
            "Age": st.column_config.TextColumn("Age", width="medium"),
            "Profit %": st.column_config.NumberColumn("Profit %", width="small", format="%.2f%%"),
        },
    )

