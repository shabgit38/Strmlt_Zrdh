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

from kite_analytics import build_metric_values, compute_period_returns, load_analytics_history
from kite_auth import bootstrap_kite_app, clear_auth_state, get_secret_value, is_token_error


SUPABASE_BATCH_SIZE = 500
HOLDINGS_TABLE_NAME = "holdings_breakdown"
HOLDINGS_COLUMN_MAP = {
    "Row Type": "row_type",
    "Symbol": "symbol",
    "ISIN": "isin",
    "Total Qty": "total_qty",
    "Buy Avg": "buy_avg",
    "Invested": "invested",
    "LTP": "ltp",
    "Present Value": "present_value",
    "P&L": "pnl",
    "P&L %": "pnl_pct",
    "P&L chg": "pnl_pct",
    "Date": "trade_date",
    "Batch Qty": "batch_qty",
    "Batch Price": "batch_price",
    "Age (Days)": "age_days",
    "Batch P&L": "batch_pnl",
    "Batch P&L %": "batch_pnl_pct",
    "Present Age": "present_age",
}
REQUIRED_HOLDINGS_COLUMNS = {"Row Type", "Symbol"}
NUMERIC_HOLDINGS_COLUMNS = [    
    "buy_avg",
    "invested",
    "ltp",
    "present_value",
    "pnl",    
    "pnl_pct",
    "batch_price",
    "batch_pnl",
    "batch_pnl_pct",
]
INTEGER_HOLDINGS_COLUMNS = ["total_qty","age_days","batch_qty"]
TA_METRIC_COLUMNS = [
    "Day Low",
    "Day High",
    "1W Low",
    "1W High",
    "1M Low",
    "1M High",
    "3M Low",
    "3M High",
    "6M Low",
    "6M High",
    "1Y Low",
    "1Y High",
    "2Y Low",
    "2Y High",    
    "EMA10",
    "EMA20",
    "EMA50",
    "EMA100",
    "EMA200",
]
RETURN_COLUMNS = [
    "1W Return %",
    "1M Return %",
    "3M Return %",
    "6M Return %",
    "1Y Return %",
    "2Y Return %",
    "YTD Return %",
]


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
    df = df.copy()
    df.columns = df.columns.astype(str).str.strip()
    df = df.dropna(how="all")
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]

    missing_columns = REQUIRED_HOLDINGS_COLUMNS - set(df.columns)
    if missing_columns:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing_columns))}")

    df = df.rename(columns=HOLDINGS_COLUMN_MAP)
    expected_columns = list(HOLDINGS_COLUMN_MAP.values())
    df = df[[column for column in expected_columns if column in df.columns]]

    df = df[df["row_type"].notna() & df["symbol"].notna()]
    if df.empty:
        raise ValueError("No holdings rows found after removing blank rows.")

    for column in NUMERIC_HOLDINGS_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    for column in INTEGER_HOLDINGS_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").apply(
                lambda value: int(value) if pd.notna(value) else None
            )

    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].apply(_normalize_trade_date)
        df["age_days"] = df["trade_date"].apply(_holding_age_days)
        df["present_age"] = df["trade_date"].apply(_holding_present_age)
    else:
        df["age_days"] = None
        df["present_age"] = None

    for column in df.columns:
        df[column] = df[column].apply(_json_safe_value)

    return df


def _normalized_symbol_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.upper().str.strip()


def enrich_holdings_breakdown_with_ltp(
    df: pd.DataFrame, ltp_by_symbol: dict[str, float]
) -> tuple[pd.DataFrame, list[str]]:
    df = df.copy()
    
    if not ltp_by_symbol or "symbol" not in df.columns:
        return df, []

    normalized_ltp_by_symbol = {
        str(symbol).upper().strip(): ltp
        for symbol, ltp in ltp_by_symbol.items()
        if symbol is not None and pd.notna(ltp)
    }
    symbol_key = _normalized_symbol_series(df["symbol"])
    live_ltp = pd.to_numeric(symbol_key.map(normalized_ltp_by_symbol), errors="coerce")
    matched_rows = live_ltp.notna()

    if "ltp" not in df.columns:
        df["ltp"] = None
    df.loc[matched_rows, "ltp"] = live_ltp[matched_rows]

    row_type = df["row_type"].astype(str).str.upper().str.strip()
    summary_rows = matched_rows & row_type.eq("SUMMARY")
    batch_rows = matched_rows & row_type.eq("BATCH")

    for column in ["total_qty", "invested", "batch_qty", "batch_price", "ltp"]:
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

    unmatched_symbols = sorted(symbol_key[~matched_rows].dropna().unique().tolist())
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
        for column in ["batch_pnl", "batch_pnl_pct", "pnl", "pnl_pct", "Batch P&L", "Batch P&L %", "P&L", "P&L %"]
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
                "Present Value": summary.get("present_value"),
                "LTP": summary.get("ltp"),
                "P&L": summary.get("pnl"),
                "P&L %": summary.get("pnl_pct"),
            }
        ]
    )


def _dataframe_height(row_count: int, *, min_rows: int = 1, max_rows: int | None = None) -> int:
    visible_rows = max(row_count, min_rows)
    if max_rows is not None:
        visible_rows = min(visible_rows, max_rows)
    header_height = 38
    row_height = 35
    border_padding = 4
    return header_height + (visible_rows * row_height) + border_padding


def _summary_expander_label(summary: pd.Series, batch_count: int) -> str:
    return (
        f"{summary.get('symbol', '-')}"
        
    )


def display_holdings_breakdown_preview(df: pd.DataFrame) -> None:
    summary_batches: list[tuple[pd.Series, list[pd.Series]]] = []
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

    if not summary_batches:
        st.dataframe(df, width="stretch")
        return

    for summary, batches in summary_batches:
        #_summary_expander_label(summary, len(batches))
        with st.expander(_format_display_value(summary.get("symbol")), expanded=True):
            summary_display_df = _summary_display_df(summary)
            st.dataframe(
                _style_pnl_columns(summary_display_df),
                width="stretch",
                height=_dataframe_height(len(summary_display_df)),#(numRows + 1) * 35 + 3
                hide_index=True,
            )

            batch_df = pd.DataFrame(batches)
            if batch_df.empty:
                st.info("No batch rows found for this summary.")
                continue

            batch_columns = [
                "trade_date",
                "batch_qty",
                "batch_price",
                "ltp",
                "present_value",
                "batch_pnl",
                "batch_pnl_pct",
                "age_days",                
                "present_age",
            ]
            display_batch_df = batch_df[[column for column in batch_columns if column in batch_df.columns]]
            display_batch_df = display_batch_df.rename(
                columns={
                    "trade_date": "Date",
                    "batch_qty": "Batch Qty",
                    "batch_price": "Batch Price",
                    "ltp": "LTP",
                    "present_value": "Present Value",
                    "batch_pnl": "Batch P&L",
                    "batch_pnl_pct": "Batch P&L %",
                    "age_days": "Age (Days)",
                    "present_age": "Present Age",
                }
            )
            st.dataframe(
                _style_pnl_columns(display_batch_df),
                width="stretch",
                height=_dataframe_height(len(display_batch_df)),#(numRows + 1) * 35 + 3
                hide_index=True,
            )


def replace_holdings_breakdown_in_supabase(df: pd.DataFrame) -> None:
    supabase_url = get_secret_value("SUPABASE_URL").strip().rstrip("/")
    supabase_key = get_secret_value("SUPABASE_SERVICE_ROLE_KEY").strip()
    table_name = get_secret_value("SUPABASE_HOLDINGS_TABLE_NAME").strip() or HOLDINGS_TABLE_NAME

    if not supabase_url or not supabase_key:
        raise ValueError(
            "Missing Supabase config. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "in .streamlit/secrets.toml or environment variables."
        )

    encoded_table_name = quote(table_name, safe="")
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    delete_endpoint = f"{supabase_url}/rest/v1/{encoded_table_name}?id=gte.0"
    try:
        delete_request = Request(delete_endpoint, headers=headers, method="DELETE")
        with urlopen(delete_request, timeout=60) as response:
            response.read()
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Failed to clear Supabase holdings table - HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to clear Supabase holdings table: {exc.reason}") from exc

    records = [_json_safe_record(record) for record in df.to_dict(orient="records")]
    insert_endpoint = f"{supabase_url}/rest/v1/{encoded_table_name}"
    for chunk in _chunk_records(records, SUPABASE_BATCH_SIZE):
        payload = json.dumps(chunk, allow_nan=False).encode("utf-8")
        request = Request(insert_endpoint, data=payload, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=60) as response:
                response.read()
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"Supabase holdings upload failed - HTTP {exc.code}: {body or exc.reason}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(f"Supabase holdings upload failed: {exc.reason}") from exc


st.subheader("Portfolio Holdings")


def _read_uploaded_file(uploaded_file) -> pd.DataFrame:
    filename = uploaded_file.name.lower()
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


def enrich_holdings_with_ta_metrics(df: pd.DataFrame, kite) -> pd.DataFrame:
    if df.empty or "instrument_token" not in df.columns:
        return df

    enriched_df = df.copy()
    as_of_date = datetime.now().date().isoformat()
    failed_symbols: list[str] = []
    for index, token in enriched_df["instrument_token"].items():
        if pd.isna(token):
            continue
        try:
            analytics_df = load_analytics_history(kite, token, as_of_date)
        except Exception:
            failed_symbols.append(str(enriched_df.loc[index].get("tradingsymbol", token)))
            continue

        metrics = build_metric_values(analytics_df)
        for column in TA_METRIC_COLUMNS:
            value = metrics.get(column)
            if value is not None:
                enriched_df.loc[index, column] = round(float(value), 2)

        returns = compute_period_returns(analytics_df, enriched_df.loc[index].get("last_price"))
        for column in RETURN_COLUMNS:
            value = returns.get(column)
            if value is not None:
                enriched_df.loc[index, column] = round(float(value), 2)

    if failed_symbols:
        st.warning(
            "Could not load TA metrics for: "
            + ", ".join(failed_symbols[:10])
            + ("..." if len(failed_symbols) > 10 else "")
        )

    return enriched_df


def _single_row_table(row: pd.Series, columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame([{column: row.get(column) for column in columns if column in row.index}])


def display_holding_ta_panels(df: pd.DataFrame) -> None:
    available_ta_columns = [column for column in TA_METRIC_COLUMNS if column in df.columns]
    available_return_columns = [column for column in RETURN_COLUMNS if column in df.columns]
    if not available_ta_columns and not available_return_columns:
        return

    st.caption("Technical metrics")
    for _, row in df.iterrows():
        symbol = _format_display_value(row.get("tradingsymbol"))
        with st.expander(symbol, expanded=False):
            if available_ta_columns:
                st.caption("High / Low / EMA")
                st.dataframe(
                    _single_row_table(row, available_ta_columns),
                    width="stretch",
                    hide_index=True,
                )
            if available_return_columns:
                st.caption("Returns")
                st.dataframe(
                    _single_row_table(row, available_return_columns),
                    width="stretch",
                    hide_index=True,
                )


def display_kite_holdings(df: pd.DataFrame, kite=None) -> pd.DataFrame | None:
    if df.empty:
        st.session_state["ltp_by_symbol"] = {}
        st.warning("No holdings found.")
        return None

    df = df.copy()
    if kite is not None:
        df = enrich_holdings_with_ta_metrics(df, kite)

    _cache_ltp_by_symbol(df)
    print("portfolio holdings columns:\n")
    print(df.columns)
    
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
            "average_price": "Average Price",
            "invested": "Invested",
            "CurrentValue": "Current Value",
            "last_price": "Last Price",            
            "pnl": "P&L",
            "pnl_pct": "P&L %",
            "day_change_percentage": "Day Change %",
        }
    )
    #st.table(display_df, width="stretch", height=_dataframe_height(len(display_df)))
    st.dataframe(_style_pnl_columns(display_df), width="stretch",height=_dataframe_height(len(display_df)))
    display_holding_ta_panels(df)
    
    total_invested = pd.to_numeric(df["invested"], errors="coerce").sum() if "invested" in df.columns else 0
    st.metric("Total Invested", f"Rs {total_invested:,.2f}", delta=f"{total_invested:.2f}")

    total_pnl = pd.to_numeric(df["pnl"], errors="coerce").sum() if "pnl" in df.columns else 0
    st.metric("Total P&L", f"Rs {total_pnl:,.2f}", delta=f"{total_pnl:.2f}")

    return df



def fetch_and_display_holdings():
    try:
        kite, _, _ = bootstrap_kite_app("Zerodha Holdings")
        holdings = kite.holdings()
        if holdings:
            df = pd.DataFrame(holdings)
            enriched_df = enrich_holdings_with_ta_metrics(df, kite)
            st.session_state["kite_holdings_df"] = enriched_df
            st.session_state["kite_holdings_download_filename"] = (
                f"holdings_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv"
            )
        else:
            st.session_state.pop("kite_holdings_df", None)
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


tab_upload_kite, tab_fetch_kite, tab_upload_holdings_breakdown = st.tabs(["Upload Kite Holdings", "Fetch from Kite","Upload Holdings Breakdown"])

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
        except ImportError as exc:
            st.error(f"Failed to read XLSX file. Install the missing dependency: {exc}")
        except Exception as exc:
            st.error(f"Failed to upload Kite holdings: {exc}")

with tab_fetch_kite:
    if st.button("Fetch Holdings from Kite", type="primary"):
        fetch_and_display_holdings()

    kite_holdings_df = st.session_state.get("kite_holdings_df")
    if kite_holdings_df is not None:
        display_kite_holdings(kite_holdings_df)
        st.download_button(
            "Download Kite Holdings as CSV",
            data=kite_holdings_df.to_csv(index=False),
            file_name=st.session_state.get(
                "kite_holdings_download_filename",
                f"holdings_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
            ),
            mime="text/csv",
            on_click="ignore",
        )


with tab_upload_holdings_breakdown:

    uploaded_brkholdings_file = st.file_uploader(
        "Upload holdings breakdown CSV or XLSX",
        type=["csv", "xlsx"],
    )

    if uploaded_brkholdings_file is not None:
        try:                    
            #_read_holdings_breakdown_upload(uploaded_holdings_file)
            brkdown_df = _read_uploaded_file(uploaded_brkholdings_file)              
            
            print("holdings breakdown before cleaning:\n", brkdown_df.head())
            holdings_breakdown_df = clean_holdings_breakdown_for_supabase(brkdown_df)
            
            print("holdings breakdown after cleaning:\n", holdings_breakdown_df.head())

            holdings_breakdown_df, unmatched_symbols = enrich_holdings_breakdown_with_ltp(
                holdings_breakdown_df,
                st.session_state.get("ltp_by_symbol", {}),
            )
            print("holdings breakdown after enrichment:\n", holdings_breakdown_df.head())

            if unmatched_symbols:
                st.warning(
                    "No live LTP found for: "
                    + ", ".join(unmatched_symbols[:10])
                    + ("..." if len(unmatched_symbols) > 10 else "")
                )

            display_holdings_breakdown_preview(holdings_breakdown_df)
            
            replace_holdings_breakdown_in_supabase(holdings_breakdown_df)
                        
        except ImportError as exc:
            st.error(f"Failed to read XLSX file. Install the missing dependency: {exc}")
        except Exception as exc:
            st.error(f"Failed to upload holdings breakdown: {exc}")



#if "access_token" in st.session_state:
#    if st.sidebar.button("Logout"):
#        clear_auth_state()
#        st.rerun()
