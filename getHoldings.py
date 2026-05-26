import json
import math
import calendar
from datetime import date, datetime
from time import strptime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st

from kite_analytics import (
    build_historic_dashboard_frames,
    display_historic_dashboard_frames,
)
from kite_auth import bootstrap_kite_app, clear_auth_state, get_secret_value, is_token_error

st.set_page_config(layout="wide") 

SUPABASE_BATCH_SIZE = 500
HOLDINGS_TABLE_NAME = "holdings_breakdown"
SUPABASE_INDICES_TABLE_NAME = "Indices_constituents"
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
SUPABASE_EXCLUDED_WRITE_COLUMNS = {"pnl_pct", "batch_pnl_pct"}


@st.cache_data(ttl=24 * 60 * 60)
def load_instrument_token_from_supabase(tickers: list[str]) -> pd.DataFrame:
    """
    Load instrument rows from Supabase for the ticker symbols entered by the user.
    """
    normalized_tickers = sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})
    if not normalized_tickers:
        return pd.DataFrame()

    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_TABLE_NAME").strip()

    if not supabase_url or not supabase_key:
        raise ValueError(
            "Missing Supabase config. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "in .streamlit/secrets.toml or environment variables."
        )

    ticker_filter = ",".join(f"tradingsymbol.eq.{quote(ticker, safe='')}" for ticker in normalized_tickers)
    endpoint = (
        f"{supabase_url}/rest/v1/{quote(table_name, safe='')}"
        f"?select=*&or=({ticker_filter})"
    )
    request = Request(endpoint, headers=_supabase_headers(supabase_key), method="GET")
    try:
        with urlopen(request, timeout=60) as response:
            records = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Supabase instrument lookup failed with HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase instrument lookup failed: {exc.reason}") from exc

    instrument_token_df = pd.DataFrame(records)
    if instrument_token_df.empty:
        return pd.DataFrame(columns=["tradingsymbol", "instrument_token"])

    if "tradingsymbol" in instrument_token_df.columns:
        instrument_token_df["tradingsymbol"] = (
            instrument_token_df["tradingsymbol"].astype(str).str.strip().str.upper()
        )
    return instrument_token_df


@st.cache_data(ttl=24 * 60 * 60)
def load_indices_from_supabase() -> dict[str, str]:
    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_INDICES_TABLE_NAME").strip() or SUPABASE_INDICES_TABLE_NAME

    if not supabase_url or not supabase_key:
        raise ValueError(
            "Missing Supabase config. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "in .streamlit/secrets.toml or environment variables."
        )

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
            f"Supabase indices lookup failed with HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Supabase indices lookup failed: {exc.reason}") from exc

    indices: dict[str, str] = {}
    for record in records:
        index_name = str(record.get("Index") or "").strip()
        constituents = str(record.get("Constituents") or "").strip()
        if index_name and constituents:
            indices[index_name] = constituents
    return indices


def resolve_tokens_from_tickers(tickers: list[str], instruments_df: pd.DataFrame) -> tuple[dict[str, int], list[str]]:
    """
    Map comma-separated tickers to instrument tokens using the instrument dump.
    """
    resolved: dict[str, int] = {}
    missing: list[str] = []
    normalized = instruments_df.copy()
    normalized["tradingsymbol"] = normalized["tradingsymbol"].astype(str).str.strip().str.upper()

    for ticker in tickers:
        matches = normalized[normalized["tradingsymbol"] == ticker]
        if matches.empty:
            missing.append(ticker)
            continue
        resolved[ticker] = int(matches.iloc[0]["instrument_token"])
    return resolved, missing


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


def enrich_holdings_breakdown_with_ltp(
    df: pd.DataFrame, ltp_by_symbol: dict[str, float]
) -> tuple[pd.DataFrame, list[str]]:
    
    #maps live LTP by symbol
    df = df.copy()
    if "symbol" not in df.columns:
        return df, []

    normalized_ltp_by_symbol = {
        str(symbol).upper().strip(): ltp
        for symbol, ltp in ltp_by_symbol.items()
        if symbol is not None and pd.notna(ltp)
    } if ltp_by_symbol else {}
    symbol_key = _normalized_symbol_series(df["symbol"])
    live_ltp = pd.to_numeric(symbol_key.map(normalized_ltp_by_symbol), errors="coerce")
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


def _dataframe_height(row_count: int, *, min_rows: int = 1, max_rows: int | None = None) -> int:
    visible_rows = max(row_count, min_rows)
    if max_rows is not None:
        visible_rows = min(visible_rows, max_rows)
    header_height = 38
    row_height = 35
    border_padding = 4
    return header_height + (visible_rows * row_height) 


def _sort_historic_dashboard_by_rng(dashboard_df: pd.DataFrame) -> pd.DataFrame:
    if dashboard_df.empty:
        return dashboard_df

    def rng_value(column: str) -> float:
        value = dashboard_df[column].iloc[0]
        if isinstance(value, str) and value.startswith("Rng:"):
            try:
                return float(value.removeprefix("Rng:").removesuffix("%"))
            except ValueError:
                return float("-inf")
        return float("-inf")

    sorted_columns = sorted(dashboard_df.columns, key=rng_value, reverse=True)
    return dashboard_df.loc[:, sorted_columns]


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
    ltp = ltp_by_symbol.get(symbol_key, record.get("ltp"))
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
    ltp = _record_numeric_value(ltp_by_symbol.get(symbol_key, summary.get("ltp")))
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
        st.session_state.pop("holdings_breakdown_editor", None)
        st.rerun()


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
                        if st.button("", key=f"{key_prefix}_edit", icon=":material/edit:", help="Edit holding"):
                            st.session_state["holdings_breakdown_editor"] = {"mode": "edit_summary", "id": row_id}
                            st.rerun()
                    with action_cols[2]:
                        if st.button("", key=f"{key_prefix}_exit", icon=":material/logout:", help="Exit holding"):
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


st.subheader("Portfolio Holdings")


def _read_uploaded_file(uploaded_file) -> pd.DataFrame:
    filename = uploaded_file.name.lower()
    st.session_state["kite_holdings_download_filename"] = filename
    filedate= filename.split("_")[1] if "_" in filename else "Unknown"
    
    #print(f"Uploaded holdings file date: {filedate}")
      
    if filename.endswith(".csv"):
        return pd.read_csv(uploaded_file)
    if filename.endswith(".xlsx"):
        return pd.read_excel(uploaded_file)    
    raise ValueError("Upload a CSV or XLSX file.")


def _cache_ltp_by_symbol(df: pd.DataFrame) -> None:
    if {"tradingsymbol", "last_price"}.issubset(df.columns):
        st.session_state["ltp_by_symbol"] = (
            df.assign(symbol=_normalized_symbol_series(df["tradingsymbol"]))
            .set_index("symbol")["last_price"]
            .dropna()
            .to_dict()
        )
    else:
        st.session_state["ltp_by_symbol"] = {}


def display_supabase_holdings_breakdown() -> None:
    try:
        holdings_breakdown_df = load_holdings_breakdown_from_supabase()
    except Exception as exc:
        st.warning(f"Could not load holdings breakdown from Supabase: {exc}")
        return

    if holdings_breakdown_df.empty:
        st.info("No holdings breakdown found in Supabase.")
        return

    holdings_breakdown_df, unmatched_symbols = enrich_holdings_breakdown_with_ltp(
        holdings_breakdown_df,
        st.session_state.get("ltp_by_symbol", {}),
    )

    st.subheader("Holdings Breakdown")
    if unmatched_symbols:
        st.warning(
            "No live LTP found for: "
            + ", ".join(unmatched_symbols[:10])
            + ("..." if len(unmatched_symbols) > 10 else "")
        )

    active_breakdown_df = _active_breakdown_df(holdings_breakdown_df)
    if active_breakdown_df.empty:
        st.info("No current holdings breakdown found in Supabase.")
    else:
        display_holdings_breakdown_preview(
            active_breakdown_df,
            enable_crud=True,
            ltp_by_symbol=st.session_state.get("ltp_by_symbol", {}),
        )

    display_exited_holdings_summary(holdings_breakdown_df)


def display_kite_holdings(df: pd.DataFrame, kite=None) -> pd.DataFrame | None:
    if df.empty:
        st.session_state["ltp_by_symbol"] = {}
        st.warning("No holdings found.")
        return None

    df = df.copy()
    _cache_ltp_by_symbol(df)
    #print("portfolio holdings columns:\n")
    #print(df.columns)
    
    df["invested"] = pd.to_numeric(df["average_price"], errors="coerce") * pd.to_numeric(
            df["quantity"], errors="coerce"
    )
    df["CurrentValue"] = pd.to_numeric(df["last_price"], errors="coerce") * pd.to_numeric(
            df["quantity"], errors="coerce"
    )
    if "pnl_pct" not in df.columns and {"pnl", "average_price", "quantity"}.issubset(df.columns):
        invested = df["invested"]        
        df["pnl_pct"] = pd.to_numeric(df["pnl"], errors="coerce").where(invested.ne(0)) / invested * 100

    display_cols = [
        "tradingsymbol",
        "quantity",
        "average_price",
        "invested",
        "CurrentValue",
        "last_price",        
        "pnl",
        "pnl_pct",
        "day_change_percentage",
    ]
    display_cols = [column for column in display_cols if column in df.columns]
    display_df = df[display_cols] if display_cols else df
    display_df = display_df.rename(
        columns={
            "tradingsymbol": "Symbol",
            "quantity": "Quantity",
            "average_price": "Avg Price",
            "invested": "Invested",
            "CurrentValue": "Current",
            "last_price": "LTP",
            "pnl": "P&L",
            "pnl_pct": "P&L %",
            "day_change_percentage": "DayChg %",
        }
    )
    #st.table(display_df, width="stretch", height=_dataframe_height(len(display_df)))
    col1, col2 ,col3= st.columns(3)
    with col1:
        total_invested = pd.to_numeric(df["invested"], errors="coerce").sum() if "invested" in df.columns else 0
        #st.write("Total Invested\n", f"{total_invested:,.2f}")
        st.metric("Total Invested", f"{total_invested:,.2f}")

    with col2:
        total_pnl = pd.to_numeric(df["pnl"], errors="coerce").sum() if "pnl" in df.columns else 0
        total_pnl_percent = (total_pnl/total_invested) *100 if total_invested != 0 else 0        
        st.metric("Total P&L", f"{total_pnl:,.2f}", delta=f"{total_pnl_percent:.2f}", format="%.2f%%")
       
        
    with col3:
        st.metric("As of", st.session_state.get("kite_holdings_download_filename").split("_")[1]) 
        
    st.dataframe(
        _style_pnl_columns(display_df),
        width="stretch",
        height=_dataframe_height(len(display_df)),
        hide_index=False,
        column_config={
            "Symbol": st.column_config.TextColumn("Symbol", width="small"),
            "Quantity": st.column_config.NumberColumn("Quantity", width="small",format="%d"),
            "Avg Price": st.column_config.NumberColumn("Avg Price", width="small", format="%.2f"),
            "Invested": st.column_config.NumberColumn("Invested", width="small", format="%.2f"),
            "Current": st.column_config.NumberColumn("Current", width="small", format="%.2f"),
            "LTP": st.column_config.NumberColumn("LTP", width="small", format="%.2f"),
            "P&L": st.column_config.NumberColumn("P&L", width="small", format="%.2f"),
            "P&L %": st.column_config.NumberColumn("P&L %", width="small", format="%.2f%%"),
            "DayChg%": st.column_config.NumberColumn("DayChg %", width="small", format="%.2f%%"),
        },
    )
    return df



def fetch_and_display_holdings():
    try:
        kite, _, _ = bootstrap_kite_app("Zerodha Holdings")
        holdings = kite.holdings()
        if holdings:
            df = pd.DataFrame(holdings)
            #print("Fetched holdings:\n", df.head())
            as_of_date = datetime.now().date().isoformat()
            returns_df, dashboard_df, failed_symbols = build_historic_dashboard_frames(
                kite,
                df.to_dict(orient="records"),
                as_of_date,
                symbol_key="tradingsymbol",
                token_key="instrument_token",
                ltp_key="last_price",
            )
            st.session_state["kite_holdings_df"] = df
            st.session_state["kite_holdings_returns_df"] = returns_df
            st.session_state["kite_holdings_dashboard_df"] = dashboard_df
            st.session_state["kite_holdings_dashboard_failed_symbols"] = failed_symbols
            st.session_state["kite_holdings_download_filename"] = (
                f"holdings_{pd.Timestamp.now().strftime('%Y-%m-%d_%H.%M.%S')}.csv"
            )
            #print("session state kite_holdings_download_filename:\n", st.session_state["kite_holdings_download_filename"])
        else:
            st.session_state.pop("kite_holdings_df", None)
            st.session_state.pop("kite_holdings_returns_df", None)
            st.session_state.pop("kite_holdings_dashboard_df", None)
            st.session_state.pop("kite_holdings_dashboard_failed_symbols", None)
            st.session_state.pop("kite_holdings_download_filename", None)
            st.session_state["ltp_by_symbol"] = {}
            st.warning("No holdings found in this account.")
    except Exception as exc:
        if is_token_error(exc):
            clear_auth_state()
            st.error("Your session expired. Please login again to view holdings.")
            st.rerun()
        st.error(f"Error fetching holdings. Please try again. Details: {exc}")


if "request_token" in st.query_params and "access_token" not in st.session_state:
    bootstrap_kite_app("Zerodha Holdings")


tab_historic_data, tab_upload_kite, tab_fetch_kite, tab_upload_holdings_breakdown = st.tabs(
    ["Historic Data", "Upload Kite Holdings", "Fetch from Kite", "Upload Holdings Breakdown"]
)

with tab_upload_kite:
    uploaded_kite_holdings_file = st.file_uploader(
        "Upload Kite holdings CSV or XLSX",
        type=["csv", "xlsx"],
        key="kite_holdings_upload",
    )

    if uploaded_kite_holdings_file is not None:
        try:
            kite_holdings_df = _read_uploaded_file(uploaded_kite_holdings_file)
            display_kite_holdings(kite_holdings_df)            
            if st.checkbox("Show holdings breakdown", key="show_upload_kite_holdings_breakdown"):
                display_supabase_holdings_breakdown()
        except ImportError as exc:
            st.error(f"Failed to read XLSX file. Install the missing dependency: {exc}")
        except Exception as exc:
            st.error(f"Failed to upload Kite holdings: {exc}")

with tab_fetch_kite:
    if st.button("Fetch Holdings from Kite", type="primary"):
        fetch_and_display_holdings()#get holdings from kite,
    #session state - kite_holdings_df, kite_holdings_download_filename, ltp_by_symbol

    kite_holdings_df = st.session_state.get("kite_holdings_df")
    if kite_holdings_df is not None:
        
        kite_holdings_download_filename = st.session_state.get("kite_holdings_download_filename", "Unknown")
        Holdings_fetchDate = kite_holdings_download_filename.split("_")[1] 

        #print("Holdings fetch date:", Holdings_fetchDate , "kite_holdings_download_filename:", kite_holdings_download_filename )
        
        st.download_button(
            "Download Kite Holdings as CSV",
            data=kite_holdings_df.to_csv(index=False),
            file_name=kite_holdings_download_filename,
            mime="text/csv",
            on_click="ignore",
        )
        display_kite_holdings(kite_holdings_df)
        failed_symbols = st.session_state.get("kite_holdings_dashboard_failed_symbols", [])
        if failed_symbols:
            st.warning(
                "Could not load dashboard data for: "
                + ", ".join(failed_symbols[:10])
                + ("..." if len(failed_symbols) > 10 else "")
            )
        display_historic_dashboard_frames(
            st.session_state.get("kite_holdings_dashboard_df", pd.DataFrame()),
            st.session_state.get("kite_holdings_returns_df", pd.DataFrame()),
            
        )
        #display_supabase_holdings_breakdown()  

with tab_upload_holdings_breakdown:

    uploaded_brkholdings_file = st.file_uploader(
        "Upload holdings breakdown CSV or XLSX",
        type=["csv", "xlsx"],
    )

    if uploaded_brkholdings_file is not None:
        try:
            brkdown_df = _read_uploaded_file(uploaded_brkholdings_file)
            upload_columns = _mapped_holdings_upload_columns(brkdown_df)
            #print("holdings breakdown upload columns:\n", upload_columns)
            #print("holdings breakdown before cleaning:\n", brkdown_df.head())
            holdings_breakdown_df = clean_holdings_breakdown_for_supabase(brkdown_df)

            #print("holdings breakdown after cleaning:\n", holdings_breakdown_df.head())

            upsert_holdings_breakdown_in_supabase(holdings_breakdown_df, upload_columns)

            holdings_breakdown_df = load_holdings_breakdown_from_supabase()
            display_holdings_breakdown_preview(holdings_breakdown_df)

        except ImportError as exc:
            st.error(f"Failed to read XLSX file. Install the missing dependency: {exc}")
        except Exception as exc:
            st.error(f"Failed to upload holdings breakdown: {exc}")


with tab_historic_data:
    st.caption("Fetch cached 2Y daily Kite data and show a sorted price ladder per ticker.")

    if "historic_tickers_input" not in st.session_state:
        st.session_state["historic_tickers_input"] = ""

    try:
        indices = load_indices_from_supabase()
    except Exception as exc:
        indices = {}
        st.warning(f"Could not load index constituents: {exc}")

    if indices:
        index_names = ["Custom"] + list(indices.keys())
        selected_index = st.selectbox("Select index", index_names, key="historic_selected_index")
        if selected_index != "Custom":
            selected_constituents = indices[selected_index]
            if st.session_state["historic_tickers_input"] != selected_constituents:
                st.session_state["historic_tickers_input"] = selected_constituents
                st.rerun()

    tickers_input = st.text_area(
        label="e.g. RELIANCE, INFY, TCS",
        key="historic_tickers_input",
        help="Enter one or more stock ticker symbols separated by commas.",
    )

    if st.button("Fetch dashboard", type="primary", key="historic_fetch_dashboard"):
        raw_tickers = [item.strip().upper() for item in tickers_input.split(",") if item.strip()]

        if not raw_tickers:
            st.warning("Enter at least one ticker symbol.")
        else:
            st.session_state["historic_pending_tickers"] = raw_tickers

    pending_historic_tickers = st.session_state.get("historic_pending_tickers")
    if pending_historic_tickers:
        try:
            as_of_date = datetime.now().date().isoformat()
            historic_kite, _, _ = bootstrap_kite_app("Zerodha Historical Data")
            instruments_df = load_instrument_token_from_supabase(pending_historic_tickers)
            token_map, missing_tickers = resolve_tokens_from_tickers(pending_historic_tickers, instruments_df)

            if missing_tickers:
                st.session_state["historic_missing_tickers"] = missing_tickers
            else:
                st.session_state.pop("historic_missing_tickers", None)

            if not token_map:
                st.error("No instrument tokens found for the selected tickers.")
                st.session_state.pop("historic_pending_tickers", None)
            else:
                token_rows = [
                    {"Ticker": ticker, "instrument_token": token}
                    for ticker, token in token_map.items()
                ]
                returns_df, dashboard_df, skipped_symbols = build_historic_dashboard_frames(
                    historic_kite,
                    token_rows,
                    as_of_date,
                )
                st.session_state["historic_returns_df"] = returns_df
                st.session_state["historic_dashboard_df"] = dashboard_df
                st.session_state["historic_skipped_symbols"] = skipped_symbols
                st.session_state.pop("historic_pending_tickers", None)

        except Exception as exc:
            if is_token_error(exc):
                clear_auth_state()
                st.error("Your session expired. Please login again to load dashboard data.")
                st.rerun()
            st.error(f"Error fetching dashboard data: {exc}")

    missing_tickers = st.session_state.get("historic_missing_tickers", [])
    if missing_tickers:
        st.warning(f"Skipped tickers with no instrument token: {', '.join(missing_tickers)}")

    skipped_symbols = st.session_state.get("historic_skipped_symbols", [])
    if skipped_symbols:
        st.warning(
            "No dashboard data returned for: "
            + ", ".join(skipped_symbols[:10])
            + ("..." if len(skipped_symbols) > 10 else "")
        )

    if "historic_returns_df" in st.session_state or "historic_dashboard_df" in st.session_state:
        display_historic_dashboard_frames(           
            _sort_historic_dashboard_by_rng(
                st.session_state.get("historic_dashboard_df", pd.DataFrame())
            ),
            st.session_state.get("historic_returns_df", pd.DataFrame()),
        )



#if "access_token" in st.session_state:
#    if st.sidebar.button("Logout"):
#        clear_auth_state()
#        st.rerun()
