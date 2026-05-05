import json
import math
from datetime import date, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st

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
    "Date": "trade_date",
    "Batch Qty": "batch_qty",
    "Batch Price": "batch_price",
    "Age (Days)": "age_days",
    "Batch P&L": "batch_pnl",
    "Present Age": "present_age",
}
REQUIRED_HOLDINGS_COLUMNS = {"Row Type", "Symbol"}
NUMERIC_HOLDINGS_COLUMNS = [
    "total_qty",
    "buy_avg",
    "invested",
    "ltp",
    "present_value",
    "pnl",
    "batch_qty",
    "batch_price",
    "batch_pnl",
]
INTEGER_HOLDINGS_COLUMNS = ["age_days"]


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

    for column in df.columns:
        df[column] = df[column].apply(_json_safe_value)

    return df


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

    records = df.to_dict(orient="records")
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


kite, _, _ = bootstrap_kite_app("Zerodha Holdings")

try:
    holdings = kite.holdings()
    if holdings:
        df = pd.DataFrame(holdings)
        print(df.columns)
        #print(df.head())
        display_cols = [
            "tradingsymbol",
            "exchange",
            "price",
            "quantity",
            "average_price",
            "last_price",
            "day_change_percentage",
            "pnl"
            
        ]
        st.subheader("Your Portfolio Holdings")
        st.dataframe(df[display_cols], width="stretch")

        total_pnl = df["pnl"].sum()
        st.metric("Total P&L", f"₹{total_pnl:,.2f}", delta=f"{total_pnl:.2f}")
        
        st.download_button(
            "Download CSV",
            data=df.to_csv(),
            file_name=f"holdings_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
             mime="text/csv",
        )


    else:
        st.warning("No holdings found in this account.")
except Exception as exc:
    if is_token_error(exc):
        clear_auth_state()
        st.error("Your session expired. Please login again to view holdings.")
        st.rerun()
    st.error("Error fetching holdings. Please try again.")


st.divider()
st.subheader("Upload Holdings Breakdown")

uploaded_holdings_file = st.file_uploader(
    "Upload holdings breakdown CSV or XLSX",
    type=["csv", "xlsx"],
)

if uploaded_holdings_file is not None:
    try:
        holdings_breakdown_df = clean_holdings_breakdown_for_supabase(
            _read_holdings_breakdown_upload(uploaded_holdings_file)
        )
        st.dataframe(holdings_breakdown_df, width="stretch")

        if st.button("Replace Supabase holdings table", type="primary"):
            replace_holdings_breakdown_in_supabase(holdings_breakdown_df)
            st.success(
                f"Supabase table {HOLDINGS_TABLE_NAME} replaced with "
                f"{len(holdings_breakdown_df):,} row(s)."
            )
    except ImportError as exc:
        st.error(f"Failed to read XLSX file. Install the missing dependency: {exc}")
    except Exception as exc:
        st.error(f"Failed to upload holdings breakdown: {exc}")



if "access_token" in st.session_state:
    if st.sidebar.button("Logout"):
        clear_auth_state()
        st.rerun()
